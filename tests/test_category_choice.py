"""Выбор категории списком и подсказки путей."""

import pytest

from app.routers.web import CUSTOM_CATEGORY, _chosen_category, _chosen_category_path
from app.services.qbittorrent_registry import category_save_path, path_suggestions, with_effective_paths

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


@pytest.mark.parametrize("choice, path, expected", [
    (CUSTOM_CATEGORY, "/anime", "/anime"),
    (CUSTOM_CATEGORY, "  /anime  ", "/anime"),
    (CUSTOM_CATEGORY, "", ""),
    # У существующей категории путь уже задан — поле формы к ней не относится.
    ("Films", "/anime", ""),
    ("", "/anime", ""),
])
def test_custom_path_belongs_only_to_a_new_category(choice, path, expected):
    assert _chosen_category_path(choice, path) == expected


def test_category_with_own_path():
    assert category_save_path("Films", CATEGORIES, "/downloads") == "/films"


def test_category_without_path_gets_a_subfolder():
    """Проверено на живых клиентах: lidarr без пути даёт /downloads/lidarr."""
    assert category_save_path("prowlarr", CATEGORIES, "/downloads") == "/downloads/prowlarr"


def test_trailing_slash_in_default_does_not_double_up():
    assert category_save_path("prowlarr", CATEGORIES, "/downloads/") == "/downloads/prowlarr"


def test_no_category_means_default_path():
    assert category_save_path("", CATEGORIES, "/downloads") == "/downloads"


def test_unknown_category_path_is_not_invented():
    """Категории нет в клиенте — угадывать, куда лягут файлы, нельзя."""
    assert category_save_path("исчезнувшая", CATEGORIES, "/downloads") == ""


def test_without_default_path_subfolder_cannot_be_built():
    assert category_save_path("prowlarr", CATEGORIES, "") == ""


def test_effective_paths_are_attached_to_every_category():
    result = with_effective_paths(CATEGORIES, "/downloads")
    assert {item["name"]: item["effective_path"] for item in result} == {
        "Films": "/films",
        "Series": "/series",
        "prowlarr": "/downloads/prowlarr",
    }
    # Исходные поля не теряются.
    assert result[0]["save_path"] == "/films"


def test_default_path_comes_first():
    result = path_suggestions(CATEGORIES, "/downloads")
    assert result[0] == {"path": "/downloads", "kind": "default", "category": ""}


def test_category_paths_follow_in_order():
    result = path_suggestions(CATEGORIES, "/downloads")
    assert [item["path"] for item in result] == ["/downloads", "/films", "/series", "/downloads/prowlarr"]


def test_category_without_path_suggests_its_subfolder():
    """Раньше такая категория молча пропадала из подсказок, хотя папка у неё есть."""
    result = path_suggestions(CATEGORIES, "/downloads")
    assert {"path": "/downloads/prowlarr", "kind": "category", "category": "prowlarr"} in result


def test_duplicate_paths_appear_once():
    twins = [{"name": "a", "save_path": "/same"}, {"name": "b", "save_path": "/same"}]
    result = path_suggestions(twins, "/same")
    assert [item["path"] for item in result] == ["/same"]


def test_works_without_default_path():
    """Без пути по умолчанию подпапку не построить — остаются только явные пути."""
    result = path_suggestions(CATEGORIES, "")
    assert [item["path"] for item in result] == ["/films", "/series"]


def test_empty_input_gives_no_suggestions():
    assert path_suggestions([], "") == []
