import logging
from contextlib import asynccontextmanager

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import init_db
from app.routers import api, health, web
from app.scheduler import start_scheduler, stop_scheduler
from app.auth import is_authenticated
from app.services.qbittorrent_client import QBittorrentClient


settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    qb_status = QBittorrentClient().test_connection()
    if qb_status.get("status") != "ok":
        logging.getLogger(__name__).warning("qBittorrent unavailable at startup host=%s error=%s", qb_status.get("host"), qb_status.get("error"))
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key, same_site="lax", https_only=False)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    public_prefixes = ("/static", "/health", "/api/health", "/login")
    if not settings.app_auth_enabled or request.url.path.startswith(public_prefixes):
        return await call_next(request)
    if is_authenticated(request):
        return await call_next(request)
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "Требуется авторизация"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(health.router)
app.include_router(api.router)
app.include_router(web.router)
