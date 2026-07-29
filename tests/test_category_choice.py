"""Выбор категории списком и подсказки путей."""

import pytest

from app.routers.web import CUSTOM_CATEGORY, _chosen_category
from app.services.qbittorrent_registry import path_suggestions

CATEGORIES = [
    {"name": "Films", "save_path": "/films"},
    {"name": "Series", "save_path": "/series"},
    {"name": "prowlarr", "save_path": ""},
]


@pytest.mark.parametrize("choice, custom, expected", [
    ("music", "", "music"),
    ("", "", ""),
    (CUSTOM_CATEGORY, "новая", "новая"),
    (CUSTOM_CATEGORY, "  с пробелами  ", "с пробелами"),
    ("  music  ", "", "music"),
    # Выбрали «свою», но ничего не вписали — это «без категории», а не литерал __custom__.
    (CUSTOM_CATEGORY, "", ""),
    (CUSTOM_CATEGORY, "   ", ""),
])
def test_category_is_resolved(choice, custom, expected):
    assert _chosen_category(choice, custom) == expected


def test_custom_text_is_ignored_when_not_chosen():
    """Поле своей категории могло остаться заполненным от прошлого выбора."""
    assert _chosen_category("Films", "забытый текст") == "Films"


def test_marker_never_leaks_as_a_category_name():
    for custom in ("", "   ", "новая"):
        assert _chosen_category(CUSTOM_CATEGORY, custom) != CUSTOM_CATEGORY


def test_default_path_comes_first():
    result = path_suggestions(CATEGORIES, "/downloads")
    assert result[0] == {"path": "/downloads", "kind": "default", "category": ""}


def test_category_paths_follow_in_order():
    result = path_suggestions(CATEGORIES, "/downloads")
    assert [item["path"] for item in result] == ["/downloads", "/films", "/series"]


def test_category_without_path_is_skipped():
    """Подсказывать пустую строку незачем."""
    assert all(item["path"] for item in path_suggestions(CATEGORIES, "/downloads"))
    assert "prowlarr" not in [item["category"] for item in path_suggestions(CATEGORIES, "/downloads")]


def test_duplicate_paths_appear_once():
    twins = [{"name": "a", "save_path": "/same"}, {"name": "b", "save_path": "/same"}]
    result = path_suggestions(twins, "/same")
    assert [item["path"] for item in result] == ["/same"]


def test_works_without_default_path():
    result = path_suggestions(CATEGORIES, "")
    assert [item["path"] for item in result] == ["/films", "/series"]


def test_empty_input_gives_no_suggestions():
    assert path_suggestions([], "") == []
