"""사용자 설정(API 키 등). 프로젝트 폴더가 아니라 사용자 홈(~/.autoedit)에 저장해 저장소에 올라가지 않게 한다.
AUTOEDIT_HOME 환경 변수로 위치를 바꿀 수 있다. 환경 변수(ELEVENLABS_API_KEY 등)가 있으면 그쪽이 우선."""

from __future__ import annotations

import json
import os
from pathlib import Path

ENV_KEYS = {"elevenlabs": "ELEVENLABS_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def home() -> Path:
    h = Path(os.environ.get("AUTOEDIT_HOME") or Path.home() / ".autoedit")
    h.mkdir(parents=True, exist_ok=True)
    return h


def _path() -> Path:
    return home() / "config.json"


def load() -> dict:
    p = _path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save(data: dict) -> None:
    p = _path()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        p.chmod(0o600)  # 키 파일은 본인만 읽기
    except OSError:
        pass


def get_key(name: str) -> str:
    return os.environ.get(ENV_KEYS[name], "") or load().get(f"{name}_api_key", "")


def set_key(name: str, value: str) -> None:
    data = load()
    data[f"{name}_api_key"] = value.strip()
    save(data)


def workspace() -> Path:
    """프로젝트가 쌓이는 작업 폴더. 기본: 문서/숏폼편집(없으면 홈/숏폼편집)."""
    custom = load().get("workspace")
    if custom:
        return Path(custom).expanduser()
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / "숏폼편집"


def voice_engine() -> str:
    """음성 엔진: voicestudio(기본, 내 PC 무료 앱) / elevenlabs(예비, 유료 키)."""
    return load().get("voice_engine") or "voicestudio"


def get(name: str, default=None):
    return load().get(name, default)


def put(**values) -> None:
    data = load()
    data.update({k: v for k, v in values.items() if v is not None})
    save(data)


def export_keys_to_env() -> None:
    """설정 파일의 Claude 키를 환경 변수로 올려 anthropic SDK가 읽게 한다(환경 변수가 이미 있으면 그대로)."""
    key = load().get("anthropic_api_key", "")
    if key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = key
