"""E2E 하네스 서버: 실제 앱(시작 화면+편집기)을 외부 의존성 없이 띄운다.

- 음성 엔진(VoiceStudio) → 같은 API로 응답하는 가짜 VoiceStudio 서버(tests/fake_voicestudio.py)
- 음성 인식(Whisper)   → 영상 길이에 맞춘 가짜 대본
- 설정·작업 폴더       → 임시 폴더(AUTOEDIT_HOME)
- 업데이트             → 임시 설치 폴더 + 가짜 GitHub(새 버전 9.9.0 게시), 다시 시작은 상태만 바꿔 흉내
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

    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from autoedit import analyze, app, editor, settings, updater
    from fake_voicestudio import start
    from test_updater import make_install, make_release

    _, vs_url, _ = start()
    # 업데이트: 임시 설치 폴더(현재 버전)와 새 버전 9.9.0을 올려 둔 가짜 GitHub
    root.mkdir(parents=True, exist_ok=True)
    updater.ROOT = make_install(root / "install")
    gh = {"/raw/acme/work/main/video-autoedit/version.json":
          json.dumps({"version": "9.9.0", "date": "2026-11-01", "notes": ["테스트용 새 기능 A", "테스트용 개선 B"]}).encode(),
          "/codeload/acme/work/zip/refs/heads/main": make_release(root, "9.9.0")}

    class GH(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = gh.get(self.path)
            self.send_response(200 if body else 404)
            self.end_headers()
            self.wfile.write(body or b"")
    ghd = ThreadingHTTPServer(("127.0.0.1", 0), GH)
    threading.Thread(target=ghd.serve_forever, daemon=True).start()
    updater.RAW = f"http://127.0.0.1:{ghd.server_address[1]}/raw"
    updater.CODELOAD = f"http://127.0.0.1:{ghd.server_address[1]}/codeload"
    os.environ["AUTOEDIT_SUPERVISED"] = "1"  # 실행기 아래에서 도는 것처럼(자동 다시 시작 경로)
    state = app.AppState.__new__(app.AppState)  # 아래 settings.put 뒤에 초기화
    app.request_restart = lambda: threading.Timer(1.5, lambda: setattr(state, "update_job", {"state": "idle"})).start()
    settings.put(workspace=str(root / "workspace"), user_name="테스트 제작자", whisper_model="small",
                 voicestudio_url=vs_url, update_source={"repo": "acme/work", "branch": "main", "subdir": "video-autoedit"})
    state.__init__()
    analyze.transcribe = fake_transcriber()
    editor._whisper_available = lambda: True
    app._whisper_available = lambda: True
    editor.transcribe = analyze.transcribe

    info = make_samples(root)
    (root / "harness.json").write_text(json.dumps({**info, "port": args.port}, ensure_ascii=False))
    app.serve_app(port=args.port, open_browser=False, app=state)


if __name__ == "__main__":
    main()
