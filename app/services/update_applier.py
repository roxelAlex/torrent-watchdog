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


def apply_update(db: Session, tracked_id: int, version_id: int) -> TrackedTorrent:
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
        if tracked.current_qb_hash and tracked.current_qb_hash.lower() != version.info_hash.lower():
            logger.info("pause old torrent id=%s hash=%s", tracked.id, tracked.current_qb_hash)
            qb.pause_torrent(tracked.current_qb_hash)
            logger.info("delete old torrent id=%s hash=%s deleteFiles=false", tracked.id, tracked.current_qb_hash)
            qb.delete_torrent(tracked.current_qb_hash, delete_files=False)

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
        if tracked.category:
            qb.set_category(new_qb_hash, tracked.category)
        if tracked.tags:
            qb.add_tags(new_qb_hash, tracked.tags)
        priorities: tuple[int, int] | None = None
        comparison_failed = False
        if new_files_only:
            diff = diff_from_json(version.changelog_text)
            if not diff:
                current_version = (
                    db.query(TorrentVersion)
                    .filter(TorrentVersion.tracked_torrent_id == tracked_id, TorrentVersion.is_current.is_(True))
                    .first()
                )
                diff = build_torrent_diff(current_version.torrent_file_path if current_version else None, version.torrent_file_path)
            priorities = _apply_new_files_only_priorities(qb, new_qb_hash, diff)
            if priorities is None:
                # Сравнивать не с чем. Без recheck qBittorrent считает, что на диске пусто,
                # и качает раздачу заново — ровно то, чего режим должен избегать.
                # Recheck ничего не удаляет, только сверяет уже лежащие файлы.
                comparison_failed = True
                logger.warning("file comparison unavailable id=%s, falling back to recheck hash=%s", tracked_id, new_qb_hash)
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
        if comparison_failed:
            code, params = "msg.update_applied.no_comparison", {}
        elif priorities:
            code = "msg.update_applied.new_files_only"
            params = {"skipped": priorities[0], "selected": priorities[1]}
        else:
            code, params = "msg.update_applied.full", {}
        _event(db, tracked_id, EventType.update_applied, code, old_hash, version.info_hash, **params)
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
    tracked = apply_update(db, tracked_id, version_id)
    db.add(messages.event(tracked_id, EventType.manual_action.value, "msg.rollback", new_hash=tracked.current_info_hash))
    db.commit()
    return tracked
