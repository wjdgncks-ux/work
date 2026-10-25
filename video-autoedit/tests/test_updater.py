"""업데이트: 가짜 GitHub(raw·codeload와 같은 경로)와 임시 설치 폴더로 확인 → 내려받기 → 적용 → 되돌리기를 검사한다."""

import io
import json
import shutil
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from autoedit import launcher, updater

REPO = Path(__file__).resolve().parent.parent
SRC = {"repo": "acme/work", "branch": "release/main", "subdir": "video-autoedit"}


def make_install(dest: Path, version: str = "1.1.0") -> Path:
    """배포 ZIP과 같은 모양의 설치 폴더 + 사용자 자료·.venv."""
    # 추적 중인 파일 + 아직 커밋하지 않은 새 파일(.gitignore 제외) = 배포 ZIP에 들어갈 파일
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=REPO, capture_output=True, text=True,
                           check=True).stdout.splitlines()
    for rel in files:
        src = REPO / rel
        if src.is_file():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)
    m = json.loads((dest / "version.json").read_text(encoding="utf-8"))
    (dest / "version.json").write_text(json.dumps({**m, "version": version}), encoding="utf-8")
    (dest / "assets" / "company").mkdir(parents=True, exist_ok=True)
    (dest / "assets" / "company" / "logo.png").write_bytes(b"USER-LOGO")
    (dest / ".venv").mkdir(exist_ok=True)
    (dest / ".venv" / "marker").write_text("venv")
    return dest


def make_release(tmp: Path, version: str, *, requirements: str | None = None, broken: bool = False) -> bytes:
    """GitHub 브랜치 ZIP 모양: <저장소-브랜치>/video-autoedit/..."""
    pkg = make_install(tmp / f"pkg-{version}", version)
    shutil.rmtree(pkg / ".venv")
    shutil.rmtree(pkg / "assets" / "company")
    (pkg / "autoedit" / "new_feature.py").write_text("VALUE = 1\n")
    (pkg / "profiles" / "company.json").write_text(
        (pkg / "profiles" / "company.json").read_text(encoding="utf-8").replace("{", '{"_updated": true, ', 1), encoding="utf-8")
    if requirements is not None:
        (pkg / "requirements.txt").write_text(requirements)
    if broken:
        (pkg / "autoedit" / "app.py").write_text("raise SystemExit(3)\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in sorted(pkg.rglob("*")):
            if p.is_file():
                zf.write(p, "work-release-main/video-autoedit/" + p.relative_to(pkg).as_posix())
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOEDIT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AUTOEDIT_GITHUB_TOKEN", raising=False)
    served = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = served.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.end_headers()
            self.wfile.write(body or b"Not Found")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setattr(updater, "RAW", base + "/raw")
    monkeypatch.setattr(updater, "CODELOAD", base + "/codeload")
    root = make_install(tmp_path / "app")
    monkeypatch.setattr(updater, "ROOT", root)
    updater.settings.put(update_source=SRC)
    updater._cache.clear()

    def publish(version, **kw):
        zip_bytes = make_release(tmp_path, version, **kw)
        notes = {"version": version, "date": "2026-11-01", "notes": ["새 기능"]}
        served["/raw/acme/work/release/main/video-autoedit/version.json"] = json.dumps(notes).encode()
        served["/codeload/acme/work/zip/refs/heads/release/main"] = zip_bytes
        return zip_bytes
    yield root, publish
    httpd.shutdown()


def test_check_download_apply_rollback(env, tmp_path):
    root, publish = env
    before = {p: (root / p).read_bytes() for p in ("autoedit/app.py", "profiles/company.json", "version.json")}
    assert updater.check(force=True)["available"] is False  # 아직 아무것도 안 올라옴 → 확인 실패지만 멈추지 않음
    publish("1.2.0")
    c = updater.check(force=True)
    assert c["available"] is True and c["latest"] == "1.2.0" and c["notes"] == ["새 기능"] and c["current"] == "1.1.0"

    pkg = updater.extract(updater.download(updater.source()), updater.updates_dir() / "staging")
    info = updater.apply(pkg, root)
    assert info["to"] == "1.2.0" and info["added"] >= 1 and info["pip"] is False
    assert updater.current(root)["version"] == "1.2.0" and (root / "autoedit" / "new_feature.py").exists()
    assert '"_updated": true' in (root / "profiles" / "company.json").read_text(encoding="utf-8")
    # 사용자 자료·가상환경은 그대로
    assert (root / "assets" / "company" / "logo.png").read_bytes() == b"USER-LOGO"
    assert (root / ".venv" / "marker").read_text() == "venv"
    assert json.loads((updater.updates_dir() / "pending.json").read_text())["to"] == "1.2.0"
    with pytest.raises(updater.UpdateError, match="같은 버전"):
        updater.apply(pkg, root)  # 같은 버전 다시 적용 거부

    back = updater.rollback(root)
    assert back == {"version": "1.1.0", "undone": "1.2.0"}
    assert not (root / "autoedit" / "new_feature.py").exists()
    assert all((root / p).read_bytes() == b for p, b in before.items())
    with pytest.raises(updater.UpdateError, match="되돌릴"):
        updater.rollback(root)


def test_rejects_bad_files(env, tmp_path):
    root, _ = env
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../outside.txt", "x")
    with pytest.raises(updater.UpdateError, match="허용되지 않는 경로"):
        updater.extract(evil, tmp_path / "x")
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("something/readme.txt", "x")
    with pytest.raises(updater.UpdateError, match="업데이트 파일이 아닙니다"):
        updater.extract(other, tmp_path / "y")
    (tmp_path / "notzip.zip").write_bytes(b"hello")
    with pytest.raises(updater.UpdateError, match="ZIP"):
        updater.extract(tmp_path / "notzip.zip", tmp_path / "z")
    assert updater.dev_checkout(REPO) is True  # 개발 폴더(git)에서는 앱 업데이트 거부
    with pytest.raises(updater.UpdateError, match="개발용"):
        updater.apply(root, REPO)


def test_requirements_change_installs_on_restart(env, tmp_path, monkeypatch):
    root, publish = env
    publish("1.3.0", requirements="pydantic>=2\nnumpy\nnewdep==1.0\n")
    pkg = updater.extract(updater.download(updater.source()), updater.updates_dir() / "staging")
    assert updater.apply(pkg, root)["pip"] is True
    calls, codes = [], iter([updater.RESTART_CODE, 0])
    monkeypatch.setattr(updater, "pip_install", lambda r: (calls.append(r), (True, ""))[1])
    runs = []
    assert launcher.supervise(["--port", "9"], run=lambda cmd, **kw: (runs.append(cmd), next(codes))[1], log=lambda m: None) == 0
    assert calls == [root] and "--no-browser" in runs[1] and runs[0][1:4] == ["-m", "autoedit", "app"]
    assert not (updater.updates_dir() / "pending.json").exists()


def test_new_version_that_fails_to_start_is_rolled_back(env, monkeypatch):
    root, publish = env
    publish("1.4.0", broken=True)
    pkg = updater.extract(updater.download(updater.source()), updater.updates_dir() / "staging")
    updater.apply(pkg, root)
    assert updater.current(root)["version"] == "1.4.0"
    codes = iter([updater.RESTART_CODE, 1, 0])  # 업데이트로 다시 시작 → 새 버전 즉시 실패 → 되돌린 뒤 정상
    assert launcher.supervise([], run=lambda cmd, **kw: next(codes), log=lambda m: None) == 0
    assert updater.current(root)["version"] == "1.1.0"
    assert updater.last_result()["state"] == "rolled_back"


def test_app_update_endpoints(env, tmp_path, monkeypatch):
    import time
    import urllib.request

    from autoedit import app as appmod
    root, publish = env
    publish("1.2.0")
    restarts = []
    monkeypatch.setattr(appmod, "request_restart", lambda: restarts.append(1))
    monkeypatch.setattr(updater.settings, "workspace", lambda: tmp_path / "ws")
    st = appmod.AppState()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), appmod.make_app_handler(st))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data)) as r:
            return json.loads(r.read())

    def wait():
        for _ in range(100):
            s = call("/app/api/update")
            if s["job"]["state"] not in ("running",):
                return s
            time.sleep(0.1)
        raise AssertionError("업데이트가 끝나지 않음")

    try:
        s = call("/app/api/update")
        assert s["version"] == "1.1.0" and s["check"]["available"] and s["dev"] is False and s["backup"] is None
        call("/app/api/update/start", {})
        s = wait()
        assert s["job"]["state"] == "restarting" and s["job"]["to"] == "1.2.0" and restarts == [1]
        assert updater.current(root)["version"] == "1.2.0" and s["backup"]["from"] == "1.1.0"
        st.update_job = {"state": "idle"}
        s = call("/app/api/update/rollback", {})
        assert s["job"]["rollback"] and updater.current(root)["version"] == "1.1.0" and len(restarts) == 2
        # 담당자가 보낸 ZIP 파일로 업데이트
        st.update_job = {"state": "idle"}
        call("/app/api/update/file", raw=make_release(tmp_path, "1.5.0"))
        assert wait()["job"]["to"] == "1.5.0" and updater.current(root)["version"] == "1.5.0"
        # 내보내기 중에는 업데이트를 막는다
        st.update_job, st.job = {"state": "idle"}, {"state": "running"}
        with pytest.raises(urllib.error.HTTPError) as ei:
            call("/app/api/update/start", {})
        assert "끝난 뒤" in json.loads(ei.value.read())["error"]
    finally:
        httpd.shutdown()


def test_entry_point_routes_app_to_launcher(monkeypatch):
    """'python -m autoedit app'은 실행기로, '--child'·다른 명령은 기존 명령행으로 간다(무거운 모듈을 쥐지 않는지)."""
    code = ("import sys, runpy; from autoedit import launcher; "
            "launcher.supervise = lambda a: print('SUP', a, 'numpy' in sys.modules) or 0; "
            "sys.argv = ['autoedit', 'app', '--port', '9']; runpy.run_module('autoedit', run_name='__main__')")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True)
    assert "SUP ['--port', '9'] False" in out.stdout, out.stderr


def test_real_processes_update_and_restart(tmp_path):
    """실제 실행기·앱 프로세스: 파일로 업데이트 → 앱 종료(75) → 실행기가 새 버전으로 같은 주소에 다시 띄움."""
    import os
    import socket
    import time
    import urllib.request

    root = make_install(tmp_path / "app")
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"workspace": str(tmp_path / "ws"),
                                                  "update_source": {"repo": "acme/none", "branch": "x", "subdir": ""}}))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "AUTOEDIT_HOME": str(home)}
    proc = subprocess.Popen([sys.executable, "-m", "autoedit", "app", "--no-browser", "--port", str(port)], cwd=root, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"

    def state():
        with urllib.request.urlopen(base + "/app/api/update", timeout=15) as r:
            return json.loads(r.read())

    def until(fn, secs=60):
        t0 = time.time()
        while time.time() - t0 < secs:
            try:
                v = fn()
                if v:
                    return v
            except OSError:
                pass
            time.sleep(0.3)
        raise AssertionError("시간 초과")

    try:
        s = until(state)
        assert s["version"] == "1.1.0" and s["supervised"] is True
        req = urllib.request.Request(base + "/app/api/update/file", data=make_release(tmp_path, "1.2.0"))
        urllib.request.urlopen(req, timeout=30).read()
        # 끝나기 직전의 옛 프로세스는 'restarting'으로 답한다 → 'idle'이면 새로 뜬 프로세스
        s = until(lambda: (st := state()) and st["job"]["state"] == "idle" and st)
        assert s["version"] == "1.2.0" and s["backup"]["from"] == "1.1.0"
        assert proc.poll() is None  # 실행기는 계속 살아 있음
    finally:
        proc.terminate()
        out = proc.communicate(timeout=10)[0]
    assert "다시 시작합니다" in out
