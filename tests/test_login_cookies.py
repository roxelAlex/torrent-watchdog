"""Регрессия: на страницу входа нельзя отдавать cookie живой сессии.

Залогиненному пользователю RuTracker отдаёт login.php вообще без формы входа,
и вход становится невозможен именно потому, что сессия ещё жива.
"""

from app.services.rutracker_auth import cookies_for_login_page

FULL_COOKIE = (
    "bb_guid=Wan3; bb_ssl=1; bb_session=0-1234567-secret; "
    "bb_t=a%3A3; cf_clearance=kOOVGNt"
)


def names(cookie: str) -> set[str]:
    return {item["name"] for item in cookies_for_login_page(cookie)}


def test_session_cookies_are_stripped():
    assert names(FULL_COOKIE).isdisjoint({"bb_session", "bb_t", "bb_data"})


def test_cloudflare_cookies_are_kept():
    """Без cf_clearance страница входа упрётся в проверку Cloudflare."""
    assert names(FULL_COOKIE) == {"cf_clearance", "bb_guid", "bb_ssl"}


def test_values_survive():
    assert {"name": "cf_clearance", "value": "kOOVGNt"} in cookies_for_login_page(FULL_COOKIE)


def test_empty_cookie_is_fine():
    assert cookies_for_login_page("") == []


def test_only_session_cookie_leaves_nothing_to_pass():
    assert cookies_for_login_page("bb_session=abc") == []
