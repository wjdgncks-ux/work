"""프로그램 업데이트: 새 버전 확인 → 내려받기 → 검증 → 백업 후 교체 → 다시 시작(필요하면 구성요소 설치).

- 출처: GitHub 저장소(공개면 키 없이, 비공개면 설정의 GitHub 키로) 또는 직접 받은 ZIP 파일.
- 새 패키지에 들어 있는 파일만 덮어쓴다. .venv·사용자 자료(assets 안 파일)·작업 폴더·설정(~/.autoedit)은 건드리지 않는다.
- 바뀌는 파일은 ~/.autoedit/updates/backup 에 먼저 복사해 두고, '되돌리기'로 복원한다(직전 1회분).
- 구성요소(requirements.txt)가 바뀌면 실행 중 교체가 막히는 윈도우 특성 때문에 다시 시작할 때 설치한다(launcher.py).
무거운 모듈(numpy 등)을 불러오지 않는다: 실행기(launcher)가 이 모듈만으로 되돌리기를 할 수 있어야 한다.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from . import settings

ROOT = Path(__file__).resolve().parent.parent
RAW = "https://raw.githubusercontent.com"
CODELOAD = "https://codeload.github.com"
API = "https://api.github.com"
DEFAULT_SOURCE = {"repo": "wjdgncks-ux/work", "branch": "claude/kind-pascal-w0102p", "subdir": "video-autoedit"}
NEVER_TOUCH = {".venv", ".git", "out", "e2e-report", "node_modules", "__pycache__"}
RESTART_CODE = 75
CHECK_EVERY = 3600


class UpdateError(RuntimeError):
    pass


def updates_dir() -> Path:
    d = settings.home() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_manifest(root: Path) -> dict:
    try:
        return json.loads((root / "version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": "0.0.0"}


def current(root: Path | None = None) -> dict:
    return read_manifest(root or ROOT)


def vkey(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in str(v).split("."))


def source(root: Path | None = None) -> dict:
    """업데이트 출처: 설정 > 지금 버전의 version.json 'update' > 기본값."""
    return {**DEFAULT_SOURCE, **(current(root).get("update") or {}), **(settings.get("update_source") or {})}


def dev_checkout(root: Path) -> bool:
    return any((p / ".git").exists() for p in (root, *root.parents))


# ---------------- 내려받기 ----------------
def _get(url: str, *, token: str = "", accept: str = "", timeout: float = 20, progress=None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "autoedit-updater"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if accept:
        req.add_header("Accept", accept)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            total = int(r.headers.get("Content-Length") or 0)
            chunks, got = [], 0
            while chunk := r.read(1 << 16):
                chunks.append(chunk)
                got += len(chunk)
                if progress and total:
                    progress(got / total)
            return b"".join(chunks)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise UpdateError("업데이트 서버에서 파일을 찾지 못했습니다. 저장소가 비공개라면 설정에 GitHub 키를 넣으세요"
                              f" (HTTP {exc.code})") from exc
        raise UpdateError(f"업데이트 서버 오류 HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"인터넷에 연결할 수 없습니다: {getattr(exc, 'reason', exc)}") from exc


def fetch_manifest(src: dict) -> dict:
    path = f"{src['subdir'].strip('/')}/version.json".lstrip("/")
    token = settings.get_key("github")
    if token:
        url = f"{API}/repos/{src['repo']}/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(src['branch'], safe='')}"
        raw = _get(url, token=token, accept="application/vnd.github.raw", timeout=8)
    else:
        raw = _get(f"{RAW}/{src['repo']}/{urllib.parse.quote(src['branch'])}/{urllib.parse.quote(path)}", timeout=8)
    try:
        data = json.loads(raw)
        str(data["version"])
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError("업데이트 정보(version.json)를 읽지 못했습니다") from exc
    return data


_cache: dict = {}


def check(force: bool = False, root: Path | None = None) -> dict:
    """{current, latest, available, notes, date, checked, error}. 한 시간에 한 번만 실제로 확인한다."""
    cur = current(root)["version"]
    if not force and _cache and time.time() - _cache["checked"] < CHECK_EVERY:
        return {**_cache, "current": cur, "available": vkey(_cache.get("latest") or "0") > vkey(cur)}
    out = {"current": cur, "checked": time.time(), "latest": None, "notes": [], "date": "", "error": ""}
    try:
        m = fetch_manifest(source(root))
        out.update(latest=str(m["version"]), notes=list(m.get("notes") or [])[:12], date=str(m.get("date") or ""))
    except UpdateError as exc:
        out["error"] = str(exc)
    out["available"] = bool(out["latest"]) and vkey(out["latest"]) > vkey(cur)
    _cache.clear()
    _cache.update(out)
    return out


def download(src: dict, progress=None) -> Path:
    token = settings.get_key("github")
    if token:
        url = f"{API}/repos/{src['repo']}/zipball/{urllib.parse.quote(src['branch'], safe='')}"
    else:
        url = f"{CODELOAD}/{src['repo']}/zip/refs/heads/{urllib.parse.quote(src['branch'])}"
    data = _get(url, token=token, timeout=120, progress=progress)
    out = updates_dir() / "download.zip"
    out.write_bytes(data)
    return out


# ---------------- 풀기·검증 ----------------
def extract(zip_path: Path, dest: Path) -> Path:
    """안전하게 풀고(경로 탈출 거부) 프로그램 폴더(version.json + autoedit/)를 찾아 돌려준다."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise UpdateError("ZIP 파일이 아니거나 손상되었습니다") from exc
    with zf:
        for info in zf.infolist():
            name = PurePosixPath(info.filename.replace("\\", "/"))
            if name.is_absolute() or ".." in name.parts or (name.parts and ":" in name.parts[0]):
                raise UpdateError(f"허용되지 않는 경로가 들어 있는 파일입니다: {info.filename}")
        zf.extractall(dest)
        for info in zf.infolist():  # 실행 권한 유지(.command)
            mode = (info.external_attr >> 16) & 0o777
            if mode & 0o111 and not info.is_dir():
                os.chmod(dest / info.filename, mode)
    found = sorted((p.parent for p in dest.rglob("version.json") if (p.parent / "autoedit" / "__init__.py").is_file()),
                   key=lambda p: len(p.parts))
    if not found:
        raise UpdateError("숏폼 편집기 업데이트 파일이 아닙니다(version.json·autoedit 폴더 없음)")
    return found[0]


def package_files(pkg: Path) -> list[str]:
    out = []
    for p in sorted(pkg.rglob("*")):
        rel = p.relative_to(pkg)
        if p.is_file() and not (set(rel.parts) & NEVER_TOUCH) and p.suffix != ".pyc":
            out.append(rel.as_posix())
    return out


# ---------------- 적용·되돌리기 ----------------
def apply(pkg: Path, root: Path | None = None, *, allow_same: bool = False, progress=None) -> dict:
    root = root or ROOT
    new, cur = read_manifest(pkg), current(root)
    if dev_checkout(root):
        raise UpdateError("개발용 폴더(git)에서는 앱 업데이트를 하지 않습니다. git pull을 쓰세요")
    if not allow_same and vkey(new["version"]) <= vkey(cur["version"]):
        raise UpdateError(f"이미 같은 버전이거나 더 새 버전입니다(지금 {cur['version']}, 파일 {new['version']})")
    files = package_files(pkg)
    backup = updates_dir() / "backup"
    if backup.exists():
        shutil.rmtree(backup)
    (backup / "files").mkdir(parents=True)
    replaced, added = [], []
    for i, rel in enumerate(files):
        src, dst = pkg / rel, root / rel
        data = src.read_bytes()
        if dst.exists():
            if dst.read_bytes() == data:
                continue
            keep = backup / "files" / rel
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, keep)
            replaced.append(rel)
        else:
            added.append(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".updating")
        tmp.write_bytes(data)
        shutil.copymode(src, tmp)
        os.replace(tmp, dst)
        if progress:
            progress((i + 1) / len(files))
    pip = "requirements.txt" in replaced or "requirements.txt" in added
    meta = {"from": cur["version"], "to": new["version"], "at": time.strftime("%Y-%m-%d %H:%M"),
            "replaced": replaced, "added": added, "root": str(root)}
    (backup / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    pending = {"from": cur["version"], "to": new["version"], "pip": pip, "root": str(root), "at": time.time()}
    (updates_dir() / "pending.json").write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
    _cache.clear()
    return {"from": cur["version"], "to": new["version"], "replaced": len(replaced), "added": len(added), "pip": pip}


def backup_info() -> dict | None:
    p = updates_dir() / "backup" / "meta.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def rollback(root: Path | None = None) -> dict:
    meta = backup_info()
    if not meta:
        raise UpdateError("되돌릴 이전 버전이 없습니다")
    root = root or Path(meta.get("root") or ROOT)
    backup = updates_dir() / "backup"
    for rel in meta["replaced"]:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / "files" / rel, dst)
    for rel in meta["added"]:
        (root / rel).unlink(missing_ok=True)
    shutil.rmtree(backup)
    (updates_dir() / "pending.json").unlink(missing_ok=True)
    _cache.clear()
    return {"version": meta["from"], "undone": meta["to"]}


def pip_install(root: Path) -> tuple[bool, str]:
    import subprocess
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(root / "requirements.txt")],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr)[-1500:]


def last_result() -> dict | None:
    """다시 시작한 뒤 실행기가 남긴 결과(구성요소 설치 실패·자동 되돌리기 등)."""
    p = updates_dir() / "result.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
