import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import SessionLocal
from app.models import TorrentStatus, TrackedTorrent
from app.services.update_checker import check_torrent

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def check_all_torrents() -> None:
    db = SessionLocal()
    try:
        torrents = db.query(TrackedTorrent).filter(TrackedTorrent.status != TorrentStatus.disabled.value).all()
        for tracked in torrents:
            try:
                check_torrent(db, tracked.id)
            except Exception:
                logger.exception("scheduled check failed id=%s", tracked.id)
                db.rollback()
    finally:
        db.close()


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
