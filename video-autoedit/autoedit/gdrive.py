"""구글 드라이브 링크로 영상 가져오기(폴더 링크·파일 링크).

- 계정 연결 없이 '링크가 있는 모든 사용자' 공유 링크만 쓴다(gdown, MIT). 비공개 파일은 받을 수 없다.
- 폴더 안(하위 폴더 포함)의 영상만 받고 문서·사진은 건너뛴다.
- 받은 영상은 작업 폴더/원본영상에 둔다. 같은 드라이브 파일을 다시 가져오면 새로 받지 않는다(.drive.json).
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
INDEX = ".drive.json"
PARTIAL = ".drive-partial"


class DriveError(ValueError):
    pass


@dataclass
class DriveItem:
    id: str
    name: str          # 파일 이름
    path: str          # 폴더 안 위치(하위 폴더/이름)


def parse_link(url: str) -> tuple[str, str]:
    """('folder' | 'file', id). 드라이브 링크가 아니면 DriveError."""
    u = (url or "").strip()
    if not re.match(r"^https?://(drive|docs)\.google\.com/", u):
        raise DriveError("구글 드라이브 링크가 아닙니다(drive.google.com/… 로 시작하는 공유 링크를 붙여 넣으세요)")
    m = re.search(r"/folders/([\w-]{10,})", u)
    if m:
        return "folder", m.group(1)
    m = re.search(r"/file/d/([\w-]{10,})", u) or re.search(r"[?&]id=([\w-]{10,})", u)
    if m:
        return "file", m.group(1)
    raise DriveError("링크에서 폴더·파일 ID를 찾지 못했습니다. 드라이브에서 '공유 → 링크 복사'한 주소를 그대로 붙여 넣으세요")


def explain(exc: Exception) -> str:
    """gdown·네트워크 오류를 사용자가 할 일로 바꿔 말한다."""
    msg = str(exc)
    low = msg.lower()
    if "too many users" in low or "quota" in low:
        return ("구글 드라이브 다운로드 한도에 걸렸습니다. 몇 시간 뒤 다시 시도하거나, "
                "파일을 '사본 만들기'로 내 드라이브에 복사한 뒤 그 링크를 넣으세요")
    if ("permission" in low or "anyone with the link" in low or "cannot retrieve the public link" in low
            or "status code 404" in low or "status code 403" in low or "status code 401" in low):
        return "공유 설정을 '링크가 있는 모든 사용자(뷰어)'로 바꿔 주세요. 비공개 파일·폴더는 받을 수 없습니다"
    names = {c.__name__ for c in type(exc).__mro__}
    if names & {"ConnectionError", "Timeout", "SSLError", "ProxyError"}:  # requests 네트워크 오류 계열
        return "구글 드라이브에 연결하지 못했습니다. 인터넷 연결(회사 방화벽)을 확인하세요"
    return f"구글 드라이브에서 받지 못했습니다: {msg.splitlines()[0][:200] if msg else type(exc).__name__}"


def _gdown():
    try:
        import gdown
    except ImportError as exc:  # 구성요소 설치 전
        raise DriveError("구글 드라이브 구성요소(gdown)가 없습니다. 프로그램을 업데이트하거나 설치 스크립트를 다시 실행하세요") from exc
    return gdown


def list_videos(url: str) -> tuple[list[DriveItem], int]:
    """링크 안의 영상 목록과 건너뛴(영상 아닌) 파일 수."""
    kind, fid = parse_link(url)
    gdown = _gdown()
    try:
        if kind == "folder":
            entries = gdown.download_folder(id=fid, skip_download=True, quiet=True, use_cookies=False)
        else:
            entries = [gdown.download(id=fid, skip_download=True, quiet=True, use_cookies=False)]
    except DriveError:
        raise
    except Exception as exc:
        raise DriveError(explain(exc)) from exc
    items, skipped = [], 0
    for e in entries or []:
        name = Path(e.path).name
        if Path(name).suffix.lower() in VIDEO_EXT:
            items.append(DriveItem(id=e.id, name=name, path=e.path))
        else:
            skipped += 1
    if not items:
        raise DriveError("링크 안에 영상 파일이 없습니다" + (f"(영상이 아닌 파일 {skipped}개는 건너뜀)" if skipped else ""))
    return items, skipped


def _index(dest: Path) -> dict:
    try:
        return json.loads((dest / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _free_name(dest: Path, name: str) -> Path:
    p = dest / name
    k = 2
    while p.exists():
        p = dest / f"{Path(name).stem} ({k}){Path(name).suffix}"
        k += 1
    return p


def fetch(item: DriveItem, dest: Path, *, progress: Callable[[float], None] = lambda f: None,
          cancel: threading.Event | None = None) -> tuple[Path, bool]:
    """영상 하나를 dest에 받는다. (경로, 이미 받아 둔 것을 재사용했는지)."""
    dest.mkdir(parents=True, exist_ok=True)
    idx = _index(dest)
    known = idx.get(item.id)
    if known and (dest / known).is_file() and (dest / known).stat().st_size > 0:
        progress(1.0)
        return dest / known, True
    part_dir = dest / PARTIAL
    part_dir.mkdir(exist_ok=True)
    part = part_dir / f"{item.id}.part"
    gdown = _gdown()

    def cb(done: int, total: int | None) -> None:
        if total:
            progress(min(done / total, 1.0))

    try:
        gdown.download(id=item.id, output=str(part), quiet=True, use_cookies=False, progress=cb, cancel=cancel)
    except Exception as exc:
        part.unlink(missing_ok=True)
        if type(exc).__name__ == "DownloadCancelled":
            raise DriveError("취소했습니다") from exc
        raise DriveError(explain(exc)) from exc
    if not part.is_file() or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        raise DriveError("빈 파일을 받았습니다. 공유 설정을 확인하세요")
    final = _free_name(dest, item.name)
    part.replace(final)
    idx = _index(dest)  # 동시에 받은 다른 파일 기록을 잃지 않게 다시 읽는다
    idx[item.id] = final.name
    (dest / INDEX).write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        part_dir.rmdir()
    except OSError:
        pass
    return final, False
