"""Откуда берётся папка загрузки.

Пустой save_path не значит «никуда»: qBittorrent берёт путь категории.
Проверено на живых раздачах — обе с пустым save_path лежат в /music и /series.
"""

import pytest

from app.models import TrackedTorrent
from app.routers.web import _effective_save_path

CATEGORIES = [
    {"name": "music", "save_path": "/music"},
    {"name": "Series", "save_path": "/series"},
    {"name": "lidarr", "save_path": ""},
]


def torrent(save_path: str = "", category: str = "") -> TrackedTorrent:
    return TrackedTorrent(title="Раздача", source_url="https://rutracker.org/forum/viewtopic.php?t=1",
                          save_path=save_path, category=category)


def test_explicit_path_wins():
    """Заполненное поле перебивает категорию — так же делает и сам qBittorrent."""
    result = _effective_save_path(torrent(save_path="/custom", category="music"), CATEGORIES)
    assert result == {"path": "/custom", "source": "explicit"}


def test_empty_path_falls_back_to_category():
    result = _effective_save_path(torrent(category="music"), CATEGORIES)
    assert result == {"path": "/music", "source": "category"}


def test_category_without_path_resolves_to_its_subfolder():
    """qBittorrent кладёт такие раздачи в подпапку с именем категории."""
    result = _effective_save_path(torrent(category="lidarr"), CATEGORIES, client_default="/downloads")
    assert result == {"path": "/downloads/lidarr", "source": "category"}


def test_no_category_means_client_default():
    result = _effective_save_path(torrent(), CATEGORIES)
    assert result == {"path": "", "source": "client"}


def test_client_default_path_is_used_when_known():
    """qBittorrent сам знает свой путь по умолчанию — спрашиваем, а не описываем словами."""
    result = _effective_save_path(torrent(), CATEGORIES, client_default="/downloads")
    assert result == {"path": "/downloads", "source": "client"}


def test_unknown_category_gives_no_path_even_with_a_default():
    """Категории нет в клиенте — куда лягут файлы, неизвестно."""
    result = _effective_save_path(torrent(category="исчезнувшая"), CATEGORIES, client_default="/downloads")
    assert result == {"path": "", "source": "unknown"}


def test_explicit_path_ignores_client_default():
    result = _effective_save_path(torrent(save_path="/custom"), CATEGORIES, client_default="/downloads")
    assert result["path"] == "/custom"


def test_unknown_category_is_not_invented():
    """Категорию завели в сервисе, но в клиенте её нет: путь придумывать нельзя."""
    result = _effective_save_path(torrent(category="исчезнувшая"), CATEGORIES)
    assert result["path"] == ""


def test_categories_unavailable_does_not_crash():
    assert _effective_save_path(torrent(category="music"), [])["source"] == "unknown"


@pytest.mark.parametrize("category, expected", [("music", "/music"), ("Series", "/series")])
def test_live_torrents_resolve_to_their_real_folders(category, expected):
    """Ровно те пары, что стоят у раздач сейчас: в qBittorrent они лежат именно там."""
    assert _effective_save_path(torrent(category=category), CATEGORIES)["path"] == expected
