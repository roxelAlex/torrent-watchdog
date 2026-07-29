import json
from pathlib import Path
from typing import Any

from app.services.torrent_parser import TorrentFile, parse_torrent_details_file


def _key(file: TorrentFile) -> str:
    return f"{file.path}\0{file.size}"


def _public(file: TorrentFile) -> dict[str, Any]:
    return {"path": file.path, "size": file.size}


def build_torrent_diff(old_torrent_path: str | None, new_torrent_path: str | None) -> dict[str, Any]:
    if not old_torrent_path or not new_torrent_path or not Path(old_torrent_path).exists() or not Path(new_torrent_path).exists():
        return {"mode": "unknown", "existing": [], "new": [], "removed": [], "changed": [], "summary": "Недостаточно данных для сравнения файлов."}

    old_details = parse_torrent_details_file(old_torrent_path)
    new_details = parse_torrent_details_file(new_torrent_path)
    old_by_key = {_key(file): file for file in old_details.files}
    new_by_key = {_key(file): file for file in new_details.files}
    old_by_path = {file.path: file for file in old_details.files}

    existing = [file for file in new_details.files if _key(file) in old_by_key]
    new_files = [file for file in new_details.files if _key(file) not in old_by_key]
    removed = [file for file in old_details.files if _key(file) not in new_by_key]
    changed = [
        file for file in new_files
        if file.path in old_by_path and old_by_path[file.path].size != file.size
    ]
    total_new_size = sum(file.size for file in new_files)
    summary = f"Новых файлов: {len(new_files)}, уже были: {len(existing)}, удалены из раздачи: {len(removed)}."

    return {
        "mode": "file_list",
        "existing": [_public(file) for file in existing],
        "new": [_public(file) for file in new_files],
        "removed": [_public(file) for file in removed],
        "changed": [_public(file) for file in changed],
        "new_size": total_new_size,
        "summary": summary,
    }


def diff_to_json(diff: dict[str, Any]) -> str:
    return json.dumps(diff, ensure_ascii=False, separators=(",", ":"))


def diff_from_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def existing_file_keys(diff: dict[str, Any]) -> set[tuple[str, int]]:
    return {(str(item.get("path", "")), int(item.get("size", 0))) for item in diff.get("existing", []) if item.get("path")}
