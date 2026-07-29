from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import bencodepy

from app.errors import InvalidInput


@dataclass(frozen=True)
class TorrentMeta:
    info_hash: str
    name: str | None


@dataclass(frozen=True)
class TorrentFile:
    path: str
    size: int


@dataclass(frozen=True)
class TorrentDetails:
    meta: TorrentMeta
    files: list[TorrentFile]
    piece_length: int | None


def parse_torrent_file(path: str | Path) -> TorrentMeta:
    data = Path(path).read_bytes()
    return parse_torrent_bytes(data)


def parse_torrent_details_file(path: str | Path) -> TorrentDetails:
    return parse_torrent_details_bytes(Path(path).read_bytes())


def parse_torrent_bytes(data: bytes) -> TorrentMeta:
    decoded = bencodepy.decode(data)
    info = decoded[b"info"]
    info_hash = sha1(bencodepy.encode(info)).hexdigest()
    raw_name = info.get(b"name") or info.get(b"name.utf-8")
    name = raw_name.decode("utf-8", errors="replace") if isinstance(raw_name, bytes) else None
    return TorrentMeta(info_hash=info_hash, name=name)


def parse_torrent_details_bytes(data: bytes) -> TorrentDetails:
    decoded = bencodepy.decode(data)
    info = decoded[b"info"]
    meta = parse_torrent_bytes(data)
    piece_length = info.get(b"piece length")
    files: list[TorrentFile] = []
    if b"files" in info:
        for item in info[b"files"]:
            raw_path = item.get(b"path.utf-8") or item.get(b"path") or []
            path = "/".join(_decode_part(part) for part in raw_path)
            files.append(TorrentFile(path=path, size=int(item.get(b"length", 0))))
    else:
        raw_name = info.get(b"name.utf-8") or info.get(b"name") or b""
        files.append(TorrentFile(path=_decode_part(raw_name), size=int(info.get(b"length", 0))))
    return TorrentDetails(meta=meta, files=files, piece_length=int(piece_length) if piece_length else None)


def _decode_part(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def magnet_info_hash(magnet_url: str) -> str:
    parsed = urlparse(magnet_url)
    qs = parse_qs(parsed.query)
    xt_values = qs.get("xt", [])
    for value in xt_values:
        value = unquote(value)
        if value.startswith("urn:btih:"):
            return value.rsplit(":", 1)[-1].lower()
    raise InvalidInput("error.magnet.no_btih")
