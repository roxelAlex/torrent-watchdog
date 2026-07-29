"""Уведомления в Telegram.

Отправка идёт в отдельном потоке и никогда не роняет проверку: недоступный
Telegram — это не повод считать, что раздача не проверена. Ошибки только в лог.

Язык уведомлений задаётся отдельно от языка интерфейса: у планировщика нет ни
запроса, ни cookie, а читать сообщения может вообще другой человек.
"""

import html
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from app.config import get_settings
from app.db import SessionLocal
from app.i18n import translate
from app.models import AppSetting, TrackedTorrent

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
SEND_TIMEOUT_SECONDS = 15

# Значок вместо цветной плашки: в списке чатов видно, что случилось, не открывая.
EVENT_ICONS = {
    "update_found": "🆕",
    "update_applied": "✅",
    "update_failed": "❌",
    "error": "⚠️",
    "qbittorrent_unavailable": "🔌",
    "no_changes": "💤",
    "manual_action": "✋",
}
DEFAULT_ICON = "🔔"

# События, о которых имеет смысл писать по умолчанию: те, что требуют решения
# или означают поломку. Проверки без изменений сюда не входят — их две в день.
DEFAULT_EVENTS = ("update_found", "update_applied", "update_failed", "error", "qbittorrent_unavailable")
AVAILABLE_EVENTS = (
    "update_found",
    "update_applied",
    "update_failed",
    "error",
    "qbittorrent_unavailable",
    "no_changes",
    "manual_action",
)

_sender = ThreadPoolExecutor(max_workers=1, thread_name_prefix="telegram")
_lock = threading.Lock()


class TelegramSettings:
    __slots__ = ("token", "chat_id", "language", "events")

    def __init__(self, token: str, chat_id: str, language: str, events: tuple[str, ...]) -> None:
        self.token = token
        self.chat_id = chat_id
        self.language = language
        self.events = events

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def wants(self, event_type: str) -> bool:
        return event_type in self.events


def parse_events(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return DEFAULT_EVENTS
    chosen = tuple(part.strip() for part in raw.split(",") if part.strip() in AVAILABLE_EVENTS)
    return chosen


def load_settings() -> TelegramSettings:
    defaults = get_settings()
    saved: dict[str, str] = {}
    try:
        db = SessionLocal()
        try:
            saved = {
                item.key: (item.value or "").strip()
                for item in db.query(AppSetting).filter(
                    AppSetting.key.in_(("telegram_token", "telegram_chat_id", "notify_language", "notify_events"))
                )
            }
        finally:
            db.close()
    except Exception:
        logger.warning("cannot read notification settings", exc_info=True)
    return TelegramSettings(
        token=saved.get("telegram_token") or defaults.telegram_token,
        chat_id=saved.get("telegram_chat_id") or defaults.telegram_chat_id,
        language=saved.get("notify_language") or defaults.notify_language,
        events=parse_events(saved.get("notify_events") if "notify_events" in saved else None),
    )


def _torrent_title(tracked_id: int | None) -> str:
    if not tracked_id:
        return ""
    try:
        db = SessionLocal()
        try:
            tracked = db.get(TrackedTorrent, tracked_id)
            return tracked.title if tracked else ""
        finally:
            db.close()
    except Exception:
        return ""


def build_message(event_type: str, code: str, params: dict, title: str, language: str) -> str:
    """Разметка Telegram: значок и тип события, название раздачи, текст.

    Всё подставляемое экранируется: в названии раздачи и в тексте ошибки
    легко встречаются угловые скобки и амперсанды, а parse_mode=HTML на них
    просто отказывается доставлять сообщение.
    """
    icon = EVENT_ICONS.get(event_type, DEFAULT_ICON)
    head = translate(f"event.{event_type}", language).capitalize()
    body = translate(code, language, **params)

    lines = [f"{icon} <b>{html.escape(head)}</b>"]
    if title:
        lines.append(f"<i>{html.escape(title)}</i>")
    lines.append("")
    lines.append(html.escape(body))
    return "\n".join(lines)


def send(text: str, settings: TelegramSettings | None = None) -> None:
    """Синхронная отправка. Бросает исключение — нужна для кнопки проверки."""
    settings = settings or load_settings()
    if not settings.configured:
        from app.errors import InvalidInput

        raise InvalidInput("error.telegram.not_configured")
    response = requests.post(
        API_URL.format(token=settings.token),
        json={
            "chat_id": settings.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=SEND_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        from app.errors import ServiceUnavailable

        raise ServiceUnavailable("error.telegram.rejected", error=_describe(response))
    logger.info("telegram notification sent chat_id=%s", settings.chat_id)


def _describe(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    description = payload.get("description") if isinstance(payload, dict) else None
    return str(description or f"HTTP {response.status_code}")


def notify_event(tracked_id: int | None, event_type: str, code: str, params: dict) -> None:
    """Поставить уведомление в очередь. Вызывается из горячего пути проверки."""
    try:
        settings = load_settings()
        if not settings.configured or not settings.wants(event_type):
            return
        _sender.submit(_deliver, tracked_id, event_type, code, params, settings)
    except Exception:
        logger.warning("cannot schedule telegram notification", exc_info=True)


def _deliver(tracked_id, event_type, code, params, settings) -> None:
    try:
        text = build_message(event_type, code, params, _torrent_title(tracked_id), settings.language)
        send(text, settings)
    except Exception as exc:
        # Проверка уже прошла успешно; молчащий Telegram её не отменяет.
        logger.warning("telegram notification failed event=%s error=%s", event_type, exc)
