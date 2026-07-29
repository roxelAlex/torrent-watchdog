"""Регрессия: «Применить обновление» не должно устанавливать старую версию."""

from datetime import datetime, timedelta

from app.models import TorrentVersion, TrackedTorrent
from app.services.update_checker import latest_pending_version


def make_torrent(db, current_hash: str) -> TrackedTorrent:
    tracked = TrackedTorrent(title="Раздача", source_url="https://example.org/t=1", current_info_hash=current_hash)
    db.add(tracked)
    db.commit()
    db.refresh(tracked)
    return tracked


def add_version(db, tracked, info_hash, detected_at, applied_at=None, is_current=False):
    version = TorrentVersion(
        tracked_torrent_id=tracked.id,
        info_hash=info_hash,
        source_url=tracked.source_url,
        detected_at=detected_at,
        applied_at=applied_at,
        is_current=is_current,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def test_applied_history_is_not_offered_again(db):
    """История применённых версий тоже не is_current — сама по себе она ни о чём не говорит."""
    day = datetime(2026, 1, 1)
    tracked = make_torrent(db, "hash-new")
    add_version(db, tracked, "hash-old", day, applied_at=day)
    add_version(db, tracked, "hash-new", day + timedelta(days=30), applied_at=day + timedelta(days=30), is_current=True)
    assert latest_pending_version(db, tracked.id) is None


def test_stale_never_applied_version_is_not_offered(db):
    """Первая версия из неудачного добавления остаётся с applied_at=None навсегда."""
    day = datetime(2026, 1, 1)
    tracked = make_torrent(db, "hash-current")
    add_version(db, tracked, "hash-stale", day)
    add_version(db, tracked, "hash-current", day + timedelta(days=60), applied_at=day + timedelta(days=60), is_current=True)
    assert latest_pending_version(db, tracked.id) is None


def test_new_version_is_offered(db):
    day = datetime(2026, 1, 1)
    tracked = make_torrent(db, "hash-current")
    add_version(db, tracked, "hash-current", day, applied_at=day, is_current=True)
    pending = add_version(db, tracked, "hash-fresh", day + timedelta(days=5))
    found = latest_pending_version(db, tracked.id)
    assert found is not None and found.id == pending.id


def test_newest_of_several_pending_wins(db):
    day = datetime(2026, 1, 1)
    tracked = make_torrent(db, "hash-current")
    add_version(db, tracked, "hash-current", day, applied_at=day, is_current=True)
    add_version(db, tracked, "hash-a", day + timedelta(days=1))
    newest = add_version(db, tracked, "hash-b", day + timedelta(days=2))
    found = latest_pending_version(db, tracked.id)
    assert found is not None and found.id == newest.id


def test_version_matching_current_hash_is_not_pending(db):
    """Повторно обнаруженный тот же хеш — не обновление."""
    day = datetime(2026, 1, 1)
    tracked = make_torrent(db, "hash-current")
    add_version(db, tracked, "hash-current", day, applied_at=day, is_current=True)
    add_version(db, tracked, "hash-current", day + timedelta(days=1))
    assert latest_pending_version(db, tracked.id) is None
