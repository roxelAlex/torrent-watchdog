import logging
import shutil
import tempfile
import time
from pathlib import Path

from bottle import HTTPError, request, response
from selenium.webdriver.common.by import By
from waitress import serve

from dtos import STATUS_OK, V1RequestBase
from flaresolverr import app
import flaresolverr_service
import utils

AUTH_COOKIES = ("bb_session", "bb_data", "bb_t")
CAPTCHA_MARKERS = ("cap_sid", "cap_code", "введите код")


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


def _has_captcha(page_source: str) -> bool:
    lowered = page_source.lower()
    return any(marker in lowered for marker in CAPTCHA_MARKERS)


def _submit_login_form(driver, username: str, password: str) -> None:
    """Заполняет и отправляет форму входа.

    На странице их две — компактная в шапке и основная ниже. Поля берём
    из одной формы, иначе браузер отправит половину полей от чужой.
    """
    password_inputs = driver.find_elements(By.NAME, "login_password")
    if not password_inputs:
        raise RuntimeError("на странице нет формы входа")
    form = password_inputs[-1].find_element(By.XPATH, "./ancestor::form")

    username_input = form.find_element(By.NAME, "login_username")
    password_input = form.find_element(By.NAME, "login_password")
    username_input.clear()
    username_input.send_keys(username)
    password_input.clear()
    password_input.send_keys(password)
    # Кнопка отправляется формой, поэтому значение «вход» уходит в нужной кодировке само.
    form.find_element(By.NAME, "login").click()


def _wait_for_auth_cookie(driver, timeout: int = 20) -> list[dict]:
    deadline = time.monotonic() + timeout
    while True:
        cookies = driver.get_cookies()
        if any(cookie.get("name") in AUTH_COOKIES for cookie in cookies):
            return cookies
        if time.monotonic() >= deadline:
            return cookies
        time.sleep(0.5)


@app.post("/login")
def login():
    """Вход на трекер логином и паролем; наружу отдаются только cookie."""
    data = request.json or {}
    username = data.get("username")
    password = data.get("password")
    login_url = data.get("login_url")
    cookies = data.get("cookies") or []
    if not isinstance(username, str) or not isinstance(password, str) or not isinstance(login_url, str):
        raise HTTPError(400, "username, password and login_url are required")
    if not username or not password:
        raise HTTPError(400, "username and password must not be empty")

    session_id = None
    try:
        session_result = flaresolverr_service.controller_v1_endpoint(V1RequestBase({"cmd": "sessions.create"}))
        session_id = session_result.session
        page_result = flaresolverr_service.controller_v1_endpoint(
            V1RequestBase({
                "cmd": "request.get",
                "url": login_url,
                "session": session_id,
                "maxTimeout": 60000,
                "cookies": cookies,
            })
        )
        if page_result.status != STATUS_OK:
            raise RuntimeError(page_result.message or "challenge was not solved")

        session, _ = flaresolverr_service.SESSIONS_STORAGE.get(session_id)
        driver = session.driver
        if _has_captcha(driver.page_source):
            return {"status": "captcha", "cookies": [], "message": "трекер запросил капчу на странице входа"}

        _submit_login_form(driver, username, password)
        result_cookies = _wait_for_auth_cookie(driver)
        if any(cookie.get("name") in AUTH_COOKIES for cookie in result_cookies):
            logging.info("rutracker login succeeded user=%s", username)
            return {"status": "ok", "cookies": result_cookies, "message": ""}

        if _has_captcha(driver.page_source):
            return {"status": "captcha", "cookies": [], "message": "трекер запросил капчу после отправки формы"}
        logging.warning("rutracker login rejected user=%s", username)
        return {"status": "rejected", "cookies": [], "message": "трекер не принял логин или пароль"}
    except HTTPError:
        raise
    except Exception as exc:
        logging.exception("browser login failed")
        raise HTTPError(502, str(exc)) from exc
    finally:
        if session_id:
            try:
                flaresolverr_service.controller_v1_endpoint(
                    V1RequestBase({"cmd": "sessions.destroy", "session": session_id})
                )
            except Exception:
                logging.warning("failed to close login browser session")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    utils.get_current_platform()
    flaresolverr_service.test_browser_installation()
    serve(app, host="0.0.0.0", port=8191, asyncore_use_poll=True)
