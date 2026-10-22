"""채널 프로필: 채널마다 다른 기본값(장르·플랫폼·CTA·해시태그·폰트·강조색·핸들·자료 폴더)을 묶어 둔다.

  python -m autoedit run a.mp4 --profile company
  → profiles/company.json 을 찾아 적용. 명령행 옵션이 프로필보다 우선한다.
프로필 안의 상대 경로(assets, bgm, sfx_dir, fonts_dir)는 프로필 파일 위치 기준.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
PATH_KEYS = ("assets", "bgm", "sfx_dir", "fonts_dir")


@dataclass
class Profile:
    key: str
    name: str = ""
    handle: str = ""
    style: str | None = None
    platforms: str | None = None
    brief: dict = field(default_factory=dict)
    hashtags: list[str] = field(default_factory=list)
    sponsored: bool = False
    render: dict = field(default_factory=dict)
    notes: str = ""


def find_profile(name: str) -> Path:
    cand = Path(name)
    if cand.suffix == ".json" and cand.exists():
        return cand
    for root in (Path.cwd(), PKG_ROOT):
        p = root / "profiles" / f"{name}.json"
        if p.exists():
            return p
    raise FileNotFoundError(f"프로필 '{name}'을 찾을 수 없습니다 (profiles/{name}.json)")


def load_profile(name: str) -> Profile:
    path = find_profile(name)
    data = json.loads(path.read_text(encoding="utf-8"))
    render = dict(data.get("render") or {})
    for k in PATH_KEYS:
        if render.get(k):
            render[k] = str((path.parent / render[k]).resolve())
    return Profile(
        key=path.stem, name=data.get("name", path.stem), handle=data.get("handle", ""),
        style=data.get("style"), platforms=data.get("platforms"), brief=data.get("brief") or {},
        hashtags=data.get("hashtags") or [], sponsored=bool(data.get("sponsored", False)),
        render=render, notes=data.get("notes", ""),
    )


def list_profiles() -> list[Path]:
    seen: dict[str, Path] = {}
    for root in (PKG_ROOT, Path.cwd()):
        for p in sorted((root / "profiles").glob("*.json")):
            seen[p.stem] = p
    return list(seen.values())
