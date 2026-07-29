from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


if is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:
        """Планировщик пишет из своего потока, пока веб-запросы читают.

        Без WAL это «database is locked»; без foreign_keys не работает
        ondelete="CASCADE", объявленный в моделях.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_db() -> None:
    from app import models

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite()
    _ensure_indexes()
    _ensure_default_qbittorrent_client(models)


def _ensure_indexes() -> None:
    """create_all пропускает уже существующие таблицы целиком, вместе с их индексами."""
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                index.create(bind=conn, checkfirst=True)


def _migrate_sqlite() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "tracked_torrents" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("tracked_torrents")}
    if "qb_client_id" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE tracked_torrents ADD COLUMN qb_client_id INTEGER"))
    if "update_mode" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE tracked_torrents ADD COLUMN update_mode VARCHAR(32) DEFAULT 'new_files_only'"))


def _ensure_default_qbittorrent_client(models) -> None:
    db = SessionLocal()
    try:
        default_client = db.query(models.QbittorrentClientConfig).filter(models.QbittorrentClientConfig.is_default.is_(True)).first()
        if not default_client:
            default_client = models.QbittorrentClientConfig(
                name="Основной",
                host=settings.qb_host,
                username=settings.qb_username,
                password=settings.qb_password,
                verify_tls=settings.qb_verify_tls,
                timeout_seconds=settings.qb_timeout_seconds,
                is_default=True,
            )
            db.add(default_client)
            db.flush()
        db.query(models.TrackedTorrent).filter(models.TrackedTorrent.qb_client_id.is_(None)).update(
            {models.TrackedTorrent.qb_client_id: default_client.id}
        )
        db.commit()
    finally:
        db.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
