"""말 없는 영상: 화면을 보고 분위기에 맞는 자막(가짜 Claude) → 자막 트랙·렌더·편집기까지."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_video  # noqa: E402

from autoedit import planner_llm, visual_captions as vc  # noqa: E402
from autoedit.analyze import analyze  # noqa: E402
from autoedit.plan import Brief  # noqa: E402
from autoedit.timeline import Piece, build_captions, build_timeline  # noqa: E402
from autoedit.transcribe import Sentence, Transcript, Word  # noqa: E402


def silent_video(path: Path, seconds: int = 12) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=1080x1920:rate=30:d={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)
    return path


SAMPLE = vc.SceneCaptions(mood="잔잔한 아침 루틴", unsure=["제품명"], captions=[
    vc.SceneCaption(start=0.3, end=2.5, text="오늘도 시작된 아침"),
    vc.SceneCaption(start=2.0, end=3.0, text="겹치는 자막"),            # 앞 자막과 겹침 → 뒤로 밀림
    vc.SceneCaption(start=5.0, end=20.0, text="커피 한 잔의 여유"),      # 너무 김 → 4초로
    vc.SceneCaption(start=11.8, end=12.5, text="끝"),                  # 영상 끝을 넘음 → 남는 구간이 짧아 버림
    vc.SceneCaption(start=7.0, end=8.0, text="   "),
])


@pytest.fixture()
def fake_claude(monkeypatch):
    seen = {}

    def call(client, *, system, messages, output, effort, fallback, max_tokens=16000):
        if output is vc.SceneCaptions:
            seen["frames"] = sum(1 for b in messages[0]["content"] if b["type"] == "image")
            seen["ctx"] = messages[0]["content"][0]["text"]
            seen["system"] = system
            return SAMPLE
        if output.__name__ == "EditPlan":
            seen["plan_text"] = " ".join(b.get("text", "") for b in messages[0]["content"] if b["type"] == "text")
        return None  # 기획서는 규칙 기반으로
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setattr(planner_llm, "structured_call", call)
    return seen


def test_to_transcript_cleans_model_output():
    tr = vc.to_transcript(SAMPLE, duration=12.0)
    assert tr.kind == "visual"
    got = [(s.start, s.end, s.text) for s in tr.sentences]
    assert got[0] == (0.3, 2.5, "오늘도 시작된 아침")
    assert got[1][0] == 2.5 and got[1][2] == "겹치는 자막"       # 겹침 제거
    assert got[2] == (5.0, 9.0, "커피 한 잔의 여유")             # 최대 4초
    assert len(got) == 3                                       # 빈 문구·너무 짧은 끝 자막 제외
    assert [w.text for w in tr.sentences[2].words] == ["커피", "한", "잔의", "여유"]
    assert Transcript.from_dict(json.loads(json.dumps(tr.to_dict()))).kind == "visual"
    assert Transcript.from_dict({"sentences": []}).kind == "speech"  # 예전 파일은 대사로


def test_is_speechless(tmp_path):
    a = analyze(silent_video(tmp_path / "s.mp4", 6), 0, tmp_path / "w1", whisper_model=None)
    assert vc.is_speechless(a) is True  # 소리 트랙 없음
    b = analyze(make_video(tmp_path / "v.mp4", seconds=6), 0, tmp_path / "w2", whisper_model=None)
    assert vc.is_speechless(b) is None   # 음성 인식을 안 해서 모름
    b.transcript = Transcript("ko", [])
    assert vc.is_speechless(b) is True   # 인식했는데 말 없음
    b.transcript = Transcript("ko", [Sentence(0, 4, "안녕하세요 오늘은 아침 루틴 세 가지를 알려 드릴게요",
                                              [Word(0, 0.5, "안녕하세요")] * 9)])
    assert vc.is_speechless(b) is False


def test_visual_captions_survive_speed_and_clip_edges():
    tr = vc.to_transcript(SAMPLE, duration=12.0)
    a = type("A", (), {"transcript": tr})()
    timeline = [Piece(0, 0.0, 1.5, 0.0, "hook"), Piece(0, 5.0, 9.0, 1.5, "body", speed=2.0)]
    caps = build_captions(timeline, [a], max_chars=12)
    texts = [c.text for c in caps]
    assert texts[0] == "오늘도 시작된" or texts[0].startswith("오늘도")  # 클립 경계에서 잘려도 보이는 만큼 표시
    assert any("커피" in t for t in texts)                         # 2배속 구간에도 표시
    cof = next(c for c in caps if "커피" in c.text)
    assert cof.start >= 1.5 and caps[-1].end <= 1.5 + 2.0 + 1e-6    # 배속에 맞춰 시간 변환
    # 대사(kind=speech)는 그대로: 빨리감기 구간에 자막 없음
    a.transcript = Transcript("ko", tr.sentences, "speech")
    assert not any("커피" in c.text for c in build_captions(timeline, [a], max_chars=12))


def test_visual_captions_are_not_cut_as_fillers(tmp_path):
    from autoedit.fillers import filler_spans
    tr = Transcript("ko", [Sentence(1.0, 2.0, "이제", [Word(1.0, 2.0, "이제")]),
                           Sentence(4.0, 5.0, "음", [Word(4.0, 5.0, "음")])], kind="visual")
    assert filler_spans(tr) == []
    assert filler_spans(Transcript("ko", tr.sentences)) != []


def test_pipeline_adds_visual_captions_and_renders(tmp_path, fake_claude):
    from autoedit.pipeline import RunOptions, run_pipeline
    video = silent_video(tmp_path / "말없는 브이로그.mp4", 12)
    res = run_pipeline([video], RunOptions(out=tmp_path / "out", whisper=None, platform="reels", llm="on", revise=0,
                                           brief={"notes": "잔잔하게"}))
    tpath = tmp_path / "out" / "말없는 브이로그" / "_analysis" / "말없는 브이로그" / "transcript.json"
    tr = json.loads(tpath.read_text(encoding="utf-8"))
    assert tr["kind"] == "visual" and tr["sentences"][0]["text"] == "오늘도 시작된 아침"
    assert 4 <= fake_claude["frames"] <= vc.MAX_FRAMES and "잔잔하게" in fake_claude["ctx"]
    assert "지어내지 않는다" in fake_claude["system"]
    assert "화면 자막(말 없는 영상" in fake_claude["plan_text"]   # 기획에도 '대사 아님'으로 전달
    ass = next((Path(res[0]["project"]) / "work").rglob("*.ass")).read_text(encoding="utf-8")
    assert "오늘도" in ass and "커피" in ass                        # 완성본 자막에 들어감
    # 다시 돌려도 이미 만든 화면 자막(사람이 고쳤을 수도 있음)은 그대로
    fake_claude.clear()
    run_pipeline([video], RunOptions(out=tmp_path / "out", whisper=None, platform="reels", llm="on", revise=0, plan_only=True))
    assert "frames" not in fake_claude


def test_pipeline_off_and_no_key(tmp_path, monkeypatch, fake_claude):
    from autoedit.pipeline import RunOptions, run_pipeline
    video = silent_video(tmp_path / "v.mp4", 6)
    run_pipeline([video], RunOptions(out=tmp_path / "o1", whisper=None, platform="reels", llm="off", revise=0,
                                     plan_only=True, visual_captions=False))
    assert "frames" not in fake_claude
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    run_pipeline([video], RunOptions(out=tmp_path / "o2", whisper=None, platform="reels", llm="off", revise=0, plan_only=True))
    assert "frames" not in fake_claude and not (tmp_path / "o2" / "v" / "_analysis" / "v" / "transcript.json").exists()


def test_editor_button_and_edit_keeps_kind(tmp_path, fake_claude):
    from autoedit.editor import EditorState
    from autoedit.pipeline import RunOptions, run_pipeline
    video = silent_video(tmp_path / "v.mp4", 8)
    res = run_pipeline([video], RunOptions(out=tmp_path / "o", whisper=None, platform="reels", llm="off", revise=0,
                                           plan_only=True, visual_captions=False))
    st = EditorState(Path(res[0]["project"]))
    assert st.visual_captions(0) == "잔잔한 아침 루틴"
    assert st.state()["transcripts"][0]["kind"] == "visual"
    st.save_transcript(0, [{"start": 0.3, "end": 2.5, "text": "고친 자막"}])
    assert st.project.analyses[0].transcript.kind == "visual"
    assert any("고친" in c.text for c in st.project.finalize(st.project.load_plan())[2])
