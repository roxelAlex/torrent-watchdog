"""Удаление файлов прошлой версии — единственное место, где сервис трогает данные.

Любое несошедшееся условие должно означать «не трогать»: цена ошибки — гигабайты
чужих файлов, которые ничем не вернуть.
"""

import pytest

from app.models import TrackedTorrent
from app.services.update_applier import may_remove_replaced_files

OLD_HASH = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NEW_HASH = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

# Пересборка: три файла ушли, пять пришли, совпадений нет — случай «Yani Neko».
REPACKED = {
    "mode": "file_list",
    "existing": [],
    "new": [{"path": f"NF - 0{i}.mkv", "size": 900 + i} for i in range(1, 6)],
    "removed": [{"path": f"AMZN - 0{i}.mkv", "size": 1700 + i} for i in range(1, 4)],
}
PARTIAL = {
    "mode": "file_list",
    "existing": [{"path": "keep.mkv", "size": 10}],
    "new": [{"path": "new.mkv", "size": 20}],
    "removed": [{"path": "gone.mkv", "size": 30}],
}
UNKNOWN = {"mode": "unknown", "existing": [], "new": [], "removed": []}


def torrent(*, flag: bool = True, qb_hash: str | None = OLD_HASH) -> TrackedTorrent:
    return TrackedTorrent(
        title="Раздача",
        source_url="https://rutracker.org/forum/viewtopic.php?t=1",
        delete_replaced_files=flag,
        current_qb_hash=qb_hash,
    )


def test_full_repack_is_the_case_we_built_this_for():
    assert may_remove_replaced_files(torrent(), REPACKED, NEW_HASH, True) is True


def test_flag_off_means_never():
    assert may_remove_replaced_files(torrent(flag=False), REPACKED, NEW_HASH, True) is False


def test_rollback_never_deletes():
    """При откате выбывшие файлы — как раз те, ради которых он и делается."""
    assert may_remove_replaced_files(torrent(), REPACKED, NEW_HASH, False) is False


def test_partial_update_keeps_everything():
    """Совпал хоть один файл — удалять нельзя: qBittorrent сносит всё разом."""
    assert may_remove_replaced_files(torrent(), PARTIAL, NEW_HASH, True) is False


def test_failed_comparison_keeps_everything():
    """Не знаем состав — не трогаем."""
    assert may_remove_replaced_files(torrent(), UNKNOWN, NEW_HASH, True) is False


def test_empty_diff_keeps_everything():
    assert may_remove_replaced_files(torrent(), {}, NEW_HASH, True) is False


def test_no_previous_torrent_means_nothing_to_delete():
    assert may_remove_replaced_files(torrent(qb_hash=None), REPACKED, NEW_HASH, True) is False
    assert may_remove_replaced_files(torrent(qb_hash=""), REPACKED, NEW_HASH, True) is False


def test_same_torrent_is_not_a_replacement():
    """Повторное применение той же версии не должно сносить её же файлы."""
    assert may_remove_replaced_files(torrent(qb_hash=NEW_HASH), REPACKED, NEW_HASH, True) is False


def test_hash_comparison_ignores_case():
    assert may_remove_replaced_files(torrent(qb_hash=NEW_HASH.upper()), REPACKED, NEW_HASH, True) is False


@pytest.mark.parametrize("diff", [REPACKED, PARTIAL, UNKNOWN, {}])
def test_every_diff_is_safe_when_flag_is_off(diff):
    assert may_remove_replaced_files(torrent(flag=False), diff, NEW_HASH, True) is False


@pytest.mark.parametrize("diff", [REPACKED, PARTIAL, UNKNOWN, {}])
def test_every_diff_is_safe_on_rollback(diff):
    assert may_remove_replaced_files(torrent(), diff, NEW_HASH, False) is False
