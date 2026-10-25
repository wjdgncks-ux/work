"""군말 제거 + 레퍼런스 스타일 카드(측정·Claude 분석·저장·편집 반영)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_transcript, make_video  # noqa: E402

from autoedit import reference  # noqa: E402
from autoedit.analyze import analyze  # noqa: E402
from autoedit.plan import Brief  # noqa: E402
from autoedit.planner_heuristic import plan_heuristic  # noqa: E402
from autoedit.platforms import PLATFORMS  # noqa: E402
from autoedit.project import finalize, render_options  # noqa: E402
from autoedit.stylecard import CaptionLook, Rhythm, StyleAnalysis, StyleCard, effective_platform, effective_style  # noqa: E402
from autoedit.styles import STYLES  # noqa: E402
from autoedit.subtitles import build_ass  # noqa: E402
from autoedit.timeline import build_captions, build_timeline  # noqa: E402

REELS = PLATFORMS["reels"]


@pytest.fixture()
def analysis(tmp_path):
    video = make_video(tmp_path / "clip.mp4", seconds=20)
    make_transcript(tmp_path / "work" / "transcript.json")
    return analyze(video, 0, tmp_path / "work", whisper_model=None)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOEDIT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(reference.settings, "workspace", lambda: tmp_path / "ws")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    return tmp_path


def sample_analysis(**caption) -> StyleAnalysis:
    return StyleAnalysis(
        summary="친근한 반말, 결과 먼저", hook_pattern="결과 먼저", hook_templates=["○○ 이렇게 바뀜", "  "],
        tone="반말, 친한 언니", sentence_style="짧은 문장", cta_style="저장 유도", title_style="숫자 + 결과",
        caption=CaptionLook(**caption), do=["0.5초마다 컷"], avoid=["원문 그대로 쓰기"])


# ---------------- 군말 ----------------
def test_fillers_removed_from_captions_and_cut(analysis):
    plan = plan_heuristic([analysis], REELS, Brief())
    plan.clips = [plan.clips[0].model_copy(update={"source": 0, "start": 7.5, "end": 19.0, "role": "body"})]
    caps = build_captions(build_timeline(plan, [analysis], cut_silence=0.35), [analysis], 14)
    text = " ".join(c.text for c in caps)
    assert "음" not in text.split() and "어" not in text.split() and "꿀팁은" in text and "매출이" in text
    kept = build_timeline(plan, [analysis], cut_silence=0.35)
    assert not any(p.start < 13.5 < p.end for p in kept)  # '음 어 그' 구간이 잘림
    assert any(p.start < 13.5 < p.end for p in build_timeline(plan, [analysis], cut_silence=0.0))  # 컷 끄면 그대로
    plan.clips = [plan.clips[0].model_copy(update={"start": 0.0, "end": 3.5})]  # '오늘은 그냥 이야기를…'
    first = build_captions(build_timeline(plan, [analysis], cut_silence=0.35), [analysis], 14)
    assert "그냥" in " ".join(c.text for c in first)  # 군말 목록에 없는 말은 남김


def test_sometimes_filler_needs_pauses():
    from autoedit.fillers import filler_words
    from autoedit.transcribe import Sentence, Transcript, Word
    words = [Word(0.0, 0.3, "그"), Word(0.32, 0.7, "사람이"), Word(1.2, 1.4, "저"), Word(1.9, 2.3, "왔어요")]
    tr = Transcript("ko", [Sentence(0, 2.3, "그 사람이 저 왔어요", words)])
    assert {w[2] for w in filler_words(tr)} == {"저"}  # '그 사람이'의 '그'는 남고, 쉼에 둘러싸인 '저'는 군말


# ---------------- 카드 반영 ----------------
def test_card_changes_pacing_captions_and_ass(analysis):
    card = StyleCard(name="t", samples=3, rhythm=Rhythm(duration=30, avg_shot=1.5, median_pause=0.5),
                     analysis=sample_analysis(position="middle", size="large", background="box", max_chars=8,
                                              text_color="#FFE500", highlight_color="#FF3366"))
    st = effective_style(STYLES["explainer"], card, REELS)
    assert st.cut_silence == 0.6 and st.pace_mult == round(1.5 * 1.3 / REELS.max_shot_seconds, 2)
    assert effective_platform(REELS, card).caption_chars == 8
    assert effective_style(STYLES["explainer"], None, REELS) is STYLES["explainer"]
    brief = Brief(reference=card)
    plan = plan_heuristic([analysis], REELS, brief)
    _, _, caps = finalize(plan, [analysis], REELS, brief)
    assert caps and max(len(c.text.replace(" ", "")) for c in caps) <= 10  # 한 단어가 길면 조금 넘을 수 있음
    opts = render_options({}, STYLES["explainer"], reference=card)
    ass = build_ass(REELS, caps, [], [], "F", "#FFFFFF", look=opts.caption_look)
    line = next(x for x in ass.splitlines() if x.startswith("Style: Caption"))
    assert ",&H0000E5FF," in line and ",3," in line and line.endswith(f",{round(REELS.height * 0.40)},1")
    assert "&H006633FF" in ass  # 강조색은 레퍼런스 색
    assert render_options({}, STYLES["explainer"]).caption_look is None


def test_prompts_carry_reference_without_raw_brief(analysis):
    from autoedit.planner_llm import build_request
    card = StyleCard(name="t", samples=2, analysis=sample_analysis())
    msgs = build_request([analysis], REELS, Brief(reference=card), [])
    ctx = json.loads(msgs[0]["content"][0]["text"].split("\n", 1)[1])
    assert "reference" not in ctx["brief"] and ctx["reference_style"]["tone"] == "반말, 친한 언니"


# ---------------- 분석·저장 ----------------
def test_build_card_measures_and_saves_without_claude(home):
    v1 = make_video(home / "r1.mp4", seconds=12)
    v2 = make_video(home / "r2.mp4", seconds=16)
    card = reference.build_card([v1, v2], name="와이프 레퍼런스", whisper_model=None)
    assert card.samples == 2 and card.analysis is None and "Claude 키 없음" in card.analyzed_by
    assert card.rhythm.duration == 14.0 and card.rhythm.median_pause >= 0
    assert reference.load_card(card.id).name == "와이프 레퍼런스" and [c.id for c in reference.list_cards()] == [card.id]
    assert any("Claude 키" in w for w in card.warnings())


def test_build_card_with_claude(home, monkeypatch):
    from autoedit import planner_llm
    seen = {}

    def fake_call(client, *, system, messages, output, effort, fallback, max_tokens=16000):
        seen["images"] = sum(1 for b in messages[0]["content"] if b["type"] == "image")
        seen["text"] = " ".join(b["text"] for b in messages[0]["content"] if b["type"] == "text")
        seen["system"] = system
        return sample_analysis(text_color="red", max_chars=40)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(planner_llm, "structured_call", fake_call)
    card = reference.build_card([make_video(home / "r1.mp4", seconds=10)], whisper_model=None)
    assert seen["images"] == reference.FRAMES_PER_REF and "측정값" in seen["text"] and "원문" not in seen["text"]
    assert "○○" in seen["system"]
    assert card.analysis.caption.text_color == "#FFFFFF" and card.analysis.caption.max_chars == 24  # 정리됨
    assert card.analysis.hook_templates == ["○○ 이렇게 바뀜"] and card.analyzed_by.startswith("Claude")
    assert any("1개" in w for w in card.warnings())


def test_channel_default_and_pipeline_resolve(home):
    from autoedit.pipeline import RunOptions, resolve
    card = reference.save_card(StyleCard(name="회사 스타일", samples=3))
    reference.set_channel_default("company", card.id)
    _, brief, _, _ = resolve(RunOptions(profile="company"))
    assert brief.reference.name == "회사 스타일"
    _, brief, _, _ = resolve(RunOptions(profile="company", reference=""))
    assert brief.reference is None  # 이번엔 쓰지 않음
    with pytest.raises(ValueError, match="스타일 카드"):
        resolve(RunOptions(reference="nope"))
    reference.delete_card(card.id)
    assert reference.channel_default("company") is None


def test_download_link(home, monkeypatch):
    with pytest.raises(reference.RefError, match="링크 형식"):
        reference.download_link("인스타 그거", home / "d")

    def boom(url, dest):
        raise RuntimeError("login required")
    monkeypatch.setattr(reference, "_fetch", boom)
    with pytest.raises(reference.RefError, match="인스타그램은 로그인"):
        reference.download_link("https://www.instagram.com/reel/abc/", home / "d")
    got = home / "d" / "x.mp4"
    monkeypatch.setattr(reference, "_fetch", lambda url, dest: (got.parent.mkdir(parents=True, exist_ok=True),
                                                                 got.write_bytes(b"v"), got)[2])
    assert reference.download_link("https://youtu.be/abc", home / "d") == got


def test_rhythm_ignores_undetected_cuts():
    with_cuts = Rhythm(duration=20, avg_shot=2.0, cuts_per_10s=4.5, first_cut=1.0, median_pause=0.4)
    no_cuts = Rhythm(duration=30, avg_shot=0.0, cuts_per_10s=0.0, first_cut=0.0, median_pause=0.6)
    r = reference.combine([with_cuts, no_cuts])
    assert r.avg_shot == 2.0 and r.first_cut == 1.0 and r.median_pause == 0.5  # 컷 값은 감지된 것만
    card = StyleCard(name="t", samples=1, rhythm=no_cuts)
    assert effective_style(STYLES["explainer"], card, REELS).pace_mult == STYLES["explainer"].pace_mult
    assert any("감지하지 못해" in w for w in card.warnings())
    assert reference._debounce([1.0, 1.1, 1.3, 2.0]) == [1.0, 1.3, 2.0]
