import logging

from sqlalchemy.orm import Session

from app.errors import InvalidInput, ServiceUnavailable
from app.i18n import translate
from app.models import EventType, TrackedTorrent
from app.services import messages
from app.services.qbittorrent_client import QBittorrentClient
from app.services.qbittorrent_registry import ensure_category, get_qb_client_config

logger = logging.getLogger(__name__)


def change_torrent_category(db: Session, tracked_id: int, category: str, category_save_path: str = "") -> TrackedTorrent:
    tracked = db.get(TrackedTorrent, tracked_id)
    if not tracked:
        raise InvalidInput("error.torrent.not_found")
    if not tracked.current_qb_hash:
        raise InvalidInput("error.torrent.no_qb_hash")

    normalized_category = category.strip()
    qb_config = get_qb_client_config(db, tracked.qb_client_id)
    qb = QBittorrentClient(qb_config)
    qb.login()
    if not qb.get_torrent(tracked.current_qb_hash):
        raise ServiceUnavailable("error.torrent.not_in_client", client=qb_config.name)

    ensure_category(qb, normalized_category, category_save_path)
    qb.set_category(tracked.current_qb_hash, normalized_category)
    old_category = tracked.category
    tracked.category = normalized_category
    db.add(messages.event(
        tracked.id,
        EventType.manual_action.value,
        "msg.category_changed",
        old_hash=tracked.current_info_hash,
        new_hash=tracked.current_info_hash,
        old=old_category or translate("category.unset"),
        new=normalized_category or translate("category.unset"),
    ))
    db.commit()
    db.refresh(tracked)
    logger.info(
        "torrent category changed id=%s client=%s category=%s",
        tracked.id,
        qb_config.name,
        normalized_category or "<empty>",
    )
    return tracked
