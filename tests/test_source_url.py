"""Под наблюдение берутся только темы RuTracker."""

import pytest

from app.services.tracker_resolver import normalize_source_url


@pytest.mark.parametrize("url", [
    "https://rutracker.org/forum/viewtopic.php?t=6760059",
    "http://rutracker.org/forum/viewtopic.php?t=1",
    "https://rutracker.org/forum/viewtopic.php?f=123&t=6760059",
    "https://www.rutracker.org/forum/viewtopic.php?t=6760059&start=50",
    # resolver всё равно собирает dl.php по topic id, так что такая ссылка равнозначна теме
    "https://rutracker.org/forum/dl.php?t=6760059",
])
def test_topic_urls_are_accepted(url):
    assert normalize_source_url(url) == url


def test_surrounding_spaces_are_trimmed():
    assert normalize_source_url("  https://rutracker.org/forum/viewtopic.php?t=42  ") == "https://rutracker.org/forum/viewtopic.php?t=42"


@pytest.mark.parametrize("url", [
    "magnet:?xt=urn:btih:abcdef0123456789abcdef0123456789abcdef01",
    "https://example.org/file.torrent",
    "https://rutracker.org/forum/index.php",  # раздел без темы
    "https://rutracker.org/forum/viewtopic.php?p=12345",  # ссылка на пост, topic id не извлечь
    "https://другой-трекер.org/forum/viewtopic.php?t=1",
    "",
    "   ",
])
def test_everything_else_is_rejected(url):
    with pytest.raises(ValueError, match="тему RuTracker"):
        normalize_source_url(url)
