from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

from app.config import get_settings
from app.services.torrent_parser import TorrentMeta, magnet_info_hash, parse_torrent_bytes


@dataclass(frozen=True)
class ResolvedTorrent:
    info_hash: str
    torrent_name: str | None
    torrent_file_path: str | None
    source_type: str
    tracker_type: str


def detect_source_type(source_url: str) -> str:
    lowered = source_url.lower()
    if lowered.startswith("magnet:"):
        return "magnet"
    if ".torrent" in lowered:
        return "torrent_url"
    return "page_url"


def detect_tracker_type(source_url: str) -> str:
    host = urlparse(source_url).netloc.lower()
    return "rutracker" if "rutracker.org" in host else "generic"


def save_torrent_bytes(data: bytes, tracked_id: int | None, info_hash: str) -> Path:
    base = get_settings().torrents_dir / (str(tracked_id) if tracked_id else "_pending")
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{info_hash}.torrent"
    path.write_bytes(data)
    return path


def resolve_source(source_url: str, tracked_id: int | None = None) -> ResolvedTorrent:
    source_type = detect_source_type(source_url)
    tracker_type = detect_tracker_type(source_url)
    if tracker_type == "rutracker" and source_type == "page_url":
        from app.services.rutracker_resolver import resolve_rutracker

        return resolve_rutracker(source_url, tracked_id=tracked_id)
    if source_type == "magnet":
        return ResolvedTorrent(
            info_hash=magnet_info_hash(source_url),
            torrent_name=None,
            torrent_file_path=None,
            source_type=source_type,
            tracker_type=tracker_type,
        )
    if source_type == "torrent_url":
        response = requests.get(source_url, timeout=30)
        response.raise_for_status()
        meta: TorrentMeta = parse_torrent_bytes(response.content)
        path = save_torrent_bytes(response.content, tracked_id, meta.info_hash)
        return ResolvedTorrent(meta.info_hash, meta.name, str(path), source_type, tracker_type)
    raise ValueError("Для ссылки на страницу нужен поддерживаемый resolver. Сейчас поддержан RuTracker.")
