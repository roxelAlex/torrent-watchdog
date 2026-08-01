"""Раздачу пересобрали целиком: сравнение прошло, но совпадений нет.

Случай из жизни: 01.08.2026 у «Yani Neko» рипы AMZN заменили на NF —
3 файла превратились в 5, ни одного общего. Сервис сказал, что не смог
сравнить состав, хотя сравнил его прекрасно и получил честный ответ.
"""

import pytest

from app.i18n import translate
from app.routers.web import _version_summary
from app.services.torrent_diff import build_torrent_diff, existing_file_keys
from app.services.torrent_parser import TorrentFile
from tests.test_torrent_parser import build_torrent


class FakeVersion:
    def __init__(self, changelog_text):
        self.changelog_text = changelog_text


def write(tmp_path, name, files):
    path = tmp_path / name
    path.write_bytes(build_torrent(files))
    return str(path)


AMZN = [(f"Yani Neko - 0{i} [WEB-DL AMZN 1080p AVC AAC].mkv", 1_700_000_000 + i) for i in (1, 2, 3)]
NF = [(f"Yani Neko - 0{i} [WEB-DL NF 1080p AVC AAC].mkv", 970_000_000 + i) for i in (1, 2, 3, 4, 5)]


@pytest.fixture
def repacked(tmp_path):
    return build_torrent_diff(write(tmp_path, "old.torrent", AMZN), write(tmp_path, "new.torrent", NF))


def test_comparison_succeeds(repacked):
    """Это не «сравнить не удалось»: дифф посчитан полностью."""
    assert repacked["mode"] == "file_list"


def test_nothing_matches(repacked):
    assert len(repacked["new"]) == 5
    assert repacked["existing"] == []
    assert len(repacked["removed"]) == 3


def test_nothing_to_skip(repacked):
    """Пустой набор совпадений — причина, по которой приоритеты не выставляются."""
    assert existing_file_keys(repacked) == set()


def test_summary_does_not_claim_a_failed_comparison(repacked):
    import json

    summary = _version_summary(FakeVersion(json.dumps(repacked)), "new_files_only", "ru")
    assert "не удалось" not in summary
    assert "5" in summary and "3" in summary


def test_summary_does_not_promise_skipping(repacked):
    """Обещать «не будут перекачиваться» нечего: совпадений ноль."""
    import json

    summary = _version_summary(FakeVersion(json.dumps(repacked)), "new_files_only", "ru")
    assert "не будут перекачиваться" not in summary


def test_missing_old_file_still_reports_a_failed_comparison(tmp_path):
    """Настоящий случай «сравнить не с чем» никуда не делся."""
    import json

    diff = build_torrent_diff(None, write(tmp_path, "new.torrent", NF))
    assert diff["mode"] == "unknown"
    summary = _version_summary(FakeVersion(json.dumps(diff)), "new_files_only", "ru")
    assert "не удалось" in summary


@pytest.mark.parametrize("language", ["ru", "en"])
def test_both_outcomes_have_distinct_texts(language):
    repack = translate("msg.update_applied.nothing_in_common", language, new=5, removed=3)
    unavailable = translate("msg.update_applied.no_comparison", language)
    assert repack != unavailable
    assert "5" in repack and "3" in repack
