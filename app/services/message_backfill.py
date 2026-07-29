"""Перевод старых записей журнала на коды сообщений.

События, записанные до появления переводов, хранят готовый русский текст.
Разбираем его по известным формулировкам обратно на код и параметры, чтобы
и старая история читалась на выбранном языке. Что не опознано — остаётся
как есть: показать сохранённый текст честнее, чем угадать.
"""

import logging
import re

from app.models import CheckEvent
from app.services.messages import dump_params

logger = logging.getLogger(__name__)

# Порядок важен: первым совпавшим и разбираем. Числовые поля объявляются
# отдельно для каждого шаблона: одно и то же имя бывает и числом, и текстом —
# «new» это и количество новых файлов, и новое название категории.
PATTERNS: tuple[tuple[str, re.Pattern[str], tuple[str, ...], frozenset[str]], ...] = (
    ("msg.check_started", re.compile(r"^Проверка начата$"), (), frozenset()),
    ("msg.no_changes", re.compile(r"^Изменений нет$"), (), frozenset()),
    ("msg.torrent_added", re.compile(r"^Раздача добавлена$"), (), frozenset()),
    ("msg.rollback", re.compile(r"^Выполнен откат на сохранённую версию\..*$"), (), frozenset()),
    ("msg.update_applied.full", re.compile(r"^Обновление применено\. Файлы на диске не удалялись\.$"), (), frozenset()),
    (
        "msg.update_found",
        re.compile(r"^Найдено обновление\. Новых файлов: (?P<new>\d+), уже были: (?P<existing>\d+), удалены из раздачи: (?P<removed>\d+)\."),
        ("new", "existing", "removed"),
        frozenset({"new", "existing", "removed"}),
    ),
    (
        "msg.update_applied.new_files_only",
        re.compile(r"старых файлов отключено (?P<skipped>\d+), к скачиванию выбрано (?P<selected>\d+)"),
        ("skipped", "selected"),
        frozenset({"skipped", "selected"}),
    ),
    (
        "msg.category_changed",
        re.compile(r"^Категория изменена: «(?P<old>[^»]*)» → «(?P<new>[^»]*)»\."),
        ("old", "new"),
        frozenset(),
    ),
    ("msg.update_failed", re.compile(r"^Ошибка применения: (?P<error>.+)$", re.S), ("error",), frozenset()),
    (
        "msg.qbittorrent_unavailable",
        re.compile(r"^(?:(?P<client>[^:]+): )?qBittorrent недоступен или отклонил операцию: (?P<error>.+)$", re.S),
        ("client", "error"),
        frozenset(),
    ),
)


def classify(message: str, event_type: str) -> tuple[str, dict] | None:
    text = (message or "").strip()
    if not text:
        return None
    for code, pattern, fields, numeric in PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        params = {}
        for field in fields:
            value = match.group(field)
            params[field] = int(value) if field in numeric and value is not None else (value or "")
        return code, params
    # Свободный текст ошибки — код есть, содержимое остаётся исходным.
    if event_type == "error":
        return "msg.raw", {"error": text}
    return None


def backfill(db) -> int:
    """Проставляет коды записям, у которых их ещё нет."""
    pending = db.query(CheckEvent).filter(CheckEvent.message_code.is_(None)).all()
    updated = 0
    for item in pending:
        classified = classify(item.message, item.event_type)
        if not classified:
            continue
        item.message_code, params = classified
        item.message_params = dump_params(params)
        updated += 1
    if updated:
        db.commit()
        logger.info("backfilled message codes for %s events of %s", updated, len(pending))
    return updated
