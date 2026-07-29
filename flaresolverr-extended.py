import logging
import shutil
import tempfile
import time
from pathlib import Path

from bottle import HTTPError, request, response
from waitress import serve

from dtos import STATUS_OK, V1RequestBase
from flaresolverr import app
import flaresolverr_service
import utils


def _wait_for_download(directory: Path, timeout: int = 30) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        files = [path for path in directory.iterdir() if not path.name.endswith(".crdownload")]
        if files:
            return files[0]
        time.sleep(0.25)
    raise RuntimeError("браузер не сохранил .torrent за отведённое время")


@app.post("/download")
def download():
    data = request.json or {}
    source_url = data.get("source_url")
    download_url = data.get("download_url")
    cookies = data.get("cookies") or []
    if not isinstance(source_url, str) or not isinstance(download_url, str) or not isinstance(cookies, list):
        raise HTTPError(400, "source_url, download_url and cookies are required")

    session_id = None
    directory = Path(tempfile.mkdtemp(prefix="rutracker-download-"))
    try:
        session_result = flaresolverr_service.controller_v1_endpoint(V1RequestBase({"cmd": "sessions.create"}))
        session_id = session_result.session
        source_result = flaresolverr_service.controller_v1_endpoint(
            V1RequestBase(
                {
                    "cmd": "request.get",
                    "url": source_url,
                    "session": session_id,
                    "maxTimeout": 60000,
                    "cookies": cookies,
                }
            )
        )
        if source_result.status != STATUS_OK:
            raise RuntimeError(source_result.message or "challenge was not solved")

        session, _ = flaresolverr_service.SESSIONS_STORAGE.get(session_id)
        session.driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(directory)},
        )
        session.driver.get(download_url)
        content = _wait_for_download(directory).read_bytes()
        if not content.startswith(b"d"):
            raise RuntimeError("вместо .torrent браузер сохранил неожиданный ответ")
        response.content_type = "application/x-bittorrent"
        return content
    except Exception as exc:
        logging.exception("browser download failed")
        raise HTTPError(502, str(exc)) from exc
    finally:
        if session_id:
            try:
                flaresolverr_service.controller_v1_endpoint(
                    V1RequestBase({"cmd": "sessions.destroy", "session": session_id})
                )
            except Exception:
                logging.warning("failed to close download browser session")
        shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    utils.get_current_platform()
    flaresolverr_service.test_browser_installation()
    serve(app, host="0.0.0.0", port=8191, asyncore_use_poll=True)
