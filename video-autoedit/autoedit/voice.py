"""음성 생성: AI 음성(기본 제공 목소리)과 인물 지정 음성(녹음으로 만든 목소리 복제).

기본 엔진: VoiceStudio(github.com/debpalash/VoiceStudio, AGPL-3.0). 내 PC에서 도는 앱이고 키·요금이 없다.
- 이 PC 안(127.0.0.1:3900)에서만 통신하며 인증이 없다(loopback 요청은 VoiceStudio가 검사하지 않음).
- 음성 합성: POST /v1/audio/speech {model: 엔진 id, input, voice: 목소리 id, response_format: wav, speed, language}
- 단어 타이밍: POST /v1/audio/transcriptions (verbose_json, timestamp_granularities[]=word) — 합성 결과를 다시 인식
- 목소리 복제: POST /profiles (multipart: name, ref_audio, kind=clone), 목록 GET /profiles, 삭제 DELETE /profiles/{id}
경로·필드명은 VoiceStudio 소스(backend/api/routers/openai_compat.py, profiles.py) 기준으로 확인했다.
엔진(모델)마다 라이선스가 다르다. 앱 기본값 OmniVoice는 모델 가중치가 CC-BY-NC(비상업용)라서
기본 엔진을 VoxCPM2(Apache-2.0)로 두고, 비상업용 엔진으로 만든 나레이션은 AI 진단에서 막는다.

예비 엔진: ElevenLabs REST API(설정 voice_engine=elevenlabs일 때만).
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
VS_URL = "http://127.0.0.1:3900"
DEFAULT_VS_MODEL = "voxcpm2"
VS_DOWNLOAD = "https://github.com/debpalash/VoiceStudio/releases/latest"
# 한국어를 지원하는 VoiceStudio 엔진. license는 각 엔진 문서·VoiceStudio 모델 라이선스 목록(docs/licensing) 기준.
# VoiceStudio 스스로 모든 모델을 '상업 이용 검토 미완료'로 표시하므로, commercial=True는 '라이선스 표기상 허용'이란 뜻.
VS_ENGINES = {
    "voxcpm2": {"label": "VoxCPM2 · 고음질", "license": "Apache-2.0", "commercial": True,
                "note": "48kHz 고음질 · 그래픽카드 권장"},
    "cosyvoice": {"label": "CosyVoice3", "license": "Apache-2.0", "commercial": True, "note": "자연스러운 억양"},
    "moss-tts-nano": {"label": "MOSS-TTS-Nano · 가벼움", "license": "Apache-2.0", "commercial": True,
                      "note": "그래픽카드 없어도 빠름 · 말 속도 조절 안 됨"},
    "omnivoice": {"label": "OmniVoice · VoiceStudio 기본값", "license": "CC-BY-NC", "commercial": False,
                  "note": "비상업용 — 회사·광고·수익 채널 영상에 쓰면 안 됨"},
}
DEFAULT_MODEL = "eleven_multilingual_v2"  # 한국어 포함 다국어 모델
SAMPLE_EXT = {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".mp4", ".mov", ".aac", ".flac"}


class VoiceError(RuntimeError):
    pass


# ---------------- 내 목소리 목록 ----------------
@dataclass
class VoiceProfile:
    id: str
    name: str
    voice: str                 # '<엔진>:<voice_id>' (voicestudio:<프로필 id> / elevenlabs:<voice_id>)
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
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    return float(out or 0)


# ---------------- ElevenLabs ----------------
class ElevenLabs:
    provider, audio_ext = "elevenlabs", "mp3"

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


# ---------------- VoiceStudio(내 PC에서 도는 무료 음성 앱) ----------------
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 내 PC 안 통신은 프록시를 거치면 안 된다


class VoiceStudio:
    provider, audio_ext = "voicestudio", "wav"

    def __init__(self, url: str = VS_URL, model: str = DEFAULT_VS_MODEL, timeout: float = 600):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout

    @property
    def label(self) -> str:
        return VS_ENGINES.get(self.model, {}).get("label", self.model)

    def _request(self, method: str, path: str, *, body: bytes | None = None, content_type: str | None = None,
                 raw: bool = False, timeout: float | None = None):
        req = urllib.request.Request(f"{self.url}/{path}", data=body, method=method)
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with _LOCAL.open(req, timeout=timeout or self.timeout) as r:
                data = r.read()
        except urllib.error.HTTPError as exc:
            raise VoiceError(self._explain(exc.code, exc.read().decode("utf-8", "replace"))) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise VoiceError(f"VoiceStudio에 연결할 수 없습니다({self.url}). VoiceStudio 앱을 켠 뒤 다시 시도하세요. "
                             f"설치: {VS_DOWNLOAD}") from exc
        return data if raw else (json.loads(data) if data else {})

    def _explain(self, status: int, text: str) -> str:
        try:
            d = json.loads(text)
        except ValueError:
            d = {}
        err = d.get("error") if isinstance(d.get("error"), dict) else {}
        detail = d.get("detail")
        msg = err.get("message") or (detail.get("message") if isinstance(detail, dict) else detail) or text[:300]
        code = err.get("code") or (detail.get("code") if isinstance(detail, dict) else "") or ""
        if status == 403 and "licen" in f"{code} {msg}".lower():
            return f"VoiceStudio 앱의 '모델' 화면에서 {self.label} 모델 라이선스를 수락하세요"
        if code == "model_not_available" or "not available" in str(msg):
            return f"VoiceStudio에 {self.label} 엔진이 아직 설치되지 않았습니다. VoiceStudio 앱의 '모델' 화면에서 설치하세요({msg})"
        if status == 503:
            return "VoiceStudio가 아직 시작 중입니다. 잠시 후 다시 시도하세요"
        return f"VoiceStudio 오류 {status}: {msg}"

    def health(self, timeout: float = 1.5) -> bool:
        try:
            self._request("GET", "health", timeout=timeout)
            return True
        except VoiceError:
            return False

    def engines(self) -> dict:
        """엔진 id → {available, reason}. VoiceStudio 버전에 따라 없을 수 있어 실패하면 빈 값."""
        try:
            data = self._request("GET", "engines/tts", timeout=5)
        except VoiceError:
            return {}
        return {b["id"]: {"available": bool(b.get("available")), "reason": b.get("reason") or ""}
                for b in data.get("backends", []) if b.get("id")}

    def stock_voices(self) -> list[dict]:
        out = [{"voice": "voicestudio:default", "name": "엔진 기본 목소리", "category": "default",
                "labels": {"note": "매번 조금씩 다름"}, "preview_url": ""}]
        for p in self._request("GET", "profiles", timeout=10):
            out.append({"voice": f"voicestudio:{p['id']}", "name": p.get("name") or p["id"],
                        "category": p.get("kind") or "clone", "labels": {"src": "VoiceStudio"},
                        "preview_url": ""})
        return out

    def clone(self, name: str, files: list[Path], description: str = "", remove_noise: bool = True) -> str:
        # VoiceStudio는 기준 녹음 1개를 받는다 → 샘플을 이어 붙여 30초 이내로(VoxCPM2가 앞 30초만 씀)
        ref = files[0].with_name(f"ref_{uuid.uuid4().hex[:8]}.wav")
        args = [a for f in files for a in ("-i", str(f))]
        try:
            ffmpeg(*args, "-filter_complex", f"concat=n={len(files)}:v=0:a=1", "-t", "30", "-ac", "1", "-ar", "44100",
                   str(ref))
            body, ctype = _multipart({"name": name, "kind": "clone", "language": "ko"},
                                     [("ref_audio", ref.name, "audio/wav", ref.read_bytes())])
        finally:
            ref.unlink(missing_ok=True)
        data = self._request("POST", "profiles", body=body, content_type=ctype, timeout=300)
        if not data.get("id"):
            raise VoiceError(f"목소리 저장 응답에 id가 없습니다: {data}")
        return data["id"]

    def delete(self, voice_id: str) -> None:
        self._request("DELETE", f"profiles/{urllib.parse.quote(voice_id)}", timeout=30)

    def tts(self, text: str, voice_id: str, speed: float | None = None) -> tuple[bytes, dict]:
        body = {"model": self.model, "input": text, "voice": voice_id or "default", "response_format": "wav",
                "speed": speed or 1.0, "language": "ko"}
        audio = self._request("POST", "v1/audio/speech", body=json.dumps(body).encode(),
                              content_type="application/json", raw=True)
        if not audio:
            raise VoiceError("음성 데이터가 비어 있습니다")
        return audio, {"words": self.align(audio)}

    def align(self, audio: bytes) -> list[dict]:
        """합성된 음성을 VoiceStudio 음성 인식으로 다시 들어 단어 시각을 얻는다. 실패하면 빈 목록(균등 배분으로 대체)."""
        body, ctype = _multipart({"model": "whisper-1", "language": "ko", "response_format": "verbose_json",
                                  "timestamp_granularities[]": "word"}, [("file", "speech.wav", "audio/wav", audio)])
        try:
            data = self._request("POST", "v1/audio/transcriptions", body=body, content_type=ctype)
        except VoiceError:
            return []
        return [{"text": str(w.get("word", "")).strip(), "start": float(w["start"]), "end": float(w["end"])}
                for w in data.get("words") or [] if w.get("start") is not None and w.get("end") is not None]


def _multipart(fields: dict, files: list[tuple[str, str, str, bytes]]) -> tuple[bytes, str]:
    boundary = "----autoedit" + uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    for key, filename, ctype, data in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'
                     f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def get_engine(provider: str | None = None):
    """설정된 음성 엔진(목소리 id 앞부분으로 고를 수 있음). 테스트에서는 이 함수를 바꿔 끼운다."""
    if (provider or settings.voice_engine()) == "elevenlabs":
        return ElevenLabs(settings.get_key("elevenlabs"))
    return VoiceStudio(settings.get("voicestudio_url") or VS_URL, settings.get("voicestudio_model") or DEFAULT_VS_MODEL)


_ready: tuple[float, bool] = (0.0, False)


def engine_ready() -> bool:
    """음성 엔진을 쓸 수 있는지. VoiceStudio는 앱이 켜져 있는지 확인(편집기가 자주 부르므로 몇 초 기억)."""
    global _ready
    if settings.voice_engine() == "elevenlabs":
        return bool(settings.get_key("elevenlabs"))
    if time.time() - _ready[0] > (10 if _ready[1] else 3):
        _ready = (time.time(), get_engine("voicestudio").health())
    return _ready[1]


def engine_info(engine: str) -> dict:
    """'voicestudio/voxcpm2' 같은 엔진 기록 → {label, license, commercial(None=모름)}."""
    provider, _, model = engine.partition("/")
    if provider == "voicestudio":
        e = VS_ENGINES.get(model)
        return {"label": f"VoiceStudio · {e['label'] if e else model}", "license": e["license"] if e else "확인 필요",
                "commercial": e["commercial"] if e else None}
    if provider == "elevenlabs":
        return {"label": "ElevenLabs", "license": "유료 요금제 약관", "commercial": True}
    return {"label": engine or "알 수 없음", "license": "확인 필요", "commercial": None}


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


def fit_words(text: str, heard: list[dict], duration: float) -> list[dict]:
    """원문 단어에 시각을 붙인다. 음성 인식 결과(heard)는 글자가 틀릴 수 있으므로 시각만 빌리고 글자는 원문을 쓴다.
    단어 수가 같으면 1:1, 다르면 글자 수 비율로 인식 결과의 시간축에 얹고, 인식이 없으면 전체 길이에 고르게 나눈다."""
    toks = text.split()
    heard = [w for w in heard if w["end"] > w["start"]]
    if not toks:
        return []
    if len(heard) == len(toks):
        return [{"start": round(w["start"], 3), "end": round(w["end"], 3), "text": t} for w, t in zip(heard, toks)]
    if heard:
        sizes = [max(1, len(w["text"])) for w in heard]
        spans = [(w["start"], w["end"]) for w in heard]
    else:
        sizes, spans = [1], [(min(0.05, duration / 4), max(duration - 0.05, duration * 3 / 4))]
    total, edges, acc = sum(sizes), [], 0
    for n in sizes:
        edges.append(acc / total)
        acc += n
    edges.append(1.0)

    def at(f: float) -> float:
        i = max(0, min(len(spans) - 1, next((k for k in range(len(spans)) if f <= edges[k + 1]), len(spans) - 1)))
        a, b = spans[i]
        return a + (f - edges[i]) / max(edges[i + 1] - edges[i], 1e-9) * (b - a)

    total_chars, out, acc = sum(len(t) for t in toks), [], 0
    for t in toks:
        f0, f1 = acc / total_chars, (acc + len(t)) / total_chars
        acc += len(t)
        out.append({"start": round(at(f0 + 1e-6), 3), "end": round(at(f1 - 1e-6), 3), "text": t})
    return out


def synthesize(text: str, voice: str, out_dir: Path, speed: float | None = None, engine=None) -> dict:
    """텍스트 → 48kHz 스테레오 wav + 단어 타이밍. 같은 문장·목소리·엔진은 다시 생성하지 않는다(시간 절약)."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise VoiceError("읽을 문장이 비어 있습니다")
    provider, _, voice_id = voice.partition(":")
    if provider not in ("voicestudio", "elevenlabs") or not voice_id:
        raise VoiceError(f"지원하지 않는 음성: {voice}")
    engine = engine or get_engine(provider)
    model = getattr(engine, "model", "")
    key = hashlib.sha1(json.dumps([voice, model, text, speed]).encode()).hexdigest()[:16]
    out_dir.mkdir(parents=True, exist_ok=True)
    wav, meta = out_dir / f"{key}.wav", out_dir / f"{key}.json"
    if wav.exists() and meta.exists():
        return json.loads(meta.read_text(encoding="utf-8"))
    audio, alignment = engine.tts(text, voice_id, speed)
    src = out_dir / f"{key}_src.{getattr(engine, 'audio_ext', 'mp3')}"
    src.write_bytes(audio)
    try:
        ffmpeg("-i", str(src), "-ac", "2", "-ar", "48000", str(wav))
    finally:
        src.unlink(missing_ok=True)
    duration = round(probe_audio_duration(wav), 3)
    words = (fit_words(text, alignment["words"], duration) if "words" in alignment
             else words_from_alignment(alignment))
    info = {"file": wav.name, "duration": duration, "words": words, "engine": f"{provider}/{model}"}
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
        id=uuid.uuid4().hex[:10], name=name, voice=f"{getattr(engine, 'provider', 'voicestudio')}:{voice_id}", kind="clone",
        samples=[str(p) for p in sample_paths], consent=consent,
        created=time.strftime("%Y-%m-%d %H:%M"), note=f"샘플 {len(sample_paths)}개 · {total:.0f}초"))
