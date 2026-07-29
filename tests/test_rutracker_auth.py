"""Разбор cookie и адресов FlareSolverr — то, что можно проверить без сети."""

import pytest

from app.services import flaresolverr
from app.services.rutracker_auth import has_auth_cookie, normalize_cookie


def test_cookie_header_prefix_is_stripped():
    assert normalize_cookie("Cookie: bb_session=1; bb_t=2") == "bb_session=1; bb_t=2"
    assert normalize_cookie("  cookie:bb_session=1  ") == "bb_session=1"


@pytest.mark.parametrize("cookie", ["bb_session=1", "cf_clearance=x; bb_data=2", "bb_t=3"])
def test_session_cookies_are_recognised(cookie):
    assert has_auth_cookie(cookie)


def test_cloudflare_cookie_alone_is_not_authorisation():
    """Ровно та ошибка, на которую пользователи попадаются чаще всего."""
    assert not has_auth_cookie("cf_clearance=abc; bb_guid=xyz")


def test_cookies_round_trip():
    header = "bb_session=1; cf_clearance=abc"
    assert flaresolverr.cookie_header(flaresolverr.cookies_from_header(header)) == header


def test_cookie_header_skips_broken_entries():
    assert flaresolverr.cookie_header([{"name": "a", "value": "1"}, {"value": "2"}, "мусор"]) == "a=1"


def test_cookie_header_deduplicates_by_name():
    """Браузер отдаёт cf_clearance и для домена, и для поддомена."""
    cookies = [
        {"name": "cf_clearance", "value": "старый", "domain": "rutracker.org"},
        {"name": "bb_session", "value": "1"},
        {"name": "cf_clearance", "value": "свежий", "domain": ".rutracker.org"},
    ]
    assert flaresolverr.cookie_header(cookies) == "cf_clearance=свежий; bb_session=1"


def test_extended_url_only_for_own_container():
    endpoint = "http://flaresolverr:8191/v1"
    assert flaresolverr.extended_url(endpoint, "/login") == "http://flaresolverr:8191/login"
    assert flaresolverr.extended_url(endpoint, "/download") == "http://flaresolverr:8191/download"


@pytest.mark.parametrize("endpoint", ["http://192.168.1.10:8191/v1", "https://solver.example/v1", None, ""])
def test_extended_url_absent_for_foreign_flaresolverr(endpoint):
    """У стандартного FlareSolverr наших endpoints нет — дёргать их нельзя."""
    assert flaresolverr.extended_url(endpoint, "/login") is None


def test_endpoint_url_adds_port_when_missing():
    assert flaresolverr.endpoint_url("http://flaresolverr", "8191") == "http://flaresolverr:8191/v1"
    assert flaresolverr.endpoint_url("http://flaresolverr:9000", "8191") == "http://flaresolverr:9000/v1"


def test_empty_address_disables_flaresolverr():
    assert flaresolverr.endpoint_url("", "8191") is None


@pytest.mark.parametrize("port", ["0", "70000", "не число"])
def test_bad_port_is_rejected(port):
    with pytest.raises(ValueError, match="Порт FlareSolverr"):
        flaresolverr.endpoint_url("http://flaresolverr", port)


def test_scheme_is_required():
    with pytest.raises(ValueError, match="http://"):
        flaresolverr.endpoint_url("ftp://flaresolverr", "8191")
