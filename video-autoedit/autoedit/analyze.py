"""원본 영상 신호 분석: 무음 구간, 음량 피크, 장면 전환, 대표 프레임."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .media import MediaInfo, extract_frame, load_audio, probe
from .transcribe import Transcript, transcribe

SR = 16000
HOP = 0.1  # 음량 분석 단위(초)


@dataclass
class Analysis:
    index: int
    info: MediaInfo
    silences: list[tuple[float, float]]
    energy_db: list[float]          # HOP 간격 RMS(dBFS)
    peaks: list[float]              # 음량 피크 시각(초)
    scene_changes: list[float]
    frames: list[tuple[float, str]] = field(default_factory=list)
    transcript: Transcript | None = None

    @property
    def path(self) -> str:
        return self.info.path

    @property
    def duration(self) -> float:
        return self.info.duration

    def energy_at(self, start: float, end: float) -> float:
        a, b = int(start / HOP), max(int(end / HOP), int(start / HOP) + 1)
        window = self.energy_db[a:b]
        return float(np.mean(window)) if window else -90.0

    def speech_ratio(self, start: float, end: float) -> float:
        span = max(end - start, 1e-6)
        silent = sum(max(0.0, min(e, end) - max(s, start)) for s, e in self.silences)
        return 1.0 - silent / span

    def to_dict(self) -> dict:
        d = asdict(self)
        d["info"] = self.info.to_dict()
        d["transcript"] = self.transcript.to_dict() if self.transcript else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Analysis":
        return cls(
            index=d["index"], info=MediaInfo(**d["info"]),
            silences=[tuple(x) for x in d["silences"]],
            energy_db=d["energy_db"], peaks=d["peaks"],
            scene_changes=d["scene_changes"],
            frames=[tuple(x) for x in d.get("frames", [])],
            transcript=Transcript.from_dict(d["transcript"]) if d.get("transcript") else None,
        )


def detect_silences(path: str, noise_db: float = -35.0, min_len: float = 0.35) -> list[tuple[float, float]]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", path, "-vn",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_len}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", proc.stderr)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        out.append((max(s, 0.0), e if e is not None else float("inf")))
    return out


def energy_envelope(pcm: np.ndarray) -> list[float]:
    hop = int(SR * HOP)
    if pcm.size < hop:
        return []
    n = pcm.size // hop
    frames = pcm[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)) + 1e-9
    return [round(float(x), 2) for x in 20 * np.log10(rms)]


def find_peaks(energy: list[float], min_gap: float = 1.5, pct: float = 88) -> list[float]:
    if not energy:
        return []
    arr = np.asarray(energy)
    thr = np.percentile(arr, pct)
    candidates = [i for i in range(1, len(arr) - 1)
                  if arr[i] >= thr and arr[i] >= arr[i - 1] and arr[i] >= arr[i + 1]]
    candidates.sort(key=lambda i: -arr[i])
    picked: list[int] = []
    for i in candidates:
        if all(abs(i - j) * HOP >= min_gap for j in picked):
            picked.append(i)
    return sorted(round(i * HOP, 2) for i in picked)


def detect_scenes(path: str, threshold: float = 0.32) -> list[float]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", path, "-an",
         "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return [round(float(x), 2) for x in re.findall(r"pts_time:([\d.]+)", proc.stderr)]


def analyze(path: str | Path, index: int, workdir: Path, *, whisper_model: str | None,
            language: str = "ko", n_frames: int = 6, force: bool = False) -> Analysis:
    """분석 결과는 workdir/analysis.json에 캐시된다. 자막을 손으로 고치고 싶으면
    workdir/transcript.json을 수정한 뒤 다시 실행하면 그대로 반영된다."""
    workdir.mkdir(parents=True, exist_ok=True)
    cache = workdir / "analysis.json"
    tpath = workdir / "transcript.json"
    if cache.exists() and not force:
        a = Analysis.from_dict(json.loads(cache.read_text(encoding="utf-8")))
        a.index = index
        if tpath.exists():
            a.transcript = Transcript.from_dict(json.loads(tpath.read_text(encoding="utf-8")))
        return a

    path = str(path)
    info = probe(path)
    pcm = load_audio(path, SR) if info.has_audio else np.zeros(0, np.float32)
    energy = energy_envelope(pcm)
    silences = detect_silences(path) if info.has_audio else [(0.0, info.duration)]
    silences = [(s, min(e, info.duration)) for s, e in silences]

    a = Analysis(
        index=index, info=info, silences=silences, energy_db=energy,
        peaks=find_peaks(energy), scene_changes=detect_scenes(path),
    )

    step = info.duration / (n_frames + 1)
    for k in range(1, n_frames + 1):
        t = round(step * k, 2)
        out = extract_frame(path, t, workdir / "frames" / f"f{k:02d}.jpg")
        a.frames.append((t, str(out)))

    if tpath.exists():
        a.transcript = Transcript.from_dict(json.loads(tpath.read_text(encoding="utf-8")))
    elif whisper_model and info.has_audio:
        a.transcript = transcribe(path, whisper_model, language)
        if a.transcript:
            tpath.write_text(json.dumps(a.transcript.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")

    cache.write_text(json.dumps(a.to_dict(), ensure_ascii=False), encoding="utf-8")
    return a
