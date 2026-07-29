"""Вход на RuTracker логином и паролем.

Cookie сессии живут недолго, и переклеивать их руками — основная
эксплуатационная боль. Вход выполняется браузером внутри нашего FlareSolverr:
перед формой стоит Cloudflare, обычным POST её не пройти, а значение кнопки
«вход» трекер ждёт в cp1251 — настоящая отправка формы решает это сама.

Капчу автоматизировать нельзя. Если трекер её показал, единственный выход —
вставить cookie руками, и сервис говорит об этом прямо.
"""

import logging
import re
import threading
import time

import requests

from app.config import get_settings
from app.db import SessionLocal
from app.models import AppSetting
from app.services import flaresolverr

logger = logging.getLogger(__name__)

LOGIN_URL = "https://rutracker.org/forum/login.php"
AUTH_COOKIE_MARKERS = ("bb_session=", "bb_data=", "bb_t=")

_login_lock = threading.Lock()
_last_attempt_at = 0.0


class LoginUnavailable(RuntimeError):
    """Войти не получилось, и повтор сам по себе не поможет."""


def normalize_cookie(cookie: str) -> str:
    return re.sub(r"^Cookie:\s*", "", cookie.strip(), flags=re.IGNORECASE)


def has_auth_cookie(cookie: str) -> bool:
    return any(marker in cookie for marker in AUTH_COOKIE_MARKERS)


def stored_cookie() -> str:
    db = SessionLocal()
    try:
        item = db.get(AppSetting, "rutracker_cookie")
        return normalize_cookie(item.value) if item and item.value else ""
    finally:
        db.close()


def _store_cookie(cookie: str) -> None:
    db = SessionLocal()
    try:
        db.merge(AppSetting(key="rutracker_cookie", value=cookie))
        db.commit()
    finally:
        db.close()


def _login_via_browser(login_endpoint: str, username: str, password: str, cookie: str) -> str:
    response = requests.post(
        login_endpoint,
        json={
            "username": username,
            "password": password,
            "login_url": LOGIN_URL,
            "cookies": flaresolverr.cookies_from_header(cookie),
        },
        timeout=150,
    )
    if response.status_code >= 500:
        raise LoginUnavailable(f"FlareSolverr не смог выполнить вход: {response.text.strip()[:200]}")
    response.raise_for_status()
    payload = response.json()

    status = payload.get("status")
    if status == "captcha":
        raise LoginUnavailable(
            "RuTracker запросил капчу при входе. Войдите в браузере и вставьте cookie "
            "на странице «Настройки» — по логину и паролю сейчас не пустит."
        )
    if status == "rejected":
        raise LoginUnavailable("RuTracker не принял логин или пароль. Проверьте их на странице «Настройки».")
    if status != "ok":
        raise LoginUnavailable(f"Неожиданный ответ входа: {payload.get('message') or status}")

    fresh_cookie = flaresolverr.cookie_header(payload.get("cookies") or [])
    if not has_auth_cookie(fresh_cookie):
        raise LoginUnavailable("Вход прошёл, но трекер не выдал cookie сессии.")
    return fresh_cookie


def refresh_cookie(
    endpoint: str | None,
    username: str,
    password: str,
    current_cookie: str,
    force: bool = False,
) -> str:
    """Войти заново и сохранить свежий cookie. Возвращает его же.

    Параллельные проверки не должны логиниться каждая: пока один поток входит,
    остальные ждут и подхватывают его результат. force — для кнопки в настройках:
    её нажал человек, ограничение частоты тут только мешает.
    """
    global _last_attempt_at

    if not username or not password:
        raise LoginUnavailable("Логин и пароль RuTracker не заданы — вход по ним невозможен.")
    login_endpoint = flaresolverr.extended_url(endpoint, "/login")
    if not login_endpoint:
        raise LoginUnavailable(
            "Вход по логину и паролю работает только через собственный FlareSolverr из этого compose-файла "
            "(адрес http://flaresolverr). Для стороннего FlareSolverr вставьте cookie вручную."
        )

    with _login_lock:
        saved = stored_cookie()
        if not force and saved and saved != current_cookie and has_auth_cookie(saved):
            logger.info("rutracker cookie already refreshed by another check")
            return saved

        interval = get_settings().rutracker_login_min_interval_seconds
        waited = time.monotonic() - _last_attempt_at
        if not force and _last_attempt_at and waited < interval:
            raise LoginUnavailable(
                f"Повторный вход на RuTracker возможен через {int(interval - waited)} с. "
                "Так сервис не долбит трекер при неверном пароле."
            )
        _last_attempt_at = time.monotonic()

        logger.info("rutracker login attempt user=%s", username)
        fresh_cookie = _login_via_browser(login_endpoint, username, password, current_cookie)
        _store_cookie(fresh_cookie)
        logger.info("rutracker login succeeded, cookie stored")
        return fresh_cookie
