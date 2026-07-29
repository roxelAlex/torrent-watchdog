"""Транспорт до FlareSolverr: разбор адреса, cookie и вызовы API.

Отдельный модуль, потому что через FlareSolverr ходят и скачивание раздачи,
и вход на трекер, а зависеть друг от друга им незачем.
"""

import requests

from urllib.parse import urlsplit, urlunsplit

# Скачивание и вход браузером — это собственные endpoints нашего образа
# (Dockerfile.flaresolverr). У стандартного FlareSolverr их нет, поэтому
# включаем их только для контейнера из этого compose-файла.
EXTENDED_HOSTNAME = "flaresolverr"


def endpoint_url(address: str, port: str) -> str | None:
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


def extended_url(endpoint: str | None, path: str) -> str | None:
    """Адрес нашего расширения или None, если настроен обычный FlareSolverr."""
    if not endpoint:
        return None
    parsed = urlsplit(endpoint)
    if parsed.hostname != EXTENDED_HOSTNAME:
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


def call(endpoint: str, payload: dict[str, object], timeout: int = 65) -> dict[str, object]:
    response = requests.post(endpoint, json=payload, timeout=timeout)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("FlareSolverr вернул некорректный ответ") from exc
    if payload.get("status") != "ok":
        raise RuntimeError(f"FlareSolverr не обработал запрос: {payload.get('message') or 'неизвестная ошибка'}")
    return payload


def solve(endpoint: str, source_url: str, cookie: str, fallback_user_agent: str) -> tuple[str, str]:
    """Пройти Cloudflare и вернуть актуальные cookie и User-Agent."""
    payload = call(
        endpoint,
        {
            "cmd": "request.get",
            "url": source_url,
            "maxTimeout": 60000,
            "cookies": cookies_from_header(cookie),
        },
    )
    solution = payload.get("solution")
    if not isinstance(solution, dict):
        raise RuntimeError("FlareSolverr не вернул решение")

    cookies = {item["name"]: item["value"] for item in cookies_from_header(cookie)}
    for item in solution.get("cookies") or []:
        if isinstance(item, dict) and item.get("name") is not None and item.get("value") is not None:
            cookies[item["name"]] = item["value"]
    solved_cookie = "; ".join(f"{name}={value}" for name, value in cookies.items())
    user_agent = solution.get("userAgent")
    return solved_cookie, user_agent if isinstance(user_agent, str) else fallback_user_agent
