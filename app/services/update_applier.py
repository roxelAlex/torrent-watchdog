import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.errors import InvalidInput, ServiceUnavailable, localize
from app.models import EventType, TorrentStatus, TorrentVersion, TrackedTorrent
from app.services import messages
from app.services.qbittorrent_client import QBittorrentClient
from app.services.qbittorrent_registry import get_qb_client_config
from app.services.torrent_diff import build_torrent_diff, diff_from_json, existing_file_keys

logger = logging.getLogger(__name__)


def _event(db: Session, tracked_id: int, event_type: EventType, code: str, old_hash: str | None, new_hash: str | None, **params) -> None:
    db.add(messages.event(tracked_id, event_type.value, code, old_hash=old_hash, new_hash=new_hash, **params))


def _wait_for_qb_files(
    qb: QBittorrentClient,
    torrent_hash: str,
    attempts: int = 60,
    delay_seconds: float = 0.5,
) -> list[dict]:
    last_error: requests.HTTPError | None = None
    for attempt in range(1, attempts + 1):
        try:
            files = qb.get_torrent_files(torrent_hash)
            if files:
                return files
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            last_error = exc

        if attempt < attempts:
            if attempt == 1 or attempt % 10 == 0:
                logger.info(
                    "waiting for qBittorrent torrent registration hash=%s attempt=%s/%s",
                    torrent_hash,
                    attempt,
                    attempts,
                )
            time.sleep(delay_seconds)

    timeout_seconds = attempts * delay_seconds
    raise ServiceUnavailable(
        "error.qb.files_timeout", hash=torrent_hash, seconds=f"{timeout_seconds:g}"
    ) from last_error


def _apply_new_files_only_priorities(qb: QBittorrentClient, torrent_hash: str, diff: dict) -> tuple[int, int] | None:
    """None означает, что сравнить состав файлов не с чем и приоритеты не выставлялись."""
    existing = existing_file_keys(diff)
    if not existing:
        return None
    files = _wait_for_qb_files(qb, torrent_hash)
    skip_ids: list[int] = []
    keep_ids: list[int] = []
    for index, item in enumerate(files):
        path = str(item.get("name", ""))
        size = int(item.get("size", 0))
        relative_path = path.split("/", 1)[1] if "/" in path else path
        if (path, size) in existing or (relative_path, size) in existing:
            skip_ids.append(index)
        else:
            keep_ids.append(index)
    qb.set_file_priority(torrent_hash, skip_ids, 0)
    qb.set_file_priority(torrent_hash, keep_ids, 1)
    return len(skip_ids), len(keep_ids)


def _sample_names(files: list[dict], limit: int = 5) -> str:
    """Несколько имён для узнаваемости: полный список бывает и в 484 файла."""
    names = [str(item.get("path", "")) for item in files[:limit] if item.get("path")]
    tail = f" и ещё {len(files) - len(names)}" if len(files) > len(names) else ""
    return ", ".join(names) + tail


def may_remove_replaced_files(
    tracked: TrackedTorrent,
    diff: dict,
    new_info_hash: str,
    allow_file_removal: bool,
) -> bool:
    """Можно ли удалить файлы прошлой версии.

    Единственный случай, где сервис вообще трогает данные пользователя, поэтому
    все условия проверяются вместе и любое несошедшееся означает «не трогать»:

    * признак включён у самой раздачи;
    * это не откат — при нём выбывшие файлы как раз те, ради которых он делается;
    * состав сравнить удалось (mode == file_list), иначе мы просто не знаем;
    * не совпал ни один файл — то есть раздачу пересобрали целиком;
    * прежний торрент существует и отличается от нового.
    """
    if not tracked.delete_replaced_files or not allow_file_removal:
        return False
    if diff.get("mode") != "file_list":
        return False
    if existing_file_keys(diff):
        return False
    if not tracked.current_qb_hash or tracked.current_qb_hash.lower() == new_info_hash.lower():
        return False
    return True


def _add_or_reuse_torrent(
    qb: QBittorrentClient,
    torrent_file_path: str,
    info_hash: str,
    save_path: str,
    category: str,
    tags: str,
    paused: bool,
) -> str:
    existing = qb.get_torrent(info_hash)
    if existing:
        logger.info("reuse existing qBittorrent torrent hash=%s after partial update", info_hash)
        return info_hash.lower()

    try:
        return qb.add_torrent_file(torrent_file_path, save_path, category, tags, paused)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 409 or not qb.get_torrent(info_hash):
            raise
        logger.info("reuse concurrently registered qBittorrent torrent hash=%s", info_hash)
        return info_hash.lower()


def apply_update(db: Session, tracked_id: int, version_id: int, allow_file_removal: bool = True) -> TrackedTorrent:
    tracked = db.get(TrackedTorrent, tracked_id)
    version = db.get(TorrentVersion, version_id)
    if not tracked or not version or version.tracked_torrent_id != tracked_id:
        raise InvalidInput("error.torrent.or_version_not_found")

    old_hash = tracked.current_info_hash
    try:
        tracked.status = TorrentStatus.updating.value
        messages.clear_error(tracked)
        db.commit()

        if not version.torrent_file_path or not Path(version.torrent_file_path).exists():
            raise InvalidInput("error.version.file_missing")

        qb_config = get_qb_client_config(db, tracked.qb_client_id)
        qb = QBittorrentClient(qb_config)
        qb_status = qb.test_connection()
        if qb_status.get("status") != "ok":
            _event(db, tracked_id, EventType.qbittorrent_unavailable, "error.qb.unavailable", old_hash, version.info_hash,
                   client=qb_config.name, error=qb_status.get("error"))
            raise ServiceUnavailable("error.qb.unavailable", client=qb_config.name, error=qb_status.get("error"))
        qb.login()

        # Дифф нужен раньше обычного: от него зависит, удалять ли файлы прошлой версии.
        diff = diff_from_json(version.changelog_text)
        if not diff:
            previous = (
                db.query(TorrentVersion)
                .filter(TorrentVersion.tracked_torrent_id == tracked_id, TorrentVersion.is_current.is_(True))
                .first()
            )
            diff = build_torrent_diff(previous.torrent_file_path if previous else None, version.torrent_file_path)
        remove_old_files = may_remove_replaced_files(tracked, diff, version.info_hash, allow_file_removal)

        old_qb_hash = tracked.current_qb_hash
        replaces_old = bool(old_qb_hash and old_qb_hash.lower() != version.info_hash.lower())
        if replaces_old:
            logger.info("pause old torrent id=%s hash=%s", tracked.id, old_qb_hash)
            qb.pause_torrent(old_qb_hash)
            if not remove_old_files:
                logger.info("delete old torrent id=%s hash=%s deleteFiles=false", tracked.id, old_qb_hash)
                qb.delete_torrent(old_qb_hash, delete_files=False)

        new_files_only = tracked.update_mode == "new_files_only"
        new_qb_hash = _add_or_reuse_torrent(
            qb,
            version.torrent_file_path,
            version.info_hash,
            tracked.save_path,
            tracked.category,
            tracked.tags,
            paused=True if new_files_only else tracked.add_paused,
        )
        removed_files = 0
        if remove_old_files:
            # Сначала убеждаемся, что замена на месте: если добавление упадёт, данные
            # уже были бы уничтожены, а взамен ничего.
            _wait_for_qb_files(qb, new_qb_hash)
            removed_files = len(diff.get("removed", []))
            logger.warning(
                "delete old torrent id=%s hash=%s deleteFiles=true files=%s",
                tracked.id, old_qb_hash, removed_files,
            )
            qb.delete_torrent(old_qb_hash, delete_files=True)

        if tracked.category:
            qb.set_category(new_qb_hash, tracked.category)
        if tracked.tags:
            qb.add_tags(new_qb_hash, tracked.tags)
        priorities: tuple[int, int] | None = None
        comparison_failed = False
        nothing_in_common = False
        if new_files_only:
            priorities = _apply_new_files_only_priorities(qb, new_qb_hash, diff)
            if priorities is None:
                # Пропускать нечего. Причин ровно две, и путать их нельзя:
                # либо сравнить не с чем, либо сравнение прошло и не нашло ни одного
                # общего файла — раздачу пересобрали целиком. Во втором случае
                # «сохранённый .torrent недоступен» было бы неправдой.
                comparison_failed = diff.get("mode") != "file_list"
                nothing_in_common = not comparison_failed
                # Без recheck qBittorrent считает, что на диске пусто, и качает раздачу
                # заново. Recheck ничего не удаляет, только сверяет уже лежащие файлы.
                logger.warning(
                    "nothing to skip id=%s reason=%s hash=%s",
                    tracked_id,
                    "no comparison" if comparison_failed else "no common files",
                    new_qb_hash,
                )
                if not remove_old_files:
                    qb.recheck_torrent(new_qb_hash)
        elif tracked.recheck_after_add:
            qb.recheck_torrent(new_qb_hash)
        if tracked.start_after_recheck:
            qb.resume_torrent(new_qb_hash)

        db.execute(update(TorrentVersion).where(TorrentVersion.tracked_torrent_id == tracked_id).values(is_current=False))
        version.is_current = True
        version.applied_at = datetime.now(timezone.utc)
        tracked.current_info_hash = version.info_hash
        tracked.current_qb_hash = new_qb_hash
        tracked.current_torrent_name = version.torrent_name
        tracked.status = TorrentStatus.updated.value
        tracked.last_update_at = datetime.now(timezone.utc)
        messages.clear_error(tracked)
        if remove_old_files:
            code = "msg.update_applied.replaced"
            params = {"removed": removed_files, "new": len(diff.get("new", []))}
        elif comparison_failed:
            code, params = "msg.update_applied.no_comparison", {}
        elif nothing_in_common:
            code = "msg.update_applied.nothing_in_common"
            params = {"new": len(diff.get("new", [])), "removed": len(diff.get("removed", []))}
        elif priorities:
            code = "msg.update_applied.new_files_only"
            params = {"skipped": priorities[0], "selected": priorities[1]}
        else:
            code, params = "msg.update_applied.full", {}
        _event(db, tracked_id, EventType.update_applied, code, old_hash, version.info_hash, **params)

        # Частичное обновление: выбывшие файлы удалить нечем — qBittorrent умеет
        # только «всё сразу», а часть файлов нужна новой версии. Сообщаем, чтобы
        # человек хотя бы знал, что осталось лежать.
        left_behind = [] if remove_old_files else diff.get("removed", [])
        if left_behind:
            _event(
                db, tracked_id, EventType.manual_action, "msg.files_left_behind",
                old_hash, version.info_hash,
                count=len(left_behind),
                names=_sample_names(left_behind),
            )
        db.commit()
        db.refresh(tracked)
        return tracked
    except Exception as exc:
        logger.exception("update failed id=%s step=apply_update error=%s", tracked_id, exc)
        tracked.status = TorrentStatus.error.value
        messages.set_error(tracked, "msg.update_failed", error=localize(exc, None))
        _event(db, tracked_id, EventType.update_failed, "msg.update_failed", old_hash, version.info_hash if version else None, error=localize(exc, None))
        db.commit()
        raise


def rollback_to_version(db: Session, tracked_id: int, version_id: int) -> TrackedTorrent:
    # Откат не удаляет файлы никогда: выбывшие — как раз те, ради которых он и делается.
    tracked = apply_update(db, tracked_id, version_id, allow_file_removal=False)
    db.add(messages.event(tracked_id, EventType.manual_action.value, "msg.rollback", new_hash=tracked.current_info_hash))
    db.commit()
    return tracked
