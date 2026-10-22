import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_transcript, make_video  # noqa: E402

from autoedit.analyze import analyze  # noqa: E402
from autoedit.cli import _finalize, main  # noqa: E402
from autoedit.compliance import check_disclosure, scan_text  # noqa: E402
from autoedit.pacing import sanitize  # noqa: E402
from autoedit.plan import Brief, EditPlan, Insert  # noqa: E402
from autoedit.planner_heuristic import plan_heuristic  # noqa: E402
from autoedit.platforms import PLATFORMS  # noqa: E402
from autoedit.timeline import _subtract_silences, map_time  # noqa: E402

REELS = PLATFORMS["reels"]


@pytest.fixture(scope="module")
def analysis(tmp_path_factory):
    d = tmp_path_factory.mktemp("src")
    video = make_video(d / "clip.mp4")
    make_transcript(d / "work" / "transcript.json")
    return analyze(video, 0, d / "work", whisper_model=None)


def test_analysis_detects_gaps(analysis):
    assert 31 < analysis.duration < 33
    assert len(analysis.silences) >= 6  # 4초마다 1.2초 무음
    assert analysis.transcript and len(analysis.transcript.sentences) == 8
    assert len(analysis.frames) == 6


def test_silence_subtraction():
    pieces = _subtract_silences(0, 10, [(2.0, 3.0), (6.0, 6.2)])
    assert pieces[0] == (0, 2.08) and pieces[1][0] == pytest.approx(2.92)
    assert len(pieces) == 2  # 0.2초 무음은 여유(PAD) 때문에 잘리지 않음


def test_heuristic_plan_structure(analysis):
    plan = plan_heuristic([analysis], REELS, Brief())
    card, timeline, captions = _finalize(plan, [analysis], REELS, Brief())
    assert plan.clips[0].role == "hook"
    assert "왜" in plan.hook_text  # 질문형 문장이 후킹으로 선택
    starts = [c.start for c in plan.clips]
    assert 12.05 not in starts  # '음 어 그' 군더더기 제외
    assert timeline[0].out_start == 0 and captions
    assert card.metrics["longest_static"] <= REELS.max_shot_seconds * 1.5
    assert not [i for i in card.issues if i.severity == "상"]


def test_map_time_prefers_body(analysis):
    plan = plan_heuristic([analysis], REELS, Brief())
    _, timeline, _ = _finalize(plan, [analysis], REELS, Brief())
    body = next(p for p in timeline if p.role != "hook")
    assert map_time(timeline, body.source, body.start + 0.1) == pytest.approx(body.out_start + 0.1, abs=1e-3)
    assert map_time(timeline, 0, 999) is None


def test_compliance_flags():
    flags = scan_text("t", "업계 1위! 부작용 없는 완치 효과 100% 보장합니다", "general")
    reasons = " ".join(f.reason for f in flags)
    assert "순위" in reasons and "효능 단정" in reasons and "부작용" in reasons
    assert any(f.severity == "중" for f in scan_text("t", "매출 3배", "general"))
    assert scan_text("t", "시술 받았어요", "medical")
    assert check_disclosure(True, "좋은 제품 #유료광고")
    assert not check_disclosure(True, "#유료광고\n좋은 제품")


def test_sponsored_autofix(analysis):
    plan = plan_heuristic([analysis], REELS, Brief(sponsored=False))
    plan.sponsored = True
    card, _, _ = _finalize(plan, [analysis], REELS, Brief(sponsored=True))
    assert plan.sns_caption.startswith("#유료광고")
    assert any(f[0] == "협찬 표기" for f in card.fixes)


def test_insert_overlap_prefers_asset(analysis):
    plan = plan_heuristic([analysis], REELS, Brief())
    plan.inserts = [Insert(source=0, t=16.0, duration=2.5, kind="text_card", text="a", asset=""),
                    Insert(source=0, t=16.5, duration=2.0, kind="asset", text="", asset="x.png")]
    sanitize(plan, [analysis])
    assert [i.kind for i in plan.inserts] == ["asset"]


def test_llm_call_handles_refusal_and_success(analysis, monkeypatch):
    pytest.importorskip("anthropic")
    from autoedit import planner_llm

    good = plan_heuristic([analysis], REELS, Brief())
    calls = []

    def fake_parse(**kw):
        calls.append(kw)
        if len(calls) == 1:
            return SimpleNamespace(stop_reason="refusal", parsed_output=None)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=good)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=fake_parse)))
    msgs = planner_llm.build_request([analysis], REELS, Brief(topic="루틴"), ["매출.png"])
    assert sum(1 for b in msgs[0]["content"] if b["type"] == "image") == 6
    assert planner_llm._call(client, msgs, "high") is None
    assert planner_llm._call(client, msgs, "high") is good
    kw = calls[-1]
    assert kw["model"] == planner_llm.MODEL and kw["output_format"] is EditPlan
    assert kw["thinking"] == {"type": "adaptive"} and kw["fallbacks"] == "default"


@pytest.mark.skipif(os.environ.get("AUTOEDIT_SKIP_RENDER") == "1", reason="렌더링 생략")
def test_end_to_end_render(tmp_path, capsys):
    video = make_video(tmp_path / "clip.mp4", seconds=20)
    make_transcript(tmp_path / "out" / "clip" / "_analysis" / "clip" / "transcript.json")
    rc = main(["run", str(video), "--out", str(tmp_path / "out"), "--whisper", "none", "--llm", "off",
               "--platform", "reels", "--sponsored"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)[0]
    info = json.loads((tmp_path / "out" / "clip" / "reels" / "work" / "render.json").read_text())
    assert info["spec_ok"] and info["size"] == "1080x1920"
    assert abs(info["loudness_lufs"] - REELS.loudness_lufs) <= 1.0
    assert Path(result["output"]).exists()
    report = Path(result["report"]).read_text(encoding="utf-8")
    assert "## 2. 상세 이슈" in report and "#유료광고" in report
