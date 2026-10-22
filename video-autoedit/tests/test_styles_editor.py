import copy
import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_transcript, make_video  # noqa: E402

from autoedit.analyze import analyze  # noqa: E402
from autoedit.cli import main  # noqa: E402
from autoedit.plan import Brief  # noqa: E402
from autoedit.planner_heuristic import plan_heuristic  # noqa: E402
from autoedit.platforms import PLATFORMS  # noqa: E402
from autoedit.profiles import load_profile  # noqa: E402
from autoedit.project import finalize  # noqa: E402
from autoedit.styles import STYLES  # noqa: E402
from autoedit.timeline import build_timeline  # noqa: E402

REELS = PLATFORMS["reels"]


@pytest.fixture(scope="module")
def analysis(tmp_path_factory):
    d = tmp_path_factory.mktemp("src")
    video = make_video(d / "clip.mp4")
    make_transcript(d / "work" / "transcript.json")
    return analyze(video, 0, d / "work", whisper_model=None)


def test_styles_differ(analysis):
    plans = {k: plan_heuristic([analysis], REELS, Brief(style=k)) for k in STYLES}
    # 유머형: 후킹은 리액션(음량 피크) 구간, 효과음에 boing
    funny = plans["funny"]
    assert funny.clips[0].role == "hook" and "리액션" in funny.clips[0].reason
    assert any(s.kind == "boing" for s in funny.sfx)
    assert any(e.kind == "flash" for e in funny.emphases)
    assert not any(i.kind == "text_card" for i in funny.inserts)
    # 시연형: 결과(뒤쪽) 우선, STEP 카드
    demo = plans["demo"]
    assert any(i.text.startswith("STEP") for i in demo.inserts)
    assert demo.clips[0].start > plans["explainer"].clips[0].start


def test_funny_keeps_comedic_pauses(analysis):
    a = copy.deepcopy(analysis)
    plan = plan_heuristic([a], REELS, Brief(style="explainer"))
    body = plan.clips[1]
    mid = (body.start + body.end) / 2
    a.silences = sorted(a.silences + [(mid - 0.25, mid + 0.25)])  # 펀치라인 앞 0.5초 '숨'
    tight = build_timeline(plan, [a], cut_silence=STYLES["explainer"].cut_silence)
    loose = build_timeline(plan, [a], cut_silence=STYLES["funny"].cut_silence)
    assert len(tight) == len(loose) + 1  # 설명형은 잘라내고, 유머형은 살린다


def test_speed_clip_timeline(analysis):
    plan = plan_heuristic([analysis], REELS, Brief())
    plan.clips[1].speed = 2.0
    _, timeline, captions = finalize(plan, [analysis], REELS, Brief())
    fast = [p for p in timeline if p.speed == 2.0]
    assert fast and fast[0].duration == pytest.approx(fast[0].src_duration / 2)
    assert not any(fast[0].out_start <= c.start < fast[0].out_end for c in captions)  # 빨리감기 구간엔 자막 없음


def test_profiles_load():
    for key in ("company", "me", "wife"):
        prof = load_profile(key)
        assert prof.style in STYLES and prof.platforms and prof.handle.startswith("@")
        assert Path(prof.render["assets"]).is_absolute()


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers, r.read()


def _post(url, data):
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def test_editor_api(tmp_path, capsys):
    video = make_video(tmp_path / "clip.mp4", seconds=20)
    make_transcript(tmp_path / "out" / "me" / "clip" / "_analysis" / "clip" / "transcript.json")
    assert main(["run", str(video), "--out", str(tmp_path / "out"), "--whisper", "none", "--llm", "off",
                 "--profile", "me", "--platform", "reels", "--plan-only"]) == 0
    pdir = Path(json.loads(capsys.readouterr().out)[0]["project"])
    project = json.loads((pdir / "project.json").read_text())
    assert project["profile"] == "me" and project["render"]["handle"] == "@내계정"

    from http.server import ThreadingHTTPServer
    from autoedit.editor import EditorState, make_handler
    st = EditorState(pdir)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(st))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, _, body = _get(base + "/")
        assert status == 200 and "수작업 편집기" in body.decode()
        state = json.loads(_get(base + "/api/state")[2])
        assert state["sources"][0]["duration"] > 19 and state["diagnosis"]["timeline"]

        req = urllib.request.Request(base + "/media/source/0", headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req) as r:
            assert r.status == 206 and len(r.read()) == 100 and r.headers["Content-Range"].startswith("bytes 0-99/")

        plan = state["plan"]
        plan["clips"].reverse()
        plan["hook_text"] = "업계 1위 비법"
        d = _post(base + "/api/diagnose", {"plan": plan})
        assert any("1위" in f["text"] for f in d["flags"])
        assert _post(base + "/api/save", {"plan": plan})["ok"]
        assert json.loads((pdir / "plan.json").read_text())["hook_text"] == "업계 1위 비법"

        sents = state["transcripts"][0]["sentences"]
        sents[1]["text"] = "왜 다들 이 방법을 모를까요 진짜?"  # 단어 수 변경 → 균등 배분
        st2 = _post(base + "/api/transcript", {"index": 0, "sentences": sents})
        assert st2["transcripts"][0]["sentences"][1]["text"].endswith("진짜?")

        st3 = _post(base + "/api/style", {"style": "funny"})
        assert st3["meta"]["style"] == "funny"

        bad = urllib.request.Request(base + "/api/save", data=b'{"plan": {"clips": 1}}',
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(bad)
        assert exc.value.code == 400
    finally:
        httpd.shutdown()
