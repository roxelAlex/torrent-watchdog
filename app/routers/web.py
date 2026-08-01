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
    client_paths,
    client_statuses,
    create_qb_client,
    get_fallback_qb_client,
    get_qb_client_config,
    category_save_path,
    list_qb_clients,
    path_suggestions,
    update_qb_client,
    with_effective_paths,
)
from app import i18n
from app.errors import localize
from app.services import flaresolverr, messages, notifier, rutracker_auth
from app.services.torrent_settings import change_torrent_category
from app.services.update_applier import apply_update, rollback_to_version
from app.services.update_checker import check_torrent, create_initial_torrent, latest_pending_version
from app.services.torrent_diff import diff_from_json

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["app_version"] = get_settings().app_version
templates.env.globals["app_name"] = get_settings().app_name

LANGUAGE_COOKIE = "lang"
DUTY_OUTCOMES = {
    "no_changes": "ok",
    "update_applied": "applied",
    "update_found": "found",
    "update_failed": "bad",
    "error": "bad",
    "qbittorrent_unavailable": "bad",
}


def current_language(request: Request) -> str:
    return i18n.normalize(request.cookies.get(LANGUAGE_COOKIE) or get_settings().app_language)


def _fmt_dt(value, language: str) -> str:
    if not value:
        return i18n.translate("common.none", language)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(get_settings().tz)).strftime(i18n.date_format(language))


def _version_summary(version: TorrentVersion | None, update_mode: str, language: str) -> str:
    """Итог зависит от режима: обещать «не будет перекачиваться» можно только для new_files_only."""
    if not version:
        return ""
    diff = diff_from_json(version.changelog_text)
    new_count = len(diff.get("new", []))
    existing_count = len(diff.get("existing", []))
    if update_mode != "new_files_only":
        if new_count:
            return i18n.translate("summary.full", language, new=new_count, existing=existing_count)
        return i18n.translate("summary.full.no_new", language)
    if not diff or diff.get("mode") == "unknown":
        return i18n.translate("summary.no_comparison", language)
    if new_count and not existing_count:
        # Совпадений нет: обещать, что что-то не будет перекачано, нельзя.
        return i18n.translate("summary.nothing_in_common", language, new=new_count,
                              removed=len(diff.get("removed", [])))
    if new_count:
        return i18n.translate("summary.new_files_only", language, new=new_count, existing=existing_count)
    return i18n.translate("summary.new_files_only.no_new", language)


def render(request: Request, template: str, context: dict, status_code: int = 200):
    """Единственная точка рендера: язык подставляется здесь, а не в каждом роуте."""
    language = current_language(request)

    def translate(key: str, **params):
        return i18n.translate(key, language, **params)

    return templates.TemplateResponse(
        template,
        {
            "request": request,
            "lang": language,
            "languages": i18n.language_options(),
            "language_flag": i18n.flag(language),
            "t": translate,
            "dt": lambda value: _fmt_dt(value, language),
            "status_label": lambda status: i18n.translate(f"status.{status}", language),
            "event_label": lambda event_type: i18n.translate(f"event.{event_type}", language),
            "event_text": lambda item: messages.render_event(item, language),
            "torrent_error": lambda tracked: messages.render_torrent_error(tracked, language),
            "version_summary": lambda version, mode: _version_summary(version, mode, language),
            "update_mode_label": lambda mode: i18n.translate(f"mode.{mode}", language),
            **context,
        },
        status_code=status_code,
    )


def event_type_options(language: str) -> list[dict[str, str]]:
    return [{"value": code, "label": i18n.translate(f"event.{code}", language)} for code in DUTY_OUTCOMES_ORDER]


DUTY_OUTCOMES_ORDER = (
    "check_started",
    "no_changes",
    "update_found",
    "update_applied",
    "update_failed",
    "manual_action",
    "error",
    "qbittorrent_unavailable",
)


def _duty_strips(db: Session, tracked_ids: list[int], language: str, limit: int = 14) -> dict[int, list[dict[str, str]]]:
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
            "title": f"{_fmt_dt(created_at, language)} — {i18n.translate(f'event.{event_type}', language)}",
        })
    return strips


CUSTOM_CATEGORY = "__custom__"


def _chosen_category(choice: str, custom: str) -> str:
    """Список и поле «своя категория» приходят раздельно, наружу нужно одно значение."""
    return custom.strip() if choice.strip() == CUSTOM_CATEGORY else choice.strip()


def _chosen_category_path(choice: str, custom_path: str) -> str:
    """Свой путь имеет смысл только для новой категории: у существующей он уже есть."""
    return custom_path.strip() if choice.strip() == CUSTOM_CATEGORY else ""


def _category_context(db: Session, client_id: int | None, current: str) -> dict:
    """Всё, что нужно шаблону для списка категорий и подсказок путей."""
    categories, categories_error = client_categories(db, client_id)
    paths = client_paths(db, client_id)
    default_save_path = str(paths.get("default_save_path") or "")
    known = [item.get("name") for item in categories]
    return {
        "categories": with_effective_paths(categories, default_save_path),
        "categories_error": categories_error,
        "client_paths": paths,
        "path_suggestions": path_suggestions(categories, default_save_path),
        # Категория раздачи могла исчезнуть из клиента — не терять её молча.
        "orphan_category": current if current and current not in known else "",
    }


def _effective_save_path(tracked: TrackedTorrent, categories: list[dict], client_default: str = "") -> dict[str, str]:
    """Куда на самом деле ляжет раздача.

    Пустой save_path не значит «никуда»: qBittorrent берёт путь категории,
    а если и у неё пусто — свой стандартный. Показывать в этом случае прочерк
    было бы неправдой: файлы-то где-то лежат.
    """
    if tracked.save_path:
        return {"path": tracked.save_path, "source": "explicit"}
    if not tracked.category:
        return {"path": client_default, "source": "client"}
    resolved = category_save_path(tracked.category, categories, client_default)
    if resolved:
        return {"path": resolved, "source": "category"}
    # Категории нет в клиенте — путь не выдумываем.
    return {"path": "", "source": "unknown"}


def _watch_state(stats: dict, language: str) -> dict[str, str]:
    if not stats["total"]:
        tone, key, params = "disabled", "empty", {}
    elif stats["errors"]:
        tone, key, params = "error", "errors", {"count": stats["errors"]}
    elif stats["updates"]:
        tone, key, params = "update_available", "updates", {"count": stats["updates"]}
    else:
        tone, key, params = "active", "calm", {}
    return {
        "tone": tone,
        "headline": i18n.translate(f"watch.{key}.headline", language, **params),
        "note": i18n.translate(f"watch.{key}.note", language),
    }


def _bool(value: str | None) -> bool:
    return value in {"on", "true", "1", "yes"}


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


@router.get("/lang/{code}")
def switch_language(code: str, request: Request, next: str = "/"):
    """Выбор языка живёт в cookie: он не должен зависеть от того, кто вошёл."""
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = _redirect(target)
    response.set_cookie(
        LANGUAGE_COOKIE,
        i18n.normalize(code),
        max_age=60 * 60 * 24 * 365,
        samesite="lax",
        httponly=False,
    )
    return response


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html", {"error": None})


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if check_credentials(username, password):
        request.session["user"] = username
        return _redirect("/")
    return render(request, "login.html", {"error": i18n.translate("login.failed", current_language(request))}, status_code=401)


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
    language = current_language(request)
    return render(request, "index.html", {
        "torrents": torrents,
        "stats": stats,
        "qb_statuses": qb_statuses,
        "ok_clients": ok_clients,
        "watch": _watch_state(stats, language),
        "duty": _duty_strips(db, [item.id for item in torrents], language),
        "next_check": next_check_at(),
    })


@router.get("/add")
def add_page(request: Request, db: Session = Depends(get_db)):
    # Понятия «основной» нет: подставляем первого по имени, выбор всё равно за пользователем.
    selected_client = get_fallback_qb_client(db)
    clients = list_qb_clients(db)
    return render(request, "add.html", {
        "settings": get_settings(),
        "error": None,
        "clients": clients,
        "selected_client_id": selected_client.id,
        **_category_context(db, selected_client.id, ""),
    })


@router.post("/add")
def add(
    request: Request,
    qb_client_id: int | None = Form(None),
    source_url: str = Form(...),
    title: str = Form(""),
    save_path: str = Form(""),
    category: str = Form(""),
    category_custom: str = Form(""),
    category_custom_path: str = Form(""),
    tags: str = Form(""),
    update_mode: str = Form("new_files_only"),
    auto_update: str | None = Form(None),
    recheck_after_add: str | None = Form(None),
    start_after_recheck: str | None = Form(None),
    add_paused: str | None = Form(None),
    db: Session = Depends(get_db),
):
    category_save_path_value = _chosen_category_path(category, category_custom_path)
    category = _chosen_category(category, category_custom)
    payload = TorrentCreate(
        qb_client_id=qb_client_id,
        source_url=source_url,
        title=title,
        save_path=save_path,
        category=category,
        category_save_path=category_save_path_value,
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
        return render(request, "add.html", {
            "settings": get_settings(),
            "error": localize(exc, current_language(request)),
            "clients": clients,
            "selected_client_id": qb_client_id,
            **_category_context(db, qb_client_id, category),
        }, status_code=400)


@router.get("/torrents/{tracked_id}")
def detail(request: Request, tracked_id: int, db: Session = Depends(get_db)):
    tracked = db.get(TrackedTorrent, tracked_id)
    if not tracked:
        raise HTTPException(status_code=404)
    versions = db.query(TorrentVersion).filter(TorrentVersion.tracked_torrent_id == tracked_id).order_by(desc(TorrentVersion.detected_at)).all()
    events = db.query(CheckEvent).filter(CheckEvent.tracked_torrent_id == tracked_id).order_by(desc(CheckEvent.created_at)).limit(80).all()
    pending_version = latest_pending_version(db, tracked_id)
    context = _category_context(db, tracked.qb_client_id, tracked.category)
    return render(request, "detail.html", {
        "torrent": tracked,
        "save_path": _effective_save_path(
            tracked, context["categories"], str(context["client_paths"].get("default_save_path") or ""),
        ),
        "versions": versions,
        "events": events,
        "pending_version": pending_version,
        **context,
        "message": request.session.pop("message", None),
        "action_error": request.session.pop("action_error", None),
    })


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
    category_custom: str = Form(""),
    category_custom_path: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        language = current_language(request)
        tracked = change_torrent_category(
            db, tracked_id,
            _chosen_category(category, category_custom),
            _chosen_category_path(category, category_custom_path),
        )
        request.session["message"] = i18n.translate(
            "detail.category_saved", language,
            category=tracked.category or i18n.translate("category.unset", language),
        )
    except Exception as exc:
        request.session["action_error"] = i18n.translate(
            "detail.category_error", current_language(request), error=localize(exc, current_language(request)),
        )
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
    if saved.get("telegram_token"):
        saved["telegram_token"] = "***"
    return render(request, "settings.html", {
        "settings": get_settings(),
        "saved": saved,
        "message": message,
        "error": error,
        "qb_clients": list_qb_clients(db),
        "qb_statuses": client_statuses(db, refresh=refresh_clients),
        "notify_events": notifier.parse_events(saved.get("notify_events")),
        "notify_available_events": notifier.AVAILABLE_EVENTS,
    }, status_code=status_code)


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
    return _settings_response(request, db, message=i18n.translate("settings.saved", current_language(request)))


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
        return _settings_response(request, db, error=localize(exc, current_language(request)))
    return _settings_response(request, db, message=i18n.translate("settings.login_ok", current_language(request)))


@router.post("/settings/test-qbittorrent")
def web_test_qb(request: Request, db: Session = Depends(get_db)):
    """Кнопка стоит над списком всех клиентов — значит и проверять должна всех."""
    statuses = client_statuses(db, refresh=True)
    if not statuses:
        return _settings_response(request, db, error=i18n.translate("settings.qb_none", current_language(request)))
    offline = [item["name"] for item in statuses if item["status"] != "ok"]
    if offline:
        return _settings_response(request, db, error=i18n.translate("settings.qb_some_offline", current_language(request), names=", ".join(offline)))
    return _settings_response(request, db, message=i18n.translate("settings.qb_all_online", current_language(request), count=len(statuses)))


@router.post("/settings/notifications")
def save_notifications(
    request: Request,
    telegram_token: str = Form(""),
    telegram_chat_id: str = Form(""),
    notify_language: str = Form(""),
    notify_events: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    """Своя форма — свой роут: общий затирал бы поля, которых в ней нет."""
    values = {
        "telegram_chat_id": telegram_chat_id,
        "notify_language": i18n.normalize(notify_language),
        # Пустой список — это «ничего не слать», а не «не заполнено»,
        # поэтому пишем пустую строку, а не пропускаем ключ.
        "notify_events": ",".join(event for event in notify_events if event in notifier.AVAILABLE_EVENTS),
    }
    # Токен, как и пароль, в форме не показывается: пустое поле означает «не менять».
    if telegram_token:
        values["telegram_token"] = telegram_token
    for key, value in values.items():
        db.merge(AppSetting(key=key, value=value))
    db.commit()
    return _settings_response(request, db, message=i18n.translate("settings.saved", current_language(request)))


@router.post("/settings/test-telegram")
def web_test_telegram(request: Request, db: Session = Depends(get_db)):
    language = current_language(request)
    try:
        settings = notifier.load_settings()
        notifier.send(i18n.translate("notify.test_text", settings.language or language), settings)
    except Exception as exc:
        return _settings_response(request, db, error=localize(exc, language))
    return _settings_response(request, db, message=i18n.translate("notify.test_sent", language))


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
        return _settings_response(request, db, error=localize(exc, current_language(request)), status_code=400)


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
    return render(request, "logs.html", {
        "events": events,
        "torrents": torrents,
        "event_type": event_type,
        "tracked_id": tracked_id,
        "event_types": event_type_options(current_language(request)),
    })
