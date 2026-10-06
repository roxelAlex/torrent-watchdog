"""Транспорт до FlareSolverr: разбор адреса, cookie и вызовы API.

Отдельный модуль, потому что через FlareSolverr ходят и скачивание раздачи,
и вход на трекер, а зависеть друг от друга им незачем.
"""

import logging
import socket

import requests

from app.errors import InvalidInput, ServiceUnavailable
from app.i18n import translate

from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# Скачивание и вход браузером — это собственные endpoints нашего образа
# (Dockerfile.flaresolverr). У стандартного FlareSolverr их нет, поэтому
# включаем их только для контейнера из этого compose-файла: по алиасу,
# заданному в compose, или по его container_name.
EXTENDED_HOSTNAME = "flaresolverr"
CONTAINER_HOSTNAME = "torrent-watchdog-flaresolverr"
EXTENDED_HOSTNAMES = {EXTENDED_HOSTNAME, CONTAINER_HOSTNAME}

# Столько же ждёт браузер в нашем образе (CHALLENGE_TIMEOUT_MS), плюс запас на
# сам HTTP-обмен: ответ должен прийти позже, чем FlareSolverr сдастся, иначе мы
# оборвём соединение и не увидим причину.
CHALLENGE_TIMEOUT_MS = 120000
CALL_TIMEOUT_SECONDS = 130


def _resolves(hostname: str) -> bool:
    try:
        socket.getaddrinfo(hostname, None)
    except OSError:
        return False
    return True


def _own_container_host(hostname: str) -> str:
    """Алиас flaresolverr, а если он пропал из сети — имя контейнера.

    Алиас живёт только в compose. Обновлялки вроде dockpeek или Watchtower
    пересоздают контейнер сами и теряют его, а имя контейнера Docker
    регистрирует в сети всегда — по нему наш FlareSolverr и находится.
    """
    if hostname != EXTENDED_HOSTNAME or _resolves(hostname) or not _resolves(CONTAINER_HOSTNAME):
        return hostname
    logger.warning(
        "FlareSolverr alias %r does not resolve, using container name %r; "
        "recreate containers with `docker compose up -d --force-recreate` to restore the alias",
        EXTENDED_HOSTNAME,
        CONTAINER_HOSTNAME,
    )
    return CONTAINER_HOSTNAME


def endpoint_url(address: str, port: str) -> str | None:
    address = address.strip().rstrip("/")
    if not address:
        return None
    if "://" not in address:
        address = f"http://{address}"

    parsed = urlsplit(address)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InvalidInput("error.flare.scheme")
    try:
        configured_port = int(port)
    except ValueError as exc:
        raise InvalidInput("error.flare.port") from exc
    if not 1 <= configured_port <= 65535:
        raise InvalidInput("error.flare.port")

    try:
        has_port = parsed.port is not None
    except ValueError as exc:
        raise InvalidInput("error.flare.port_in_address") from exc
    netloc = parsed.netloc if has_port else f"{parsed.netloc}:{configured_port}"
    host = _own_container_host(parsed.hostname)
    if host != parsed.hostname:
        netloc = netloc.replace(parsed.hostname, host, 1)
    return f"{urlunsplit((parsed.scheme, netloc, parsed.path, '', '')).rstrip('/')}/v1"


def extended_url(endpoint: str | None, path: str) -> str | None:
    """Адрес нашего расширения или None, если настроен обычный FlareSolverr."""
    if not endpoint:
        return None
    parsed = urlsplit(endpoint)
    if parsed.hostname not in EXTENDED_HOSTNAMES:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def cookies_from_header(cookie: str) -> list[dict[str, str]]:
    cookies = []
    for part in cookie.split(";"):
        name, separator, value = part.strip().partition("=")
        if name and separator:
            cookies.append({"name": name, "value": value})
    return cookies


def cookie_header(cookies: list[dict]) -> str:
    """Собирает строку Cookie, отбрасывая повторы по имени.

    Браузер отдаёт один и тот же cookie для домена и поддомена (`rutracker.org`
    и `.rutracker.org`), а дважды названный cookie в заголовке — заявка на
    неприятности. Побеждает последний: он свежее.
    """
    unique: dict[str, str] = {}
    for item in cookies:
        if isinstance(item, dict) and item.get("name") and item.get("value") is not None:
            unique[item["name"]] = item["value"]
    return "; ".join(f"{name}={value}" for name, value in unique.items())


def call(endpoint: str, payload: dict[str, object], timeout: int = CALL_TIMEOUT_SECONDS) -> dict[str, object]:
    response = requests.post(endpoint, json=payload, timeout=timeout)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise ServiceUnavailable("error.flare.bad_response") from exc
    if payload.get("status") != "ok":
        raise ServiceUnavailable("error.flare.rejected", error=payload.get("message") or translate("error.flare.unknown"))
    return payload


def solve(endpoint: str, source_url: str, cookie: str, fallback_user_agent: str) -> tuple[str, str]:
    """Пройти Cloudflare и вернуть актуальные cookie и User-Agent."""
    payload = call(
        endpoint,
        {
            "cmd": "request.get",
            "url": source_url,
            "maxTimeout": CHALLENGE_TIMEOUT_MS,
            "cookies": cookies_from_header(cookie),
        },
    )
    solution = payload.get("solution")
    if not isinstance(solution, dict):
        raise ServiceUnavailable("error.flare.no_solution")

    cookies = {item["name"]: item["value"] for item in cookies_from_header(cookie)}
    for item in solution.get("cookies") or []:
        if isinstance(item, dict) and item.get("name") is not None and item.get("value") is not None:
            cookies[item["name"]] = item["value"]
    solved_cookie = "; ".join(f"{name}={value}" for name, value in cookies.items())
    user_agent = solution.get("userAgent")
    return solved_cookie, user_agent if isinstance(user_agent, str) else fallback_user_agent
