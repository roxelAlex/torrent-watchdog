import re
from dataclasses import dataclass, replace
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


RUTRACKER_TOPIC_RE = re.compile(r"[?&]t=(\d+)")
RUTRACKER_TOPIC_HINT = (
    "Нужна ссылка на тему RuTracker вида https://rutracker.org/forum/viewtopic.php?t=123456. "
    "Magnet и прямые ссылки на .torrent не принимаются."
)


def normalize_source_url(source_url: str) -> str:
    """Под наблюдение берутся только темы RuTracker.

    С темы каждый раз скачивается свежий .torrent, поэтому есть с чем сравнивать
    состав файлов. У magnet файла раздачи нет вовсе, а остальные источники
    сервис намеренно не отслеживает.
    """
    url = source_url.strip()
    if not url:
        raise ValueError(RUTRACKER_TOPIC_HINT)
    if detect_tracker_type(url) != "rutracker" or not RUTRACKER_TOPIC_RE.search(url):
        raise ValueError(RUTRACKER_TOPIC_HINT)
    return url


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


def adopt_torrent_file(resolved: ResolvedTorrent, tracked_id: int) -> ResolvedTorrent:
    """Переносит файл из _pending в папку раздачи, когда у неё появился id.

    Позволяет добавлять раздачу за одно скачивание: до вставки в БД id ещё нет,
    а второй resolve означал бы ещё один проход через FlareSolverr.
    """
    if not resolved.torrent_file_path:
        return resolved
    source = Path(resolved.torrent_file_path)
    if not source.exists():
        return resolved
    target_dir = get_settings().torrents_dir / str(tracked_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    source.replace(target)
    return replace(resolved, torrent_file_path=str(target))


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
