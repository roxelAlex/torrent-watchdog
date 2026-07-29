import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import InvalidInput
from app.models import QbittorrentClientConfig
from app.services.cache import TTLCache
from app.services.qbittorrent_client import QBittorrentClient

logger = logging.getLogger(__name__)

_status_cache = TTLCache(get_settings().qb_status_cache_seconds)
_categories_cache = TTLCache(get_settings().qb_categories_cache_seconds)
_paths_cache = TTLCache(get_settings().qb_categories_cache_seconds)


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
        raise InvalidInput("error.qb.client_not_found")
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


def client_paths(db: Session, client_id: int | None = None) -> dict[str, object]:
    """Куда клиент пишет по умолчанию и что делает с файлами при смене категории.

    qBittorrent знает это сам, гадать не нужно: default save path лежит в
    настройках, а torrent_changed_tmm_enabled говорит, переезжает ли раздача
    вслед за категорией. Для раздач в ручном режиме перемещения не будет.
    """
    try:
        config = get_qb_client_config(db, client_id)
    except Exception:
        return {}

    def fetch() -> dict[str, object]:
        try:
            qb = QBittorrentClient(config)
            qb.login(timeout=qb.probe_timeout)
            prefs = qb.get_preferences()
            return {
                "default_save_path": str(prefs.get("save_path") or ""),
                "temp_path": str(prefs.get("temp_path") or "") if prefs.get("temp_path_enabled") else "",
                "auto_tmm": bool(prefs.get("auto_tmm_enabled")),
                "relocate_on_category_change": bool(prefs.get("torrent_changed_tmm_enabled")),
            }
        except Exception as exc:
            logger.warning("cannot read qBittorrent preferences client=%s error=%s", config.name, exc)
            return {}

    return _paths_cache.get_or_set(f"{config.id}:{config.host}:{config.updated_at}", fetch)


def category_save_path(category: str, categories: list[dict], default_save_path: str) -> str:
    """Куда qBittorrent реально кладёт раздачи этой категории.

    У категории без собственного пути раздачи уходят в подпапку с её именем
    внутри пути по умолчанию. Проверено на живых клиентах: lidarr и radarr без
    пути дают /downloads/lidarr и /downloads/radarr. Показывать для них просто
    /downloads было бы неправдой.

    Для категории, которой в клиенте нет, путь не угадываем — возвращаем пусто.
    """
    if not category:
        return default_save_path
    match = next((item for item in categories if item.get("name") == category), None)
    if match is None:
        return ""
    own = (match.get("save_path") or "").strip()
    if own:
        return own
    if not default_save_path:
        return ""
    return f"{default_save_path.rstrip('/')}/{category}"


def with_effective_paths(categories: list[dict], default_save_path: str = "") -> list[dict]:
    """Категории с добавленным полем «куда на самом деле»."""
    return [
        {**item, "effective_path": category_save_path(item.get("name", ""), categories, default_save_path)}
        for item in categories
    ]


def path_suggestions(categories: list[dict], default_save_path: str = "") -> list[dict[str, str]]:
    """Пути, которые клиент уже знает: свой по умолчанию и пути категорий.

    Ничего не выдумываем — только то, что следует из ответов qBittorrent.
    Подсказываем итоговые пути, включая подпапки категорий без своего пути.
    """
    suggestions: list[dict[str, str]] = []
    seen: set[str] = set()
    if default_save_path:
        suggestions.append({"path": default_save_path, "kind": "default", "category": ""})
        seen.add(default_save_path)
    for item in categories:
        path = category_save_path(item.get("name", ""), categories, default_save_path)
        if not path or path in seen:
            continue
        seen.add(path)
        suggestions.append({"path": path, "kind": "category", "category": item.get("name", "")})
    return suggestions


def ensure_category(qb: QBittorrentClient, name: str, save_path: str = "") -> None:
    """Заводит категорию, если её ещё нет. Пустое имя — это «без категории».

    Пустой save_path означает «пусть qBittorrent решает»: он положит раздачи
    в подпапку с именем категории внутри пути по умолчанию.
    """
    if not name:
        return
    if any(item.get("name") == name for item in qb.get_categories()):
        return
    qb.create_category(name, save_path.strip())
    invalidate_qb_caches()


def invalidate_qb_caches() -> None:
    _status_cache.invalidate()
    _categories_cache.invalidate()
    _paths_cache.invalidate()
