import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import SessionLocal
from app.models import CheckEvent, TorrentStatus, TorrentVersion, TrackedTorrent
from app.services.update_checker import check_torrent

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def _check_one(tracked_id: int) -> None:
    """Отдельная сессия на раздачу: проверки идут параллельно."""
    db = SessionLocal()
    try:
        check_torrent(db, tracked_id)
    except Exception:
        logger.exception("scheduled check failed id=%s", tracked_id)
        db.rollback()
    finally:
        db.close()


def check_all_torrents() -> None:
    db = SessionLocal()
    try:
        tracked_ids = [
            row[0]
            for row in db.query(TrackedTorrent.id)
            .filter(TrackedTorrent.status != TorrentStatus.disabled.value)
            .all()
        ]
    finally:
        db.close()

    if tracked_ids:
        # Один резолв RuTracker — это до трёх попыток с ожиданием между ними,
        # последовательный обход растягивал ночную проверку на часы.
        workers = max(1, min(get_settings().check_max_workers, len(tracked_ids)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="check") as pool:
            list(pool.map(_check_one, tracked_ids))

    run_maintenance()


def run_maintenance() -> None:
    """Ретеншен: журнал и .torrent-файлы иначе растут бесконечно."""
    settings = get_settings()
    db = SessionLocal()
    try:
        events = _prune_events(db, settings.event_retention_days)
        files = _prune_orphan_torrent_files(db, settings.torrent_file_retention_days)
        if events or files:
            logger.info("maintenance done events_deleted=%s files_deleted=%s", events, files)
    except Exception:
        logger.exception("maintenance failed")
        db.rollback()
    finally:
        db.close()


def _prune_events(db, retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    # Наивный UTC: именно так CURRENT_TIMESTAMP пишет значения в SQLite.
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=retention_days)
    deleted = db.query(CheckEvent).filter(CheckEvent.created_at < cutoff).delete(synchronize_session=False)
    db.commit()
    return deleted


def _prune_orphan_torrent_files(db, retention_days: int) -> int:
    """Удаляет .torrent, на которые не ссылается ни одна версия.

    Так чистятся и брошенные файлы в _pending, если добавление раздачи упало.
    Свежие файлы не трогаем: в этот момент может идти добавление.
    """
    if retention_days <= 0:
        return 0
    known = {
        Path(path).resolve()
        for (path,) in db.query(TorrentVersion.torrent_file_path).filter(TorrentVersion.torrent_file_path.isnot(None))
        if path
    }
    cutoff = (datetime.now() - timedelta(days=retention_days)).timestamp()
    deleted = 0
    for path in get_settings().torrents_dir.rglob("*.torrent"):
        try:
            if path.resolve() in known or path.stat().st_mtime > cutoff:
                continue
            path.unlink()
            deleted += 1
            logger.info("removed orphan torrent file path=%s", path)
        except OSError:
            logger.warning("cannot remove torrent file path=%s", path, exc_info=True)
    return deleted


def next_check_at():
    job = scheduler.get_job("daily-torrent-check") if scheduler.running else None
    return job.next_run_time if job else None


def start_scheduler() -> None:
    settings = get_settings()
    if scheduler.running:
        return
    scheduler.add_job(
        check_all_torrents,
        CronTrigger(hour=settings.check_hour, minute=settings.check_minute, timezone=ZoneInfo(settings.tz)),
        id="daily-torrent-check",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("scheduler started daily at %02d:%02d %s", settings.check_hour, settings.check_minute, settings.tz)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
