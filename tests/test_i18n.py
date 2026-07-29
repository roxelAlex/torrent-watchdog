"""Полнота каталогов. Тест стережёт добавление нового языка: пропущенный ключ виден сразу."""

import re
from pathlib import Path

import pytest

from app import i18n

TEMPLATES = Path("app/templates")
TRANSLATED = [code for code in i18n.LANGUAGES if code != i18n.SOURCE_LANGUAGE]


def test_source_language_exists():
    assert i18n.SOURCE_LANGUAGE in i18n.MESSAGES
    assert i18n.MESSAGES[i18n.SOURCE_LANGUAGE]


def test_english_is_available():
    assert "en" in i18n.LANGUAGES


@pytest.mark.parametrize("code", TRANSLATED)
def test_no_missing_keys(code):
    missing = sorted(set(i18n.MESSAGES[i18n.SOURCE_LANGUAGE]) - set(i18n.MESSAGES[code]))
    assert not missing, f"в каталоге {code} не хватает ключей: {missing}"


@pytest.mark.parametrize("code", TRANSLATED)
def test_no_extra_keys(code):
    extra = sorted(set(i18n.MESSAGES[code]) - set(i18n.MESSAGES[i18n.SOURCE_LANGUAGE]))
    assert not extra, f"в каталоге {code} лишние ключи: {extra}"


@pytest.mark.parametrize("code", TRANSLATED)
def test_placeholders_match(code):
    """Параметр, потерянный при переводе, — это дырка в тексте у пользователя."""
    source = i18n.MESSAGES[i18n.SOURCE_LANGUAGE]
    broken = {}
    for key, template in i18n.MESSAGES[code].items():
        expected = set(re.findall(r"{(\w+)}", source.get(key, "")))
        actual = set(re.findall(r"{(\w+)}", template))
        if expected != actual:
            broken[key] = (sorted(expected), sorted(actual))
    assert not broken, f"в каталоге {code} расходятся параметры: {broken}"


@pytest.mark.parametrize("code", i18n.LANGUAGES)
def test_language_has_name_and_date_format(code):
    assert i18n.LANGUAGE_NAMES.get(code)
    assert "%" in i18n.date_format(code)


def test_template_keys_are_all_translated():
    """Ключ, которого нет в каталоге, покажется пользователю как есть."""
    used = set()
    for template in TEMPLATES.glob("*.html"):
        used |= set(re.findall(r"""\bt\(\s*["']([\w.]+)["']""", template.read_text()))
    unknown = sorted(used - set(i18n.MESSAGES[i18n.SOURCE_LANGUAGE]))
    assert not unknown, f"в шаблонах используются ключи без перевода: {unknown}"


def test_unknown_language_falls_back_to_source():
    assert i18n.normalize("klingon") == i18n.SOURCE_LANGUAGE
    assert i18n.normalize(None) == i18n.SOURCE_LANGUAGE


def test_missing_key_returns_itself_instead_of_crashing():
    assert i18n.translate("no.such.key", "en") == "no.such.key"


def test_translation_differs_between_languages():
    assert i18n.translate("nav.torrents", "ru") != i18n.translate("nav.torrents", "en")


def test_params_are_substituted():
    assert "7" in i18n.translate("watch.errors.headline", "en", count=7)
    assert "7" in i18n.translate("watch.errors.headline", "ru", count=7)
