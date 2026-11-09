"""배경음악 자동 선택: 작업 폴더의 '배경음악' 폴더에 넣어 둔 음원 중에서 영상 분위기에 맞는 곡을 고른다.

- 음원은 프로그램에 들어 있지 않다(저작권). 사용권이 확인된 곡만 사용자가 넣는다(유튜브 오디오 보관함 등).
- 하위 폴더 이름을 분위기로 쓴다: 배경음악/신나는/a.mp3, 배경음악/잔잔한/b.mp3 … (폴더 없이 넣어도 된다)
- Claude 기획이 곡 목록(파일 이름·분위기)을 보고 plan.bgm을 고르고, 키가 없거나 고르지 않으면 영상 종류로 고른다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

FOLDER = "배경음악"
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}
README = """여기에 배경음악을 넣으면 자동 편집이 영상 분위기에 맞는 곡을 골라 깝니다.

- 반드시 사용권이 확인된 음원만 넣으세요(유튜브 오디오 보관함, 구매한 라이선스 음원 등).
  회사 채널은 '상업적 이용 가능'인지 꼭 확인하세요. 인터넷에서 받은 유행곡은 넣지 마세요.
- 분위기별 폴더를 만들면 더 잘 고릅니다: 신나는 / 귀여운 / 잔잔한 / 감성 / 긴장 / 경쾌한
- 릴스·틱톡 인기 음원(트렌드 사운드)은 업로드할 때 앱에서 직접 고르는 편이 노출에 유리합니다.
  그때는 새 프로젝트에서 '배경음악 자동'을 끄세요.
"""
STYLE_MOODS = {
    "funny": ("신나", "귀여", "유쾌", "코믹", "경쾌", "funny", "happy", "cute", "upbeat", "comic"),
    "explainer": ("잔잔", "차분", "경쾌", "밝은", "calm", "chill", "light", "corporate"),
    "demo": ("경쾌", "밝은", "신나", "upbeat", "bright", "corporate"),
}


def library(ws: Path) -> Path:
    d = ws / FOLDER
    d.mkdir(parents=True, exist_ok=True)
    note = d / "여기에 음원을 넣으세요.txt"
    if not note.exists():
        note.write_text(README, encoding="utf-8")
    return d


def tracks(ws: Path) -> list[dict]:
    """[{file: 배경음악 폴더 기준 경로, mood: 상위 폴더 이름 또는 ''}] — 이름순."""
    root = library(ws)
    out = []
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.suffix.lower() in AUDIO_EXT and not f.name.startswith("."):
            rel = f.relative_to(root)
            out.append({"file": rel.as_posix(), "mood": rel.parts[0] if len(rel.parts) > 1 else ""})
    return out


def resolve(ws: Path, name: str) -> Path | None:
    """plan.bgm(배경음악 폴더 기준 경로) → 실제 파일. 폴더 밖·없는 파일이면 None."""
    if not name:
        return None
    root = (ws / FOLDER).resolve()
    p = (root / name).resolve()
    if root not in p.parents or not p.is_file():
        return None
    return p


def settle(plan, ws: Path, style: str, seed: str, *, from_llm: bool, enabled: bool = True) -> None:
    """plan.bgm 확정: 끄면 비우고, Claude가 고른 곡이 실제로 없으면(또는 규칙 기반이면) 영상 종류로 고른다.
    Claude가 일부러 비워 둔 경우(편집 요청으로 음악 빼기 등)는 그대로 둔다."""
    if not enabled:
        plan.bgm = ""
        return
    if plan.bgm and resolve(ws, plan.bgm):
        return
    if from_llm and not plan.bgm:
        return
    plan.bgm = pick(ws, style, seed)


def pick(ws: Path, style: str, seed: str = "") -> str:
    """키가 없을 때: 영상 종류에 맞는 분위기 폴더/파일 이름의 곡, 없으면 아무 곡. 같은 영상은 늘 같은 곡."""
    items = tracks(ws)
    if not items:
        return ""
    words = STYLE_MOODS.get(style, ())
    fit = [t for t in items if any(w in (t["mood"] + " " + t["file"]).lower() for w in words)] or items
    k = int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(fit)
    return fit[k]["file"]
