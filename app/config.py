from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "torrent-watchdog"
    app_version: str = "0.3.7"
    app_host: str = "0.0.0.0"
    app_port: int = 8096
    tz: str = "Asia/Yekaterinburg"

    database_url: str = "sqlite:////data/app.db"
    data_dir: Path = Path("/data")
    torrents_dir: Path = Path("/data/torrents")

    qb_host: str = "http://192.168.0.220:8090"
    qb_username: str = "admin"
    qb_password: str = "adminadmin"
    qb_verify_tls: bool = False
    qb_timeout_seconds: int = 30

    check_interval_hours: int = 24
    check_hour: int = 4
    check_minute: int = 0

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
    rutracker_user_agent: str = "Mozilla/5.0"
    rutracker_retry_delay_seconds: int = 10
    rutracker_max_attempts: int = 3
    flaresolver_address: str = ""
    flaresolver_port: int = 8191

    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.torrents_dir.mkdir(parents=True, exist_ok=True)
    return settings
