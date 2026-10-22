"""운영체제 연동: 탐색기/Finder로 폴더 열기."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def reveal(path: Path) -> bool:
    """폴더(또는 파일이 든 폴더)를 파일 탐색기로 연다. 화면이 없는 환경이면 False."""
    target = path if path.is_dir() else path.parent
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # noqa: S606 — 로컬 폴더 열기
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, FileNotFoundError):
        return False
