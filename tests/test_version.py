"""Версия должна приходить из образа, а не из пользовательского .env.

Иначе после обновления контейнера подвал страницы и /health показывали бы
старый номер: .env копируют один раз и больше не трогают.
"""

from pathlib import Path

from app.config import APP_VERSION, get_settings

VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def test_version_matches_the_file():
    assert APP_VERSION == VERSION_FILE.read_text(encoding="utf-8").strip()


def test_settings_expose_the_same_version():
    assert get_settings().app_version == APP_VERSION


def test_environment_cannot_override_it(monkeypatch):
    """Оставшийся в чужом .env APP_VERSION не должен ничего подменять."""
    monkeypatch.setenv("APP_VERSION", "9.9.9")
    get_settings.cache_clear()
    try:
        assert get_settings().app_version == APP_VERSION
    finally:
        get_settings.cache_clear()


def test_version_looks_like_a_version():
    parts = APP_VERSION.split(".")
    assert len(parts) == 3 and all(part.isdigit() for part in parts), APP_VERSION
