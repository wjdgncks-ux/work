"""CapCut 방식 수작업 편집기 서버(브라우저 UI). 표준 라이브러리만 사용하며 127.0.0.1에서만 연다.

  python -m autoedit edit out/<채널>/<영상>/<플랫폼>

편집기를 처음 열면 자동 무음 컷을 실제 클립으로 확정(bake)한다 → 타임라인에 보이는 그대로 출력된다.

API
  GET  /api/state                         프로젝트·기획서·원본 신호·자막·진단
  POST /api/diagnose {plan}               저장 없이 진단
  POST /api/save {plan}                   plan.json 저장(자동 저장)
  POST /api/style {style}                 스타일 변경
  POST /api/replan {style}                AI 재편집(현재 편집을 새 기획으로 교체)
  POST /api/autofill {plan}               정지 구간에 줌 강조 자동 채우기
  POST /api/transcript {index, sentences} 캡션(대사) 수정
  POST /api/import?kind=media|asset&name= 파일 가져오기(본문=파일)
  POST /api/render {plan, preview}        내보내기(백그라운드)
  POST /api/transcribe {index}            자동 캡션(음성 인식)
  POST /api/settings {elevenlabs_key}     음성 엔진 키 저장(~/.autoedit)
  GET  /api/voices/stock                  AI 음성 목록
  POST /api/voices/sample?ext=webm        녹음·파일 샘플 업로드(본문=파일)
  POST /api/voices/clone {name, samples, consent}  인물 지정 음성 만들기
  POST /api/voices/delete {id}
  POST /api/voices/preview {voice, text}  미리 듣기
  POST /api/narration {text, voice, voice_name, source, t, original}  나레이션 음성 생성
  POST /api/narration/draft {plan, instruction}                     AI 대본 초안
  GET  /api/render/status
  GET  /media/source/<i> · /media/thumbs/<i> · /media/sfx/<종류> · /media/asset/<파일명>
  GET  /media/output?preview=1 | ?v=<버전>[&download=1]
  GET  /media/review/<검수표_vN.html|업로드문구_vN.txt>
  GET  /api/history · /api/history/<파일>   자동 백업 목록·내용
  POST /api/review {status, memo, done}    컨펌 상태·수정 요청 메모
  POST /api/reveal                         프로젝트 폴더 열기
"""

from __future__ import annotations

import json
import math
import mimetypes
import os
import re
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from pydantic import ValidationError

from . import settings, voice
from .media import ffmpeg, probe
from .osutil import reveal
from .narration import create_narration, draft_script
from .pacing import fill_static_gaps
from .plan import EditPlan
from .planner_heuristic import attach_assets, plan_heuristic
from .project import REVIEW_STATES, Project, finalize
from .sfx import RECIPES, ensure_sfx
from .platforms import PLATFORMS
from .styles import STYLES
from .transcribe import Sentence, Transcript, Word, transcribe

STATIC = Path(__file__).parent / "editor_static"
CHUNK = 1 << 16
WEB_CODECS = {"h264", "vp8", "vp9", "av1"}
MEDIA_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
ASSET_EXT = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov"}
SFX_LEN = {"whoosh": 0.45, "pop": 0.18, "ding": 0.9, "boom": 0.8, "boing": 0.7, "riser": 1.0}
SFX_LABEL = {"whoosh": "휙(전환)", "pop": "뽁(강조)", "ding": "띵(정답·수치)", "boom": "쿵(후킹)",
             "boing": "띠용(유머)", "riser": "상승음(반전 직전)"}
THUMB_H = 64


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


def ensure_thumbs(video: Path, duration: float, aspect: float, workdir: Path) -> dict:
    """타임라인 필름스트립용 썸네일 시트(격자 한 장). 키프레임만 디코딩해서 빠르게 만든다."""
    meta_path, sheet = workdir / "thumbs.json", workdir / "thumbs.jpg"
    if meta_path.exists() and sheet.exists() and sheet.stat().st_mtime >= video.stat().st_mtime:
        return json.loads(meta_path.read_text())
    interval = max(1.0, duration / 240)
    count = max(1, math.ceil(duration / interval))
    cols = min(20, count)
    rows = math.ceil(count / cols)
    tw = max(16, int(round(THUMB_H * aspect / 2)) * 2)
    ffmpeg("-skip_frame", "nokey", "-i", str(video), "-vf",
           f"fps=1/{interval},scale={tw}:{THUMB_H},tile={cols}x{rows}", "-frames:v", "1", "-q:v", "5", str(sheet))
    meta = {"interval": interval, "count": count, "cols": cols, "rows": rows, "w": tw, "h": THUMB_H}
    meta_path.write_text(json.dumps(meta))
    return meta


def _whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _llm_ready() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


class EditorState:
    def __init__(self, pdir: Path, whisper_model: str | None = None, in_app: bool = False):
        self.project = Project.load(pdir)
        self.in_app = in_app
        self.whisper_model = whisper_model
        self.project.bake()
        self.proxies: list[Path] = []
        self.thumbs: list[dict] = []
        for i in range(len(self.project.analyses)):
            self._prepare_source(i)
        self.sfx_files = ensure_sfx(self.project.pdir / "_sfx", self._path("sfx_dir"))
        self.lock = threading.Lock()
        self.job: dict = {"state": "idle"}
        self.voices = voice.VoiceStore()
        self._stock: list[dict] | None = None

    def _path(self, key: str) -> Path | None:
        v = self.project.meta.get("render", {}).get(key)
        return Path(v) if v else None

    def _prepare_source(self, i: int) -> None:
        a = self.project.analyses[i]
        adir = Path(self.project.meta["analysis_dirs"][i])
        proxy = ensure_proxy(a.path, adir)
        self.proxies.append(proxy)
        self.thumbs.append(ensure_thumbs(proxy, a.duration, a.info.aspect, adir))

    # ---------- 직렬화 ----------
    def diagnosis(self, plan: EditPlan) -> dict:
        plan = plan.model_copy(deep=True)
        card, timeline, captions = self.project.finalize(plan)
        return {
            "score": card.score, "headline": card.headline, "metrics": card.metrics,
            "issues": [vars(i) for i in card.issues],
            "flags": [vars(f) for f in card.flags],
            "fixes": card.fixes,
            "captions": [vars(c) for c in captions],
        }

    def assets(self) -> list[str]:
        d = self._path("assets")
        if not d or not d.is_dir():
            return []
        return sorted(f.name for f in d.iterdir() if f.is_file() and f.suffix.lower() in ASSET_EXT)

    def state(self) -> dict:
        pr = self.project
        plan = pr.load_plan()
        pf = pr.platform
        rc = pr.meta.get("render", {})
        outputs = {}
        for key, preview in (("final", False), ("preview", True)):
            path = pr.output_path(preview)
            ok = bool(path and path.exists())
            ver = pr.versions[-1]["v"] if (not preview and pr.versions) else None
            outputs[key] = {"exists": ok, "mtime": path.stat().st_mtime if ok else 0, "version": ver,
                            "url": f"/media/output?preview=1" if preview else f"/media/output?v={ver or 0}",
                            "path": str(path) if path else ""}
        return {
            "meta": {
                "name": pr.name, "profile": pr.meta.get("profile"), "planner": pr.meta.get("planner"),
                "dir": str(pr.pdir),
                "platform": {"key": pf.key, "label": pf.label, "width": pf.width, "height": pf.height, "fps": pf.fps,
                             "target": pf.target_seconds, "max_shot": pf.max_shot_seconds,
                             "safe_top": pf.safe_top, "safe_bottom": pf.safe_bottom},
                "style": pr.style.key, "zoom": pr.style.zoom,
                "styles": [{"key": s.key, "label": s.label} for s in STYLES.values()],
                "brief": pr.meta["brief"], "reframe": rc.get("reframe") or "auto",
                "highlight": rc.get("highlight") or "#FFE500", "handle": rc.get("handle") or "",
                "bgm": Path(rc["bgm"]).name if rc.get("bgm") else "", "llm": _llm_ready(),
            },
            "plan": plan.model_dump(),
            "sources": [{
                "index": a.index, "name": Path(a.path).name, "duration": a.duration,
                "width": a.info.display_size[0], "height": a.info.display_size[1],
                "energy": a.energy_db, "url": f"/media/source/{a.index}",
                "thumbs": {**self.thumbs[a.index], "url": f"/media/thumbs/{a.index}"},
            } for a in pr.analyses],
            "transcripts": [a.transcript.to_dict() if a.transcript else None for a in pr.analyses],
            "assets": self.assets(),
            "sfx": [{"kind": k, "label": SFX_LABEL[k], "len": SFX_LEN[k], "url": f"/media/sfx/{k}"} for k in RECIPES],
            "outputs": outputs,
            "versions": pr.versions,
            "platform_labels": {k: PLATFORMS[k].label for k in (pr.meta.get("platforms") or [pr.platform.key])},
            "review": pr.review,
            "review_states": list(REVIEW_STATES),
            "history": pr.history()[:15],
            "app": self.in_app,
            "diagnosis": self.diagnosis(plan),
            "voice": {
                "ready": voice.engine_ready(),
                "mine": [{"id": v.id, "name": v.name, "voice": v.voice, "kind": v.kind, "note": v.note,
                          "created": v.created} for v in self.voices.list()],
                "whisper": _whisper_available(),
            },
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

    def stock_voices(self) -> list[dict]:
        if self._stock is None:
            self._stock = voice.get_engine().stock_voices()
        return self._stock

    def narration(self, d: dict) -> dict:
        nar = create_narration(self.project.pdir, str(d["text"]), str(d["voice"]), str(d.get("voice_name", "")),
                               int(d["source"]), float(d["t"]), original=d.get("original", "duck"))
        return nar.model_dump()

    def preview_voice(self, v: str, text: str) -> str:
        info = voice.synthesize(text or "안녕하세요. 이 목소리로 나레이션을 읽어 드릴게요.", v,
                                self.project.pdir / "narration")
        return f"/media/narration/{info['file']}"

    def clone(self, name: str, samples: list[str], consent: str) -> None:
        root = (self.voices.root / "samples").resolve()
        paths = [Path(p).resolve() for p in samples]
        if any(root not in p.parents or not p.exists() for p in paths):  # 업로드한 샘플만 허용
            raise ValueError("샘플 경로가 올바르지 않습니다")
        voice.create_clone(name.strip() or "내 목소리", paths, consent, store=self.voices)

    def delete_voice(self, vid: str) -> None:
        prof = self.voices.remove(vid)
        if prof and prof.kind == "clone" and voice.engine_ready():
            try:
                voice.get_engine().delete(prof.voice.partition(":")[2])
            except voice.VoiceError:
                pass  # 엔진 쪽 삭제 실패는 목록 삭제를 막지 않는다

    def auto_captions(self, index: int) -> None:
        a = self.project.analyses[index]
        if not _whisper_available():
            raise ValueError("음성 인식(faster-whisper)이 설치되지 않았습니다: pip install faster-whisper")
        tr = transcribe(a.path, self.whisper_model or "small", self.project.meta.get("language", "ko"))
        if tr is None:
            raise ValueError("음성 인식 모델을 불러오지 못했습니다(첫 실행은 인터넷 연결 필요)")
        self.project.save_transcript(index, tr)

    def replan(self, style: str) -> None:
        """현재 원본 전체로 새 기획을 만들고, 무음 컷을 클립으로 확정한다."""
        pr = self.project
        pr.set_style(style)
        brief = pr.brief
        plan = None
        if _llm_ready():
            from .planner_llm import plan_llm
            plan, _ = plan_llm(pr.analyses, pr.platform, brief, self.assets())
        if plan is None:
            plan = plan_heuristic(pr.analyses, pr.platform, brief)
            attach_assets(plan, pr.analyses, self._path("assets"))
        finalize(plan, pr.analyses, pr.platform, brief, auto_pace=True)
        pr.save_plan(plan)
        pr.meta["baked"] = False
        pr.meta["planner"] = ("Claude" if _llm_ready() else "규칙 기반") + f" 재편집({STYLES[style].label})"
        pr.bake()

    def autofill(self, plan: EditPlan) -> EditPlan:
        pr = self.project
        fill_static_gaps(plan, pr.analyses, pr.platform, pr.style)
        return plan

    def import_file(self, kind: str, name: str, body: bytes) -> str:
        name = Path(name).name
        ext = Path(name).suffix.lower()
        if kind == "asset":
            if ext not in ASSET_EXT:
                raise ValueError(f"자료로 쓸 수 없는 형식: {ext}")
            d = self._path("assets") or (self.project.pdir / "assets")
            d.mkdir(parents=True, exist_ok=True)
            self.project.meta.setdefault("render", {})["assets"] = str(d.resolve())
            self.project.save_meta()
            (d / name).write_bytes(body)
            return name
        if ext not in MEDIA_EXT:
            raise ValueError(f"영상으로 쓸 수 없는 형식: {ext}")
        d = self.project.pdir / "media"
        d.mkdir(exist_ok=True)
        dest = d / name
        dest.write_bytes(body)
        self.project.add_source(dest, self.whisper_model)
        self._prepare_source(len(self.project.analyses) - 1)
        return name

    def start_render(self, plan: EditPlan, preview: bool, request_review: bool = False) -> dict:
        with self.lock:
            if self.job.get("state") == "running":
                return self.job
            self.job = {"state": "running", "preview": preview, "started": time.time(), "progress": 0.0}

        def on_progress(frac: float) -> None:
            self.job["progress"] = round(frac, 3)

        def work():
            try:
                info, card = self.project.render(plan, preview=preview, planner_suffix=" + 수작업 편집",
                                                 progress=on_progress, creator=settings.get("user_name", ""))
                if not preview and request_review:
                    self.project.set_review(status="컨펌 요청")
                self.job = {"state": "done", "preview": preview, "info": info, "score": card.score, "progress": 1.0,
                            "version": info.get("version"), "elapsed": round(time.time() - self.job["started"], 1)}
            except Exception as exc:  # 렌더링 실패는 UI에 그대로 보여준다
                self.job = {"state": "error", "preview": preview, "error": str(exc)[-1500:],
                            "trace": traceback.format_exc()[-1500:]}

        threading.Thread(target=work, daemon=True).start()
        return self.job


def make_handler(source):
    """source: EditorState 또는 현재 편집 중인 EditorState를 돌려주는 함수(시작 화면 앱에서 프로젝트 전환용)."""
    get_st = source if callable(source) else (lambda: source)

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

        def _file(self, path: Path | None, download: bool = False):
            if not path or not path.is_file():
                return self._error("파일 없음", HTTPStatus.NOT_FOUND)
            size = path.stat().st_size
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            start, end = 0, size - 1
            m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
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
            if download:
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(path.name)}")
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

        def _raw(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        def _plan(self, data: dict) -> EditPlan:
            return EditPlan.model_validate(data["plan"])

        # ---------- 라우팅 ----------
        def do_GET(self):
            url = urlparse(self.path)
            path = unquote(url.path)
            q = parse_qs(url.query)
            st = get_st()
            if st is None and (path.startswith("/api/") or path.startswith("/media/")):
                return self._error("열린 프로젝트가 없습니다. 시작 화면에서 프로젝트를 여세요.", HTTPStatus.CONFLICT)
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
                    return self._file(st.proxies[int(path.rsplit("/", 1)[1])])
                if path.startswith("/media/thumbs/"):
                    i = int(path.rsplit("/", 1)[1])
                    return self._file(Path(st.project.meta["analysis_dirs"][i]) / "thumbs.jpg")
                if path.startswith("/media/sfx/"):
                    return self._file(st.sfx_files.get(path.rsplit("/", 1)[1]))
                if path == "/media/output":
                    if q.get("preview", ["0"])[0] == "1":
                        return self._file(st.project.output_path(True), download="download" in q)
                    v = int(q.get("v", ["0"])[0] or 0)
                    hit = next((x for x in st.project.versions if x["v"] == v), None)
                    extra = q.get("p", [""])[0]
                    if hit and extra:
                        name = (hit.get("extra") or {}).get(extra)
                        target = st.project.pdir / name if name else None
                    else:
                        target = st.project.pdir / hit["file"] if hit else st.project.output_path(False)
                    return self._file(target, download="download" in q)
                if path.startswith("/media/review/"):
                    name = Path(path.rsplit("/", 1)[1]).name
                    if not re.fullmatch(r"(검수표_v\d+\.html|업로드문구_v\d+\.txt)", name):
                        return self._error("없는 경로", HTTPStatus.NOT_FOUND)
                    return self._file(st.project.pdir / name, download="download" in q)
                if path == "/api/history":
                    return self._json({"history": st.project.history()})
                if path.startswith("/api/history/"):
                    return self._json({"plan": st.project.load_history(path.rsplit("/", 1)[1]).model_dump()})
                if path == "/api/voices/stock":
                    return self._json({"voices": st.stock_voices()})
                if path.startswith("/media/narration/"):
                    name = Path(path.rsplit("/", 1)[1]).name
                    return self._file(st.project.pdir / "narration" / name)
                if path.startswith("/media/asset/"):
                    d = st._path("assets")
                    name = Path(path.rsplit("/", 1)[1]).name  # 경로 탈출 방지
                    return self._file(d / name if d else None)
                return self._error("없는 경로", HTTPStatus.NOT_FOUND)
            except voice.VoiceError as exc:
                return self._error(str(exc))
            except (IndexError, ValueError):
                return self._error("잘못된 요청")

        def do_POST(self):
            url = urlparse(self.path)
            path = url.path
            st = get_st()
            if st is None:
                return self._error("열린 프로젝트가 없습니다. 시작 화면에서 프로젝트를 여세요.", HTTPStatus.CONFLICT)
            try:
                if path == "/api/import":
                    q = parse_qs(url.query)
                    name = st.import_file(q.get("kind", ["media"])[0], q["name"][0], self._raw())
                    return self._json({"ok": True, "name": name, "state": st.state()})
                if path == "/api/voices/sample":
                    ext = parse_qs(url.query).get("ext", ["webm"])[0]
                    p, d = st.voices.save_sample(self._raw(), ext)
                    return self._json({"path": str(p), "duration": d})
                data = json.loads(self._raw() or b"{}")
                if path == "/api/diagnose":
                    return self._json(st.diagnosis(self._plan(data)))
                if path == "/api/save":
                    plan = self._plan(data)
                    st.project.save_plan(plan)
                    return self._json({"ok": True, "diagnosis": st.diagnosis(plan)})
                if path == "/api/style":
                    st.project.set_style(data["style"])
                    return self._json(st.state())
                if path == "/api/replan":
                    st.replan(data["style"])
                    return self._json(st.state())
                if path == "/api/autofill":
                    return self._json({"plan": st.autofill(self._plan(data)).model_dump()})
                if path == "/api/transcript":
                    st.save_transcript(int(data["index"]), data["sentences"])
                    return self._json(st.state())
                if path == "/api/render":
                    return self._json(st.start_render(self._plan(data), bool(data.get("preview")),
                                                      bool(data.get("request_review"))))
                if path == "/api/review":
                    return self._json({"review": st.project.set_review(
                        status=data.get("status"), memo=data.get("memo"), done=data.get("done"))})
                if path == "/api/reveal":
                    return self._json({"ok": reveal(st.project.pdir), "path": str(st.project.pdir)})
                if path == "/api/transcribe":
                    st.auto_captions(int(data["index"]))
                    return self._json(st.state())
                if path == "/api/settings":
                    if "elevenlabs_key" in data:
                        settings.set_key("elevenlabs", data["elevenlabs_key"])
                        st._stock = None
                    return self._json({"voice_ready": voice.engine_ready()})
                if path == "/api/voices/clone":
                    st.clone(data.get("name", ""), data.get("samples", []), data.get("consent", ""))
                    return self._json(st.state()["voice"])
                if path == "/api/voices/delete":
                    st.delete_voice(data["id"])
                    return self._json(st.state()["voice"])
                if path == "/api/voices/preview":
                    return self._json({"url": st.preview_voice(data["voice"], data.get("text", ""))})
                if path == "/api/narration":
                    return self._json({"narration": st.narration(data)})
                if path == "/api/narration/draft":
                    plan = self._plan(data)
                    _, timeline, _ = st.project.finalize(plan.model_copy(deep=True))
                    lines, engine = draft_script(plan, timeline, st.project.analyses, st.project.brief,
                                                 str(data.get("instruction", "")))
                    return self._json({"lines": [ln.model_dump() for ln in lines], "engine": engine})
                return self._error("없는 경로", HTTPStatus.NOT_FOUND)
            except ValidationError as exc:
                return self._error("편집 데이터 형식 오류: " + "; ".join(
                    f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5]))
            except voice.VoiceError as exc:
                return self._error(str(exc))
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                return self._error(f"잘못된 요청: {exc}")

    return Handler


def serve(pdir: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True,
          whisper_model: str | None = None) -> None:
    print("편집기 준비 중(첫 실행은 썸네일·프록시 생성으로 시간이 걸릴 수 있음)…", flush=True)
    st = EditorState(pdir, whisper_model)
    httpd = ThreadingHTTPServer((host, port), make_handler(st))
    url = f"http://{host}:{port}/"
    print(f"편집기: {url}  (종료: Ctrl+C)\n프로젝트: {pdir}", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
