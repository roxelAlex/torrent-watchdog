from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SourceType(StrEnum):
    page_url = "page_url"
    torrent_url = "torrent_url"
    magnet = "magnet"


class TrackerType(StrEnum):
    generic = "generic"
    rutracker = "rutracker"


class TorrentStatus(StrEnum):
    active = "active"
    update_available = "update_available"
    updating = "updating"
    updated = "updated"
    error = "error"
    disabled = "disabled"


class EventType(StrEnum):
    check_started = "check_started"
    no_changes = "no_changes"
    update_found = "update_found"
    update_applied = "update_applied"
    update_failed = "update_failed"
    manual_action = "manual_action"
    error = "error"
    qbittorrent_unavailable = "qbittorrent_unavailable"


class TrackedTorrent(Base):
    __tablename__ = "tracked_torrents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    source_url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32), default=SourceType.page_url.value)
    tracker_type: Mapped[str] = mapped_column(String(32), default=TrackerType.generic.value)
    current_info_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_qb_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    qb_client_id: Mapped[int | None] = mapped_column(ForeignKey("qbittorrent_clients.id"), nullable=True)
    current_torrent_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    save_path: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(255), default="")
    tags: Mapped[str] = mapped_column(Text, default="")
    auto_update: Mapped[bool] = mapped_column(Boolean, default=False)
    recheck_after_add: Mapped[bool] = mapped_column(Boolean, default=True)
    start_after_recheck: Mapped[bool] = mapped_column(Boolean, default=True)
    add_paused: Mapped[bool] = mapped_column(Boolean, default=True)
    update_mode: Mapped[str] = mapped_column(String(32), default="new_files_only")
    status: Mapped[str] = mapped_column(String(32), default=TorrentStatus.active.value)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    versions: Mapped[list["TorrentVersion"]] = relationship(back_populates="tracked", cascade="all, delete-orphan")
    events: Mapped[list["CheckEvent"]] = relationship(back_populates="tracked", cascade="all, delete-orphan")
    qb_client: Mapped["QbittorrentClientConfig | None"] = relationship(back_populates="torrents")


class QbittorrentClientConfig(Base):
    __tablename__ = "qbittorrent_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    host: Mapped[str] = mapped_column(Text)
    username: Mapped[str] = mapped_column(String(255), default="")
    password: Mapped[str] = mapped_column(Text, default="")
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    torrents: Mapped[list[TrackedTorrent]] = relationship(back_populates="qb_client")


class TorrentVersion(Base):
    __tablename__ = "torrent_versions"
    # История версий всегда читается по одной раздаче и в порядке обнаружения.
    __table_args__ = (Index("ix_torrent_versions_tracked_detected", "tracked_torrent_id", "detected_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tracked_torrent_id: Mapped[int] = mapped_column(ForeignKey("tracked_torrents.id", ondelete="CASCADE"))
    info_hash: Mapped[str] = mapped_column(String(64), index=True)
    torrent_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    torrent_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    changelog_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    tracked: Mapped[TrackedTorrent] = relationship(back_populates="versions")


class CheckEvent(Base):
    __tablename__ = "check_events"
    # Полоска вахты, журнал раздачи и общий журнал фильтруют по раздаче и сортируют по времени.
    __table_args__ = (
        Index("ix_check_events_tracked_created", "tracked_torrent_id", "created_at"),
        Index("ix_check_events_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tracked_torrent_id: Mapped[int | None] = mapped_column(ForeignKey("tracked_torrents.id", ondelete="CASCADE"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    old_info_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_info_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tracked: Mapped[TrackedTorrent | None] = relationship(back_populates="events")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
