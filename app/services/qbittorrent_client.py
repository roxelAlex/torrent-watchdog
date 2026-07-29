from pathlib import Path
from typing import Any
import logging

import requests

from app.config import get_settings
from app.models import QbittorrentClientConfig
from app.utils import mask_url

logger = logging.getLogger(__name__)


class QBittorrentClient:
    def __init__(self, config: QbittorrentClientConfig | None = None) -> None:
        settings = get_settings()
        self.name = config.name if config else "Основной"
        self.base_url = (config.host if config else settings.qb_host).rstrip("/")
        self.username = config.username if config else settings.qb_username
        self.password = config.password if config else settings.qb_password
        self.verify_tls = config.verify_tls if config else settings.qb_verify_tls
        self.timeout = config.timeout_seconds if config else settings.qb_timeout_seconds
        # Проба не должна ждать рабочий таймаут: её цена платится при каждом рендере.
        self.probe_timeout = min(self.timeout, settings.qb_probe_timeout_seconds)
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def login(self, timeout: int | None = None) -> None:
        response = self.session.post(
            self._url("/api/v2/auth/login"),
            data={"username": self.username, "password": self.password},
            timeout=timeout or self.timeout,
            verify=self.verify_tls,
        )
        response.raise_for_status()
        if response.status_code == 204 and any(name.startswith("QBT_SID") for name in self.session.cookies.keys()):
            return
        if response.text.strip() != "Ok.":
            raise RuntimeError("qBittorrent отклонил логин")

    def test_connection(self) -> dict[str, str]:
        """Один запрос версии; логин только если клиент его потребовал."""
        try:
            response = self.session.get(self._url("/api/v2/app/version"), timeout=self.probe_timeout, verify=self.verify_tls)
            if response.status_code == 403:
                self.login(timeout=self.probe_timeout)
                response = self.session.get(self._url("/api/v2/app/version"), timeout=self.probe_timeout, verify=self.verify_tls)
            response.raise_for_status()
            return {"status": "ok", "host": mask_url(self.base_url), "version": response.text.strip()}
        except Exception as exc:
            return {"status": "unavailable", "host": mask_url(self.base_url), "error": str(exc)}

    def _post(self, path: str, data: dict[str, Any] | None = None, files: dict[str, Any] | None = None) -> requests.Response:
        response = self.session.post(self._url(path), data=data or {}, files=files, timeout=self.timeout, verify=self.verify_tls)
        if response.status_code == 403:
            self.login()
            response = self.session.post(self._url(path), data=data or {}, files=files, timeout=self.timeout, verify=self.verify_tls)
        response.raise_for_status()
        return response

    def _post_first_available(self, paths: list[str], data: dict[str, Any]) -> str:
        last_error: Exception | None = None
        for path in paths:
            try:
                self._post(path, data=data)
                return path
            except requests.HTTPError as exc:
                last_error = exc
                if exc.response is None or exc.response.status_code != 404:
                    raise
        if last_error:
            raise last_error
        raise RuntimeError("Не задан endpoint qBittorrent")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        response = self.session.get(self._url(path), params=params or {}, timeout=self.timeout, verify=self.verify_tls)
        if response.status_code == 403:
            self.login()
            response = self.session.get(self._url(path), params=params or {}, timeout=self.timeout, verify=self.verify_tls)
        response.raise_for_status()
        return response

    def get_torrent(self, torrent_hash: str) -> dict[str, Any] | None:
        torrents = self.get_torrent_list()
        needle = torrent_hash.lower()
        return next((item for item in torrents if item.get("hash", "").lower() == needle), None)

    def add_torrent_file(self, torrent_file_path: str, save_path: str, category: str = "", tags: str = "", paused: bool = True) -> str:
        path = Path(torrent_file_path)
        with path.open("rb") as torrent_file:
            self._post(
                "/api/v2/torrents/add",
                data={
                    "savepath": save_path,
                    "category": category,
                    "tags": tags,
                    "paused": "true" if paused else "false",
                },
                files={"torrents": (path.name, torrent_file, "application/x-bittorrent")},
            )
        return path.stem.lower()

    def add_magnet(self, magnet_url: str, save_path: str, category: str = "", tags: str = "", paused: bool = True) -> str:
        self._post(
            "/api/v2/torrents/add",
            data={
                "urls": magnet_url,
                "savepath": save_path,
                "category": category,
                "tags": tags,
                "paused": "true" if paused else "false",
            },
        )
        return magnet_url.lower().split("btih:")[-1].split("&")[0]

    def pause_torrent(self, torrent_hash: str) -> None:
        endpoint = self._post_first_available(["/api/v2/torrents/pause", "/api/v2/torrents/stop"], data={"hashes": torrent_hash})
        logger.info("qbittorrent host=%s action=pause endpoint=%s hash=%s result=ok", mask_url(self.base_url), endpoint, torrent_hash)

    def resume_torrent(self, torrent_hash: str) -> None:
        endpoint = self._post_first_available(["/api/v2/torrents/resume", "/api/v2/torrents/start"], data={"hashes": torrent_hash})
        logger.info("qbittorrent host=%s action=resume endpoint=%s hash=%s result=ok", mask_url(self.base_url), endpoint, torrent_hash)

    def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> None:
        # Safety invariant: default and all callers keep deleteFiles=false so payload data is never removed.
        self._post(
            "/api/v2/torrents/delete",
            data={"hashes": torrent_hash, "deleteFiles": "true" if delete_files else "false"},
        )
        logger.info("qbittorrent host=%s action=delete hash=%s deleteFiles=%s result=ok", mask_url(self.base_url), torrent_hash, delete_files)

    def recheck_torrent(self, torrent_hash: str) -> None:
        self._post("/api/v2/torrents/recheck", data={"hashes": torrent_hash})
        logger.info("qbittorrent host=%s action=recheck hash=%s result=ok", mask_url(self.base_url), torrent_hash)

    def set_category(self, torrent_hash: str, category: str) -> None:
        self._post("/api/v2/torrents/setCategory", data={"hashes": torrent_hash, "category": category})
        logger.info(
            "qbittorrent host=%s action=setCategory hash=%s result=ok",
            mask_url(self.base_url),
            torrent_hash,
        )

    def add_tags(self, torrent_hash: str, tags: str) -> None:
        if tags.strip():
            self._post("/api/v2/torrents/addTags", data={"hashes": torrent_hash, "tags": tags})

    def get_torrent_properties(self, torrent_hash: str) -> dict[str, Any]:
        return self._get("/api/v2/torrents/properties", params={"hash": torrent_hash}).json()

    def get_torrent_list(self) -> list[dict[str, Any]]:
        return self._get("/api/v2/torrents/info").json()

    def get_torrent_files(self, torrent_hash: str) -> list[dict[str, Any]]:
        return self._get("/api/v2/torrents/files", params={"hash": torrent_hash}).json()

    def set_file_priority(self, torrent_hash: str, file_ids: list[int], priority: int) -> None:
        if not file_ids:
            return
        for chunk_start in range(0, len(file_ids), 200):
            chunk = file_ids[chunk_start:chunk_start + 200]
            self._post(
                "/api/v2/torrents/filePrio",
                data={"hash": torrent_hash, "id": "|".join(str(item) for item in chunk), "priority": str(priority)},
            )
        logger.info("qbittorrent host=%s action=filePrio hash=%s files=%s priority=%s result=ok", mask_url(self.base_url), torrent_hash, len(file_ids), priority)

    def get_categories(self) -> list[dict[str, str]]:
        categories = self._get("/api/v2/torrents/categories").json()
        if not isinstance(categories, dict):
            return []
        return [
            {
                "name": name,
                "save_path": data.get("savePath", "") if isinstance(data, dict) else "",
            }
            for name, data in sorted(categories.items())
        ]
