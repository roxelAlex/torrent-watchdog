"""Окружение для тестов задаётся до импорта приложения.

app.config создаёт каталоги данных прямо при импорте, а app.db — движок,
поэтому переменные должны быть выставлены раньше любого `from app...`.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="torrent-watchdog-tests-")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("TORRENTS_DIR", os.path.join(_TMP, "torrents"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{os.path.join(_TMP, 'test.db')}")
os.environ.setdefault("RUTRACKER_COOKIE", "")

import pytest  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app import models  # noqa: E402,F401


@pytest.fixture
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
