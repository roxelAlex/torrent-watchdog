"""Alembic должен приводить пустую базу ровно к тому, что описано в моделях.

Раньше схему вели create_all и семь ручных ALTER TABLE. Тест сторожит, чтобы
миграции не разошлись с моделями: расхождение всплыло бы только у того, кто
поставит сервис с нуля.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.db import Base

ROOT = Path(__file__).resolve().parent.parent


def migrate(database_url: str) -> None:
    subprocess.run(
        [sys.executable, "-c",
         "from alembic import command; from alembic.config import Config;"
         f"c = Config(r'{ROOT / 'alembic.ini'}'); c.set_main_option('sqlalchemy.url', r'{database_url}');"
         "command.upgrade(c, 'head')"],
        cwd=ROOT, check=True, capture_output=True,
    )


@pytest.fixture
def migrated(tmp_path):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    migrate(url)
    return create_engine(url)


def test_all_tables_are_created(migrated):
    assert set(inspect(migrated).get_table_names()) >= set(Base.metadata.tables)


def test_columns_match_the_models(migrated):
    inspector = inspect(migrated)
    mismatched = {}
    for name, table in Base.metadata.tables.items():
        actual = {column["name"] for column in inspector.get_columns(name)}
        expected = {column.name for column in table.columns}
        if expected - actual:
            mismatched[name] = sorted(expected - actual)
    assert not mismatched, f"миграции отстали от моделей: {mismatched}"


def test_indexes_match_the_models(migrated):
    inspector = inspect(migrated)
    missing = {}
    for name, table in Base.metadata.tables.items():
        actual = {index["name"] for index in inspector.get_indexes(name)}
        expected = {index.name for index in table.indexes}
        if expected - actual:
            missing[name] = sorted(expected - actual)
    assert not missing, f"индексы не созданы миграцией: {missing}"


def test_version_is_stamped(migrated):
    with migrated.connect() as connection:
        from sqlalchemy import text
        assert connection.execute(text("select version_num from alembic_version")).scalar()


def test_running_twice_is_harmless(tmp_path):
    """Контейнер перезапускается часто, миграции идут при каждом старте."""
    url = f"sqlite:///{tmp_path / 'twice.db'}"
    migrate(url)
    migrate(url)
    assert set(inspect(create_engine(url)).get_table_names()) >= set(Base.metadata.tables)
