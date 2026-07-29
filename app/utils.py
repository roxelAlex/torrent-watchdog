from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SECRET_KEYS = {"passkey", "password", "token", "auth", "cookie", "sid"}


def mask_url(value: str) -> str:
    parts = urlsplit(value)
    if not parts.query:
        return value
    masked = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        masked.append((key, "***" if key.lower() in SECRET_KEYS else item))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(masked), parts.fragment))
