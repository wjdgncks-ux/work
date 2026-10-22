"""설치 점검(doctor)과 운영체제별 한글 글꼴 선택.

설치 후 '안 돼요'의 대부분은 ffmpeg 누락, 자막 필터(libass) 없는 ffmpeg, 한글 글꼴 없음, 디스크 부족이다.
시작 화면과 `python -m autoedit doctor`가 같은 점검을 보여 준다.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from . import settings

# 운영체제 기본 한글 글꼴(설치 없이 있는 것)
OS_FONTS = {"win32": "Malgun Gothic", "darwin": "Apple SD Gothic Neo"}
LINUX_FONTS = ("Noto Sans CJK KR", "NanumGothic", "Noto Sans KR", "UnDotum")


@dataclass
class Check:
    key: str
    name: str
    ok: bool
    required: bool
    detail: str
    fix: str = ""


@functools.lru_cache(maxsize=32)
def _fc_has(family: str) -> bool | None:
    """fontconfig로 글꼴 존재 확인. fc-list가 없으면 None(확인 불가)."""
    if not shutil.which("fc-list"):
        return None
    out = subprocess.run(["fc-list", f":family={family}", "family"], capture_output=True, text=True).stdout
    return bool(out.strip())


def resolve_font(name: str = "auto") -> str:
    """'auto'면 운영체제 기본 한글 글꼴. 지정 글꼴이 없다고 확인되면 기본 글꼴로 바꾼다."""
    if name and name != "auto" and _fc_has(name) is not False:
        return name
    if sys.platform in OS_FONTS:
        return OS_FONTS[sys.platform]
    for f in LINUX_FONTS:
        if _fc_has(f):
            return f
    return name if name and name != "auto" else LINUX_FONTS[0]


def _ffmpeg_info() -> tuple[bool, str, bool]:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return False, "", False
    ver = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True).stdout.split("\n")[0]
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return True, ver, " ass " in filters and " subtitles " in filters


def run_checks(workspace: Path | None = None) -> list[Check]:
    checks: list[Check] = []
    py = sys.version_info
    checks.append(Check("python", "Python 3.11 이상", py >= (3, 11), True, f"{py.major}.{py.minor}.{py.micro}",
                        "python.org에서 3.11 이상 설치"))
    has, ver, libass = _ffmpeg_info()
    install = ("winget install -e --id Gyan.FFmpeg" if sys.platform == "win32"
               else "brew install ffmpeg" if sys.platform == "darwin" else "sudo apt install ffmpeg")
    checks.append(Check("ffmpeg", "ffmpeg(영상 처리)", has, True, ver or "없음",
                        f"설치: {install} — 설치 후 프로그램을 다시 실행"))
    if has:
        checks.append(Check("libass", "자막 넣기 기능(libass)", libass, True,
                            "있음" if libass else "이 ffmpeg에는 자막 기능이 없음", f"전체 기능 ffmpeg로 다시 설치: {install}"))
    font = resolve_font("auto")
    found = _fc_has(font)
    checks.append(Check("font", "한글 글꼴", found is not False, True, font + ("" if found is not None else " (운영체제 기본)"),
                        "sudo apt install fonts-noto-cjk" if sys.platform.startswith("linux") else "운영체제 한글 글꼴 확인"))
    try:
        import faster_whisper  # noqa: F401
        whisper = True
    except ImportError:
        whisper = False
    checks.append(Check("whisper", "자동 캡션(음성 인식)", whisper, False,
                        "설치됨 — 첫 사용 때 모델을 내려받음(인터넷 필요)" if whisper else "없음",
                        "pip install faster-whisper"))
    checks.append(Check("elevenlabs", "AI 음성·내 목소리(ElevenLabs 키)", bool(settings.get_key("elevenlabs")), False,
                        "설정됨" if settings.get_key("elevenlabs") else "없음", "설정 화면에서 키 입력"))
    checks.append(Check("anthropic", "AI 편집 기획·대본(Claude 키)", bool(settings.get_key("anthropic")), False,
                        "설정됨" if settings.get_key("anthropic") else "없음 — 규칙 기반 편집으로 동작",
                        "설정 화면에서 키 입력"))
    ws = workspace or settings.workspace()
    try:
        ws.mkdir(parents=True, exist_ok=True)
        probe = ws / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        free = shutil.disk_usage(ws).free / 1024 ** 3
        checks.append(Check("disk", "작업 폴더 여유 공간", free >= 5, True, f"{ws} · {free:.0f}GB 남음",
                            "5GB 이상 비우거나 설정에서 작업 폴더를 다른 드라이브로"))
    except OSError as exc:
        checks.append(Check("disk", "작업 폴더 쓰기", False, True, f"{ws}: {exc}", "설정에서 작업 폴더 변경"))
    return checks


def as_dicts(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]


def main() -> int:
    checks = run_checks()
    for c in checks:
        mark = "✔" if c.ok else ("✖" if c.required else "–")
        print(f" {mark} {c.name}: {c.detail}" + ("" if c.ok else f"\n     → {c.fix}"))
    bad = [c for c in checks if c.required and not c.ok]
    print("\n필수 항목 모두 정상" if not bad else f"\n필수 항목 {len(bad)}개를 먼저 해결하세요")
    return 1 if bad else 0
