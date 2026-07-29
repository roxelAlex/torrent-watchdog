"""Выбор клиента qBittorrent. Понятия «основной» нет — есть явная привязка и запасной."""

from app.models import QbittorrentClientConfig
from app.services.qbittorrent_registry import get_fallback_qb_client, get_qb_client_config, list_qb_clients


def add_client(db, name: str, host: str = "http://qb.example:8080") -> QbittorrentClientConfig:
    client = QbittorrentClientConfig(name=name, host=host)
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


def test_explicit_client_wins(db):
    first = add_client(db, "Alpha")
    second = add_client(db, "Zulu")
    assert get_qb_client_config(db, second.id).id == second.id
    assert get_qb_client_config(db, first.id).id == first.id


def test_fallback_is_first_by_name(db):
    add_client(db, "Zulu")
    alpha = add_client(db, "Alpha")
    assert get_fallback_qb_client(db).id == alpha.id
    assert get_qb_client_config(db, None).id == alpha.id


def test_unknown_client_id_falls_back(db):
    alpha = add_client(db, "Alpha")
    assert get_qb_client_config(db, 999).id == alpha.id


def test_empty_database_gets_client_from_env(db):
    client = get_fallback_qb_client(db)
    assert client.id is not None
    assert list_qb_clients(db) == [client]


def test_clients_are_listed_alphabetically(db):
    add_client(db, "Zulu")
    add_client(db, "Alpha")
    add_client(db, "Mike")
    assert [client.name for client in list_qb_clients(db)] == ["Alpha", "Mike", "Zulu"]
