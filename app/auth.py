import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings
from app.i18n import translate

security = HTTPBasic(auto_error=False)


def is_authenticated(request: Request) -> bool:
    settings = get_settings()
    if not settings.app_auth_enabled:
        return True
    return request.session.get("user") == settings.app_auth_username


def check_credentials(username: str, password: str) -> bool:
    settings = get_settings()
    ok_user = secrets.compare_digest(username, settings.app_auth_username)
    ok_password = secrets.compare_digest(password, settings.app_auth_password)
    return ok_user and ok_password


def require_auth(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)) -> str:
    settings = get_settings()
    if not settings.app_auth_enabled:
        return "anonymous"
    if is_authenticated(request):
        return settings.app_auth_username
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=translate("error.auth.required"))
    if not check_credentials(credentials.username, credentials.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"}, detail=translate("login.failed"))
    return credentials.username
