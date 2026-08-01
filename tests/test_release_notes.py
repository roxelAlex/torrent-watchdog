"""Заметки к релизу берутся из CHANGELOG, а не пишутся отдельно."""

import re
from pathlib import Path

import pytest

from scripts.release_notes import extract

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
VERSIONS = re.findall(r"^## (\S+)", CHANGELOG, re.M)

SAMPLE = """# Changelog

## 1.1.0

- вторая строка
- ещё одна

## 1.0.0

- самая первая
"""


def test_current_version_has_notes():
    """Иначе релиз выйдет с пустым описанием."""
    assert extract(CHANGELOG, VERSION), f"в CHANGELOG нет раздела {VERSION}"


@pytest.mark.parametrize("version", VERSIONS)
def test_every_version_has_notes(version):
    assert extract(CHANGELOG, version).strip()


def test_section_stops_at_the_next_version():
    notes = extract(SAMPLE, "1.1.0")
    assert "вторая строка" in notes and "ещё одна" in notes
    assert "самая первая" not in notes
    assert "## " not in notes


def test_last_section_reaches_the_end():
    assert extract(SAMPLE, "1.0.0").strip() == "- самая первая"


def test_unknown_version_gives_nothing():
    assert extract(SAMPLE, "9.9.9") == ""


def test_version_is_not_matched_as_a_prefix():
    """0.8.1 не должна выдавать раздел 0.8.10 и наоборот."""
    sample = "## 0.8.10\n\n- десятая\n\n## 0.8.1\n\n- первая\n"
    assert extract(sample, "0.8.1").strip() == "- первая"
    assert extract(sample, "0.8.10").strip() == "- десятая"


def test_russian_changelog_covers_the_same_versions():
    russian = (ROOT / "CHANGELOG.ru.md").read_text(encoding="utf-8")
    assert set(re.findall(r"^## (\S+)", russian, re.M)) == set(VERSIONS)
