#!/usr/bin/env python3
"""Достаёт раздел CHANGELOG для указанной версии.

Заметки к релизу должны быть теми же словами, что и в CHANGELOG: писать их
дважды — значит однажды разойтись. Скрипт берёт всё между `## <версия>` и
следующим заголовком того же уровня.

    python scripts/release_notes.py 0.8.4 [CHANGELOG.md]
"""

import re
import sys
from pathlib import Path


def extract(changelog: str, version: str) -> str:
    pattern = rf"^## {re.escape(version)}\s*$(.*?)(?=^## |\Z)"
    match = re.search(pattern, changelog, re.M | re.S)
    return match.group(1).strip() if match else ""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    version = sys.argv[1].lstrip("v")
    path = Path(sys.argv[2] if len(sys.argv) > 2 else "CHANGELOG.md")
    notes = extract(path.read_text(encoding="utf-8"), version)
    if not notes:
        print(f"в {path} нет раздела для версии {version}", file=sys.stderr)
        return 1
    print(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
