import logging
from datetime import datetime, timezone

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.errors import InvalidInput
from app.models import EventType, TorrentStatus, TorrentVersion, TrackedTorrent
from app.services import messages
from app.services.qbittorrent_client import QBittorrentClient
from app.services.qbittorrent_registry import ensure_category, get_qb_client_config
from app.services.torrent_diff import build_torrent_diff, diff_to_json
from app.services.tracker_resolver import adopt_torrent_file, normalize_source_url, resolve_source
from app.services.update_applier import apply_update
from app.utils import mask_url

logger = logging.getLogger(__name__)


def create_initial_torrent(db: Session, payload) -> TrackedTorrent:
    # Единственная точка входа для веба и API: проверяем источник здесь.
    source_url = normalize_source_url(payload.source_url)
    resolved = resolve_source(source_url, tracked_id=None)
    qb_config = get_qb_client_config(db, payload.qb_client_id)
    tracked = TrackedTorrent(
        qb_client_id=qb_config.id,
        title=payload.title or resolved.torrent_name or source_url,
        source_url=source_url,
        source_type=resolved.source_type,
        tracker_type=resolved.tracker_type,
        current_info_hash=resolved.info_hash,
        current_torrent_name=resolved.torrent_name,
        save_path=payload.save_path,
        category=payload.category,
        tags=payload.tags,
        auto_update=payload.auto_update,
        recheck_after_add=payload.recheck_after_add,
        start_after_recheck=payload.start_after_recheck,
        add_paused=payload.add_paused,
        update_mode=payload.update_mode,
        status=TorrentStatus.active.value,
    )
    db.add(tracked)
    db.commit()
    db.refresh(tracked)

    # Файл уже скачан выше — просто перекладываем его из _pending в папку раздачи.
    resolved = adopt_torrent_file(resolved, tracked.id)
    qb_hash = ""
    try:
        qb = QBittorrentClient(qb_config)
        qb.login()
        # Своя категория должна существовать до добавления: иначе раздача осядет без неё.
        ensure_category(qb, payload.category)
        qb_hash = qb.add_torrent_file(resolved.torrent_file_path or "", payload.save_path, payload.category, payload.tags, payload.add_paused)
        tracked.current_qb_hash = qb_hash
        db.commit()
        if payload.recheck_after_add:
            qb.recheck_torrent(qb_hash)
        if payload.start_after_recheck:
            qb.resume_torrent(qb_hash)
    except Exception as exc:
        tracked.status = TorrentStatus.error.value
        messages.set_error(tracked, "msg.qbittorrent_unavailable", client=qb_config.name, error=str(exc))
        if qb_hash:
            tracked.current_qb_hash = qb_hash
        db.add(TorrentVersion(
            tracked_torrent_id=tracked.id,
            info_hash=resolved.info_hash,
            torrent_name=resolved.torrent_name,
            torrent_file_path=resolved.torrent_file_path,
            source_url=source_url,
            is_current=True,
        ))
        db.add(messages.event(
            tracked.id,
            EventType.qbittorrent_unavailable.value,
            "msg.qbittorrent_unavailable",
            new_hash=resolved.info_hash,
            client=qb_config.name,
            error=str(exc),
        ))
        db.commit()
        db.refresh(tracked)
        return tracked

    tracked.current_qb_hash = qb_hash
    tracked.current_info_hash = resolved.info_hash
    tracked.current_torrent_name = resolved.torrent_name
    version = TorrentVersion(
        tracked_torrent_id=tracked.id,
        info_hash=resolved.info_hash,
        torrent_name=resolved.torrent_name,
        torrent_file_path=resolved.torrent_file_path,
        source_url=source_url,
        applied_at=datetime.now(timezone.utc),
        is_current=True,
    )
    db.add(version)
    db.add(messages.event(tracked.id, EventType.manual_action.value, "msg.torrent_added", new_hash=resolved.info_hash))
    db.commit()
    db.refresh(tracked)
    return tracked


def check_torrent(db: Session, tracked_id: int) -> TrackedTorrent:
    tracked = db.get(TrackedTorrent, tracked_id)
    if not tracked:
        raise InvalidInput("error.torrent.not_found")
    if tracked.status == TorrentStatus.disabled.value:
        return tracked

    old_hash = tracked.current_info_hash
    now = datetime.now(timezone.utc)
    db.add(messages.event(tracked.id, EventType.check_started.value, "msg.check_started", old_hash=old_hash))
    db.commit()
    try:
        resolved = resolve_source(tracked.source_url, tracked_id=tracked.id)
        logger.info(
            "checked torrent id=%s title=%s url=%s old=%s new=%s at=%s",
            tracked.id,
            tracked.title,
            mask_url(tracked.source_url),
            old_hash,
            resolved.info_hash,
            now.isoformat(),
        )
        tracked.last_check_at = now
        messages.clear_error(tracked)
        if resolved.info_hash == old_hash:
            if tracked.status in {TorrentStatus.updated.value, TorrentStatus.error.value}:
                tracked.status = TorrentStatus.active.value
            db.add(messages.event(tracked.id, EventType.no_changes.value, "msg.no_changes", old_hash=old_hash, new_hash=resolved.info_hash))
            db.commit()
            db.refresh(tracked)
            return tracked

        current_version = (
            db.query(TorrentVersion)
            .filter(TorrentVersion.tracked_torrent_id == tracked.id, TorrentVersion.is_current.is_(True))
            .order_by(desc(TorrentVersion.detected_at))
            .first()
        )
        diff = build_torrent_diff(current_version.torrent_file_path if current_version else None, resolved.torrent_file_path)
        version = TorrentVersion(
            tracked_torrent_id=tracked.id,
            info_hash=resolved.info_hash,
            torrent_name=resolved.torrent_name,
            torrent_file_path=resolved.torrent_file_path,
            source_url=tracked.source_url,
            is_current=False,
            changelog_text=diff_to_json(diff),
        )
        tracked.status = TorrentStatus.update_available.value
        db.add(version)
        db.flush()
        db.add(messages.event(
            tracked.id,
            EventType.update_found.value,
            "msg.update_found",
            old_hash=old_hash,
            new_hash=resolved.info_hash,
            new=len(diff.get("new", [])),
            existing=len(diff.get("existing", [])),
            removed=len(diff.get("removed", [])),
        ))
        db.commit()
        if tracked.auto_update:
            return apply_update(db, tracked.id, version.id)
        db.refresh(tracked)
        return tracked
    except Exception as exc:
        logger.exception("check failed id=%s url=%s error=%s", tracked.id, mask_url(tracked.source_url), exc)
        tracked.status = TorrentStatus.error.value
        messages.set_error(tracked, "msg.raw", error=str(exc))
        tracked.last_check_at = now
        db.add(messages.event(tracked.id, EventType.error.value, "msg.raw", old_hash=old_hash, error=str(exc)))
        db.commit()
        return tracked


def current_version(db: Session, tracked_id: int) -> TorrentVersion | None:
    return (
        db.query(TorrentVersion)
        .filter(TorrentVersion.tracked_torrent_id == tracked_id, TorrentVersion.is_current.is_(True))
        .order_by(desc(TorrentVersion.detected_at))
        .first()
    )


def latest_pending_version(db: Session, tracked_id: int) -> TorrentVersion | None:
    """Новая версия, ожидающая применения.

    Одного is_current=False мало: после применения обновления предыдущая версия
    тоже перестаёт быть текущей, и «Применить» установило бы старую раздачу
    поверх новой. Ожидающей считается только версия, которую ещё не применяли
    и которую обнаружили позже текущей.
    """
    tracked = db.get(TrackedTorrent, tracked_id)
    query = db.query(TorrentVersion).filter(
        TorrentVersion.tracked_torrent_id == tracked_id,
        TorrentVersion.is_current.is_(False),
        TorrentVersion.applied_at.is_(None),
    )
    if tracked and tracked.current_info_hash:
        query = query.filter(TorrentVersion.info_hash != tracked.current_info_hash)
    current = current_version(db, tracked_id)
    if current:
        query = query.filter(TorrentVersion.detected_at > current.detected_at)
    return query.order_by(desc(TorrentVersion.detected_at)).first()
