"""ffmpeg/ffprobe 호출 래퍼."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np


class FFmpegError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FFmpegError(f"{tool}를 찾을 수 없습니다. ffmpeg 6.x(libass 포함)를 설치하세요.")


def run(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=capture, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-2000:]
        raise FFmpegError(f"명령 실패 ({args[0]}): {tail}")
    return proc


def ffmpeg(*args: str) -> subprocess.CompletedProcess:
    return run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args])


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    rotation: int
    has_audio: bool
    codec: str = ""

    @property
    def display_size(self) -> tuple[int, int]:
        if self.rotation in (90, 270):
            return self.height, self.width
        return self.width, self.height

    @property
    def aspect(self) -> float:
        w, h = self.display_size
        return w / h

    def to_dict(self) -> dict:
        return asdict(self)


def probe(path: str | Path) -> MediaInfo:
    out = run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]).stdout
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise FFmpegError(f"영상 스트림이 없습니다: {path}")
    has_audio = any(s["codec_type"] == "audio" for s in data["streams"])
    num, _, den = video.get("avg_frame_rate", "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = abs(int(sd["rotation"])) % 360
    if "rotate" in (video.get("tags") or {}):
        rotation = abs(int(video["tags"]["rotate"])) % 360
    duration = float(data["format"].get("duration") or video.get("duration") or 0)
    return MediaInfo(
        path=str(path), duration=duration,
        width=int(video["width"]), height=int(video["height"]),
        fps=fps or 30.0, rotation=rotation, has_audio=has_audio, codec=video.get("codec_name", ""),
    )


def load_audio(path: str | Path, sr: int = 16000) -> np.ndarray:
    """모노 float32 PCM. 오디오가 없으면 빈 배열."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
         "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(proc.stdout, dtype=np.float32)


def extract_frame(path: str | Path, t: float, out: Path, width: int = 512) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-ss", f"{max(t, 0):.3f}", "-i", str(path), "-frames:v", "1",
           "-vf", f"scale={width}:-2", "-q:v", "4", str(out))
    return out
