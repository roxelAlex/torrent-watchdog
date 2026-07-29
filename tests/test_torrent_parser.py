"""info_hash — основа всей логики: по нему решается, вышло ли обновление."""

from hashlib import sha1

import bencodepy
import pytest

from app.services.torrent_parser import magnet_info_hash, parse_torrent_bytes, parse_torrent_details_bytes


def build_torrent(files: list[tuple[str, int]], name: str = "Раздача") -> bytes:
    info = {
        b"name": name.encode(),
        b"piece length": 262144,
        b"pieces": b"\x00" * 20,
        b"files": [{b"length": size, b"path": [part.encode() for part in path.split("/")]} for path, size in files],
    }
    return bencodepy.encode({b"announce": b"http://tracker.example/ann", b"info": info})


def test_info_hash_is_sha1_of_info_dict_only():
    data = build_torrent([("a.flac", 100)])
    expected = sha1(bencodepy.encode(bencodepy.decode(data)[b"info"])).hexdigest()
    assert parse_torrent_bytes(data).info_hash == expected


def test_info_hash_ignores_fields_outside_info():
    """Смена трекера не должна выглядеть как новая версия раздачи."""
    info = bencodepy.decode(build_torrent([("a.flac", 100)]))[b"info"]
    first = bencodepy.encode({b"announce": b"http://one.example/ann", b"info": info})
    second = bencodepy.encode({b"announce": b"http://two.example/ann", b"info": info})
    assert parse_torrent_bytes(first).info_hash == parse_torrent_bytes(second).info_hash


def test_info_hash_changes_when_file_added():
    one = parse_torrent_bytes(build_torrent([("a.flac", 100)])).info_hash
    two = parse_torrent_bytes(build_torrent([("a.flac", 100), ("b.flac", 200)])).info_hash
    assert one != two


def test_parses_name_and_files():
    details = parse_torrent_details_bytes(build_torrent([("cd1/a.flac", 100), ("cd1/b.flac", 200)]))
    assert details.meta.name == "Раздача"
    assert [(file.path, file.size) for file in details.files] == [("cd1/a.flac", 100), ("cd1/b.flac", 200)]


def test_single_file_torrent():
    data = bencodepy.encode({b"info": {b"name": b"solo.mkv", b"length": 42, b"piece length": 16384, b"pieces": b"\x00" * 20}})
    details = parse_torrent_details_bytes(data)
    assert [(file.path, file.size) for file in details.files] == [("solo.mkv", 42)]


def test_magnet_info_hash():
    magnet = "magnet:?xt=urn:btih:ABCDEF0123456789ABCDEF0123456789ABCDEF01&dn=test"
    assert magnet_info_hash(magnet) == "abcdef0123456789abcdef0123456789abcdef01"


def test_magnet_without_btih_is_rejected():
    with pytest.raises(ValueError):
        magnet_info_hash("magnet:?dn=test")
