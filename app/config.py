from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_version() -> str:
    """Версия берётся из файла VERSION, который лежит в образе.

    Полем настроек она быть не может: тогда её задаёт .env, а его копируют
    один раз и больше не трогают. После обновления контейнера подвал страницы
    и /health показывали бы старый номер при новом коде.
    """
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


APP_VERSION = _read_version()


class Settings(BaseSettings):
    app_name: str = "torrent-watchdog"
    app_host: str = "0.0.0.0"
    app_port: int = 8096
    tz: str = "UTC"
    # Язык по умолчанию; переключатель в шапке запоминает выбор в cookie.
    app_language: str = "ru"

    database_url: str = "sqlite:////data/app.db"
    data_dir: Path = Path("/data")
    torrents_dir: Path = Path("/data/torrents")

    qb_host: str = "http://192.168.1.10:8080"
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
    # По умолчанию выключено: чужая установка после обновления образа не должна
    # внезапно начать удалять данные.
    default_delete_replaced_files: bool = False

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
    # Cloudflare отказывает не разово, а окнами в десятки минут: три попытки
    # подряд укладывались в три минуты и сгорали внутри одного такого окна.
    # Повтор через полчаса ловит момент, когда трекер снова пускает.
    rutracker_retry_delay_seconds: int = 1800
    rutracker_max_attempts: int = 5
    flaresolver_address: str = ""
    flaresolver_port: int = 8191

    # Уведомления в Telegram. Обычно задаются на странице «Настройки».
    telegram_token: str = ""
    telegram_chat_id: str = ""
    # Язык уведомлений отдельный: их читает не обязательно тот, кто открывает интерфейс.
    notify_language: str = "ru"

    log_level: str = "INFO"

    @property
    def app_version(self) -> str:
        """Не поле, а свойство: переменной окружения её не подменить."""
        return APP_VERSION

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.torrents_dir.mkdir(parents=True, exist_ok=True)
    return settings
