"""시작 화면 앱: 바탕화면 아이콘으로 실행하는 단일 진입점.

  python -m autoedit app   (start-windows.bat / start-mac.command 가 이것을 실행)

- /        시작 화면: 새 프로젝트(영상 끌어다 놓기 → 채널·스타일 선택 → 자동 편집), 최근 프로젝트, 설정, 설치 점검
- /edit    CapCut 방식 편집기(열린 프로젝트 하나). 편집기 API(/api/*, /media/*)는 editor.py 그대로 사용
- 이미 실행 중이면 새로 띄우지 않고 브라우저만 연다(아이콘을 두 번 눌러도 안전)

프로젝트는 작업 폴더(기본: 문서/숏폼편집)/<채널>/<영상 이름>/<플랫폼>/ 에 쌓인다.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import traceback
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import doctor, settings, voice
from .editor import STATIC, EditorState, _whisper_available, make_handler
from .media import probe
from .osutil import reveal
from .pipeline import RunOptions, run_pipeline
from .platforms import PLATFORMS
from .profiles import list_profiles, load_profile
from .project import REVIEW_STATES
from .styles import STYLES

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
STYLE_DESC = {"explainer": "정보·노하우. 질문·숫자로 후킹, 짧은 공백까지 컷",
              "funny": "웃긴 장면. 리액션 미리보기, 웃음 타이밍 유지",
              "demo": "제품 시연·리뷰. 결과 먼저, 작업 구간 빨리감기"}


class AppState:
    def __init__(self):
        settings.export_keys_to_env()
        self.editor: EditorState | None = None
        self.job: dict = {"state": "idle"}
        self.lock = threading.Lock()
        self._checks: list[dict] | None = None

    # ---------- 경로 ----------
    @property
    def workspace(self) -> Path:
        ws = settings.workspace()
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    @property
    def originals(self) -> Path:
        d = self.workspace / "원본영상"
        d.mkdir(exist_ok=True)
        return d

    def project_dir(self, pid: str) -> Path:
        pdir = (self.workspace / pid).resolve()
        if self.workspace.resolve() not in pdir.parents or not (pdir / "project.json").exists():
            raise ValueError("없는 프로젝트입니다")
        return pdir

    def whisper_model(self) -> str | None:
        m = settings.get("whisper_model", "small")
        return None if m == "none" or not _whisper_available() else m

    # ---------- 조회 ----------
    def checks(self, refresh: bool = False) -> list[dict]:
        if self._checks is None or refresh:
            self._checks = doctor.as_dicts(doctor.run_checks(self.workspace))
        return self._checks

    def projects(self) -> list[dict]:
        ws = self.workspace
        out = []
        for pj in list(ws.glob("*/*/*/project.json")) + list(ws.glob("*/*/project.json")):
            pdir = pj.parent
            if "_analysis" in pdir.parts or "원본영상" in pdir.parts:
                continue
            try:
                meta = json.loads(pj.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            plan = pdir / "plan.json"
            updated = max(pj.stat().st_mtime, plan.stat().st_mtime if plan.exists() else 0)
            versions = meta.get("versions") or []
            cover = ""
            if versions and (pdir / versions[-1].get("cover", "")).is_file() and versions[-1].get("cover"):
                cover = versions[-1]["cover"]
            elif (pdir / "preview.cover.jpg").exists():
                cover = "preview.cover.jpg"
            name = Path(meta["sources"][0]).stem if len(meta["sources"]) == 1 else "merged"
            prof = meta.get("profile")
            out.append({
                "id": pdir.relative_to(ws).as_posix(), "name": name, "platform": meta.get("platform"),
                "platform_label": " + ".join(PLATFORMS[k].label.replace("Instagram ", "").replace("YouTube ", "")
                                             for k in (meta.get("platforms") or [meta.get("platform")]) if k in PLATFORMS),
                "profile": prof, "style": (meta.get("brief") or {}).get("style", "explainer"),
                "status": (meta.get("review") or {}).get("status", "편집 중"),
                "versions": len(versions), "latest": versions[-1] if versions else None,
                "created": meta.get("created", ""), "updated": updated,
                "updated_text": time.strftime("%m-%d %H:%M", time.localtime(updated)),
                "cover": cover, "missing_source": not all(Path(s).exists() for s in meta["sources"]),
            })
        out.sort(key=lambda p: -p["updated"])
        return out

    def home(self) -> dict:
        profiles = []
        for p in list_profiles():
            prof = load_profile(str(p))
            profiles.append({"key": prof.key, "name": prof.name, "style": prof.style, "platforms": prof.platforms,
                             "handle": prof.handle, "notes": prof.notes})
        cfg = settings.load()
        return {
            "workspace": str(self.workspace), "profiles": profiles,
            "styles": [{"key": s.key, "label": s.label, "desc": STYLE_DESC[s.key]} for s in STYLES.values()],
            "platforms": [{"key": p.key, "label": p.label} for p in PLATFORMS.values()],
            "projects": self.projects(), "review_states": list(REVIEW_STATES), "checks": self.checks(),
            "settings": {"voicestudio_url": cfg.get("voicestudio_url") or voice.VS_URL,
                         "voicestudio_model": cfg.get("voicestudio_model") or voice.DEFAULT_VS_MODEL,
                         "voice_models": [{"id": k, **v} for k, v in voice.VS_ENGINES.items()],
                         "anthropic": bool(settings.get_key("anthropic")),
                         "whisper_model": cfg.get("whisper_model", "small"), "whisper_installed": _whisper_available(),
                         "user_name": cfg.get("user_name", "")},
            "job": self.job, "editor_open": self.editor.project.pdir.relative_to(self.workspace).as_posix()
            if self.editor and self.workspace in self.editor.project.pdir.parents else None,
        }

    # ---------- 동작 ----------
    def upload(self, name: str, body: bytes) -> dict:
        name = Path(name).name
        ext = Path(name).suffix.lower()
        if ext not in VIDEO_EXT:
            raise ValueError(f"영상 파일만 올릴 수 있습니다({', '.join(sorted(VIDEO_EXT))})")
        dest = self.originals / name
        k = 2
        while dest.exists():  # 같은 이름이면 (2), (3)… — 프로젝트 이름도 같이 갈라진다
            dest = self.originals / f"{Path(name).stem} ({k}){ext}"
            k += 1
        dest.write_bytes(body)
        try:
            info = probe(dest)
        except Exception as exc:  # 영상이 아니거나 깨진 파일
            dest.unlink(missing_ok=True)
            raise ValueError(f"{name}: 영상 파일을 읽을 수 없습니다") from exc
        return {"id": dest.name, "name": dest.name, "duration": round(info.duration, 1),
                "size": f"{info.display_size[0]}x{info.display_size[1]}"}

    def create(self, d: dict) -> dict:
        with self.lock:
            if self.job.get("state") == "running":
                raise ValueError("이미 자동 편집이 진행 중입니다")
            files = [self.originals / Path(f).name for f in d.get("files", [])]
            if not files or not all(f.exists() for f in files):
                raise ValueError("올린 영상이 없습니다")
            platforms = [p for p in d.get("platforms", []) if p in PLATFORMS] or None
            opts = RunOptions(
                profile=d.get("profile") or None, style=d.get("style") or None,
                platform=",".join(platforms) if platforms else None, mode=d.get("mode", "each"),
                out=self.workspace, brief={"topic": d.get("topic", ""), "cta": d.get("cta", "")},
                sponsored=bool(d.get("sponsored")), whisper=self.whisper_model() if d.get("captions", True) else None,
                plan_only=not d.get("auto_render"),
            )
            self.job = {"state": "running", "step": "준비 중", "progress": 0.0, "started": time.time()}

        def on_progress(msg: str, frac: float) -> None:
            self.job.update(step=msg, progress=round(frac, 3))

        def work():
            try:
                results = run_pipeline(files, opts, on_progress)
                ids = [Path(r["project"]).resolve().relative_to(self.workspace.resolve()).as_posix() for r in results]
                self.job = {"state": "done", "projects": ids, "progress": 1.0,
                            "elapsed": round(time.time() - self.job["started"], 1)}
            except Exception as exc:  # 사용자에게 그대로 보여 준다
                self.job = {"state": "error", "error": str(exc)[-800:], "trace": traceback.format_exc()[-1500:]}

        threading.Thread(target=work, daemon=True).start()
        return self.job

    def open(self, pid: str) -> None:
        pdir = self.project_dir(pid)
        meta = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
        missing = [s for s in meta["sources"] if not Path(s).exists()]
        if missing:
            raise ValueError(f"원본 영상이 옮겨졌거나 지워졌습니다: {Path(missing[0]).name}")
        self.editor = EditorState(pdir, self.whisper_model(), in_app=True)

    def save_settings(self, d: dict) -> None:
        model = d.get("voicestudio_model")
        if model is not None and model not in voice.VS_ENGINES:
            raise ValueError("알 수 없는 음성 엔진")
        settings.put(voicestudio_model=model, voicestudio_url=(d.get("voicestudio_url") or "").strip() or None)
        voice._ready = (0.0, False)
        if d.get("anthropic_key") is not None and d["anthropic_key"] != "":
            settings.set_key("anthropic", d["anthropic_key"])
            settings.export_keys_to_env()
        ws = d.get("workspace")
        if ws:
            Path(ws).expanduser().mkdir(parents=True, exist_ok=True)
        settings.put(whisper_model=d.get("whisper_model"), user_name=d.get("user_name"), workspace=ws or None)
        self._checks = None

    def set_status(self, pid: str, status: str) -> None:
        if status not in REVIEW_STATES:
            raise ValueError("알 수 없는 상태")
        pj = self.project_dir(pid) / "project.json"
        meta = json.loads(pj.read_text(encoding="utf-8"))
        meta.setdefault("review", {"status": "편집 중", "memo": "", "done": []})["status"] = status
        pj.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def make_app_handler(app: AppState):
    Base = make_handler(lambda: app.editor)

    class AppHandler(Base):
        def do_GET(self):
            url = urlparse(self.path)
            path = unquote(url.path)
            q = parse_qs(url.query)
            try:
                if path in ("/", "/home"):
                    return self._file(STATIC / "home.html")
                if path == "/edit":
                    if app.editor is None:
                        self.send_response(HTTPStatus.FOUND)
                        self.send_header("Location", "/")
                        self.end_headers()
                        return
                    return self._file(STATIC / "index.html")
                if path == "/app/api/ping":
                    return self._json({"app": "autoedit"})
                if path == "/app/api/home":
                    return self._json(app.home())
                if path == "/app/api/job":
                    return self._json(app.job)
                if path == "/app/media/cover":
                    pdir = app.project_dir(q["id"][0])
                    return self._file(pdir / Path(q["file"][0]).name)
            except (KeyError, ValueError) as exc:
                return self._error(str(exc))
            return super().do_GET()

        def do_POST(self):
            path = urlparse(self.path).path
            if not path.startswith("/app/api/"):
                return super().do_POST()
            q = parse_qs(urlparse(self.path).query)
            try:
                if path == "/app/api/upload":
                    return self._json(app.upload(q["name"][0], self._raw()))
                data = json.loads(self._raw() or b"{}")
                if path == "/app/api/create":
                    return self._json(app.create(data))
                if path == "/app/api/open":
                    app.open(data["id"])
                    return self._json({"ok": True})
                if path == "/app/api/settings":
                    app.save_settings(data)
                    return self._json(app.home())
                if path == "/app/api/checks":
                    app.checks(refresh=True)
                    return self._json(app.home())
                if path == "/app/api/status":
                    app.set_status(data["id"], data["status"])
                    return self._json({"ok": True})
                if path == "/app/api/reveal":
                    target = app.project_dir(data["id"]) if data.get("id") else app.workspace
                    return self._json({"ok": reveal(target), "path": str(target)})
                return self._error("없는 경로", HTTPStatus.NOT_FOUND)
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                return self._error(str(exc))

    return AppHandler


def _running_app(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/app/api/ping", timeout=1.5) as r:
            return json.loads(r.read()).get("app") == "autoedit"
    except (OSError, ValueError):
        return False


def _free_port(start: int) -> int:
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise OSError("빈 포트를 찾지 못했습니다")


def serve_app(port: int = 8765, open_browser: bool = True, app: AppState | None = None) -> int:
    if _running_app(port):
        print(f"이미 실행 중입니다 → http://127.0.0.1:{port}/", flush=True)
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        return 0
    port = _free_port(port)
    app = app or AppState()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_app_handler(app))
    url = f"http://127.0.0.1:{port}/"
    print(f"숏폼 편집기: {url}\n작업 폴더: {app.workspace}\n이 창을 닫으면 프로그램이 종료됩니다.", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
