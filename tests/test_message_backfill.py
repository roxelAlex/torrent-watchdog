"""Разбор старых записей журнала обратно на коды.

Тексты взяты из живой базы — ровно те формулировки, что накопились за три месяца.
"""

import pytest

from app.i18n import translate
from app.services.message_backfill import classify


@pytest.mark.parametrize("message, event_type, code, params", [
    ("Проверка начата", "check_started", "msg.check_started", {}),
    ("Изменений нет", "no_changes", "msg.no_changes", {}),
    ("Раздача добавлена", "manual_action", "msg.torrent_added", {}),
    (
        "Найдено обновление. Новых файлов: 24, уже были: 452, удалены из раздачи: 23.",
        "update_found", "msg.update_found", {"new": 24, "existing": 452, "removed": 23},
    ),
    (
        "Обновление применено в режиме только новых файлов: старых файлов отключено 472, "
        "к скачиванию выбрано 3. Recheck не запускался.",
        "update_applied", "msg.update_applied.new_files_only", {"skipped": 472, "selected": 3},
    ),
    (
        "Категория изменена: «Music» → «music».",
        "manual_action", "msg.category_changed", {"old": "Music", "new": "music"},
    ),
    (
        "Ошибка применения: 409 Client Error: Conflict",
        "update_failed", "msg.update_failed", {"error": "409 Client Error: Conflict"},
    ),
    (
        "qBittorrent недоступен или отклонил операцию: 404 Client Error",
        "qbittorrent_unavailable", "msg.qbittorrent_unavailable",
        {"client": "", "error": "404 Client Error"},
    ),
])
def test_known_messages_are_classified(message, event_type, code, params):
    assert classify(message, event_type) == (code, params)


def test_free_form_error_keeps_its_text():
    text = "RuTracker не вернул успешный ответ после 3 попыток: 502 Server Error"
    assert classify(text, "error") == ("msg.raw", {"error": text})


def test_unknown_manual_action_is_left_alone():
    """Разовая легаси-запись: угадать нельзя, показываем как записали."""
    assert classify("Статус восстановлен после совместимости qBittorrent 5.x start/resume.", "manual_action") is None


def test_empty_message_is_left_alone():
    assert classify("", "check_started") is None


def test_classified_message_renders_in_both_languages():
    code, params = classify("Изменений нет", "no_changes")
    assert translate(code, "ru", **params) == "Изменений нет"
    assert translate(code, "en", **params) == "No changes"


def test_numbers_survive_the_round_trip():
    code, params = classify(
        "Найдено обновление. Новых файлов: 3, уже были: 472, удалены из раздачи: 0.", "update_found",
    )
    english = translate(code, "en", **params)
    assert "3" in english and "472" in english and english.startswith("Update found")
