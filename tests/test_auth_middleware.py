"""Порядок middleware: сессия должна разворачиваться до проверки входа.

Ошибка пряталась за `APP_AUTH_ENABLED=false`: при выключенном входе проверка
выходит раньше, чем трогает сессию. В `.env.example` вход включён, поэтому
у любой новой установки не открывалась ни одна страница — только 500.
"""

from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app import main


def test_session_middleware_is_the_outer_layer():
    """Starlette делает внешним слоем добавленный последним — он первый в списке."""
    classes = [item.cls for item in main.app.user_middleware]
    assert SessionMiddleware in classes, "сессия вообще не подключена"
    assert classes.index(SessionMiddleware) == 0, (
        "сессия должна быть снаружи проверки входа, иначе request.session в ней недоступен"
    )


def test_protected_page_redirects_to_login(monkeypatch):
    monkeypatch.setattr(main.settings, "app_auth_enabled", True)
    response = TestClient(main.app).get("/", follow_redirects=False)
    assert response.status_code == 303, response.text[:300]
    assert response.headers["location"] == "/login"


def test_login_page_opens_with_auth_enabled(monkeypatch):
    monkeypatch.setattr(main.settings, "app_auth_enabled", True)
    response = TestClient(main.app).get("/login")
    assert response.status_code == 200


def test_api_answers_401_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(main.settings, "app_auth_enabled", True)
    response = TestClient(main.app).get("/api/torrents")
    assert response.status_code == 401


def test_health_stays_public(monkeypatch):
    monkeypatch.setattr(main.settings, "app_auth_enabled", True)
    assert TestClient(main.app).get("/health").status_code == 200
