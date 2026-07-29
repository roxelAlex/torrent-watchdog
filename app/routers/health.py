from fastapi import APIRouter
from sqlalchemy import text
from zoneinfo import ZoneInfo
from datetime import datetime

from app.config import get_settings
from app.db import SessionLocal
from app.services.qbittorrent_client import QBittorrentClient
from app.services.qbittorrent_registry import client_statuses

router = APIRouter()


@router.get("/health")
@router.get("/api/health")
def health() -> dict[str, str]:
    settings = get_settings()
    db_status = "ok"
    try:
        db = SessionLocal()
        db.execute(text("select 1"))
        db.close()
    except Exception as exc:
        db_status = f"error: {exc}"
    qb_statuses = []
    try:
        db = SessionLocal()
        qb_statuses = client_statuses(db)
        db.close()
    except Exception:
        qb_statuses = [QBittorrentClient().test_connection()]
    ok_clients = sum(1 for item in qb_statuses if item.get("status") == "ok")
    return {
        "status": "ok" if db_status == "ok" and ok_clients else "degraded",
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "database": db_status,
        "qbittorrent_status": "ok" if ok_clients else "unavailable",
        "qbittorrent_clients_ok": str(ok_clients),
        "qbittorrent_clients_total": str(len(qb_statuses)),
        "current_time": datetime.now(ZoneInfo(settings.tz)).isoformat(),
        "timezone": settings.tz,
    }
