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
    """음성 엔진 대신: 글자 수에 비례한 길이의 톤 mp3 + 균등 글자 타이밍."""
    provider, audio_ext, model = "voicestudio", "mp3", "voxcpm2"

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
        return [{"voice": "voicestudio:stock1", "name": "지수", "category": "premade", "labels": {}, "preview_url": ""}]

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
    a = voice.synthesize("오늘의 핵심 정리", "voicestudio:v1", tmp_path, engine=eng)
    b = voice.synthesize("오늘의  핵심 정리", "voicestudio:v1", tmp_path, engine=eng)  # 공백만 다름 → 캐시
    assert a == b and len(eng.calls) == 1
    assert (tmp_path / a["file"]).exists() and a["duration"] > 0.5 and len(a["words"]) == 3


def test_narration_captions_and_render(tmp_path, analysis):
    eng = FakeEngine()
    brief = Brief()
    plan = plan_heuristic([analysis], REELS, brief)
    _, timeline, caps0 = finalize(plan, [analysis], REELS, brief)
    src, t = place(timeline, 1.0)
    nar = create_narration(tmp_path, "이 영상 하나로 정리합니다", "voicestudio:v1", "테스트", src, t, engine=eng)
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
    assert prof.voice == "voicestudio:cloned123" and store.list()[0].name == "와이프"
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


def test_fit_words_keeps_script_text():
    heard = [{"text": "핵심만정리해", "start": 0.1, "end": 0.9}, {"text": "드립니다", "start": 1.0, "end": 1.6}]
    words = voice.fit_words("핵심만 정리해 드립니다", heard, 1.8)
    assert [w["text"] for w in words] == ["핵심만", "정리해", "드립니다"]  # 글자는 원문, 시각은 인식 결과
    assert words[0]["start"] == pytest.approx(0.1) and words[1]["end"] == pytest.approx(0.9, abs=0.01)
    assert words[2]["start"] == pytest.approx(1.0, abs=0.01) and words[2]["end"] == pytest.approx(1.6, abs=0.01)
    even = voice.fit_words("하나 둘", [], 1.0)  # 인식 실패 → 전체 길이에 고르게
    assert even[0]["start"] == pytest.approx(0.05) and even[-1]["end"] == pytest.approx(0.95)


def test_voicestudio_engine_against_fake_server(tmp_path):
    from fake_voicestudio import start
    httpd, url, fake = start()
    try:
        vs = voice.VoiceStudio(url, "voxcpm2")
        assert vs.health() and vs.engines()["moss-tts-nano"]["available"] is False
        assert [v["voice"] for v in vs.stock_voices()] == ["voicestudio:default", "voicestudio:demo01"]
        info = voice.synthesize("오늘의 핵심 세 가지", "voicestudio:demo01", tmp_path, engine=vs)
        assert fake.speech[-1] == {"model": "voxcpm2", "input": "오늘의 핵심 세 가지", "voice": "demo01",
                                   "response_format": "wav", "speed": 1.0, "language": "ko"}
        assert [w["text"] for w in info["words"]] == ["오늘의", "핵심", "세", "가지"]
        assert info["engine"] == "voicestudio/voxcpm2"
        assert (tmp_path / info["file"]).exists() and not list(tmp_path.glob("*_src.*"))
        # 녹음 여러 개 → wav 하나로 합쳐 30초 이내
        a, b = tmp_path / "a.mp3", tmp_path / "b.mp3"
        for f in (a, b):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=20", str(f)], check=True)
        prof = voice.create_clone("와이프", [a, b], "동의: 본인", store=voice.VoiceStore(tmp_path / "v"), engine=vs)
        assert prof.voice == "voicestudio:p001" and fake.created[0]["bytes"] < 44100 * 2 * 31
        assert not list(tmp_path.glob("ref_*.wav"))
        vs.delete("p001")
        assert "p001" not in [p["id"] for p in fake.profiles]
        with pytest.raises(voice.VoiceError, match="설치"):
            voice.VoiceStudio(url, "moss-tts-nano").tts("안녕", "default")
        with pytest.raises(voice.VoiceError, match="라이선스"):
            voice.VoiceStudio(url, "cosyvoice").tts("안녕", "default")
    finally:
        httpd.shutdown()
        httpd.server_close()
    with pytest.raises(voice.VoiceError, match="VoiceStudio 앱을 켠"):
        voice.VoiceStudio(url, "voxcpm2").tts("안녕", "default")  # 앱이 꺼져 있을 때


def test_editor_voice_api(tmp_path, monkeypatch, capsys):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from fake_voicestudio import start

    from autoedit import editor
    from autoedit.cli import main

    monkeypatch.setenv("AUTOEDIT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(voice, "_ready", (0.0, False))
    vs_httpd, vs_url, fake = start()
    voice.settings.put(voicestudio_url="http://127.0.0.1:9")  # 아무것도 없는 주소 = 앱이 꺼진 상태

    video = make_video(tmp_path / "clip.mp4", seconds=20)
    make_transcript(tmp_path / "out" / "clip" / "_analysis" / "clip" / "transcript.json")
    assert main(["run", str(video), "--out", str(tmp_path / "out"), "--whisper", "none", "--llm", "off",
                 "--platform", "reels", "--plan-only"]) == 0
    pdir = Path(json.loads(capsys.readouterr().out)[0]["project"])
    st = editor.EditorState(pdir)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), editor.make_handler(st))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path):
        return json.loads(urllib.request.urlopen(base + path).read())

    def post(path, data, raw=False):
        body = data if raw else json.dumps(data).encode()
        req = urllib.request.Request(base + path, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def post_err(path, data) -> str:
        with pytest.raises(urllib.error.HTTPError) as ei:
            post(path, data)
        return json.loads(ei.value.read())["error"]

    try:
        state = get("/api/state")
        assert state["voice"]["ready"] is False and state["voice"]["mine"] == []
        assert state["voice"]["model"] == "voxcpm2" and state["voice"]["engine"] == "voicestudio"
        v = post("/api/settings", {"voicestudio_url": vs_url})["voice"]
        assert v["ready"] is True
        assert json.loads((tmp_path / "home" / "config.json").read_text())["voicestudio_url"] == vs_url
        stock = get("/api/voices/stock")
        assert {x["voice"] for x in stock["voices"]} == {"voicestudio:default", "voicestudio:demo01"}
        assert stock["engines"]["moss-tts-nano"]["available"] is False
        assert "알 수 없는" in post_err("/api/settings", {"voicestudio_model": "nope"})

        sample = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=3", "-f", "webm", "-"],
                                capture_output=True, check=True).stdout
        s1 = post("/api/voices/sample?ext=webm", sample, raw=True)
        assert s1["duration"] == pytest.approx(3, abs=0.2)
        post_err("/api/voices/clone", {"name": "와이프", "samples": [s1["path"]], "consent": ""})  # 동의 없이는 불가
        post_err("/api/voices/clone", {"name": "x", "samples": [str(video)], "consent": "동의"})  # 업로드 안 한 경로
        mine = post("/api/voices/clone", {"name": "와이프", "samples": [s1["path"]], "consent": "동의: 본인"})["mine"]
        assert mine[0]["name"] == "와이프" and mine[0]["voice"] == "voicestudio:p001"
        stock = get("/api/voices/stock")
        assert "voicestudio:p001" not in {x["voice"] for x in stock["voices"]}  # 내 목소리와 중복 표시 안 함

        n = post("/api/narration", {"text": "핵심만 정리해 드립니다", "voice": mine[0]["voice"], "voice_name": "와이프",
                                    "source": 0, "t": 4.5, "original": "mute"})["narration"]
        assert n["audio"].startswith("narration/") and n["engine"] == "voicestudio/voxcpm2"
        assert [w["text"] for w in n["words"]] == ["핵심만", "정리해", "드립니다"]
        assert fake.speech[-1]["voice"] == "p001"
        assert urllib.request.urlopen(base + "/media/" + n["audio"]).status == 200

        plan = state["plan"]
        plan["narrations"].append(n)
        d = post("/api/diagnose", {"plan": plan})
        assert any("정리해" in c["text"] for c in d["captions"])
        assert not any("비상업용" in i["problem"] for i in d["issues"])

        # 설치 안 된 엔진 / 라이선스 미수락 → 무엇을 해야 하는지 알려 준다
        line = {"text": "다음 문장", "voice": "voicestudio:default", "source": 0, "t": 1}
        post("/api/settings", {"voicestudio_model": "moss-tts-nano"})
        assert "설치" in post_err("/api/narration", line)
        post("/api/settings", {"voicestudio_model": "cosyvoice"})
        assert "라이선스" in post_err("/api/narration", line)
        # 비상업용 엔진으로 만든 나레이션 → AI 진단에서 '상'
        post("/api/settings", {"voicestudio_model": "omnivoice"})
        n2 = post("/api/narration", {**line, "text": "개인 시험용 문장", "t": 12})["narration"]
        assert n2["engine"] == "voicestudio/omnivoice"
        plan["narrations"].append(n2)
        d = post("/api/diagnose", {"plan": plan})
        assert any("비상업용" in i["problem"] and i["severity"] == "상" for i in d["issues"])

        draft = post("/api/narration/draft", {"plan": plan, "instruction": ""})
        assert draft["lines"] and "규칙" in draft["engine"]
        assert post("/api/voices/delete", {"id": mine[0]["id"]})["mine"] == []
        assert "p001" not in [p["id"] for p in fake.profiles]
    finally:
        httpd.shutdown()
        vs_httpd.shutdown()


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
