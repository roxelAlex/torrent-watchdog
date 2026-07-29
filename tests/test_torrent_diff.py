"""От диффа зависит, какие файлы НЕ будут перекачаны заново."""

from app.services.torrent_diff import build_torrent_diff, existing_file_keys
from tests.test_torrent_parser import build_torrent


def write(tmp_path, name, files):
    path = tmp_path / name
    path.write_bytes(build_torrent(files))
    return str(path)


def test_added_file_is_new_and_rest_is_existing(tmp_path):
    old = write(tmp_path, "old.torrent", [("a.flac", 100), ("b.flac", 200)])
    new = write(tmp_path, "new.torrent", [("a.flac", 100), ("b.flac", 200), ("c.flac", 300)])
    diff = build_torrent_diff(old, new)
    assert [item["path"] for item in diff["new"]] == ["c.flac"]
    assert {item["path"] for item in diff["existing"]} == {"a.flac", "b.flac"}
    assert diff["removed"] == []
    assert diff["new_size"] == 300


def test_resized_file_counts_as_new_not_existing(tmp_path):
    """Тот же путь с другим размером — перекачивать надо, иначе останется битый файл."""
    old = write(tmp_path, "old.torrent", [("a.flac", 100)])
    new = write(tmp_path, "new.torrent", [("a.flac", 150)])
    diff = build_torrent_diff(old, new)
    assert [item["path"] for item in diff["new"]] == ["a.flac"]
    assert diff["existing"] == []
    assert [item["path"] for item in diff["changed"]] == ["a.flac"]


def test_removed_file_is_reported(tmp_path):
    old = write(tmp_path, "old.torrent", [("a.flac", 100), ("b.flac", 200)])
    new = write(tmp_path, "new.torrent", [("a.flac", 100)])
    diff = build_torrent_diff(old, new)
    assert [item["path"] for item in diff["removed"]] == ["b.flac"]


def test_missing_file_degrades_to_unknown(tmp_path):
    """Без старого файла нельзя утверждать, что что-то уже скачано."""
    new = write(tmp_path, "new.torrent", [("a.flac", 100)])
    diff = build_torrent_diff(None, new)
    assert diff["mode"] == "unknown"
    assert existing_file_keys(diff) == set()


def test_existing_file_keys_pair_path_and_size(tmp_path):
    old = write(tmp_path, "old.torrent", [("a.flac", 100)])
    new = write(tmp_path, "new.torrent", [("a.flac", 100), ("b.flac", 200)])
    assert existing_file_keys(build_torrent_diff(old, new)) == {("a.flac", 100)}
