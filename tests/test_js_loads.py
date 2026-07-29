"""Скрипт должен переживать загрузку страницы.

Обращение к const до объявления роняло весь файл: на странице добавления
переставали работать и выбор своей категории, и подсказки путей, и
подтверждения удаления. `node --check` такое не ловит — он про синтаксис.
Тест запускается, только если рядом есть node.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path("app/static/app.js").resolve()
SMOKE = Path("tests/js/smoke.js").resolve()

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="нужен node")


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", *args], capture_output=True, text=True)


def test_syntax_is_valid():
    result = run("--check", str(SCRIPT))
    assert result.returncode == 0, result.stderr


def test_script_survives_page_load():
    result = run(str(SMOKE), str(SCRIPT))
    assert result.returncode == 0, result.stderr.strip()
    assert result.stdout.strip() == "ok"
