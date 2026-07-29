from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.db import get_db
from app.models import CheckEvent, TorrentStatus, TrackedTorrent
from app.schemas import TorrentCategoryUpdate, TorrentCreate, TorrentRead
from app.services.qbittorrent_client import QBittorrentClient
from app.services.qbittorrent_registry import client_statuses, get_qb_client_config, list_qb_clients
from app.services.rutracker_resolver import resolve_rutracker
from app.services.torrent_settings import change_torrent_category
from app.services.update_applier import apply_update
from app.services.update_checker import check_torrent, create_initial_torrent, latest_pending_version

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


@router.get("/torrents", response_model=list[TorrentRead])
def torrents(db: Session = Depends(get_db)):
    return db.query(TrackedTorrent).order_by(TrackedTorrent.created_at.desc()).all()


@router.post("/torrents", response_model=TorrentRead)
def create_torrent(payload: TorrentCreate, db: Session = Depends(get_db)):
    try:
        return create_initial_torrent(db, payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/torrents/{tracked_id}", response_model=TorrentRead)
def get_torrent(tracked_id: int, db: Session = Depends(get_db)):
    tracked = db.get(TrackedTorrent, tracked_id)
    if not tracked:
        raise HTTPException(status_code=404, detail="Раздача не найдена")
    return tracked


@router.post("/torrents/{tracked_id}/check", response_model=TorrentRead)
def check(tracked_id: int, db: Session = Depends(get_db)):
    return check_torrent(db, tracked_id)


@router.post("/torrents/{tracked_id}/apply-update", response_model=TorrentRead)
def apply(tracked_id: int, db: Session = Depends(get_db)):
    version = latest_pending_version(db, tracked_id)
    if not version:
        raise HTTPException(status_code=404, detail="Нет ожидающего обновления")
    return apply_update(db, tracked_id, version.id)


@router.post("/torrents/{tracked_id}/category", response_model=TorrentRead)
def update_category(
    tracked_id: int,
    payload: TorrentCategoryUpdate,
    db: Session = Depends(get_db),
):
    try:
        return change_torrent_category(db, tracked_id, payload.category)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/events")
def events(db: Session = Depends(get_db), limit: int = 100):
    return db.query(CheckEvent).order_by(CheckEvent.created_at.desc()).limit(limit).all()


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    total = db.query(func.count(TrackedTorrent.id)).scalar() or 0
    return {
        "total": total,
        "active": db.query(func.count(TrackedTorrent.id)).filter(TrackedTorrent.status.in_([TorrentStatus.active.value, TorrentStatus.updated.value])).scalar() or 0,
        "updated": db.query(func.count(TrackedTorrent.id)).filter(TrackedTorrent.status == TorrentStatus.updated.value).scalar() or 0,
        "update_available": db.query(func.count(TrackedTorrent.id)).filter(TrackedTorrent.status == TorrentStatus.update_available.value).scalar() or 0,
        "errors": db.query(func.count(TrackedTorrent.id)).filter(TrackedTorrent.status == TorrentStatus.error.value).scalar() or 0,
        "qbittorrent_clients": len(list_qb_clients(db)),
    }


@router.post("/test/qbittorrent")
def test_qbittorrent(client_id: int | None = None, db: Session = Depends(get_db)):
    qb = QBittorrentClient(get_qb_client_config(db, client_id))
    return qb.test_connection()


@router.get("/qbittorrent/categories")
def qbittorrent_categories(client_id: int | None = None, db: Session = Depends(get_db)):
    try:
        qb = QBittorrentClient(get_qb_client_config(db, client_id))
        qb.login()
        return {"status": "ok", "categories": qb.get_categories()}
    except Exception as exc:
        return {"status": "unavailable", "categories": [], "error": str(exc)}


@router.get("/qbittorrent/clients")
def qbittorrent_clients(db: Session = Depends(get_db)):
    return {"clients": client_statuses(db)}


@router.post("/test/rutracker")
def test_rutracker(source_url: str):
    resolved = resolve_rutracker(source_url)
    return {"status": "ok", "info_hash": resolved.info_hash, "torrent_name": resolved.torrent_name}
