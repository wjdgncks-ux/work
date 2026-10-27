"""시작 화면 프로젝트 목록(많아져도 빠르게·찾기·보관)과 편집 요청(참고사항) 전달."""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from autoedit import app as appmod  # noqa: E402
from autoedit.plan import Brief  # noqa: E402


def fake_project(ws: Path, channel: str, name: str, **meta) -> Path:
    pdir = ws / channel / name / "reels"
    pdir.mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "sources": [str(ws / "원본영상" / f"{name}.mp4")], "platform": "reels", "profile": channel,
        "brief": {"style": "explainer", "topic": f"{name} 주제", **meta.pop("brief", {})}, **meta,
    }, ensure_ascii=False), encoding="utf-8")
    return pdir


def test_many_projects_listed_fast_with_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOEDIT_HOME", str(tmp_path / "home"))
    ws = tmp_path / "ws"
    monkeypatch.setattr(appmod.settings, "workspace", lambda: ws)
    for i in range(120):
        fake_project(ws, ["company", "me", "wife"][i % 3], f"영상{i:03d}", brief={"notes": "첫 장면은 완성품부터" if i == 7 else ""})
    (ws / "레퍼런스" / "x" / "_analysis").mkdir(parents=True)
    st = appmod.AppState()
    t0 = time.perf_counter()
    items = st.projects()
    first = time.perf_counter() - t0
    assert len(items) == 120 and items[0]["topic"].endswith("주제") and items[0]["archived"] is False
    assert next(p for p in items if p["name"] == "영상007")["notes"] == "첫 장면은 완성품부터"
    t0 = time.perf_counter()
    again = st.projects()
    assert len(again) == 120 and time.perf_counter() - t0 <= first + 0.05  # 바뀐 게 없으면 다시 읽지 않음
    pid = next(p["id"] for p in items if p["name"] == "영상010")
    st.set_archived(pid, True)
    os.utime(ws / pid / "project.json", (time.time() + 5, time.time() + 5))  # 같은 초에 바뀌어도 반영되게
    assert next(p for p in st.projects() if p["id"] == pid)["archived"] is True
    st.set_archived(pid, False)
    os.utime(ws / pid / "project.json", (time.time() + 10, time.time() + 10))
    assert next(p for p in st.projects() if p["id"] == pid)["archived"] is False
    import shutil
    shutil.rmtree((ws / pid).parent)  # 지운 프로젝트는 목록·캐시에서 빠짐
    assert len(st.projects()) == 119 and len(st._plist) == 119


def test_notes_reach_brief_and_prompt(tmp_path):
    from autoedit.pipeline import RunOptions, resolve
    from autoedit.planner_llm import SYSTEM
    _, brief, _, _ = resolve(RunOptions(brief={"notes": "30초 안으로\n가격 이야기는 빼기"}))
    assert brief.notes.startswith("30초")
    assert "brief.notes" in SYSTEM and "[요청 미반영]" in SYSTEM
    assert Brief(notes="x").model_dump()["notes"] == "x"
