import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import CheckEvent, EventType, TorrentStatus, TorrentVersion, TrackedTorrent
from app.services.qbittorrent_client import QBittorrentClient
from app.services.qbittorrent_registry import get_qb_client_config
from app.services.torrent_diff import build_torrent_diff, diff_from_json, existing_file_keys

logger = logging.getLogger(__name__)


def _event(db: Session, tracked_id: int, event_type: EventType, message: str, old_hash: str | None, new_hash: str | None) -> None:
    db.add(CheckEvent(tracked_torrent_id=tracked_id, event_type=event_type.value, message=message, old_info_hash=old_hash, new_info_hash=new_hash))


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
    raise RuntimeError(
        f"qBittorrent не предоставил список файлов для нового торрента "
        f"{torrent_hash} за {timeout_seconds:g} секунд"
    ) from last_error


def _apply_new_files_only_priorities(qb: QBittorrentClient, torrent_hash: str, diff: dict) -> tuple[int, int]:
    existing = existing_file_keys(diff)
    if not existing:
        return 0, 0
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
        raise ValueError("Раздача или версия не найдена")

    old_hash = tracked.current_info_hash
    try:
        tracked.status = TorrentStatus.updating.value
        tracked.last_error = None
        db.commit()

        if not version.torrent_file_path or not Path(version.torrent_file_path).exists():
            raise RuntimeError("Сохранённый .torrent файл не найден")

        qb_config = get_qb_client_config(db, tracked.qb_client_id)
        qb = QBittorrentClient(qb_config)
        qb_status = qb.test_connection()
        if qb_status.get("status") != "ok":
            _event(db, tracked_id, EventType.qbittorrent_unavailable, f"{qb_config.name}: qBittorrent недоступен: {qb_status.get('error')}", old_hash, version.info_hash)
            raise RuntimeError(f"{qb_config.name}: qBittorrent недоступен: {qb_status.get('error')}")
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
        skipped = 0
        selected = 0
        if new_files_only:
            diff = diff_from_json(version.changelog_text)
            if not diff:
                current_version = (
                    db.query(TorrentVersion)
                    .filter(TorrentVersion.tracked_torrent_id == tracked_id, TorrentVersion.is_current.is_(True))
                    .first()
                )
                diff = build_torrent_diff(current_version.torrent_file_path if current_version else None, version.torrent_file_path)
            skipped, selected = _apply_new_files_only_priorities(qb, new_qb_hash, diff)
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
        tracked.last_error = None
        if new_files_only:
            message = f"Обновление применено в режиме только новых файлов: старых файлов отключено {skipped}, к скачиванию выбрано {selected}. Recheck не запускался."
        else:
            message = "Обновление применено. Файлы на диске не удалялись."
        _event(db, tracked_id, EventType.update_applied, message, old_hash, version.info_hash)
        db.commit()
        db.refresh(tracked)
        return tracked
    except Exception as exc:
        logger.exception("update failed id=%s step=apply_update error=%s", tracked_id, exc)
        tracked.status = TorrentStatus.error.value
        tracked.last_error = str(exc)
        _event(db, tracked_id, EventType.update_failed, f"Ошибка применения: {exc}", old_hash, version.info_hash if version else None)
        db.commit()
        raise


def rollback_to_version(db: Session, tracked_id: int, version_id: int) -> TrackedTorrent:
    tracked = apply_update(db, tracked_id, version_id)
    db.add(CheckEvent(
        tracked_torrent_id=tracked_id,
        event_type=EventType.manual_action.value,
        message="Выполнен откат на сохранённую версию. Файлы на диске не удалялись.",
        new_info_hash=tracked.current_info_hash,
    ))
    db.commit()
    return tracked
