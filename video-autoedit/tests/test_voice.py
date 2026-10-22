import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_transcript, make_video  # noqa: E402

from autoedit import voice  # noqa: E402
from autoedit.analyze import analyze  # noqa: E402
from autoedit.narration import create_narration, draft_script, place  # noqa: E402
from autoedit.plan import Brief  # noqa: E402
from autoedit.planner_heuristic import plan_heuristic  # noqa: E402
from autoedit.platforms import PLATFORMS  # noqa: E402
from autoedit.project import finalize, render_options  # noqa: E402
from autoedit.render import render  # noqa: E402
from autoedit.styles import STYLES  # noqa: E402

REELS = PLATFORMS["reels"]


class FakeEngine:
    """ElevenLabs 대신: 글자 수에 비례한 길이의 톤 mp3 + 균등 글자 타이밍."""
    model = "fake"

    def __init__(self):
        self.calls, self.cloned = [], []

    def tts(self, text, voice_id, speed=None):
        self.calls.append((text, voice_id))
        dur = max(0.6, len(text.replace(" ", "")) / 6)
        mp3 = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=f=440:d={dur}",
                              "-f", "mp3", "-"], capture_output=True, check=True).stdout
        n = len(text)
        return mp3, {"characters": list(text),
                     "character_start_times_seconds": [dur * i / n for i in range(n)],
                     "character_end_times_seconds": [dur * (i + 1) / n for i in range(n)]}

    def clone(self, name, files, description="", remove_noise=True):
        self.cloned.append((name, [f.name for f in files]))
        return "cloned123"

    def stock_voices(self):
        return [{"voice": "elevenlabs:stock1", "name": "지수", "category": "premade", "labels": {}, "preview_url": ""}]

    def delete(self, voice_id):
        pass


@pytest.fixture()
def analysis(tmp_path):
    video = make_video(tmp_path / "clip.mp4", seconds=20)
    make_transcript(tmp_path / "work" / "transcript.json")
    return analyze(video, 0, tmp_path / "work", whisper_model=None)


def test_words_from_alignment():
    text = "안녕 하세요 여러분"
    al = {"characters": list(text), "character_start_times_seconds": [i * .1 for i in range(len(text))],
          "character_end_times_seconds": [(i + 1) * .1 for i in range(len(text))]}
    words = voice.words_from_alignment(al)
    assert [w["text"] for w in words] == ["안녕", "하세요", "여러분"]
    assert words[1]["start"] == pytest.approx(0.3) and words[2]["end"] == pytest.approx(1.0)


def test_synthesize_caches(tmp_path):
    eng = FakeEngine()
    a = voice.synthesize("오늘의 핵심 정리", "elevenlabs:v1", tmp_path, engine=eng)
    b = voice.synthesize("오늘의  핵심 정리", "elevenlabs:v1", tmp_path, engine=eng)  # 공백만 다름 → 캐시
    assert a == b and len(eng.calls) == 1
    assert (tmp_path / a["file"]).exists() and a["duration"] > 0.5 and len(a["words"]) == 3


def test_narration_captions_and_render(tmp_path, analysis):
    eng = FakeEngine()
    brief = Brief()
    plan = plan_heuristic([analysis], REELS, brief)
    _, timeline, caps0 = finalize(plan, [analysis], REELS, brief)
    src, t = place(timeline, 1.0)
    nar = create_narration(tmp_path, "이 영상 하나로 정리합니다", "elevenlabs:v1", "테스트", src, t, engine=eng)
    plan.narrations.append(nar)
    card, timeline, caps = finalize(plan, [analysis], REELS, brief)
    # 나레이션 구간의 자막은 나레이션 문장으로 바뀐다
    in_span = [c.text for c in caps if 1.0 <= c.start < 1.0 + nar.duration]
    assert in_span and "정리합니다" in " ".join(in_span)
    assert not any("음성이 아직" in i.problem for i in card.issues)

    opts = render_options({}, STYLES["explainer"], preview=True)
    info = render(plan, timeline, caps, [analysis], REELS, opts, tmp_path / "out.mp4", tmp_path / "work_r")
    ass = (tmp_path / "work_r" / "subs.ass").read_text()
    assert "AI 음성" in ass and info["spec_ok"]
    graph = (tmp_path / "work_r" / "graph.txt").read_text()
    assert "between(t," in graph and "[speech]" in graph  # 원본 소리 덕킹 + 나레이션 믹스


def test_scorecard_flags_missing_audio_and_label(analysis):
    brief = Brief()
    plan = plan_heuristic([analysis], REELS, brief)
    from autoedit.plan import Narration
    plan.narrations.append(Narration(source=0, t=4.5, text="완치 보장"))
    card, _, _ = finalize(plan, [analysis], REELS, brief)
    assert any("음성이 아직" in i.problem for i in card.issues)
    assert any("완치" in f.text for f in card.flags)


def test_clone_requires_consent(tmp_path):
    store = voice.VoiceStore(tmp_path / "voices")
    sample = tmp_path / "s.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=2", str(sample)], check=True)
    with pytest.raises(voice.VoiceError):
        voice.create_clone("와이프", [sample], consent="", store=store, engine=FakeEngine())
    prof = voice.create_clone("와이프", [sample], consent="본인 동의 2026-10-22", store=store, engine=FakeEngine())
    assert prof.voice == "elevenlabs:cloned123" and store.list()[0].name == "와이프"
    p, d = store.save_sample(sample.read_bytes(), ".mp3")
    assert p.exists() and d == pytest.approx(2.0, abs=0.1)


def test_elevenlabs_request_shapes(monkeypatch):
    sent = []

    class Resp:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.body

    def fake_urlopen(req, timeout=0):
        sent.append(req)
        if req.full_url.endswith("/v1/voices"):
            return Resp(json.dumps({"voices": [{"voice_id": "a1", "name": "지수", "category": "premade"}]}).encode())
        if "with-timestamps" in req.full_url:
            return Resp(json.dumps({"audio_base64": base64.b64encode(b"ID3").decode(),
                                    "alignment": {"characters": ["가"], "character_start_times_seconds": [0],
                                                  "character_end_times_seconds": [0.2]}}).encode())
        return Resp(json.dumps({"voice_id": "new1", "requires_verification": False}).encode())

    monkeypatch.setattr(voice.urllib.request, "urlopen", fake_urlopen)
    el = voice.ElevenLabs("KEY")
    assert el.stock_voices()[0]["voice"] == "elevenlabs:a1"
    audio, al = el.tts("가", "vid", speed=1.1)
    req = sent[-1]
    assert req.full_url == "https://api.elevenlabs.io/v1/text-to-speech/vid/with-timestamps?output_format=mp3_44100_128"
    assert req.get_header("Xi-api-key") == "KEY"
    body = json.loads(req.data)
    assert body == {"text": "가", "model_id": "eleven_multilingual_v2", "voice_settings": {"speed": 1.1}}
    assert audio == b"ID3" and al["characters"] == ["가"]


def test_draft_script_fallback(analysis, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    brief = Brief(cta="저장해두세요")
    plan = plan_heuristic([analysis], REELS, brief)
    _, timeline, _ = finalize(plan, [analysis], REELS, brief)
    lines, engine = draft_script(plan, timeline, [analysis], brief)
    assert "규칙" in engine and lines[0].text == plan.hook_text and lines[-1].text == "저장해두세요"


def test_editor_voice_api(tmp_path, monkeypatch, capsys):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from autoedit import editor
    from autoedit.cli import main

    monkeypatch.setenv("AUTOEDIT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    eng = FakeEngine()
    monkeypatch.setattr(voice, "get_engine", lambda: eng)

    video = make_video(tmp_path / "clip.mp4", seconds=20)
    make_transcript(tmp_path / "out" / "clip" / "_analysis" / "clip" / "transcript.json")
    assert main(["run", str(video), "--out", str(tmp_path / "out"), "--whisper", "none", "--llm", "off",
                 "--platform", "reels", "--plan-only"]) == 0
    pdir = Path(json.loads(capsys.readouterr().out)[0]["project"])
    st = editor.EditorState(pdir)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), editor.make_handler(st))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def post(path, data, raw=False):
        body = data if raw else json.dumps(data).encode()
        req = urllib.request.Request(base + path, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    try:
        state = json.loads(urllib.request.urlopen(base + "/api/state").read())
        assert state["voice"]["ready"] is False and state["voice"]["mine"] == []
        assert post("/api/settings", {"elevenlabs_key": "k-123"})["voice_ready"] is True
        assert json.loads((tmp_path / "home" / "config.json").read_text())["elevenlabs_api_key"] == "k-123"

        sample = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=3", "-f", "webm", "-"],
                                capture_output=True, check=True).stdout
        s1 = post("/api/voices/sample?ext=webm", sample, raw=True)
        assert s1["duration"] == pytest.approx(3, abs=0.2)
        with pytest.raises(urllib.error.HTTPError):  # 동의 없이는 불가
            post("/api/voices/clone", {"name": "와이프", "samples": [s1["path"]], "consent": ""})
        with pytest.raises(urllib.error.HTTPError):  # 업로드하지 않은 경로는 거부
            post("/api/voices/clone", {"name": "x", "samples": [str(video)], "consent": "동의"})
        mine = post("/api/voices/clone", {"name": "와이프", "samples": [s1["path"]], "consent": "동의: 본인"})["mine"]
        assert mine[0]["name"] == "와이프" and eng.cloned

        n = post("/api/narration", {"text": "정리해 드립니다", "voice": mine[0]["voice"], "voice_name": "와이프",
                                    "source": 0, "t": 4.5, "original": "mute"})["narration"]
        assert n["audio"].startswith("narration/") and n["words"]
        assert urllib.request.urlopen(base + "/media/" + n["audio"]).status == 200

        plan = state["plan"]
        plan["narrations"].append(n)
        d = post("/api/diagnose", {"plan": plan})
        assert any("정리해" in c["text"] for c in d["captions"])
        draft = post("/api/narration/draft", {"plan": plan, "instruction": ""})
        assert draft["lines"] and "규칙" in draft["engine"]
        assert post("/api/voices/delete", {"id": mine[0]["id"]})["mine"] == []
    finally:
        httpd.shutdown()


def test_multi_platform_export_reuses_body(tmp_path, analysis):
    """Reels+TikTok 한 프로젝트: 컷 인코딩은 한 번, 플랫폼별 파일은 자막 위치만 달리 합성."""
    import time as _t
    from autoedit.project import Project
    brief = Brief()
    plan = plan_heuristic([analysis], REELS, brief)
    meta = {"sources": [analysis.path], "analysis_dirs": [str(tmp_path / "work")], "platform": "reels",
            "platforms": ["reels", "tiktok"], "brief": brief.model_dump(), "render": {}, "versions": []}
    pdir = tmp_path / "proj"
    pdir.mkdir()
    (pdir / "project.json").write_text(json.dumps(meta))
    proj = Project(pdir, meta, [analysis])
    seen = []
    info, _ = proj.render(plan, progress=seen.append)
    assert info["version"] == 1 and set(info["extra"]) == {"tiktok"}
    tiktok = pdir / info["extra"]["tiktok"]
    assert tiktok.exists() and not (pdir / "work_tiktok" / "pieces").exists()  # 본편 재사용
    assert seen == sorted(seen) and seen[-1] == pytest.approx(1.0)
    assert (pdir / "검수표_v1.html").exists() and (pdir / "업로드문구_v1.txt").exists()
    # 같은 편집이라도 두 번째 내보내기는 v2로 새로 남는다
    info2, _ = proj.render(plan)
    assert info2["version"] == 2 and (pdir / info["file"].split("/")[-1]).exists()
