from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import QbittorrentClientConfig
from app.services.qbittorrent_client import QBittorrentClient


def list_qb_clients(db: Session) -> list[QbittorrentClientConfig]:
    return db.query(QbittorrentClientConfig).order_by(QbittorrentClientConfig.is_default.desc(), QbittorrentClientConfig.name).all()


def get_default_qb_client(db: Session) -> QbittorrentClientConfig:
    client = db.query(QbittorrentClientConfig).filter(QbittorrentClientConfig.is_default.is_(True)).first()
    if client:
        return client

    settings = get_settings()
    client = QbittorrentClientConfig(
        name="Основной",
        host=settings.qb_host,
        username=settings.qb_username,
        password=settings.qb_password,
        verify_tls=settings.qb_verify_tls,
        timeout_seconds=settings.qb_timeout_seconds,
        is_default=True,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


def get_qb_client_config(db: Session, client_id: int | None) -> QbittorrentClientConfig:
    if client_id:
        client = db.get(QbittorrentClientConfig, client_id)
        if client:
            return client
    return get_default_qb_client(db)


def create_qb_client(
    db: Session,
    name: str,
    host: str,
    username: str,
    password: str,
    verify_tls: bool,
    timeout_seconds: int,
    is_default: bool = False,
) -> QbittorrentClientConfig:
    if is_default:
        db.query(QbittorrentClientConfig).update({QbittorrentClientConfig.is_default: False})
    client = QbittorrentClientConfig(
        name=name.strip() or host,
        host=host.rstrip("/"),
        username=username,
        password=password,
        verify_tls=verify_tls,
        timeout_seconds=timeout_seconds,
        is_default=is_default,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


def set_default_qb_client(db: Session, client_id: int) -> None:
    client = db.get(QbittorrentClientConfig, client_id)
    if not client:
        raise ValueError("Клиент qBittorrent не найден")
    db.query(QbittorrentClientConfig).update({QbittorrentClientConfig.is_default: False})
    client.is_default = True
    db.commit()


def update_qb_client(
    db: Session,
    client_id: int,
    name: str,
    host: str,
    username: str,
    password: str,
    verify_tls: bool,
    timeout_seconds: int,
) -> QbittorrentClientConfig:
    client = db.get(QbittorrentClientConfig, client_id)
    if not client:
        raise ValueError("Клиент qBittorrent не найден")
    client.name = name.strip() or client.name
    client.host = host.rstrip("/")
    client.username = username
    if password:
        client.password = password
    client.verify_tls = verify_tls
    client.timeout_seconds = timeout_seconds
    db.commit()
    db.refresh(client)
    return client


def test_qb_client(client: QbittorrentClientConfig) -> dict[str, str]:
    return QBittorrentClient(client).test_connection()


def client_statuses(db: Session) -> list[dict[str, str]]:
    statuses = []
    for client in list_qb_clients(db):
        result = test_qb_client(client)
        statuses.append({
            "id": str(client.id),
            "name": client.name,
            "host": client.host,
            "status": result.get("status", "unknown"),
            "version": result.get("version", ""),
            "error": result.get("error", ""),
            "is_default": "true" if client.is_default else "false",
        })
    return statuses
