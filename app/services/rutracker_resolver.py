import re
import logging
import time
from urllib.parse import urlsplit, urlunsplit

import requests

from app.config import get_settings
from app.db import SessionLocal
from app.models import AppSetting
from app.services.torrent_parser import parse_torrent_bytes
from app.services.tracker_resolver import ResolvedTorrent, save_torrent_bytes


TOPIC_RE = re.compile(r"[?&]t=(\d+)")
logger = logging.getLogger(__name__)


def _runtime_setting(key: str, default: str) -> str:
    try:
        db = SessionLocal()
        item = db.get(AppSetting, key)
        db.close()
        return item.value.strip() if item and item.value.strip() else default
    except Exception:
        return default


def _normalize_cookie(cookie: str) -> str:
    return re.sub(r"^Cookie:\s*", "", cookie.strip(), flags=re.IGNORECASE)


def _has_auth_cookie(cookie: str) -> bool:
    return any(marker in cookie for marker in ("bb_session=", "bb_data=", "bb_t="))


def _flaresolver_endpoint(address: str, port: str) -> str | None:
    address = address.strip().rstrip("/")
    if not address:
        return None
    if "://" not in address:
        address = f"http://{address}"

    parsed = urlsplit(address)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Адрес FlareSolverr должен начинаться с http:// или https://")
    try:
        configured_port = int(port)
    except ValueError as exc:
        raise ValueError("Порт FlareSolverr должен быть числом от 1 до 65535") from exc
    if not 1 <= configured_port <= 65535:
        raise ValueError("Порт FlareSolverr должен быть числом от 1 до 65535")

    try:
        has_port = parsed.port is not None
    except ValueError as exc:
        raise ValueError("Порт в адресе FlareSolverr указан неверно") from exc
    netloc = parsed.netloc if has_port else f"{parsed.netloc}:{configured_port}"
    return f"{urlunsplit((parsed.scheme, netloc, parsed.path, '', '')).rstrip('/')}/v1"


def _cookies_from_header(cookie: str) -> list[dict[str, str]]:
    cookies = []
    for part in cookie.split(";"):
        name, separator, value = part.strip().partition("=")
        if name and separator:
            cookies.append({"name": name, "value": value})
    return cookies


def _flaresolverr_request(endpoint: str, payload: dict[str, object]) -> dict[str, object]:
    response = requests.post(
        endpoint,
        json=payload,
        timeout=65,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("FlareSolverr вернул некорректный ответ") from exc
    if payload.get("status") != "ok":
        raise RuntimeError(f"FlareSolverr не обработал запрос: {payload.get('message') or 'неизвестная ошибка'}")
    return payload


def _flaresolverr_downloader_url(endpoint: str) -> str | None:
    parsed = urlsplit(endpoint)
    if parsed.hostname != "flaresolverr":
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, "/download", "", ""))


def _download_with_browser(downloader_url: str, source_url: str, download_url: str, cookie: str) -> bytes:
    response = requests.post(
        downloader_url,
        json={
            "source_url": source_url,
            "download_url": download_url,
            "cookies": _cookies_from_header(cookie),
        },
        timeout=100,
    )
    response.raise_for_status()
    return response.content


def _solve_with_flaresolverr(endpoint: str, source_url: str, cookie: str, fallback_user_agent: str) -> tuple[str, str]:
    payload = _flaresolverr_request(
        endpoint,
        {
            "cmd": "request.get",
            "url": source_url,
            "maxTimeout": 60000,
            "cookies": _cookies_from_header(cookie),
        },
    )
    solution = payload.get("solution")
    if not isinstance(solution, dict):
        raise RuntimeError("FlareSolverr не вернул решение")

    cookies = {item["name"]: item["value"] for item in _cookies_from_header(cookie)}
    for item in solution.get("cookies") or []:
        if isinstance(item, dict) and item.get("name") is not None and item.get("value") is not None:
            cookies[item["name"]] = item["value"]
    solved_cookie = "; ".join(f"{name}={value}" for name, value in cookies.items())
    user_agent = solution.get("userAgent")
    return solved_cookie, user_agent if isinstance(user_agent, str) else fallback_user_agent


def _download_torrent_until_success(
    download_url: str,
    solver_url: str,
    headers: dict[str, str],
    topic_id: str,
    flaresolver_endpoint: str | None,
) -> bytes:
    settings = get_settings()
    attempt = 1
    while True:
        try:
            downloader_url = _flaresolverr_downloader_url(flaresolver_endpoint) if flaresolver_endpoint else None
            if downloader_url:
                content = _download_with_browser(downloader_url, solver_url, download_url, headers["Cookie"])
            elif flaresolver_endpoint:
                solved_cookie, solved_user_agent = _solve_with_flaresolverr(
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
                raise ValueError("RuTracker не вернул .torrent. Проверьте cookies или доступность темы.")
            logger.info("rutracker download success topic_id=%s attempt=%s", topic_id, attempt)
            return content
        except Exception as exc:
            logger.warning("rutracker download failed topic_id=%s attempt=%s error=%s", topic_id, attempt, exc)
            if settings.rutracker_max_attempts > 0 and attempt >= settings.rutracker_max_attempts:
                raise RuntimeError(f"RuTracker не вернул успешный ответ после {attempt} попыток: {exc}") from exc
            attempt += 1
            time.sleep(max(1, settings.rutracker_retry_delay_seconds))


def resolve_rutracker(source_url: str, tracked_id: int | None = None) -> ResolvedTorrent:
    settings = get_settings()
    if not settings.rutracker_enabled:
        raise ValueError("RuTracker resolver отключён")
    rutracker_cookie = _normalize_cookie(_runtime_setting("rutracker_cookie", settings.rutracker_cookie))
    if not rutracker_cookie:
        raise ValueError("Для RuTracker нужно задать RUTRACKER_COOKIE")
    if not _has_auth_cookie(rutracker_cookie):
        raise ValueError("RuTracker cookie не содержит авторизационных cookie. Нужен полный Request Header Cookie, а не только cf_clearance.")
    flaresolver_endpoint = _flaresolver_endpoint(
        _runtime_setting("flaresolver_address", settings.flaresolver_address),
        _runtime_setting("flaresolver_port", str(settings.flaresolver_port)),
    )
    match = TOPIC_RE.search(source_url)
    if not match:
        raise ValueError("Не удалось определить topic_id RuTracker")

    topic_id = match.group(1)
    download_url = f"https://rutracker.org/forum/dl.php?t={topic_id}"
    headers = {
        "Cookie": rutracker_cookie,
        "User-Agent": settings.rutracker_user_agent,
        "Referer": source_url,
    }
    content = _download_torrent_until_success(download_url, source_url, headers, topic_id, flaresolver_endpoint)
    meta = parse_torrent_bytes(content)
    path = save_torrent_bytes(content, tracked_id, meta.info_hash)
    return ResolvedTorrent(meta.info_hash, meta.name, str(path), "page_url", "rutracker")
