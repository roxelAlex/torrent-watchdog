import logging

from sqlalchemy.orm import Session

from app.models import CheckEvent, EventType, TrackedTorrent
from app.services.qbittorrent_client import QBittorrentClient
from app.services.qbittorrent_registry import get_qb_client_config

logger = logging.getLogger(__name__)


def change_torrent_category(db: Session, tracked_id: int, category: str) -> TrackedTorrent:
    tracked = db.get(TrackedTorrent, tracked_id)
    if not tracked:
        raise ValueError("Раздача не найдена")
    if not tracked.current_qb_hash:
        raise RuntimeError("У раздачи нет связанного торрента в qBittorrent")

    normalized_category = category.strip()
    qb_config = get_qb_client_config(db, tracked.qb_client_id)
    qb = QBittorrentClient(qb_config)
    qb.login()
    if not qb.get_torrent(tracked.current_qb_hash):
        raise RuntimeError(f"Торрент не найден в клиенте «{qb_config.name}»")

    qb.set_category(tracked.current_qb_hash, normalized_category)
    old_category = tracked.category
    tracked.category = normalized_category
    db.add(
        CheckEvent(
            tracked_torrent_id=tracked.id,
            event_type=EventType.manual_action.value,
            message=(
                f"Категория изменена: «{old_category or 'без категории'}» → "
                f"«{normalized_category or 'без категории'}»."
            ),
            old_info_hash=tracked.current_info_hash,
            new_info_hash=tracked.current_info_hash,
        )
    )
    db.commit()
    db.refresh(tracked)
    logger.info(
        "torrent category changed id=%s client=%s category=%s",
        tracked.id,
        qb_config.name,
        normalized_category or "<empty>",
    )
    return tracked
