import re
import logging
import threading
import time

import requests

from app.config import get_settings
from app.db import SessionLocal
from app.errors import InvalidInput, ServiceUnavailable, localize
from app.models import AppSetting
from app.services import flaresolverr, rutracker_auth
from app.services.torrent_parser import parse_torrent_bytes
from app.services.tracker_resolver import ResolvedTorrent, save_torrent_bytes


TOPIC_RE = re.compile(r"[?&]t=(\d+)")
logger = logging.getLogger(__name__)

# FlareSolverr — один браузер на контейнер. Две параллельные проверки заставляют его
# решать два вызова Cloudflare сразу, и тогда по таймауту падают оба: проверено,
# последовательно та же раздача проходит. Параллелить можно всё остальное, но не это.
_flaresolverr_lock = threading.Lock()


class SessionExpired(InvalidInput):
    """Трекер отдал не файл, а страницу — сессия истекла или её не приняли."""


def _runtime_settings(defaults: dict[str, str]) -> dict[str, str]:
    """Значения из app_settings с падением на .env — за одну сессию на весь резолв."""
    try:
        db = SessionLocal()
        try:
            saved = {
                item.key: item.value.strip()
                for item in db.query(AppSetting).filter(AppSetting.key.in_(defaults)).all()
            }
        finally:
            db.close()
    except Exception:
        saved = {}
    return {key: saved.get(key) or default for key, default in defaults.items()}


def _download_with_browser(downloader_url: str, source_url: str, download_url: str, cookie: str) -> bytes:
    response = requests.post(
        downloader_url,
        json={
            "source_url": source_url,
            "download_url": download_url,
            "cookies": flaresolverr.cookies_from_header(cookie),
        },
        timeout=100,
    )
    # 401 отдаётся, когда браузеру подсунули страницу вместо файла: проверку
    # содержимого делает сам endpoint, здесь мы только опознаём его вердикт.
    if response.status_code == 401:
        raise SessionExpired("error.rutracker.not_a_torrent", error=_error_text(response))
    response.raise_for_status()
    return response.content


def _error_text(response: requests.Response) -> str:
    """Текст ошибки из JSON-ответа: иначе в лог уезжают escape-последовательности."""
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:200]
    message = payload.get("error") if isinstance(payload, dict) else None
    return str(message or payload)[:200]


def _fetch_torrent(
    download_url: str,
    solver_url: str,
    headers: dict[str, str],
    flaresolver_endpoint: str | None,
) -> bytes:
    downloader_url = flaresolverr.extended_url(flaresolver_endpoint, "/download")
    if downloader_url:
        # Блокировка только на время работы браузера: ожидание между попытками
        # и обычные HTTP-запросы её не держат.
        with _flaresolverr_lock:
            content = _download_with_browser(downloader_url, solver_url, download_url, headers["Cookie"])
    elif flaresolver_endpoint:
        with _flaresolverr_lock:
            solved_cookie, solved_user_agent = flaresolverr.solve(
                flaresolver_endpoint,
                solver_url,
                headers["Cookie"],
                headers["User-Agent"],
            )
        request_headers = {**headers, "Cookie": solved_cookie, "User-Agent": solved_user_agent}
        response = requests.get(download_url, headers=request_headers, timeout=30)
        response.raise_for_status()
        content = response.content
    else:
        response = requests.get(download_url, headers=headers, timeout=30)
        response.raise_for_status()
        content = response.content

    if not content.startswith(b"d"):
        raise SessionExpired("error.rutracker.no_torrent")
    return content


def _download_torrent_until_success(
    download_url: str,
    solver_url: str,
    headers: dict[str, str],
    topic_id: str,
    flaresolver_endpoint: str | None,
    relogin=None,
) -> bytes:
    settings = get_settings()
    attempt = 1
    while True:
        try:
            content = _fetch_torrent(download_url, solver_url, headers, flaresolver_endpoint)
            logger.info("rutracker download success topic_id=%s attempt=%s", topic_id, attempt)
            return content
        except Exception as exc:
            logger.warning("rutracker download failed topic_id=%s attempt=%s error=%s", topic_id, attempt, exc)
            # Вместо файла пришла страница — почти всегда это протухшая сессия.
            # Если есть логин с паролем, входим заново и пробуем ещё раз.
            if isinstance(exc, SessionExpired) and relogin:
                try:
                    headers["Cookie"] = relogin()
                    logger.info("rutracker session refreshed, retrying topic_id=%s", topic_id)
                except rutracker_auth.LoginUnavailable as login_exc:
                    logger.warning("rutracker relogin unavailable: %s", login_exc)
                    raise login_exc from exc
            if settings.rutracker_max_attempts > 0 and attempt >= settings.rutracker_max_attempts:
                raise ServiceUnavailable("error.rutracker.attempts", attempts=attempt, error=localize(exc, None)) from exc
            attempt += 1
            time.sleep(max(1, settings.rutracker_retry_delay_seconds))


def resolve_rutracker(source_url: str, tracked_id: int | None = None) -> ResolvedTorrent:
    settings = get_settings()
    if not settings.rutracker_enabled:
        raise InvalidInput("error.rutracker.disabled")
    runtime = _runtime_settings({
        "rutracker_cookie": settings.rutracker_cookie,
        "rutracker_username": settings.rutracker_username,
        "rutracker_password": settings.rutracker_password,
        "flaresolver_address": settings.flaresolver_address,
        "flaresolver_port": str(settings.flaresolver_port),
    })
    flaresolver_endpoint = flaresolverr.endpoint_url(runtime["flaresolver_address"], runtime["flaresolver_port"])
    username = runtime["rutracker_username"]
    password = runtime["rutracker_password"]
    has_credentials = bool(username and password)

    def relogin() -> str:
        return rutracker_auth.refresh_cookie(flaresolver_endpoint, username, password, cookie)

    cookie = rutracker_auth.normalize_cookie(runtime["rutracker_cookie"])
    if not rutracker_auth.has_auth_cookie(cookie) and has_credentials:
        # Cookie нет или он без сессии — входим сами, вместо того чтобы падать.
        cookie = relogin()
    if not cookie:
        raise InvalidInput("error.rutracker.no_access")
    if not rutracker_auth.has_auth_cookie(cookie):
        raise InvalidInput("error.rutracker.cookie_incomplete")

    match = TOPIC_RE.search(source_url)
    if not match:
        raise InvalidInput("error.rutracker.no_topic_id")

    topic_id = match.group(1)
    download_url = f"https://rutracker.org/forum/dl.php?t={topic_id}"
    headers = {
        "Cookie": cookie,
        "User-Agent": settings.rutracker_user_agent,
        "Referer": source_url,
    }
    content = _download_torrent_until_success(
        download_url,
        source_url,
        headers,
        topic_id,
        flaresolver_endpoint,
        relogin=relogin if has_credentials else None,
    )
    meta = parse_torrent_bytes(content)
    path = save_torrent_bytes(content, tracked_id, meta.info_hash)
    return ResolvedTorrent(meta.info_hash, meta.name, str(path), "page_url", "rutracker")
