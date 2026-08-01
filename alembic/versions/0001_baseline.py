"""Базовая схема.

Проект жил на create_all и ручных ALTER TABLE, поэтому у существующих установок
таблицы уже есть. Миграция это учитывает: то, что уже создано, пропускается, а
недостающее досоздаётся. Так одна и та же ревизия годится и для пустой базы,
и для той, что работает с мая.

Revision ID: 0001
Revises:
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = _existing_tables()

    if "qbittorrent_clients" not in tables:
        op.create_table(
            "qbittorrent_clients",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("host", sa.Text(), nullable=False),
            sa.Column("username", sa.String(255)),
            sa.Column("password", sa.Text()),
            sa.Column("verify_tls", sa.Boolean()),
            sa.Column("timeout_seconds", sa.Integer()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "tracked_torrents" not in tables:
        op.create_table(
            "tracked_torrents",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(255)),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("source_type", sa.String(32)),
            sa.Column("tracker_type", sa.String(32)),
            sa.Column("current_info_hash", sa.String(64)),
            sa.Column("current_qb_hash", sa.String(64)),
            sa.Column("qb_client_id", sa.Integer(), sa.ForeignKey("qbittorrent_clients.id")),
            sa.Column("current_torrent_name", sa.String(255)),
            sa.Column("save_path", sa.Text()),
            sa.Column("category", sa.String(255)),
            sa.Column("tags", sa.Text()),
            sa.Column("auto_update", sa.Boolean()),
            sa.Column("recheck_after_add", sa.Boolean()),
            sa.Column("start_after_recheck", sa.Boolean()),
            sa.Column("add_paused", sa.Boolean()),
            sa.Column("delete_replaced_files", sa.Boolean()),
            sa.Column("update_mode", sa.String(32)),
            sa.Column("status", sa.String(32)),
            sa.Column("last_check_at", sa.DateTime(timezone=True)),
            sa.Column("last_update_at", sa.DateTime(timezone=True)),
            sa.Column("last_error", sa.Text()),
            sa.Column("last_error_code", sa.String(64)),
            sa.Column("last_error_params", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    else:
        # Колонки, добавлявшиеся по ходу жизни проекта ручными ALTER TABLE.
        present = _columns("tracked_torrents")
        for column in (
            sa.Column("qb_client_id", sa.Integer()),
            sa.Column("update_mode", sa.String(32), server_default="new_files_only"),
            sa.Column("delete_replaced_files", sa.Boolean(), server_default="0"),
            sa.Column("last_error_code", sa.String(64)),
            sa.Column("last_error_params", sa.Text()),
        ):
            if column.name not in present:
                op.add_column("tracked_torrents", column)

    if "torrent_versions" not in tables:
        op.create_table(
            "torrent_versions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tracked_torrent_id", sa.Integer(), sa.ForeignKey("tracked_torrents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("info_hash", sa.String(64), nullable=False),
            sa.Column("torrent_name", sa.String(255)),
            sa.Column("torrent_file_path", sa.Text()),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("applied_at", sa.DateTime(timezone=True)),
            sa.Column("is_current", sa.Boolean()),
            sa.Column("changelog_text", sa.Text()),
        )

    if "check_events" not in tables:
        op.create_table(
            "check_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tracked_torrent_id", sa.Integer(), sa.ForeignKey("tracked_torrents.id", ondelete="CASCADE")),
            sa.Column("event_type", sa.String(32), nullable=False),
            sa.Column("message", sa.Text()),
            sa.Column("message_code", sa.String(64)),
            sa.Column("message_params", sa.Text()),
            sa.Column("old_info_hash", sa.String(64)),
            sa.Column("new_info_hash", sa.String(64)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    else:
        present = _columns("check_events")
        for column in (sa.Column("message_code", sa.String(64)), sa.Column("message_params", sa.Text())):
            if column.name not in present:
                op.add_column("check_events", column)

    if "app_settings" not in tables:
        op.create_table(
            "app_settings",
            sa.Column("key", sa.String(255), primary_key=True),
            sa.Column("value", sa.Text()),
        )

    # Понятия «основной клиент» нет с 0.4.0, а колонка NOT NULL ломала бы вставку.
    if "qbittorrent_clients" in tables and "is_default" in _columns("qbittorrent_clients"):
        op.drop_column("qbittorrent_clients", "is_default")

    for table, name, columns in (
        ("torrent_versions", "ix_torrent_versions_info_hash", ["info_hash"]),
        ("torrent_versions", "ix_torrent_versions_tracked_detected", ["tracked_torrent_id", "detected_at"]),
        ("check_events", "ix_check_events_event_type", ["event_type"]),
        ("check_events", "ix_check_events_tracked_created", ["tracked_torrent_id", "created_at"]),
        ("check_events", "ix_check_events_created", ["created_at"]),
    ):
        existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}
        if name not in existing:
            op.create_index(name, table, columns)


def downgrade() -> None:
    # Откат базовой схемы означал бы удаление всех данных — намеренно не поддержан.
    raise NotImplementedError("базовая ревизия не откатывается")
