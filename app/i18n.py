"""Переводы интерфейса.

Новый язык добавляется одним файлом в app/locales: код языка — имя файла,
внутри NAME, DATE_FORMAT и MESSAGES. Ничего регистрировать не нужно, каталог
сканируется сам, и язык сразу появляется в переключателе.

Ключи семантические, а не русский текст: так правка формулировки на одном
языке не рвёт связь с остальными. Полноту каталогов стережёт тест
tests/test_i18n.py — он падает, если в переводе не хватает ключей.
"""

import importlib
import logging
import pkgutil
from typing import Any

from app import locales

logger = logging.getLogger(__name__)

# Язык, на котором пишется каталог-эталон: из него берутся недостающие строки.
SOURCE_LANGUAGE = "ru"


def _load() -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, str], dict[str, str]]:
    messages: dict[str, dict[str, str]] = {}
    names: dict[str, str] = {}
    date_formats: dict[str, str] = {}
    flags: dict[str, str] = {}
    for module in pkgutil.iter_modules(locales.__path__):
        code = module.name
        loaded = importlib.import_module(f"{locales.__name__}.{code}")
        messages[code] = getattr(loaded, "MESSAGES", {})
        names[code] = getattr(loaded, "NAME", code.upper())
        date_formats[code] = getattr(loaded, "DATE_FORMAT", "%d.%m.%Y %H:%M")
        # Флаг необязателен: без него язык всё равно работает, просто со значком глобуса.
        flags[code] = getattr(loaded, "FLAG", "🌐")
    return messages, names, date_formats, flags


MESSAGES, LANGUAGE_NAMES, DATE_FORMATS, LANGUAGE_FLAGS = _load()
LANGUAGES = tuple(sorted(MESSAGES, key=lambda code: (code != SOURCE_LANGUAGE, code)))


def normalize(language: str | None) -> str:
    """Приводит что угодно к поддерживаемому языку."""
    if language and language in MESSAGES:
        return language
    return SOURCE_LANGUAGE if SOURCE_LANGUAGE in MESSAGES else (LANGUAGES[0] if LANGUAGES else SOURCE_LANGUAGE)


def translate(key: str, language: str | None = None, **params: Any) -> str:
    """Строка по ключу. Недостающий перевод падает на язык-эталон, затем на сам ключ."""
    code = normalize(language)
    template = MESSAGES.get(code, {}).get(key)
    if template is None:
        template = MESSAGES.get(SOURCE_LANGUAGE, {}).get(key)
    if template is None:
        logger.warning("missing translation key=%s language=%s", key, code)
        return key
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        # Текст с параметрами лучше показать сырым, чем уронить страницу.
        logger.warning("cannot format translation key=%s language=%s", key, code)
        return template


def date_format(language: str | None = None) -> str:
    return DATE_FORMATS.get(normalize(language), "%d.%m.%Y %H:%M")


def flag(language: str | None = None) -> str:
    return LANGUAGE_FLAGS.get(normalize(language), "🌐")


def language_options() -> list[dict[str, str]]:
    return [
        {
            "code": code,
            "name": LANGUAGE_NAMES.get(code, code.upper()),
            "flag": LANGUAGE_FLAGS.get(code, "🌐"),
        }
        for code in LANGUAGES
    ]
