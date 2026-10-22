"""음성 생성: AI 음성(기본 제공 목소리)과 인물 지정 음성(녹음으로 만든 목소리 복제).

엔진: ElevenLabs REST API(표준 라이브러리 urllib로 호출).
- 음성 합성: POST /v1/text-to-speech/{voice_id}/with-timestamps → 오디오 + 글자별 타이밍(자막 동기화용)
- 목소리 복제(Instant Voice Cloning): POST /v1/voices/add (multipart: name, files, remove_background_noise)
- 목소리 목록: GET /v1/voices, 삭제: DELETE /v1/voices/{voice_id}
경로·필드명은 공식 Python SDK(elevenlabs 2.x) 소스 기준으로 확인했다.

내 목소리 목록과 녹음 샘플은 사용자 홈(~/.autoedit/voices)에 저장한다(저장소·프로젝트 폴더에 남기지 않음).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import settings
from .media import ffmpeg

BASE = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_multilingual_v2"  # 한국어 포함 다국어 모델
SAMPLE_EXT = {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".mp4", ".mov", ".aac", ".flac"}


class VoiceError(RuntimeError):
    pass


# ---------------- 내 목소리 목록 ----------------
@dataclass
class VoiceProfile:
    id: str
    name: str
    voice: str                 # 'elevenlabs:<voice_id>'
    kind: str = "clone"        # clone(인물 지정) / stock(AI 기본 음성 즐겨찾기)
    samples: list[str] = field(default_factory=list)
    consent: str = ""          # 복제 동의 기록
    created: str = ""
    note: str = ""


class VoiceStore:
    def __init__(self, root: Path | None = None):
        self.root = root or settings.home() / "voices"
        (self.root / "samples").mkdir(parents=True, exist_ok=True)
        self.path = self.root / "voices.json"

    def list(self) -> list[VoiceProfile]:
        if not self.path.exists():
            return []
        return [VoiceProfile(**d) for d in json.loads(self.path.read_text(encoding="utf-8"))]

    def _write(self, items: list[VoiceProfile]) -> None:
        self.path.write_text(json.dumps([asdict(v) for v in items], ensure_ascii=False, indent=1), encoding="utf-8")

    def get(self, vid: str) -> VoiceProfile | None:
        return next((v for v in self.list() if v.id == vid), None)

    def add(self, profile: VoiceProfile) -> VoiceProfile:
        items = [v for v in self.list() if v.id != profile.id] + [profile]
        self._write(items)
        return profile

    def remove(self, vid: str) -> VoiceProfile | None:
        items = self.list()
        hit = next((v for v in items if v.id == vid), None)
        self._write([v for v in items if v.id != vid])
        return hit

    def save_sample(self, data: bytes, ext: str) -> tuple[Path, float]:
        """녹음/업로드 파일을 모노 44.1kHz mp3로 정리해 저장. (경로, 길이초)"""
        ext = ext.lower() if ext.startswith(".") else "." + ext.lower()
        if ext not in SAMPLE_EXT:
            raise VoiceError(f"음성 샘플로 쓸 수 없는 형식: {ext}")
        sid = uuid.uuid4().hex[:12]
        raw = self.root / "samples" / f"{sid}_raw{ext}"
        raw.write_bytes(data)
        out = self.root / "samples" / f"{sid}.mp3"
        try:
            ffmpeg("-i", str(raw), "-vn", "-ac", "1", "-ar", "44100", "-b:a", "128k", str(out))
        finally:
            raw.unlink(missing_ok=True)
        return out, round(probe_audio_duration(out), 2)


def probe_audio_duration(path: Path) -> float:
    import subprocess
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


# ---------------- ElevenLabs ----------------
class ElevenLabs:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 120):
        if not api_key:
            raise VoiceError("ElevenLabs API 키가 없습니다. 편집기 '음성' 탭의 설정 또는 ELEVENLABS_API_KEY 환경 변수로 입력하세요.")
        self.key, self.model, self.timeout = api_key, model, timeout

    def _request(self, method: str, path: str, *, params: dict | None = None, body: bytes | None = None,
                 content_type: str | None = None) -> dict:
        url = f"{BASE}/{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        req = urllib.request.Request(url, data=body, method=method, headers={"xi-api-key": self.key})
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            msg = {401: "API 키가 올바르지 않습니다", 402: "요금제 한도를 넘었습니다",
                   429: "요청이 너무 많습니다. 잠시 후 다시 시도하세요"}.get(exc.code, f"ElevenLabs 오류 {exc.code}")
            raise VoiceError(f"{msg}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise VoiceError(f"ElevenLabs 연결 실패: {exc.reason}") from exc
        return json.loads(raw) if raw else {}

    def stock_voices(self) -> list[dict]:
        data = self._request("GET", "v1/voices")
        return [{"voice": f"elevenlabs:{v['voice_id']}", "name": v.get("name", ""), "category": v.get("category", ""),
                 "labels": v.get("labels") or {}, "preview_url": v.get("preview_url") or ""}
                for v in data.get("voices", [])]

    def clone(self, name: str, files: list[Path], description: str = "", remove_noise: bool = True) -> str:
        boundary = "----autoedit" + uuid.uuid4().hex
        parts: list[bytes] = []
        for k, v in (("name", name), ("description", description),
                     ("remove_background_noise", "true" if remove_noise else "false")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for f in files:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{f.name}"\r\n'
                         f"Content-Type: audio/mpeg\r\n\r\n".encode() + f.read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        data = self._request("POST", "v1/voices/add", body=b"".join(parts),
                             content_type=f"multipart/form-data; boundary={boundary}")
        if not data.get("voice_id"):
            raise VoiceError(f"목소리 생성 응답에 voice_id가 없습니다: {data}")
        return data["voice_id"]

    def delete(self, voice_id: str) -> None:
        self._request("DELETE", f"v1/voices/{voice_id}")

    def tts(self, text: str, voice_id: str, speed: float | None = None) -> tuple[bytes, dict]:
        body: dict = {"text": text, "model_id": self.model}
        if speed:
            body["voice_settings"] = {"speed": speed}
        data = self._request("POST", f"v1/text-to-speech/{voice_id}/with-timestamps",
                             params={"output_format": "mp3_44100_128"},
                             body=json.dumps(body).encode(), content_type="application/json")
        audio = data.get("audio_base64")
        if not audio:
            raise VoiceError("음성 데이터가 비어 있습니다")
        return base64.b64decode(audio), data.get("alignment") or data.get("normalized_alignment") or {}


def get_engine():
    """설정된 음성 엔진. 테스트에서는 이 함수를 바꿔 끼운다."""
    return ElevenLabs(settings.get_key("elevenlabs"))


def engine_ready() -> bool:
    return bool(settings.get_key("elevenlabs"))


# ---------------- 합성(캐시) ----------------
def words_from_alignment(al: dict) -> list[dict]:
    """글자별 타이밍 → 띄어쓰기 단위 단어 타이밍."""
    chars = al.get("characters") or []
    starts = al.get("character_start_times_seconds") or []
    ends = al.get("character_end_times_seconds") or []
    words, buf, ws, we = [], "", None, None
    for ch, a, b in zip(chars, starts, ends):
        if ch.isspace():
            if buf:
                words.append({"start": round(ws, 3), "end": round(we, 3), "text": buf})
            buf, ws = "", None
            continue
        if ws is None:
            ws = a
        buf += ch
        we = b
    if buf:
        words.append({"start": round(ws, 3), "end": round(we, 3), "text": buf})
    return words


def synthesize(text: str, voice: str, out_dir: Path, speed: float | None = None, engine=None) -> dict:
    """텍스트 → 48kHz 스테레오 wav + 단어 타이밍. 같은 문장·목소리는 다시 생성하지 않는다(요금 절약)."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise VoiceError("읽을 문장이 비어 있습니다")
    provider, _, voice_id = voice.partition(":")
    if provider != "elevenlabs" or not voice_id:
        raise VoiceError(f"지원하지 않는 음성: {voice}")
    engine = engine or get_engine()
    key = hashlib.sha1(json.dumps([voice, getattr(engine, "model", ""), text, speed]).encode()).hexdigest()[:16]
    out_dir.mkdir(parents=True, exist_ok=True)
    wav, meta = out_dir / f"{key}.wav", out_dir / f"{key}.json"
    if wav.exists() and meta.exists():
        return json.loads(meta.read_text(encoding="utf-8"))
    audio, alignment = engine.tts(text, voice_id, speed)
    mp3 = out_dir / f"{key}.mp3"
    mp3.write_bytes(audio)
    ffmpeg("-i", str(mp3), "-ac", "2", "-ar", "48000", str(wav))
    mp3.unlink(missing_ok=True)
    info = {"file": wav.name, "duration": round(probe_audio_duration(wav), 3), "words": words_from_alignment(alignment)}
    meta.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return info


def create_clone(name: str, sample_paths: list[Path], consent: str, store: VoiceStore | None = None,
                 engine=None) -> VoiceProfile:
    if not consent:
        raise VoiceError("목소리 주인의 동의 확인이 필요합니다")
    if not sample_paths:
        raise VoiceError("녹음 샘플이 없습니다")
    store = store or VoiceStore()
    engine = engine or get_engine()
    voice_id = engine.clone(name, sample_paths, description="autoedit 인물 지정 음성")
    total = sum(probe_audio_duration(p) for p in sample_paths)
    return store.add(VoiceProfile(
        id=uuid.uuid4().hex[:10], name=name, voice=f"elevenlabs:{voice_id}", kind="clone",
        samples=[str(p) for p in sample_paths], consent=consent,
        created=time.strftime("%Y-%m-%d %H:%M"), note=f"샘플 {len(sample_paths)}개 · {total:.0f}초"))
