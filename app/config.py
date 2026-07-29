from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "torrent-watchdog"
    app_version: str = "0.7.2"
    app_host: str = "0.0.0.0"
    app_port: int = 8096
    tz: str = "Asia/Yekaterinburg"
    # Язык по умолчанию; переключатель в шапке запоминает выбор в cookie.
    app_language: str = "ru"

    database_url: str = "sqlite:////data/app.db"
    data_dir: Path = Path("/data")
    torrents_dir: Path = Path("/data/torrents")

    qb_host: str = "http://192.168.0.220:8090"
    qb_username: str = "admin"
    qb_password: str = "adminadmin"
    qb_verify_tls: bool = False
    qb_timeout_seconds: int = 30
    # Проба доступности не должна стоить столько же, сколько рабочая операция:
    # её цена платится при каждом рендере страницы.
    qb_probe_timeout_seconds: int = 4
    qb_status_cache_seconds: int = 20
    qb_categories_cache_seconds: int = 120

    check_hour: int = 4
    check_minute: int = 0
    # Больше потоков — больше одновременных запросов к трекеру и FlareSolverr.
    check_max_workers: int = 3

    event_retention_days: int = 180
    torrent_file_retention_days: int = 30

    default_auto_update: bool = False
    default_recheck_after_add: bool = True
    default_start_after_recheck: bool = True
    default_add_paused: bool = True

    app_auth_enabled: bool = True
    app_auth_username: str = "admin"
    app_auth_password: str = "change_me"
    app_secret_key: str = "change_me_random_secret"

    rutracker_enabled: bool = True
    rutracker_cookie: str = ""
    rutracker_username: str = ""
    rutracker_password: str = ""
    # Защита от долбёжки трекера при неверном пароле.
    rutracker_login_min_interval_seconds: int = 300
    rutracker_user_agent: str = "Mozilla/5.0"
    rutracker_retry_delay_seconds: int = 10
    rutracker_max_attempts: int = 3
    flaresolver_address: str = ""
    flaresolver_port: int = 8191

    # Уведомления в Telegram. Обычно задаются на странице «Настройки».
    telegram_token: str = ""
    telegram_chat_id: str = ""
    # Язык уведомлений отдельный: их читает не обязательно тот, кто открывает интерфейс.
    notify_language: str = "ru"

    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.torrents_dir.mkdir(parents=True, exist_ok=True)
    return settings
