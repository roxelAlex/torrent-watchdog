from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import QbittorrentClientConfig
from app.services.cache import TTLCache
from app.services.qbittorrent_client import QBittorrentClient

_status_cache = TTLCache(get_settings().qb_status_cache_seconds)
_categories_cache = TTLCache(get_settings().qb_categories_cache_seconds)


def list_qb_clients(db: Session) -> list[QbittorrentClientConfig]:
    return db.query(QbittorrentClientConfig).order_by(QbittorrentClientConfig.name).all()


def get_fallback_qb_client(db: Session) -> QbittorrentClientConfig:
    """Клиент для раздач без явной привязки — первый по имени.

    Понятия «основной» нет: каждая раздача привязывается к клиенту при
    добавлении, а это запасной вариант для старых записей и пустой базы.
    """
    client = db.query(QbittorrentClientConfig).order_by(QbittorrentClientConfig.name).first()
    if client:
        return client

    settings = get_settings()
    client = QbittorrentClientConfig(
        name="qBittorrent",
        host=settings.qb_host,
        username=settings.qb_username,
        password=settings.qb_password,
        verify_tls=settings.qb_verify_tls,
        timeout_seconds=settings.qb_timeout_seconds,
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
    return get_fallback_qb_client(db)


def create_qb_client(
    db: Session,
    name: str,
    host: str,
    username: str,
    password: str,
    verify_tls: bool,
    timeout_seconds: int,
) -> QbittorrentClientConfig:
    client = QbittorrentClientConfig(
        name=name.strip() or host,
        host=host.rstrip("/"),
        username=username,
        password=password,
        verify_tls=verify_tls,
        timeout_seconds=timeout_seconds,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    invalidate_qb_caches()
    return client


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
    invalidate_qb_caches()
    return client


def test_qb_client(client: QbittorrentClientConfig) -> dict[str, str]:
    return QBittorrentClient(client).test_connection()


def _probe(client: QbittorrentClientConfig) -> dict[str, str]:
    result = test_qb_client(client)
    return {
        "id": str(client.id),
        "name": client.name,
        "host": client.host,
        "status": result.get("status", "unknown"),
        "version": result.get("version", ""),
        "error": result.get("error", ""),
    }


def client_statuses(db: Session, refresh: bool = False) -> list[dict[str, str]]:
    """Статусы всех клиентов: параллельно и с коротким кэшем.

    Вызывается на каждый рендер главной, настроек и /health. Без кэша каждый
    недоступный клиент добавлял к странице свой таймаут соединения.
    """
    clients = list_qb_clients(db)
    if not clients:
        return []
    # Правка клиента меняет updated_at и тем самым сама сбрасывает кэш.
    key = "|".join(f"{client.id}:{client.host}:{client.updated_at}" for client in clients)
    if refresh:
        _status_cache.invalidate()

    def probe_all() -> list[dict[str, str]]:
        if len(clients) == 1:
            return [_probe(clients[0])]
        with ThreadPoolExecutor(max_workers=min(len(clients), 8), thread_name_prefix="qb-probe") as pool:
            return list(pool.map(_probe, clients))

    return _status_cache.get_or_set(key, probe_all)


def client_categories(db: Session, client_id: int | None = None) -> tuple[list[dict[str, str]], str | None]:
    """Категории qBittorrent для выпадающих списков. Меняются редко — кэшируются."""
    try:
        config = get_qb_client_config(db, client_id)
    except Exception as exc:
        return [], str(exc)

    def fetch() -> tuple[list[dict[str, str]], str | None]:
        try:
            qb = QBittorrentClient(config)
            # Путь рендера страницы: ждать полный рабочий таймаут ради списка категорий незачем.
            qb.login(timeout=qb.probe_timeout)
            return qb.get_categories(), None
        except Exception as exc:
            return [], str(exc)

    return _categories_cache.get_or_set(f"{config.id}:{config.host}:{config.updated_at}", fetch)


def invalidate_qb_caches() -> None:
    _status_cache.invalidate()
    _categories_cache.invalidate()
