"""Сообщения событий: код плюс параметры вместо готового текста.

Событие живёт в базе годами, а язык читателя может смениться завтра, поэтому
в базу пишется, что произошло, а не как это назвать. Текст собирается при
показе. У записей, сделанных до появления переводов, кода нет — тогда
показывается сохранённый тогда текст как есть.
"""

import json
import logging
from typing import Any

from app.i18n import translate
from app.models import CheckEvent, TrackedTorrent

logger = logging.getLogger(__name__)


def event(
    tracked_id: int | None,
    event_type: str,
    code: str,
    old_hash: str | None = None,
    new_hash: str | None = None,
    **params: Any,
) -> CheckEvent:
    return CheckEvent(
        tracked_torrent_id=tracked_id,
        event_type=event_type,
        message_code=code,
        message_params=dump_params(params),
        old_info_hash=old_hash,
        new_info_hash=new_hash,
        # Текст на языке-эталоне остаётся в message: базу читают и без приложения.
        message=translate(code, None, **params),
    )


def set_error(tracked: TrackedTorrent, code: str, **params: Any) -> str:
    tracked.last_error_code = code
    tracked.last_error_params = dump_params(params)
    tracked.last_error = translate(code, None, **params)
    return tracked.last_error


def clear_error(tracked: TrackedTorrent) -> None:
    tracked.last_error = None
    tracked.last_error_code = None
    tracked.last_error_params = None


def dump_params(params: dict[str, Any]) -> str | None:
    return json.dumps(params, ensure_ascii=False) if params else None


def load_params(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("cannot parse message params: %r", raw[:80])
        return {}
    return parsed if isinstance(parsed, dict) else {}


def render(code: str | None, params: str | None, fallback: str | None, language: str | None) -> str:
    """Текст сообщения на нужном языке; без кода — сохранённый текст как есть."""
    if not code:
        return fallback or ""
    return translate(code, language, **load_params(params))


def render_event(item: CheckEvent, language: str | None) -> str:
    return render(item.message_code, item.message_params, item.message, language)


def render_torrent_error(tracked: TrackedTorrent, language: str | None) -> str:
    return render(tracked.last_error_code, tracked.last_error_params, tracked.last_error, language)
