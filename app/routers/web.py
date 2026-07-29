from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, joinedload
from datetime import timezone
from zoneinfo import ZoneInfo

from app.auth import check_credentials
from app.config import get_settings
from app.db import get_db
from app.models import AppSetting, CheckEvent, TorrentStatus, TorrentVersion, TrackedTorrent
from app.scheduler import next_check_at
from app.schemas import TorrentCreate
from app.services.qbittorrent_client import QBittorrentClient
from app.services.qbittorrent_registry import (
    client_categories,
    client_statuses,
    create_qb_client,
    get_fallback_qb_client,
    get_qb_client_config,
    list_qb_clients,
    update_qb_client,
)
from app.services import flaresolverr, rutracker_auth
from app.services.torrent_settings import change_torrent_category
from app.services.update_applier import apply_update, rollback_to_version
from app.services.update_checker import check_torrent, create_initial_torrent, latest_pending_version
from app.services.torrent_diff import diff_from_json

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["app_version"] = get_settings().app_version
templates.env.globals["app_name"] = get_settings().app_name
STATUS_LABEL = {
    "active": "активна",
    "update_available": "найдено обновление",
    "updating": "обновляется",
    "updated": "обновлено",
    "error": "ошибка",
    "disabled": "отключена",
}
EVENT_LABEL = {
    "check_started": "проверка начата",
    "no_changes": "без изменений",
    "update_found": "обновление найдено",
    "update_applied": "обновление применено",
    "update_failed": "ошибка обновления",
    "manual_action": "ручное действие",
    "error": "ошибка",
    "qbittorrent_unavailable": "qBittorrent недоступен",
}
DUTY_OUTCOMES = {
    "no_changes": "ok",
    "update_applied": "applied",
    "update_found": "found",
    "update_failed": "bad",
    "error": "bad",
    "qbittorrent_unavailable": "bad",
}
templates.env.globals["status_label"] = STATUS_LABEL
templates.env.globals["event_label"] = EVENT_LABEL


def _fmt_dt(value) -> str:
    if not value:
        return "нет"
    settings = get_settings()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(settings.tz)).strftime("%d.%m.%Y %H:%M")


def _version_summary(version: TorrentVersion | None, update_mode: str = "new_files_only") -> str:
    """Итог зависит от режима: обещать «не будет перекачиваться» можно только для new_files_only."""
    if not version:
        return ""
    diff = diff_from_json(version.changelog_text)
    new_count = len(diff.get("new", []))
    existing_count = len(diff.get("existing", []))
    if update_mode != "new_files_only":
        if new_count:
            return f"Новых файлов: {new_count}, уже имеющихся: {existing_count}. Раздача будет заменена целиком, qBittorrent сверит файлы на диске."
        return "Раздача будет заменена целиком, qBittorrent сверит файлы на диске."
    if not diff or diff.get("mode") == "unknown":
        return "Сравнить состав файлов с прошлой версией не удалось: её сохранённый .torrent недоступен. Вместо пропуска старых файлов будет запущен recheck."
    if new_count:
        return f"Новых файлов: {new_count}. Уже имеющиеся файлы не будут перекачиваться: {existing_count}."
    return "Состав раздачи изменился, новых файлов по списку не найдено."


templates.env.filters["dt"] = _fmt_dt
templates.env.globals["version_summary"] = _version_summary


def _duty_strips(db: Session, tracked_ids: list[int], limit: int = 14) -> dict[int, list[dict[str, str]]]:
    """Последние исходы проверок для полосок вахты — одним запросом на весь реестр."""
    if not tracked_ids:
        return {}
    ranked = (
        select(
            CheckEvent.tracked_torrent_id.label("tracked_id"),
            CheckEvent.event_type,
            CheckEvent.created_at,
            func.row_number()
            .over(partition_by=CheckEvent.tracked_torrent_id, order_by=CheckEvent.created_at.desc())
            .label("position"),
        )
        .where(
            CheckEvent.tracked_torrent_id.in_(tracked_ids),
            CheckEvent.event_type.in_(DUTY_OUTCOMES),
        )
        .subquery()
    )
    rows = db.execute(
        select(ranked.c.tracked_id, ranked.c.event_type, ranked.c.created_at)
        .where(ranked.c.position <= limit)
        .order_by(ranked.c.tracked_id, ranked.c.created_at)
    ).all()

    strips: dict[int, list[dict[str, str]]] = {tracked_id: [] for tracked_id in tracked_ids}
    for tracked_id, event_type, created_at in rows:
        strips[tracked_id].append({
            "kind": DUTY_OUTCOMES[event_type],
            "title": f"{_fmt_dt(created_at)} — {EVENT_LABEL.get(event_type, event_type)}",
        })
    return strips


def _watch_state(stats: dict) -> dict[str, str]:
    if not stats["total"]:
        return {
            "tone": "disabled",
            "headline": "Вахта пустая",
            "note": "Добавьте первую раздачу — сервис начнёт сверять её хеш каждый день.",
        }
    if stats["errors"]:
        return {
            "tone": "error",
            "headline": f"Ошибок: {stats['errors']}",
            "note": "Откройте раздачу, чтобы увидеть причину и повторить проверку.",
        }
    if stats["updates"]:
        return {
            "tone": "update_available",
            "headline": f"Ждут решения: {stats['updates']}",
            "note": "Для этих раздач найдена новая версия. Примените обновление или откройте раздачу.",
        }
    return {
        "tone": "active",
        "headline": "Всё спокойно",
        "note": "Ни одна раздача не требует внимания.",
    }


def _bool(value: str | None) -> bool:
    return value in {"on", "true", "1", "yes"}


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if check_credentials(username, password):
        request.session["user"] = username
        return _redirect("/")
    return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный логин или пароль"}, status_code=401)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return _redirect("/login")


@router.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    # joinedload: иначе шаблон дотягивает клиента отдельным запросом на каждую строку.
    torrents = (
        db.query(TrackedTorrent)
        .options(joinedload(TrackedTorrent.qb_client))
        .order_by(desc(TrackedTorrent.created_at))
        .all()
    )
    stats = {
        "total": len(torrents),
        "active": sum(1 for item in torrents if item.status in {TorrentStatus.active.value, TorrentStatus.updated.value}),
        "updates": sum(1 for item in torrents if item.status == TorrentStatus.update_available.value),
        "errors": sum(1 for item in torrents if item.status == TorrentStatus.error.value),
        "last_check": db.query(func.max(TrackedTorrent.last_check_at)).scalar(),
    }
    qb_statuses = client_statuses(db)
    ok_clients = sum(1 for item in qb_statuses if item["status"] == "ok")
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "torrents": torrents,
            "stats": stats,
            "qb_statuses": qb_statuses,
            "ok_clients": ok_clients,
            "watch": _watch_state(stats),
            "duty": _duty_strips(db, [item.id for item in torrents]),
            "next_check": next_check_at(),
        },
    )


@router.get("/add")
def add_page(request: Request, db: Session = Depends(get_db)):
    # Понятия «основной» нет: подставляем первого по имени, выбор всё равно за пользователем.
    selected_client = get_fallback_qb_client(db)
    clients = list_qb_clients(db)
    categories, categories_error = client_categories(db, selected_client.id)
    return templates.TemplateResponse(
        "add.html",
        {
            "request": request,
            "settings": get_settings(),
            "error": None,
            "clients": clients,
            "selected_client_id": selected_client.id,
            "categories": categories,
            "categories_error": categories_error,
        },
    )


@router.post("/add")
def add(
    request: Request,
    qb_client_id: int | None = Form(None),
    source_url: str = Form(...),
    title: str = Form(""),
    save_path: str = Form(""),
    category: str = Form(""),
    tags: str = Form(""),
    update_mode: str = Form("new_files_only"),
    auto_update: str | None = Form(None),
    recheck_after_add: str | None = Form(None),
    start_after_recheck: str | None = Form(None),
    add_paused: str | None = Form(None),
    db: Session = Depends(get_db),
):
    payload = TorrentCreate(
        qb_client_id=qb_client_id,
        source_url=source_url,
        title=title,
        save_path=save_path,
        category=category,
        tags=tags,
        update_mode=update_mode,
        auto_update=_bool(auto_update),
        recheck_after_add=_bool(recheck_after_add),
        start_after_recheck=_bool(start_after_recheck),
        add_paused=_bool(add_paused),
    )
    try:
        tracked = create_initial_torrent(db, payload)
        return _redirect(f"/torrents/{tracked.id}")
    except Exception as exc:
        clients = list_qb_clients(db)
        categories, categories_error = client_categories(db, qb_client_id)
        return templates.TemplateResponse(
            "add.html",
            {
                "request": request,
                "settings": get_settings(),
                "error": str(exc),
                "clients": clients,
                "selected_client_id": qb_client_id,
                "categories": categories,
                "categories_error": categories_error,
            },
            status_code=400,
        )


@router.get("/torrents/{tracked_id}")
def detail(request: Request, tracked_id: int, db: Session = Depends(get_db)):
    tracked = db.get(TrackedTorrent, tracked_id)
    if not tracked:
        raise HTTPException(status_code=404)
    versions = db.query(TorrentVersion).filter(TorrentVersion.tracked_torrent_id == tracked_id).order_by(desc(TorrentVersion.detected_at)).all()
    events = db.query(CheckEvent).filter(CheckEvent.tracked_torrent_id == tracked_id).order_by(desc(CheckEvent.created_at)).limit(80).all()
    pending_version = latest_pending_version(db, tracked_id)
    categories, categories_error = client_categories(db, tracked.qb_client_id)
    return templates.TemplateResponse(
        "detail.html",
        {
            "request": request,
            "t": tracked,
            "versions": versions,
            "events": events,
            "pending_version": pending_version,
            "categories": categories,
            "categories_error": categories_error,
            "message": request.session.pop("message", None),
            "action_error": request.session.pop("action_error", None),
        },
    )


@router.post("/torrents/{tracked_id}/check")
def web_check(tracked_id: int, db: Session = Depends(get_db)):
    check_torrent(db, tracked_id)
    return _redirect(f"/torrents/{tracked_id}")


@router.post("/torrents/{tracked_id}/apply-update")
def web_apply(tracked_id: int, db: Session = Depends(get_db)):
    version = latest_pending_version(db, tracked_id)
    if version:
        apply_update(db, tracked_id, version.id)
    return _redirect(f"/torrents/{tracked_id}")


@router.post("/torrents/{tracked_id}/rollback/{version_id}")
def web_rollback(tracked_id: int, version_id: int, db: Session = Depends(get_db)):
    rollback_to_version(db, tracked_id, version_id)
    return _redirect(f"/torrents/{tracked_id}")


@router.post("/torrents/{tracked_id}/toggle-auto-update")
def toggle_auto(tracked_id: int, db: Session = Depends(get_db)):
    tracked = db.get(TrackedTorrent, tracked_id)
    if tracked:
        tracked.auto_update = not tracked.auto_update
        db.commit()
    return _redirect(f"/torrents/{tracked_id}")


@router.post("/torrents/{tracked_id}/category")
def change_category(
    request: Request,
    tracked_id: int,
    category: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        tracked = change_torrent_category(db, tracked_id, category)
        request.session["message"] = f"Категория изменена на «{tracked.category or 'без категории'}»."
    except Exception as exc:
        request.session["action_error"] = f"Не удалось изменить категорию: {exc}"
    return _redirect(f"/torrents/{tracked_id}")


@router.post("/torrents/{tracked_id}/disable")
def disable(tracked_id: int, db: Session = Depends(get_db)):
    tracked = db.get(TrackedTorrent, tracked_id)
    if tracked:
        tracked.status = TorrentStatus.disabled.value
        db.commit()
    return _redirect("/")


@router.post("/torrents/{tracked_id}/enable")
def enable(tracked_id: int, db: Session = Depends(get_db)):
    tracked = db.get(TrackedTorrent, tracked_id)
    if tracked:
        tracked.status = TorrentStatus.active.value
        db.commit()
    return _redirect(f"/torrents/{tracked_id}")


@router.post("/torrents/{tracked_id}/delete")
def delete(tracked_id: int, db: Session = Depends(get_db)):
    tracked = db.get(TrackedTorrent, tracked_id)
    if tracked:
        db.delete(tracked)
        db.commit()
    return _redirect("/")


def _settings_response(
    request: Request,
    db: Session,
    message: str | None = None,
    error: str | None = None,
    status_code: int = 200,
    refresh_clients: bool = False,
):
    saved = {item.key: item.value for item in db.query(AppSetting).all()}
    # Пароль в шаблон не уходит: там нужен только факт, что он задан.
    if saved.get("rutracker_password"):
        saved["rutracker_password"] = "***"
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": get_settings(),
            "saved": saved,
            "message": message,
            "error": error,
            "qb_clients": list_qb_clients(db),
            "qb_statuses": client_statuses(db, refresh=refresh_clients),
        },
        status_code=status_code,
    )


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    return _settings_response(request, db)


@router.post("/settings")
def save_settings(
    request: Request,
    rutracker_username: str = Form(""),
    rutracker_password: str = Form(""),
    rutracker_cookie: str = Form(""),
    flaresolver_address: str = Form(""),
    flaresolver_port: str = Form(""),
    db: Session = Depends(get_db),
):
    values = {
        "rutracker_username": rutracker_username,
        "rutracker_cookie": rutracker_cookie,
        "flaresolver_address": flaresolver_address,
        "flaresolver_port": flaresolver_port,
    }
    # Пустое поле пароля означает «оставить как есть»: он не показывается в форме,
    # иначе каждое сохранение настроек стирало бы его.
    if rutracker_password:
        values["rutracker_password"] = rutracker_password
    for key, value in values.items():
        db.merge(AppSetting(key=key, value=value))
    db.commit()
    return _settings_response(request, db, message="Настройки сохранены.")


@router.post("/settings/test-rutracker")
def web_test_rutracker(request: Request, db: Session = Depends(get_db)):
    """Проверка входа по кнопке: сразу видно, примет трекер логин или запросит капчу."""
    saved = {item.key: item.value for item in db.query(AppSetting).all()}
    settings = get_settings()
    endpoint = flaresolverr.endpoint_url(
        saved.get("flaresolver_address") or settings.flaresolver_address,
        saved.get("flaresolver_port") or str(settings.flaresolver_port),
    )
    try:
        rutracker_auth.refresh_cookie(
            endpoint,
            saved.get("rutracker_username") or settings.rutracker_username,
            saved.get("rutracker_password") or settings.rutracker_password,
            rutracker_auth.normalize_cookie(saved.get("rutracker_cookie") or ""),
            force=True,
        )
    except Exception as exc:
        return _settings_response(request, db, error=str(exc))
    return _settings_response(request, db, message="Вход выполнен, cookie сессии обновлён.")


@router.post("/settings/test-qbittorrent")
def web_test_qb(request: Request, db: Session = Depends(get_db)):
    """Кнопка стоит над списком всех клиентов — значит и проверять должна всех."""
    statuses = client_statuses(db, refresh=True)
    if not statuses:
        return _settings_response(request, db, error="Ни одного клиента qBittorrent не настроено.")
    offline = [item["name"] for item in statuses if item["status"] != "ok"]
    if offline:
        return _settings_response(request, db, error=f"Нет связи: {', '.join(offline)}. Подробности — в карточке клиента.")
    return _settings_response(request, db, message=f"Все клиенты на связи: {len(statuses)}.")


@router.post("/settings/qbittorrent")
def add_qb_client(
    request: Request,
    name: str = Form(...),
    host: str = Form(...),
    username: str = Form(""),
    password: str = Form(""),
    verify_tls: str | None = Form(None),
    timeout_seconds: int = Form(30),
    db: Session = Depends(get_db),
):
    try:
        create_qb_client(db, name, host, username, password, _bool(verify_tls), timeout_seconds)
        return _redirect("/settings")
    except Exception as exc:
        return _settings_response(request, db, error=str(exc), status_code=400)


@router.post("/settings/qbittorrent/{client_id}")
def edit_qb_client(
    client_id: int,
    name: str = Form(...),
    host: str = Form(...),
    username: str = Form(""),
    password: str = Form(""),
    verify_tls: str | None = Form(None),
    timeout_seconds: int = Form(30),
    db: Session = Depends(get_db),
):
    update_qb_client(db, client_id, name, host, username, password, _bool(verify_tls), timeout_seconds)
    return _redirect("/settings")


@router.get("/logs")
def logs(request: Request, event_type: str = "", tracked_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(CheckEvent)
    if event_type:
        query = query.filter(CheckEvent.event_type == event_type)
    if tracked_id:
        query = query.filter(CheckEvent.tracked_torrent_id == tracked_id)
    events = query.order_by(desc(CheckEvent.created_at)).limit(300).all()
    torrents = db.query(TrackedTorrent).order_by(TrackedTorrent.title).all()
    return templates.TemplateResponse("logs.html", {"request": request, "events": events, "torrents": torrents, "event_type": event_type, "tracked_id": tracked_id})
