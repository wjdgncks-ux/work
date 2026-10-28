"""채널 프로필: 채널마다 다른 기본값(장르·플랫폼·CTA·해시태그·폰트·강조색·핸들·자료 폴더)을 묶어 둔다.

  python -m autoedit run a.mp4 --profile company
  → profiles/company.json 을 찾아 적용. 명령행 옵션이 프로필보다 우선한다.
프로필 안의 상대 경로(assets, bgm, sfx_dir, fonts_dir)는 프로필 파일 위치 기준.

시작 화면에서 고친·추가한·지운 채널은 사용자 폴더(~/.autoedit/profiles)에 저장한다. 프로그램 폴더의 기본 채널보다
우선하고, 업데이트로 프로그램 파일이 바뀌어도 그대로 남는다. 지운 기본 채널은 {"deleted": true} 표시 파일로 숨긴다.
채널 key는 작업 폴더의 채널 폴더 이름이라 이름을 바꿔도 key는 바뀌지 않는다(기존 프로젝트 경로 유지).
"""

from __future__ import annotations

import json
import re
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


def user_dir() -> Path:
    from . import settings
    return settings.home() / "profiles"


def _roots() -> list[Path]:
    """뒤에 올수록 우선: 프로그램 기본 → 실행 폴더 → 사용자 폴더."""
    return [PKG_ROOT / "profiles", Path.cwd() / "profiles", user_dir()]


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def find_profile(name: str) -> Path:
    cand = Path(name)
    if cand.suffix == ".json" and cand.exists():
        return cand
    for root in reversed(_roots()):
        p = root / f"{name}.json"
        if p.exists():
            if _read(p).get("deleted"):
                break
            return p
    raise FileNotFoundError(f"채널 '{name}'을 찾을 수 없습니다 (profiles/{name}.json)")


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


BUILTIN_ORDER = ("company", "me", "wife")


def list_profiles() -> list[Path]:
    seen: dict[str, Path] = {}
    for root in _roots():
        for p in sorted(root.glob("*.json")):
            seen[p.stem] = p
    live = [p for p in seen.values() if not _read(p).get("deleted")]

    def order(p: Path):
        d = _read(p)
        return (d.get("order", BUILTIN_ORDER.index(p.stem) if p.stem in BUILTIN_ORDER else 100), d.get("created", ""), p.stem)
    return sorted(live, key=order)


# ---------------- 시작 화면에서 채널 편집 ----------------
EDITABLE = ("name", "handle", "style", "platforms", "hashtags")
STYLES_OK = ("explainer", "funny", "demo")


def _key_for(name: str, taken: set[str]) -> str:
    """채널 폴더 이름: 이름에서 파일 이름에 못 쓰는 글자를 빼서 만든다(한글 그대로)."""
    base = re.sub(r'[\\/:*?"<>|.\s]+', "_", name).strip("_")[:30] or "channel"
    key, k = base, 2
    while key in taken:
        key, k = f"{base}_{k}", k + 1
    return key


def save_profile(data: dict, key: str | None = None) -> str:
    """채널 추가(key 없음) 또는 수정. 이름·핸들·기본 영상 종류·올릴 곳·해시태그·CTA만 바꾸고 나머지(자막 색·자료 폴더 등)는 유지."""
    import time
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 30:
        raise ValueError("채널 이름을 1~30자로 적어 주세요")
    current = {p.stem: p for p in list_profiles()}
    if any(load_profile(str(p)).name == name and k != key for k, p in current.items()):
        raise ValueError(f"'{name}' 채널이 이미 있습니다")
    if key:
        if key not in current:
            raise ValueError("없는 채널입니다")
        base = _read(current[key])
        for k in PATH_KEYS:  # 프로그램 폴더 기준 상대 경로를 절대 경로로 옮겨 적는다
            v = (base.get("render") or {}).get(k)
            if v and not Path(v).is_absolute():
                base["render"][k] = str((current[key].parent / v).resolve())
    else:
        all_keys = {p.stem for r in _roots() for p in r.glob("*.json")}
        key = _key_for(name, all_keys)
        base = {"brief": {"industry": "general", "goal": "reach"}, "render": {"font": "auto"},
                "created": time.strftime("%Y-%m-%d %H:%M:%S"), "order": 200}
    style = data.get("style", base.get("style"))
    if style not in STYLES_OK:
        raise ValueError("영상 종류를 골라 주세요")
    platforms = data.get("platforms", base.get("platforms") or "reels")
    if isinstance(platforms, list):
        platforms = ",".join(platforms)
    if not platforms:
        raise ValueError("올릴 곳을 하나 이상 고르세요")
    tags = data.get("hashtags", base.get("hashtags") or [])
    if isinstance(tags, str):
        tags = [("#" + t.lstrip("#")) for t in re.split(r"[\s,]+", tags) if t.strip("#")]
    base.update(name=name, handle=str(data.get("handle", base.get("handle", "")) or "").strip(), style=style,
                platforms=platforms, hashtags=tags[:30])
    base.pop("deleted", None)
    if "cta" in data:
        base.setdefault("brief", {})["cta"] = str(data["cta"] or "").strip()
    d = user_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    return key


def delete_profile(key: str) -> None:
    """채널을 목록에서 뺀다. 그 채널의 프로젝트·완성본은 지우지 않는다."""
    if key not in {p.stem for p in list_profiles()}:
        raise ValueError("없는 채널입니다")
    d = user_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(json.dumps({"deleted": True}), encoding="utf-8")
