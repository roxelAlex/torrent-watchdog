from collections.abc import Generator
from pathlib import Path

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

    _run_migrations()
    _ensure_qbittorrent_client(models)
    _drop_unused_settings(models)
    _backfill_message_codes()
    _recover_interrupted_updates(models)


def _run_migrations() -> None:
    """Схему ведёт Alembic.

    Раньше это были create_all плюс список ручных ALTER TABLE — семь штук к
    версии 0.8, и каждый следующий пришлось бы дописывать туда же. Базовая
    ревизия умеет и пустую базу, и ту, что работает с мая: существующее она
    пропускает, недостающее досоздаёт.
    """
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


def _backfill_message_codes() -> None:
    """Старые записи журнала переводим на коды, чтобы история читалась на любом языке."""
    from app.services.message_backfill import backfill

    db = SessionLocal()
    try:
        backfill(db)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("message backfill failed")
        db.rollback()
    finally:
        db.close()


def _drop_unused_settings(models) -> None:
    """Ключи от удалённых полей формы иначе остаются в базе и выглядят настройками."""
    db = SessionLocal()
    try:
        removed = (
            db.query(models.AppSetting)
            .filter(models.AppSetting.key.notin_(models.RUNTIME_SETTING_KEYS))
            .delete(synchronize_session=False)
        )
        if removed:
            db.commit()
    finally:
        db.close()


def _recover_interrupted_updates(models) -> None:
    """Статус updating снимается только завершением apply_update.

    Если процесс умер во время применения, раздача осталась бы «обновляется» навсегда —
    подпись врала бы бессрочно, а причины в журнале не было бы вовсе.
    """
    db = SessionLocal()
    try:
        stuck = db.query(models.TrackedTorrent).filter(models.TrackedTorrent.status == models.TorrentStatus.updating.value).all()
        from app.services import messages

        for tracked in stuck:
            messages.set_error(tracked, "msg.update_interrupted")
            tracked.status = models.TorrentStatus.error.value
            db.add(messages.event(tracked.id, models.EventType.update_failed.value, "msg.update_interrupted"))
        if stuck:
            db.commit()
    finally:
        db.close()


def _ensure_qbittorrent_client(models) -> None:
    """В пустой базе заводит клиент из .env и привязывает к нему раздачи без клиента."""
    db = SessionLocal()
    try:
        client = db.query(models.QbittorrentClientConfig).order_by(models.QbittorrentClientConfig.name).first()
        if not client:
            client = models.QbittorrentClientConfig(
                name="qBittorrent",
                host=settings.qb_host,
                username=settings.qb_username,
                password=settings.qb_password,
                verify_tls=settings.qb_verify_tls,
                timeout_seconds=settings.qb_timeout_seconds,
            )
            db.add(client)
            db.flush()
        db.query(models.TrackedTorrent).filter(models.TrackedTorrent.qb_client_id.is_(None)).update(
            {models.TrackedTorrent.qb_client_id: client.id}
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
