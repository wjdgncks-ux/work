"""수작업 편집기 서버(브라우저 UI). 표준 라이브러리만 사용하며 127.0.0.1에서만 연다.

  python -m autoedit edit out/<영상>/<플랫폼>

API
  GET  /api/state                 프로젝트·기획서·원본 신호·자막·진단
  POST /api/diagnose {plan}       저장 없이 진단(타임라인·점수)
  POST /api/save {plan}           plan.json 저장
  POST /api/style {style}         편집 스타일 변경
  POST /api/transcript {index, sentences}  자막(대사) 수정
  POST /api/render {plan, preview}         렌더링 시작(백그라운드)
  GET  /api/render/status
  GET  /media/source/<i> · /media/output?preview=1 · /media/asset/<파일명>   (Range 지원)
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import ValidationError

from .media import ffmpeg, probe
from .plan import EditPlan
from .project import Project
from .styles import STYLES
from .transcribe import Sentence, Transcript, Word

STATIC = Path(__file__).parent / "editor_static"
CHUNK = 1 << 16
WEB_CODECS = {"h264", "vp8", "vp9", "av1"}


def ensure_proxy(src: str, workdir: Path) -> Path:
    """브라우저에서 바로 재생·탐색할 수 있는 편집용 사본. 원본이 HEVC(아이폰 기본)·ProRes거나 FHD 초과면 만든다.
    시간축은 원본과 같으므로 편집 결과(plan.json)는 그대로 원본으로 렌더링된다."""
    info = probe(src)
    w, h = info.display_size
    if info.codec in WEB_CODECS and w * h <= 1920 * 1080:
        return Path(src)
    proxy = workdir / "proxy.mp4"
    if proxy.exists() and proxy.stat().st_mtime >= Path(src).stat().st_mtime:
        return proxy
    print(f"  · 편집용 프록시 생성: {Path(src).name} ({info.codec} {w}x{h})", flush=True)
    ffmpeg("-i", src, "-vf", "scale='if(gt(iw,ih),-2,720)':'if(gt(iw,ih),720,-2)'", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "26", "-g", "15", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(proxy))
    return proxy


class EditorState:
    def __init__(self, pdir: Path):
        self.project = Project.load(pdir)
        self.proxies = [ensure_proxy(src, Path(d)) for src, d in
                        zip(self.project.meta["sources"], self.project.meta["analysis_dirs"])]
        self.lock = threading.Lock()
        self.job: dict = {"state": "idle"}

    # ---------- 직렬화 ----------
    def diagnosis(self, plan: EditPlan) -> dict:
        plan = plan.model_copy(deep=True)
        card, timeline, captions = self.project.finalize(plan)
        return {
            "score": card.score, "headline": card.headline, "metrics": card.metrics,
            "issues": [vars(i) for i in card.issues],
            "flags": [vars(f) for f in card.flags],
            "fixes": card.fixes,
            "timeline": [{"source": p.source, "start": p.start, "end": p.end, "out_start": p.out_start,
                          "duration": round(p.duration, 3), "role": p.role, "speed": p.speed} for p in timeline],
            "captions": [vars(c) for c in captions],
            "normalized": plan.model_dump(),
        }

    def state(self) -> dict:
        pr = self.project
        plan = pr.load_plan()
        pf = pr.platform
        assets_dir = pr.meta.get("render", {}).get("assets")
        assets = sorted(f.name for f in Path(assets_dir).iterdir() if f.is_file()) if assets_dir and Path(assets_dir).is_dir() else []
        outputs = {}
        for key, preview in (("final", False), ("preview", True)):
            path = pr.output_path(preview)
            outputs[key] = {"exists": path.exists(), "mtime": path.stat().st_mtime if path.exists() else 0,
                            "url": f"/media/output?preview={int(preview)}"}
        return {
            "meta": {
                "name": pr.name, "profile": pr.meta.get("profile"), "planner": pr.meta.get("planner"),
                "platform": {"key": pf.key, "label": pf.label, "width": pf.width, "height": pf.height,
                             "target": pf.target_seconds, "max_shot": pf.max_shot_seconds},
                "style": pr.style.key, "styles": [{"key": s.key, "label": s.label} for s in STYLES.values()],
                "brief": pr.meta["brief"], "render": pr.meta.get("render", {}),
            },
            "plan": plan.model_dump(),
            "sources": [{
                "index": a.index, "name": Path(a.path).name, "duration": a.duration,
                "width": a.info.display_size[0], "height": a.info.display_size[1],
                "silences": a.silences, "peaks": a.peaks, "scenes": a.scene_changes,
                "url": f"/media/source/{a.index}",
            } for a in pr.analyses],
            "transcripts": [a.transcript.to_dict() if a.transcript else None for a in pr.analyses],
            "assets": assets,
            "outputs": outputs,
            "diagnosis": self.diagnosis(plan),
        }

    # ---------- 동작 ----------
    def save_transcript(self, index: int, sentences: list[dict]) -> None:
        a = self.project.analyses[index]
        old = {(round(s.start, 2), round(s.end, 2)): s for s in (a.transcript.sentences if a.transcript else [])}
        new: list[Sentence] = []
        for d in sentences:
            start, end, text = float(d["start"]), float(d["end"]), str(d["text"]).strip()
            if not text or end <= start:
                continue
            toks = text.split()
            prev = old.get((round(start, 2), round(end, 2)))
            if prev and len(prev.words) == len(toks):  # 단어 수가 같으면 타이밍 유지, 글자만 교체
                words = [Word(w.start, w.end, t) for w, t in zip(prev.words, toks)]
            else:  # 단어 수가 바뀌면 문장 구간에 균등 배분
                span = (end - start) / len(toks)
                words = [Word(round(start + i * span, 3), round(start + (i + 1) * span - 0.02, 3), t)
                         for i, t in enumerate(toks)]
            new.append(Sentence(start, end, text, words))
        new.sort(key=lambda s: s.start)
        lang = a.transcript.language if a.transcript else "ko"
        self.project.save_transcript(index, Transcript(lang, new))

    def start_render(self, plan: EditPlan, preview: bool) -> dict:
        with self.lock:
            if self.job.get("state") == "running":
                return self.job
            self.job = {"state": "running", "preview": preview, "started": time.time()}

        def work():
            try:
                info, card = self.project.render(plan, preview=preview, planner_suffix=" + 수작업 편집")
                self.job = {"state": "done", "preview": preview, "info": info, "score": card.score,
                            "elapsed": round(time.time() - self.job["started"], 1)}
            except Exception as exc:  # 렌더링 실패는 UI에 그대로 보여준다
                self.job = {"state": "error", "preview": preview, "error": str(exc)[-1500:],
                            "trace": traceback.format_exc()[-1500:]}

        threading.Thread(target=work, daemon=True).start()
        return self.job


def make_handler(st: EditorState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "autoedit-editor"

        def log_message(self, fmt, *args):  # 조용히
            pass

        # ---------- 응답 도우미 ----------
        def _json(self, data, status=HTTPStatus.OK):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, msg: str, status=HTTPStatus.BAD_REQUEST):
            self._json({"error": msg}, status)

        def _file(self, path: Path):
            if not path or not path.is_file():
                return self._error("파일 없음", HTTPStatus.NOT_FOUND)
            size = path.stat().st_size
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:  # 끝에서 N바이트
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(HTTPStatus.OK)
            length = end - start + 1
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                with path.open("rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(CHUNK, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):  # 브라우저가 탐색 중 연결을 끊는 것은 정상
                pass

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def _plan(self, data: dict) -> EditPlan:
            return EditPlan.model_validate(data["plan"])

        # ---------- 라우팅 ----------
        def do_GET(self):
            url = urlparse(self.path)
            path = unquote(url.path)
            try:
                if path in ("/", "/index.html"):
                    return self._file(STATIC / "index.html")
                if path == "/favicon.ico":
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.end_headers()
                    return
                if path == "/api/state":
                    return self._json(st.state())
                if path == "/api/render/status":
                    return self._json(st.job)
                if path.startswith("/media/source/"):
                    i = int(path.rsplit("/", 1)[1])
                    return self._file(st.proxies[i])
                if path == "/media/output":
                    preview = parse_qs(url.query).get("preview", ["0"])[0] == "1"
                    return self._file(st.project.output_path(preview))
                if path.startswith("/media/asset/"):
                    d = st.project.meta.get("render", {}).get("assets")
                    name = Path(path.rsplit("/", 1)[1]).name  # 경로 탈출 방지
                    return self._file(Path(d) / name if d else None)
                return self._error("없는 경로", HTTPStatus.NOT_FOUND)
            except (IndexError, ValueError):
                return self._error("잘못된 요청")

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                data = self._body()
                if path == "/api/diagnose":
                    return self._json(st.diagnosis(self._plan(data)))
                if path == "/api/save":
                    plan = self._plan(data)
                    st.project.save_plan(plan)
                    return self._json({"ok": True, "diagnosis": st.diagnosis(plan)})
                if path == "/api/style":
                    st.project.set_style(data["style"])
                    return self._json(st.state())
                if path == "/api/transcript":
                    st.save_transcript(int(data["index"]), data["sentences"])
                    return self._json(st.state())
                if path == "/api/render":
                    return self._json(st.start_render(self._plan(data), bool(data.get("preview"))))
                return self._error("없는 경로", HTTPStatus.NOT_FOUND)
            except ValidationError as exc:
                return self._error("기획서 형식 오류: " + "; ".join(
                    f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5]))
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                return self._error(f"잘못된 요청: {exc}")

    return Handler


def serve(pdir: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    st = EditorState(pdir)
    httpd = ThreadingHTTPServer((host, port), make_handler(st))
    url = f"http://{host}:{port}/"
    print(f"수작업 편집기: {url}  (종료: Ctrl+C)\n프로젝트: {pdir}", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
