"""기획 검증·보정(LLM/규칙 기획 공통 후처리).

- 원본 범위 밖 시각 잘라내기, 단어 중간 컷 방지(단어 경계로 스냅)
- 화면 변화 없이 길게 이어지는 구간에 줌 펀치 자동 삽입(컷 리듬 보정)
- 강조·효과음 과밀 방지
"""

from __future__ import annotations

from .analyze import Analysis
from .plan import EditPlan, Emphasis
from .platforms import Platform
from .scorecard import _visual_events
from .timeline import build_timeline, map_time

MIN_FX_GAP = 1.2  # 같은 원본에서 효과음끼리 최소 간격(초)


def _snap(a: Analysis, t: float, *, to: str) -> float:
    if not a.transcript:
        return t
    words = a.transcript.words()
    for w in words:
        if w.start < t < w.end:  # 단어 한가운데면 경계로 이동
            return w.start if to == "start" else w.end
    return t


def sanitize(plan: EditPlan, analyses: list[Analysis]) -> None:
    n = len(analyses)
    plan.clips = [c for c in plan.clips if 0 <= c.source < n]
    for c in plan.clips:
        a = analyses[c.source]
        c.start = round(max(0.0, _snap(a, c.start, to="start")), 3)
        c.end = round(min(a.duration, _snap(a, c.end, to="end")), 3)
    plan.clips = [c for c in plan.clips if c.end - c.start >= 0.3]
    if plan.clips and plan.clips[0].role != "hook":
        hooks = [c for c in plan.clips if c.role == "hook"]
        if hooks:
            plan.clips.remove(hooks[0])
            plan.clips.insert(0, hooks[0])
    for lst in (plan.emphases, plan.sfx, plan.inserts):
        lst[:] = [x for x in lst if 0 <= x.source < n and 0 <= x.t <= analyses[x.source].duration]
    # 자료화면끼리 겹치면 하나만: 실제 자료(asset)를 텍스트 카드보다 우선
    kept: list = []
    for ins in sorted(plan.inserts, key=lambda i: (i.source, i.t)):
        prev = kept[-1] if kept else None
        if prev and prev.source == ins.source and ins.t < prev.t + prev.duration:
            if ins.kind == "asset" and prev.kind == "text_card":
                kept[-1] = ins
            continue
        kept.append(ins)
    plan.inserts = kept
    plan.sfx.sort(key=lambda s: (s.source, s.t))
    thinned = []
    for s in plan.sfx:
        if thinned and thinned[-1].source == s.source and s.t - thinned[-1].t < MIN_FX_GAP:
            continue
        thinned.append(s)
    plan.sfx = thinned


def fill_static_gaps(plan: EditPlan, analyses: list[Analysis], platform: Platform) -> int:
    """platform.max_shot_seconds를 넘는 정지 구간 중앙에 줌 펀치를 넣는다. 추가 개수 반환."""
    timeline = build_timeline(plan, analyses)
    added = 0
    for _ in range(40):
        ev = _visual_events(plan, timeline, analyses)
        gaps = [(ev[i], ev[i + 1]) for i in range(len(ev) - 1) if ev[i + 1] - ev[i] > platform.max_shot_seconds]
        if not gaps:
            break
        s, e = gaps[0]
        mid = (s + e) / 2
        piece = next((p for p in timeline if p.out_start <= mid < p.out_end), None)
        if piece is None:
            break
        t = round(piece.start + mid - piece.out_start, 3)
        mapped = map_time(timeline, piece.source, t)
        if mapped is None or abs(mapped - mid) > 0.5:  # 후킹 프리뷰와 겹치는 구간 등: 더 못 메움
            break
        plan.emphases.append(Emphasis(source=piece.source, t=t, kind="zoom_punch", keyword=""))
        added += 1
    return added
