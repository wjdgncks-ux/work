"""디스크 정리·프로젝트 삭제.

- 정리(완성본만 남기기): 렌더링 중간 파일(work*, preview), 편집기 재생용 사본(proxy.mp4)을 지운다. 완성본·검수표·업로드 문구·
  기획서·대사(transcript)는 남긴다. 중간 파일은 다시 편집하거나 내보낼 때 새로 만들어진다.
  원본 영상까지 지우면 공간은 가장 많이 비지만 그 프로젝트는 다시 편집할 수 없다(완성본만 남음).
- 삭제: 프로젝트 폴더와 그 프로젝트만 쓰던 분석 폴더를 통째로 지운다.
- 원본 영상은 작업 폴더의 '원본영상' 안에 있는 것(시작 화면에서 올린 사본)만, 다른 프로젝트가 쓰지 않을 때만 지운다.
  명령행으로 다른 곳의 영상을 직접 편집한 경우 그 원본은 절대 건드리지 않는다.
- 모든 삭제는 작업 폴더 안의 경로인지 확인한 뒤에만 한다.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

ORIGINALS = "원본영상"


class HousekeepingError(ValueError):
    pass


def size_of(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return 0


def _inside(ws: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(ws.resolve())
        return True
    except ValueError:
        return False


def _meta(pdir: Path) -> dict:
    return json.loads((pdir / "project.json").read_text(encoding="utf-8"))


def all_projects(ws: Path) -> list[Path]:
    out = []
    for pj in list(ws.glob("*/*/*/project.json")) + list(ws.glob("*/*/project.json")):
        if not ({"_analysis", ORIGINALS, "레퍼런스"} & set(pj.parent.parts)):
            out.append(pj.parent)
    return out


def _used_by_others(ws: Path, pdir: Path) -> tuple[set[str], set[str]]:
    """다른 프로젝트가 쓰는 (원본 경로, 분석 폴더) 집합."""
    sources, adirs = set(), set()
    me = pdir.resolve()
    for other in all_projects(ws):
        if other.resolve() == me:
            continue
        try:
            m = _meta(other)
        except (OSError, ValueError):
            continue
        sources |= {str(Path(s).resolve()) for s in m.get("sources", [])}
        adirs |= {str(Path(a).resolve()) for a in m.get("analysis_dirs", [])}
    return sources, adirs


def _parts(ws: Path, pdir: Path) -> dict[str, list[Path]]:
    meta = _meta(pdir)
    work = [p for p in pdir.iterdir() if p.is_dir() and (p.name.startswith("work") or p.name == "_sfx")]
    work += [p for p in (pdir / "preview.mp4", pdir / "preview.cover.jpg") if p.exists()]
    adirs = [Path(a) for a in meta.get("analysis_dirs", []) if _inside(ws, Path(a))]
    proxies = [a / "proxy.mp4" for a in adirs if (a / "proxy.mp4").exists()]
    originals = [Path(s) for s in meta.get("sources", [])
                 if Path(s).exists() and _inside(ws / ORIGINALS, Path(s))]
    return {"work": work, "proxies": proxies, "analysis": adirs, "originals": originals}


def usage(ws: Path, pdir: Path) -> dict:
    """정리·삭제 창에 보여 줄 용량(바이트)과 원본 공유 여부."""
    parts = _parts(ws, pdir)
    shared_src, _ = _used_by_others(ws, pdir)
    own_originals = [p for p in parts["originals"] if str(p.resolve()) not in shared_src]
    meta = _meta(pdir)
    outputs = size_of(pdir) - sum(size_of(p) for p in parts["work"])
    return {
        "work": sum(size_of(p) for p in parts["work"]) + sum(size_of(p) for p in parts["proxies"]),
        "outputs": max(outputs, 0),
        "originals": sum(size_of(p) for p in own_originals),
        "originals_shared": len(parts["originals"]) - len(own_originals),
        "originals_outside": len(meta.get("sources", [])) - len(parts["originals"]),
        "versions": len(meta.get("versions") or []),
        "slimmed": meta.get("slimmed"),
    }


def _remove(ws: Path, p: Path) -> int:
    if not _inside(ws, p) or p.resolve() == ws.resolve():
        raise HousekeepingError(f"작업 폴더 밖이라 지우지 않습니다: {p}")
    n = size_of(p)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        p.unlink(missing_ok=True)
    return n


def slim(ws: Path, pdir: Path, *, originals: bool = False) -> int:
    """완성본만 남기기. 지운 바이트 수를 돌려준다."""
    parts = _parts(ws, pdir)
    freed = sum(_remove(ws, p) for p in parts["work"] + parts["proxies"])
    removed_src = []
    if originals:
        shared_src, _ = _used_by_others(ws, pdir)
        for src in parts["originals"]:
            if str(src.resolve()) not in shared_src:
                freed += _remove(ws, src)
                removed_src.append(src.name)
    meta = _meta(pdir)
    prev = meta.get("slimmed") or {}
    meta["slimmed"] = {"at": time.strftime("%Y-%m-%d %H:%M"), "freed": freed + int(prev.get("freed", 0)),
                       "originals_removed": bool(prev.get("originals_removed")) or bool(removed_src)}
    pj = pdir / "project.json"
    st = pj.stat()
    pj.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.utime(pj, ns=(st.st_atime_ns, st.st_mtime_ns))  # 정리는 편집이 아니다: '최근 수정'·자동 정리 기준 시각을 바꾸지 않음
    return freed


def auto_candidates(projects: list[dict], *, days: float, now: float, skip: set[str] = frozenset()) -> list[dict]:
    """자동 정리 대상: '업로드 완료'이거나 보관한 프로젝트 중 마지막 수정 뒤 days일이 지난 것.
    원본은 지우지 않으므로(다시 편집 가능) 대상이 틀려도 잃는 것은 다시 만들 수 있는 파일뿐이다."""
    out = []
    for p in projects:
        if p["id"] in skip or p.get("originals_removed"):
            continue
        if not (p.get("status") == "업로드 완료" or p.get("archived")):
            continue
        if now - p["updated"] >= days * 86400:
            out.append(p)
    return out


def delete(ws: Path, pdir: Path, *, originals: bool = True) -> int:
    """프로젝트를 통째로 지운다(되돌릴 수 없음). 다른 프로젝트가 쓰는 분석·원본은 남긴다."""
    if not (pdir / "project.json").exists():
        raise HousekeepingError("프로젝트를 찾지 못했습니다")
    parts = _parts(ws, pdir)
    shared_src, shared_adirs = _used_by_others(ws, pdir)
    freed = 0
    for a in parts["analysis"]:
        if str(a.resolve()) not in shared_adirs:
            freed += _remove(ws, a)
    if originals:
        for src in parts["originals"]:
            if str(src.resolve()) not in shared_src:
                freed += _remove(ws, src)
    freed += _remove(ws, pdir)
    # 비게 된 상위 폴더(영상 이름 폴더, 그 안의 _analysis) 정리 — 채널 폴더와 작업 폴더 자체는 남긴다
    for d in (pdir.parent / "_analysis", pdir.parent):
        if d.is_dir() and _inside(ws, d) and d.resolve() != ws.resolve() and not any(d.iterdir()):
            d.rmdir()
    return freed


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit in ("B", "KB") else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"
