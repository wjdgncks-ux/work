"""'영상을 보고' 자동 편집: 장면 시트·움직임, 예능 자막, 배경음악 자동, 짧은 클립 여러 개 → 한 편."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_video  # noqa: E402

from autoedit import bgm, planner_llm, scenesheet  # noqa: E402
from autoedit.analyze import analyze  # noqa: E402
from autoedit.plan import Brief, Clip, EditPlan, Insert  # noqa: E402
from autoedit.platforms import PLATFORMS  # noqa: E402

REELS = PLATFORMS["reels"]


def clip(path: Path, seconds: int, color: str = "testsrc2") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"{color}=size=720x1280:rate=30:d={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)
    return path


def tone(path: Path, seconds: int = 20) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:a", "libmp3lame", str(path)], check=True)
    return path


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOEDIT_HOME", str(tmp_path / "home"))
    w = tmp_path / "ws"
    from autoedit import settings
    monkeypatch.setattr(settings, "workspace", lambda: w)
    return w


# ---------------- 장면 시트·움직임 ----------------
def test_scene_sheets_and_motion(tmp_path):
    a = analyze(clip(tmp_path / "cat.mp4", 30), 0, tmp_path / "w", whisper_model=None)
    assert scenesheet.step_for([a]) == 1.0
    assert scenesheet.step_for([a] * 8) == 2.5  # 240초 → 96장 안에 들게 간격을 넓힘
    sheets = scenesheet.sheets(a, 1.0)
    assert [t for t, _ in sheets] == [0, 12, 24] and all(f.exists() for _, f in sheets)
    assert scenesheet.sheets(a, 1.0) == sheets  # 캐시
    vals = scenesheet.motion(a)
    assert 110 <= len(vals) <= 125 and (tmp_path / "w" / "motion.json").exists()
    flat = [1.0] * 40
    assert scenesheet.motion_peaks(flat) == []  # 처음부터 끝까지 비슷하면 '순간' 없음
    spiky = [1.0] * 40
    spiky[8], spiky[9], spiky[30] = 9.0, 8.0, 7.0
    assert scenesheet.motion_peaks(spiky) == [2.0, 7.5]  # 붙어 있는 봉우리는 하나로


def test_request_shows_sheets_motion_and_music(tmp_path, ws):
    a = analyze(clip(tmp_path / "a.mp4", 14), 0, tmp_path / "wa", whisper_model=None)
    b = analyze(clip(tmp_path / "b.mp4", 8, "rgbtestsrc"), 1, tmp_path / "wb", whisper_model=None)
    tracks = [{"file": "신나는/x.mp3", "mood": "신나는"}]
    msgs = planner_llm.build_request([a, b], REELS, Brief(style="funny"), [], tracks)
    blocks = msgs[0]["content"]
    texts = " ".join(x.get("text", "") for x in blocks)
    assert sum(1 for x in blocks if x["type"] == "image") == 3  # 시트 2장(14초) + 1장(8초)
    assert "장면 시트: 0s부터 1초 간격" in texts and "원본 1 장면 시트" in texts
    assert json.loads(blocks[0]["text"].split("\n", 1)[1])["bgm_files"] == tracks
    for rule in ("장면 시트", "fun_text", "bgm_files"):
        assert rule in planner_llm.SYSTEM


def test_request_falls_back_to_frames(tmp_path, monkeypatch):
    a = analyze(clip(tmp_path / "a.mp4", 6), 0, tmp_path / "wa", whisper_model=None)

    def boom(*a, **k):
        raise RuntimeError("ffmpeg 없음")
    monkeypatch.setattr(scenesheet, "sheets", boom)
    msgs = planner_llm.build_request([a], REELS, Brief(), [])
    assert sum(1 for x in msgs[0]["content"] if x["type"] == "image") == len(a.frames) == 6


# ---------------- 예능 자막 ----------------
def test_fun_text_layer_survives_card_overlap():
    from autoedit.pacing import sanitize
    a = type("A", (), {"duration": 20.0, "transcript": None, "silences": [], "scene_changes": [], "peaks": []})()
    plan = EditPlan(topic="t", platform="reels", hook_text="", clips=[Clip(source=0, start=0, end=10, role="hook", reason="")],
                    emphases=[], sfx=[], cta_text="", title_candidates=[], sns_caption="", hashtags=[], sponsored=False,
                    rationale="", risk_notes=[], inserts=[
                        Insert(source=0, t=2.0, duration=2.0, kind="text_card", text="핵심", asset=""),
                        Insert(source=0, t=2.5, duration=1.5, kind="fun_text", text="(당황)", asset=""),
                        Insert(source=0, t=3.0, duration=1.5, kind="fun_text", text="겹침", asset=""),
                        Insert(source=0, t=5.0, duration=1.5, kind="fun_text", text="냥냥펀치", asset=""),
                        Insert(source=0, t=7.0, duration=1.0, kind="fun_text", text="  ", asset="")])
    sanitize(plan, [a])
    assert [(i.kind, i.text) for i in plan.inserts] == [("text_card", "핵심"), ("fun_text", "(당황)"), ("fun_text", "냥냥펀치")]


# ---------------- 배경음악 ----------------
def test_bgm_library_pick_and_resolve(ws, tmp_path):
    assert bgm.tracks(ws) == [] and (ws / "배경음악" / "여기에 음원을 넣으세요.txt").exists()
    assert bgm.pick(ws, "funny", "x") == ""
    tone(ws / "배경음악" / "잔잔한" / "calm.mp3", 2)
    tone(ws / "배경음악" / "신나는" / "party.mp3", 2)
    tone(ws / "배경음악" / "loose.mp3", 2)
    assert [t["mood"] for t in bgm.tracks(ws)] == ["", "신나는", "잔잔한"]
    assert bgm.pick(ws, "funny", "a") == bgm.pick(ws, "funny", "a") == "신나는/party.mp3"
    assert bgm.pick(ws, "explainer", "a") == "잔잔한/calm.mp3"
    assert bgm.resolve(ws, "신나는/party.mp3").name == "party.mp3"
    outside = tone(tmp_path / "secret.mp3", 1)
    assert bgm.resolve(ws, "../secret.mp3") is None and bgm.resolve(ws, str(outside)) is None
    p = type("P", (), {"bgm": "없는곡.mp3"})()
    bgm.settle(p, ws, "funny", "a", from_llm=True)
    assert p.bgm == "신나는/party.mp3"  # Claude가 없는 곡을 고르면 대신 고름
    p.bgm = ""
    bgm.settle(p, ws, "funny", "a", from_llm=True)
    assert p.bgm == ""  # Claude가 일부러 비우면(음악 빼기) 그대로
    bgm.settle(p, ws, "funny", "a", from_llm=False)
    assert p.bgm == "신나는/party.mp3"
    bgm.settle(p, ws, "funny", "a", from_llm=False, enabled=False)
    assert p.bgm == ""


# ---------------- 짧은 클립 여러 개 → 한 편(가짜 Claude 기획) ----------------
def test_highlight_from_clips_with_fun_text_and_music(ws, tmp_path, monkeypatch):
    from autoedit.pipeline import RunOptions, run_pipeline
    tone(ws / "배경음악" / "귀여운" / "cute.mp3", 30)
    a = clip(tmp_path / "고양이1.mp4", 8)
    b = clip(tmp_path / "고양이2.mp4", 6, "rgbtestsrc")
    seen = {}

    def fake(client, *, system, messages, output, effort, fallback, max_tokens=16000):
        if output is EditPlan:
            seen["images"] = sum(1 for x in messages[0]["content"] if x["type"] == "image")
            seen["bgm"] = json.loads(messages[0]["content"][0]["text"].split("\n", 1)[1])["bgm_files"]
            return EditPlan(
                topic="고양이와 장난", platform="reels", hook_text="냥냥펀치 실화?", cta_text="저장해 두고 또 보기",
                clips=[Clip(source=1, start=1.0, end=3.5, role="hook", reason="가장 강한 반응"),
                       Clip(source=0, start=2.0, end=6.0, role="body", reason="장난 시작"),
                       Clip(source=1, start=3.5, end=5.5, role="body", reason="마무리")],
                emphases=[], sfx=[], title_candidates=["a", "b", "c"], sns_caption="고양이", hashtags=["#고양이"],
                sponsored=False, rationale="", risk_notes=[], bgm="귀여운/cute.mp3",
                inserts=[Insert(source=1, t=1.2, duration=1.5, kind="fun_text", text="(당황)", asset=""),
                         Insert(source=0, t=3.0, duration=1.5, kind="fun_text", text="냥냥펀치 발사", asset="")])
        return None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setattr(planner_llm, "structured_call", fake)
    res = run_pipeline([a, b], RunOptions(out=ws, whisper=None, platform="reels", llm="on", revise=0, mode="merge",
                                          style="funny"))
    assert len(res) == 1 and res[0]["name"] == "고양이1 외 1개" and res[0]["planner"].startswith("Claude")
    assert seen["images"] == 2 and seen["bgm"] == [{"file": "귀여운/cute.mp3", "mood": "귀여운"}]
    pdir = Path(res[0]["project"])
    assert json.loads((pdir / "plan.json").read_text(encoding="utf-8"))["bgm"] == "귀여운/cute.mp3"
    ass = next(pdir.rglob("subs.ass")).read_text(encoding="utf-8")
    assert "Style: Fun" in ass and "(당황)" in ass and "냥냥펀치 발사" in ass and "\\frz" in ass
    out = res[0]["output"]
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", out],
                             capture_output=True, text=True).stdout.split()
    assert "audio" in streams  # 소리 없는 원본인데 배경음악이 깔림
    lufs = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", out, "-af", "volumedetect", "-f", "null", "-"],
                          capture_output=True, text=True).stderr
    assert "mean_volume: -inf" not in lufs


def test_app_reports_ai_and_bgm_count(ws, tmp_path, monkeypatch):
    from autoedit import app as appmod
    monkeypatch.setattr(appmod.settings, "workspace", lambda: ws)
    st = appmod.AppState()
    assert st.home()["bgm_count"] == 0
    tone(ws / "배경음악" / "a.mp3", 1)
    assert st.home()["bgm_count"] == 1
    from autoedit.project import group_name
    assert group_name(["/x/a.mp4"]) == "a" and group_name(["/x/a.mp4", "/x/b.mp4", "/x/c.mp4"]) == "a 외 2개"
