"""Ошибки, которые видит пользователь.

Сервисы не знают, на каком языке читают страницу, поэтому бросают код и
параметры, а текст собирается в роутере. str() даёт текст на языке-эталоне —
чтобы в логах и в трейсбеке было видно, что случилось.
"""

from typing import Any

from app.i18n import translate


class TranslatableError(Exception):
    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        super().__init__(translate(code, None, **params))

    def localized(self, language: str | None) -> str:
        return translate(self.code, language, **self.params)


class InvalidInput(TranslatableError, ValueError):
    """Пользователь ввёл что-то не то — сообщение объясняет, что именно."""


class ServiceUnavailable(TranslatableError, RuntimeError):
    """Внешняя система не ответила или отказала."""


def localize(exc: BaseException, language: str | None) -> str:
    """Текст любой ошибки на нужном языке: чужие исключения показываются как есть."""
    if isinstance(exc, TranslatableError):
        return exc.localized(language)
    return str(exc)
