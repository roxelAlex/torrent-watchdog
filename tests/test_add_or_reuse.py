"""Раздача, которая уже лежит в клиенте, и тело ответа в тексте ошибки.

qBittorrent 5.x отвечает на /torrents/add кодом 409 с пустым телом, когда не
добавил ни одной раздачи. Дубликат по инфохешу — ровно этот случай: добавление
нового отслеживания для раздачи, залитой руками, падало в статус «ошибка».
"""

import pytest
import requests

from app.services.qbittorrent_client import QBittorrentClient, _raise_for_status


class FakeResponse:
    def __init__(self, status_code: int, text: str = "", reason: str = ""):
        self.status_code = status_code
        self.text = text
        self.reason = reason
        self.url = "http://qb.test/api/v2/torrents/add"
        self.request = None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Client Error: {self.reason} for url: {self.url}",
                response=self,
            )


class FakeClient(QBittorrentClient):
    """Клиент без сети: список раздач и ответ на добавление задаются тестом."""

    def __init__(self, torrents: list[dict], add_status: int = 200):
        self.base_url = "http://qb.test"
        self.torrents = torrents
        self.add_status = add_status
        # Чем станет список раздач к моменту отказа: так воспроизводится гонка.
        self.appear_on_add: list[dict] | None = None
        self.calls: list[tuple] = []

    def get_torrent_list(self):
        self.calls.append(("list",))
        return self.torrents

    def add_torrent_file(self, torrent_file_path, save_path, category="", tags="", paused=True):
        self.calls.append(("add", save_path, category, tags, paused))
        if self.add_status >= 400:
            if self.appear_on_add is not None:
                self.torrents = self.appear_on_add
            FakeResponse(self.add_status, reason="Conflict").raise_for_status()
        return "abc123"

    def set_category(self, torrent_hash, category):
        self.calls.append(("set_category", torrent_hash, category))

    def add_tags(self, torrent_hash, tags):
        self.calls.append(("add_tags", torrent_hash, tags))


HASH = "CA7EEEC68604FF32F6B8B47348740B071967E0BF"


def test_new_torrent_is_added_as_usual():
    qb = FakeClient(torrents=[])
    assert qb.add_or_reuse_torrent_file("/data/t.torrent", HASH, "/series", "Series", "", False) == "abc123"
    assert ("add", "/series", "Series", "", False) in qb.calls
    assert not any(call[0] == "set_category" for call in qb.calls)


def test_torrent_already_in_client_is_reused_without_add():
    """Список раздач известен заранее — добавление даже не пробуем."""
    qb = FakeClient(torrents=[{"hash": HASH.lower(), "name": "Steel Ball Run"}])
    assert qb.add_or_reuse_torrent_file("/data/t.torrent", HASH, "", "Series", "anime") == HASH.lower()
    assert not any(call[0] == "add" for call in qb.calls)
    assert ("set_category", HASH, "Series") in qb.calls
    assert ("add_tags", HASH, "anime") in qb.calls


def test_conflict_on_add_is_reused_when_hash_shows_up():
    """Раздачу зарегистрировали между проверкой списка и добавлением."""
    qb = FakeClient(torrents=[], add_status=409)
    qb.appear_on_add = [{"hash": HASH.lower()}]
    assert qb.add_or_reuse_torrent_file("/data/t.torrent", HASH, "", "Series", "") == HASH.lower()
    assert ("set_category", HASH, "Series") in qb.calls


def test_conflict_without_the_hash_in_client_is_a_real_error():
    """409 не всегда дубликат: раздачи нет — значит отказ настоящий."""
    qb = FakeClient(torrents=[{"hash": "0" * 40}], add_status=409)
    with pytest.raises(requests.HTTPError):
        qb.add_or_reuse_torrent_file("/data/t.torrent", HASH, "", "Series", "")


def test_response_body_explains_the_error():
    with pytest.raises(requests.HTTPError) as error:
        _raise_for_status(FakeResponse(400, text="Category cannot be empty\n", reason="Bad Request"))
    assert "Category cannot be empty" in str(error.value)
    assert error.value.response.status_code == 400


def test_empty_body_leaves_the_message_alone():
    with pytest.raises(requests.HTTPError) as error:
        _raise_for_status(FakeResponse(409, text="", reason="Conflict"))
    assert str(error.value).endswith("for url: http://qb.test/api/v2/torrents/add")


def test_body_repeating_the_reason_phrase_adds_nothing():
    """Так отвечает 409 на /torrents/add: в теле одно слово Conflict."""
    with pytest.raises(requests.HTTPError) as error:
        _raise_for_status(FakeResponse(409, text="Conflict", reason="Conflict"))
    assert "Conflict: Conflict" not in str(error.value)
