"""E2E 하네스 서버: 실제 앱(시작 화면+편집기)을 외부 의존성 없이 띄운다.

- 음성 엔진(ElevenLabs) → 가짜 엔진(톤 음성 + 균등 글자 타이밍)
- 음성 인식(Whisper)   → 영상 길이에 맞춘 가짜 대본
- 설정·작업 폴더       → 임시 폴더(AUTOEDIT_HOME)
- 샘플 영상            → 까다로운 파일 이름(한글·공백·작은따옴표)의 VP9 영상(헤드리스 브라우저 재생용)

  python tests/e2e/harness.py --root /tmp/e2e --port 8790
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))           # tests/ (fixtures, test_voice)
sys.path.insert(0, str(HERE.parent.parent))    # 패키지 루트

SAMPLE_NAME = "아침 루틴 '꿀팁' 촬영.webm"


def make_samples(root: Path) -> dict:
    from fixtures import make_video
    media = root / "media"
    media.mkdir(parents=True, exist_ok=True)
    mp4 = make_video(media / "base.mp4", seconds=32)
    webm = media / SAMPLE_NAME
    if not webm.exists():
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(mp4), "-c:v", "libvpx-vp9", "-deadline", "realtime",
                        "-cpu-used", "8", "-b:v", "1M", "-c:a", "libopus", str(webm)], check=True)
    return {"webm": str(webm)}


def fake_transcriber():
    from fixtures import SENTENCES
    from autoedit.media import probe
    from autoedit.transcribe import Sentence, Transcript, Word

    def transcribe(path, model_size="small", language="ko"):
        dur = probe(path).duration
        sents = []
        for i, text in enumerate(SENTENCES):
            start, end = i * 4.0 + 0.05, i * 4.0 + 2.75
            if end > dur:
                break
            toks = text.split()
            span = (end - start) / len(toks)
            words = [Word(round(start + k * span, 3), round(start + (k + 1) * span - 0.02, 3), t) for k, t in enumerate(toks)]
            sents.append(Sentence(start, end, text, words))
        return Transcript(language, sents)
    return transcribe


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--port", type=int, default=8790)
    args = ap.parse_args()
    root = Path(args.root).resolve()
    os.environ["AUTOEDIT_HOME"] = str(root / "home")
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("ELEVENLABS_API_KEY", None)

    from autoedit import analyze, app, editor, settings, voice
    from test_voice import FakeEngine

    settings.put(workspace=str(root / "workspace"), user_name="테스트 제작자", whisper_model="small",
                 elevenlabs_api_key="fake-key")
    eng = FakeEngine()
    voice.get_engine = lambda: eng
    analyze.transcribe = fake_transcriber()
    editor._whisper_available = lambda: True
    app._whisper_available = lambda: True
    editor.transcribe = analyze.transcribe

    info = make_samples(root)
    (root / "harness.json").write_text(json.dumps({**info, "port": args.port}, ensure_ascii=False))
    app.serve_app(port=args.port, open_browser=False)


if __name__ == "__main__":
    main()
