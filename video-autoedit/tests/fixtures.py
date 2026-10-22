"""테스트용 합성 영상: 가로 1920x1080, 4초 주기로 2.8초 발화 + 1.2초 무음, 대사 transcript.json 동봉."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

SENTENCES = [
    "안녕하세요 오늘은 그냥 이야기를 해볼게요",
    "왜 다들 이 방법을 모를까요?",
    "첫 번째 꿀팁은 아침 루틴입니다",
    "음 어 그",
    "이걸로 매출이 3배 늘었습니다",
    "두 번째는 기록하는 습관이에요",
    "사실 제일 중요한 건 핵심 하나예요",
    "저장해두고 필요할 때 보세요 팔로우도 부탁해요",
]


def make_video(path: Path, seconds: int = 32) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    audio = "aevalsrc='0.4*sin(2*PI*220*t)*lt(mod(t,4),2.8)*(1+1.2*gt(mod(t,16),12))':s=48000:d=%d" % seconds
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:d={seconds}",
        "-f", "lavfi", "-i", audio,
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path),
    ], check=True)
    return path


def make_transcript(path: Path) -> Path:
    sentences = []
    for i, text in enumerate(SENTENCES):
        start, end = i * 4.0 + 0.05, i * 4.0 + 2.75
        toks = text.split()
        span = (end - start) / len(toks)
        words = [{"start": round(start + k * span, 3), "end": round(start + (k + 1) * span - 0.02, 3), "text": t}
                 for k, t in enumerate(toks)]
        sentences.append({"start": start, "end": end, "text": text, "words": words})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"language": "ko", "sentences": sentences}, ensure_ascii=False), encoding="utf-8")
    return path
