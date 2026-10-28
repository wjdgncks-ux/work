"""가짜 gdown: 구글 드라이브 없이 공유 폴더·파일 가져오기를 시험한다(실제 gdown과 같은 함수·반환 형태).

FOLDERS[폴더 id] = [(파일 id, 폴더 안 경로, 원본 파일)], FILES[파일 id] = (이름, 원본 파일).
id에 'private'가 들어가면 권한 오류, 'quota'가 들어가면 다운로드 한도 오류를 낸다.
"""

from __future__ import annotations

import collections
import shutil
import sys
import types
from pathlib import Path

GoogleDriveFileToDownload = collections.namedtuple("GoogleDriveFileToDownload", ("id", "path", "local_path"))
FOLDERS: dict[str, list[tuple[str, str, Path]]] = {}
FILES: dict[str, tuple[str, Path]] = {}
CALLS: list[str] = []


class DownloadError(Exception):
    pass


class FileURLRetrievalError(DownloadError):
    pass


class DownloadCancelled(Exception):
    pass


def _check(fid: str) -> None:
    if "private" in fid:
        raise FileURLRetrievalError("Cannot retrieve the public link of the file. You may need to change the "
                                    "permission to 'Anyone with the link', or have had many accesses.")
    if "quota" in fid:
        raise FileURLRetrievalError("Too many users have viewed or downloaded this file recently.")


def download_folder(url=None, id=None, output=None, quiet=False, skip_download=False, use_cookies=True, **kw):
    _check(id)
    if id not in FOLDERS:
        raise DownloadError(f"Failed to retrieve folder contents for folder ID: {id} (status code 404).")
    assert skip_download, "앱은 목록만 받고 파일은 하나씩 받는다"
    return [GoogleDriveFileToDownload(fid, path, None) for fid, path, _ in FOLDERS[id]]


def download(url=None, output=None, quiet=False, id=None, skip_download=False, progress=None, cancel=None,
             use_cookies=True, **kw):
    _check(id)
    src = next((s for fs in FOLDERS.values() for fid, _, s in fs if fid == id), None)
    name = next((Path(p).name for fs in FOLDERS.values() for fid, p, _ in fs if fid == id), None)
    if id in FILES:
        name, src = FILES[id]
    if src is None:
        raise FileURLRetrievalError("Cannot retrieve the public link of the file (status code 404).")
    if skip_download:
        return GoogleDriveFileToDownload(id, name, None)
    CALLS.append(id)
    data = Path(src).read_bytes()
    step = max(len(data) // 4, 1)
    with open(output, "wb") as f:
        for i in range(0, len(data), step):
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled()
            f.write(data[i:i + step])
            if progress:
                progress(min(i + step, len(data)), len(data))
    return output


def install() -> None:
    """import gdown 이 이 모듈을 가리키게 한다."""
    mod = sys.modules[__name__]
    sys.modules["gdown"] = mod
    sys.modules["gdown.exceptions"] = types.SimpleNamespace(DownloadError=DownloadError,
                                                            FileURLRetrievalError=FileURLRetrievalError,
                                                            DownloadCancelled=DownloadCancelled)


def reset() -> None:
    FOLDERS.clear()
    FILES.clear()
    CALLS.clear()


def copy_sample(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest)
    return dest
