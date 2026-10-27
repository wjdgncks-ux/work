"""디스크 정리(완성본만 남기기)와 프로젝트 통째 삭제: 지울 것만 지우고, 공유·작업 폴더 밖 파일은 건드리지 않는다."""

import json
from pathlib import Path

import pytest

from autoedit import app as appmod
from autoedit import housekeeping as hk


def put(p: Path, n: int) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * n)
    return p


def project(ws: Path, channel: str, name: str, sources: list[Path], platform: str = "reels") -> Path:
    root = ws / channel / name
    pdir = root / platform
    adirs = [root / "_analysis" / s.stem for s in sources]
    for a in adirs:
        put(a / "transcript.json", 10)
        put(a / "proxy.mp4", 3000)
    put(pdir / "work" / "pieces" / "body.mkv", 5000)
    put(pdir / "work_preview" / "a.mkv", 1000)
    put(pdir / "_sfx" / "pop.wav", 200)
    put(pdir / "preview.mp4", 800)
    put(pdir / "preview.cover.jpg", 50)
    put(pdir / "v1.mp4", 700)
    put(pdir / "v1.cover.jpg", 30)
    put(pdir / "검수표.html", 40)
    put(pdir / "narration" / "abc.wav", 90)
    (pdir / "project.json").write_text(json.dumps({
        "sources": [str(s) for s in sources], "analysis_dirs": [str(a) for a in adirs], "platform": platform,
        "profile": channel, "brief": {"style": "explainer"}, "versions": [{"file": "v1.mp4", "cover": "v1.cover.jpg"}],
    }, ensure_ascii=False), encoding="utf-8")
    return pdir


@pytest.fixture()
def ws(tmp_path):
    return tmp_path / "ws"


def test_slim_keeps_outputs_and_regenerable_inputs(ws):
    src = put(ws / hk.ORIGINALS / "a.mp4", 9000)
    pdir = project(ws, "me", "a", [src])
    u = hk.usage(ws, pdir)
    assert u["work"] == 5000 + 1000 + 200 + 800 + 50 + 3000 and u["originals"] == 9000 and u["versions"] == 1
    freed = hk.slim(ws, pdir)
    assert freed == u["work"]
    left = {p.relative_to(pdir).as_posix() for p in pdir.rglob("*") if p.is_file()}
    assert left == {"v1.mp4", "v1.cover.jpg", "검수표.html", "narration/abc.wav", "project.json"}
    assert src.exists() and (ws / "me/a/_analysis/a/transcript.json").exists()
    assert not (ws / "me/a/_analysis/a/proxy.mp4").exists()
    meta = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
    assert meta["slimmed"]["originals_removed"] is False and meta["slimmed"]["freed"] == freed
    assert hk.slim(ws, pdir) == 0  # 두 번 해도 안전


def test_slim_originals_only_unshared_inside_workspace(ws, tmp_path):
    mine = put(ws / hk.ORIGINALS / "mine.mp4", 4000)
    shared = put(ws / hk.ORIGINALS / "shared.mp4", 6000)
    outside = put(tmp_path / "내 영상" / "outside.mp4", 1000)
    pdir = project(ws, "me", "merged", [mine, shared, outside])
    project(ws, "wife", "shared", [shared])  # 다른 프로젝트가 같은 원본을 씀
    u = hk.usage(ws, pdir)
    assert u["originals"] == 4000 and u["originals_shared"] == 1 and u["originals_outside"] == 1
    hk.slim(ws, pdir, originals=True)
    assert not mine.exists() and shared.exists() and outside.exists()
    assert json.loads((pdir / "project.json").read_text(encoding="utf-8"))["slimmed"]["originals_removed"] is True


def test_delete_removes_project_but_not_shared_or_outside(ws, tmp_path):
    mine = put(ws / hk.ORIGINALS / "mine.mp4", 4000)
    shared = put(ws / hk.ORIGINALS / "shared.mp4", 6000)
    outside = put(tmp_path / "outside.mp4", 1000)
    pdir = project(ws, "me", "clip", [mine, shared, outside])
    reels_shorts = project(ws, "me", "clip", [mine, shared, outside], platform="shorts")  # 같은 영상의 다른 플랫폼
    other = project(ws, "wife", "s", [shared])
    hk.delete(ws, pdir)
    assert not pdir.exists() and reels_shorts.exists()
    assert mine.exists() and (ws / "me/clip/_analysis/mine").exists()  # 쇼츠 프로젝트가 아직 씀
    hk.delete(ws, reels_shorts)
    assert not (ws / "me" / "clip").exists() and not mine.exists()  # 마지막 프로젝트면 영상 폴더·원본까지 정리
    assert shared.exists() and outside.exists() and other.exists() and (ws / "me").is_dir()
    with pytest.raises(hk.HousekeepingError):
        hk.delete(ws, pdir)


def test_delete_can_keep_originals_and_refuses_escape(ws, tmp_path):
    src = put(ws / hk.ORIGINALS / "k.mp4", 100)
    pdir = project(ws, "me", "k", [src])
    hk.delete(ws, pdir, originals=False)
    assert src.exists() and not pdir.exists()
    evil = project(ws, "me", "evil", [src])
    meta = json.loads((evil / "project.json").read_text(encoding="utf-8"))
    put(tmp_path / "victim" / "proxy.mp4", 10)
    meta["analysis_dirs"] = [str(tmp_path / "victim"), str(ws / ".." / "victim")]
    (evil / "project.json").write_text(json.dumps(meta), encoding="utf-8")
    hk.slim(ws, evil)
    hk.delete(ws, evil)
    assert (tmp_path / "victim" / "proxy.mp4").exists()  # 작업 폴더 밖 분석 경로는 무시
    with pytest.raises(hk.HousekeepingError):
        hk._remove(ws, tmp_path / "victim")
    with pytest.raises(hk.HousekeepingError):
        hk._remove(ws, ws)


def test_app_endpoints_guard_and_block_open(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOEDIT_HOME", str(tmp_path / "home"))
    ws = tmp_path / "ws"
    monkeypatch.setattr(appmod.settings, "workspace", lambda: ws)
    a = project(ws, "me", "a", [put(ws / hk.ORIGINALS / "a.mp4", 500)])
    b = project(ws, "me", "b", [put(ws / hk.ORIGINALS / "b.mp4", 500)])
    st = appmod.AppState()
    ida, idb = (p.relative_to(ws).as_posix() for p in (a, b))
    u = st.usage(ida)
    assert u["originals_text"] == "500B" and u["work_text"].endswith("KB")
    st.job = {"state": "running"}
    with pytest.raises(ValueError, match="자동 편집"):
        st.delete(ida, True)
    st.job = {"state": "idle"}
    assert st.slim(ida, True)["freed"] > 500
    item = next(p for p in st.projects() if p["id"] == ida)
    assert item["slimmed"] and item["originals_removed"]
    with pytest.raises(ValueError, match="완성본만"):
        st.open(ida)
    st.set_archived(idb, True)
    r = st.slim_archived(False)
    assert r["projects"] == 1 and (ws / hk.ORIGINALS / "b.mp4").exists()
    st.delete(idb, True)
    assert [p["id"] for p in st.projects()] == [ida]
    with pytest.raises(ValueError):
        st.usage(idb)
