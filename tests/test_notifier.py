"""Уведомления в Telegram: выбор событий и сборка текста."""

import pytest

from app.services import notifier


@pytest.mark.parametrize("raw, expected", [
    (None, notifier.DEFAULT_EVENTS),
    ("", ()),
    ("update_found", ("update_found",)),
    ("update_found,error", ("update_found", "error")),
    (" update_found , error ", ("update_found", "error")),
    ("update_found,несуществующее", ("update_found",)),
    ("check_started", ()),
])
def test_event_selection_is_parsed(raw, expected):
    assert notifier.parse_events(raw) == expected


def test_empty_selection_means_silence_not_defaults():
    """Снятые галочки — осознанный выбор, а не незаполненная настройка."""
    assert notifier.parse_events("") == ()
    assert notifier.parse_events(None) == notifier.DEFAULT_EVENTS


def test_routine_checks_are_not_reported_by_default():
    assert "no_changes" not in notifier.DEFAULT_EVENTS
    assert "check_started" not in notifier.AVAILABLE_EVENTS or "check_started" not in notifier.DEFAULT_EVENTS


def test_attention_events_are_reported_by_default():
    for event in ("update_found", "update_failed", "error", "qbittorrent_unavailable"):
        assert event in notifier.DEFAULT_EVENTS


def settings(events=("update_found",), token="t", chat_id="1"):
    return notifier.TelegramSettings(token=token, chat_id=chat_id, language="ru", events=events)


def test_not_configured_without_token_or_chat():
    assert not settings(token="").configured
    assert not settings(chat_id="").configured
    assert settings().configured


def test_wants_only_selected_events():
    chosen = settings(events=("update_found",))
    assert chosen.wants("update_found")
    assert not chosen.wants("error")


def test_message_carries_title_and_body():
    text = notifier.build_message(
        "update_found", "msg.update_found",
        {"new": 3, "existing": 470, "removed": 0},
        "Zenless Zero Zone OST", "ru",
    )
    assert "Zenless Zero Zone OST" in text
    assert "3" in text and "470" in text


def test_message_is_translated():
    params = {"new": 3, "existing": 470, "removed": 0}
    russian = notifier.build_message("update_found", "msg.update_found", params, "T", "ru")
    english = notifier.build_message("update_found", "msg.update_found", params, "T", "en")
    assert russian != english
    assert english.startswith("Update found")
    assert russian.startswith("Обновление найдено")


def test_message_without_title_has_no_empty_line():
    text = notifier.build_message("no_changes", "msg.no_changes", {}, "", "en")
    assert text == "No changes\n\nNo changes"


def test_send_without_settings_is_rejected_clearly():
    from app.errors import TranslatableError

    with pytest.raises(TranslatableError) as failure:
        notifier.send("текст", settings(token=""))
    assert failure.value.code == "error.telegram.not_configured"
