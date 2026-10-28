"""규칙 기반 편집 기획(API 키 없이 동작하는 기본값). 스타일(styles.py)별로 기준이 달라진다.

전략
1) 후보 단위: 대사가 있으면 문장, 없으면 발화 구간/장면 단위(시연형은 장면 단위를 길게 유지)
2) 점수: 스타일 가중치 × (음성 에너지, 발화 밀도, 후킹 키워드, 음량 피크, 장면 전환, 영상 내 위치)
3) 후킹: 설명형=키워드 문장, 유머형=가장 큰 리액션 피크, 시연형=결과(뒤쪽·'완성' 키워드) 장면 → 맨 앞으로
4) 나머지는 점수순으로 목표 길이까지 채운 뒤 시간순 정렬. 시연형의 말 없는 긴 구간은 빨리감기
5) 강조·효과음·카드는 스타일의 효과음 매핑과 카드 규칙을 따른다
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from .analyze import HOP, Analysis
from .plan import Brief, Clip, EditPlan, Emphasis, Insert, SfxCue
from .platforms import Platform
from .styles import STYLES, Style

LIST_WORDS = ["첫째", "둘째", "셋째", "첫 번째", "두 번째", "세 번째", "가지", "단계", "순서", "체크"]
CTA_WORDS = ["구독", "저장", "댓글", "팔로우", "좋아요", "링크", "프로필", "문의"]
FILLERS = {"음", "어", "그", "아", "저", "이제", "약간", "뭐", "그냥", "네"}
STOP = FILLERS | {"그리고", "근데", "이거", "저거", "그거", "진짜", "너무", "정말", "있는", "하는", "이렇게", "그래서",
                  "되게", "안녕하세요", "여러분", "오늘", "번째", "이야기", "부탁", "필요", "다들", "제일", "하나"}
VERB_END = re.compile(r"(습니다|입니다|해요|세요|어요|아요|에요|예요|게요|까요|니다|는데|하고|해서|했|하는|할|된|되는)$")
NUM = re.compile(r"\d+(\.\d+)?\s*(가지|개|배|분|초|원|%|단계|만|천|억)?|[한두세네다섯일이삼사오육칠팔구십]+\s*(가지|배|단계)")
GREETING = ("안녕하세요", "여러분", "오늘은", "반갑습니다", "제 이름은")


@dataclass
class Unit:
    source: int
    start: float
    end: float
    text: str
    score: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start


def _units(a: Analysis, style: Style) -> list[Unit]:
    if a.transcript and a.transcript.sentences:
        units: list[Unit] = []
        for s in a.transcript.sentences:
            if units and units[-1].duration < 1.2 and s.start - units[-1].end < 0.5:
                units[-1].end, units[-1].text = s.end, units[-1].text + " " + s.text
            else:
                units.append(Unit(a.index, s.start, s.end, s.text))
        if style.speedup_silent:  # 시연형: 대사 사이의 긴 작업 구간도 후보로(빨리감기 대상)
            gaps = [(x.end, y.start) for x, y in zip(units, units[1:]) if y.start - x.end > 4.0]
            units += [Unit(a.index, g0 + 0.2, g1 - 0.2, "") for g0, g1 in gaps]
            units.sort(key=lambda u: u.start)
        return units
    bounds = sorted({0.0, a.duration, *a.scene_changes})
    if style.key == "demo":  # 시연형: 장면 단위를 쪼개지 않고 유지(최대 12초)
        units = []
        for s, e in zip(bounds[:-1], bounds[1:]):
            t = s
            while e - t > 0.6:
                units.append(Unit(a.index, round(t, 2), round(min(t + 12.0, e), 2), ""))
                t += 12.0
        return units
    speech = []
    prev = 0.0
    for s, e in a.silences:
        if s - prev > 0.5:
            speech.append((prev, s))
        prev = e
    if a.duration - prev > 0.5:
        speech.append((prev, a.duration))
    spans = speech if a.info.has_audio and speech else list(zip(bounds[:-1], bounds[1:]))
    units = []
    for s, e in spans:
        t = s
        while e - t > 0.6:
            units.append(Unit(a.index, round(t, 2), round(min(t + 3.0, e), 2), ""))
            t += 3.0
    return units


def _score(u: Unit, a: Analysis, med_energy: float, style: Style) -> float:
    score = style.w_energy * (a.energy_at(u.start, u.end) - med_energy) / 6.0
    score += style.w_speech * a.speech_ratio(u.start, u.end)
    score += style.w_peak * sum(1 for p in a.peaks if u.start <= p <= u.end)
    score += style.w_scene * 0.5 * min(3, sum(1 for c in a.scene_changes if u.start <= c <= u.end))
    score += style.w_position * (u.start / max(a.duration, 1e-6))
    if u.text:
        score += min(3, sum(1 for w in style.hook_words if w in u.text)) * style.w_keyword
        score += 1.0 if NUM.search(u.text) else 0
        toks = u.text.split()
        if toks and all(t.strip(".,!?") in FILLERS for t in toks):
            score -= 3
        if u.text.startswith(GREETING):  # 인사·자기소개는 군더더기
            score -= 1.5
    if u.duration < 0.8:
        score -= 1
    return round(score, 3)


def _peak_hook(analyses: list[Analysis]) -> Unit | None:
    """유머형 후킹: 가장 큰 리액션(음량 피크) 앞 1.2초~뒤 1.0초."""
    best = None
    for a in analyses:
        for p in a.peaks:
            e = a.energy_db[min(int(p / HOP), len(a.energy_db) - 1)] if a.energy_db else -90
            if best is None or e > best[0]:
                best = (e, a, p)
    if best is None:
        return None
    _, a, p = best
    start, end = max(0.0, p - 1.2), min(a.duration, p + 1.0)
    text = a.transcript.text_between(start, end) if a.transcript else ""
    return Unit(a.index, round(start, 2), round(end, 2), text, round(best[0], 1))


def _word_time(a: Analysis, u: Unit, needle: str) -> float | None:
    if not a.transcript:
        return None
    for w in a.transcript.words():
        if u.start - 0.05 <= w.start <= u.end and needle in w.text:
            return w.start
    return None


def _short(text: str, limit: int = 15) -> str:
    out = ""
    for tok in text.replace("\n", " ").split():
        if len((out + tok).replace(" ", "")) > limit:
            break
        out = f"{out} {tok}".strip()
    return out or text[:limit]


def _keywords(analyses: list[Analysis], n: int = 6) -> list[str]:
    cnt: Counter[str] = Counter()
    for a in analyses:
        if a.transcript:
            for w in a.transcript.words():
                t = re.sub(r"[^\w가-힣]", "", w.text)
                t = re.sub(r"(은|는|이|가|을|를|에|의|도|로|으로|에서|까지|요)$", "", t)
                if len(t) >= 2 and t not in STOP and not t.isdigit() and not VERB_END.search(w.text.strip(".,!?")):
                    cnt[t] += 1
    return [w for w, _ in cnt.most_common(n)]


def plan_heuristic(analyses: list[Analysis], platform: Platform, brief: Brief,
                   style: Style | None = None) -> EditPlan:
    style = style or STYLES[brief.style]
    # 화면을 보고 쓴 자막(kind=visual)은 말이 아니다: 대사 기준 클립 선택·후킹 문구 뽑기에 쓰지 않는다(말 없는 영상으로 처리)
    analyses = [replace(a, transcript=None) if a.transcript and a.transcript.kind == "visual" else a for a in analyses]
    units: list[Unit] = []
    for a in analyses:
        med = float(np.median(a.energy_db)) if a.energy_db else -40.0
        for u in _units(a, style):
            u.score = _score(u, a, med, style)
            units.append(u)
    if not units:
        a = analyses[0]
        units = [Unit(a.index, 0.0, min(a.duration, platform.target_seconds[1]), "", 0.0)]

    lo, hi = (x * style.length_mult for x in platform.target_seconds)
    shrink = 1.15 if style.cut_silence < 0.5 else 1.05  # 무음 컷으로 줄어드는 몫
    goal = (lo + hi) / 2 * shrink

    # 빨리감기(시연형): 말 없는 4초 이상 구간 → 출력 3초 안팎
    speed: dict[int, float] = {}
    if style.speedup_silent:
        for u in units:
            a = analyses[u.source]
            if u.duration > 4.0 and (not u.text) and a.speech_ratio(u.start, u.end) < 0.4:
                speed[id(u)] = round(min(4.0, max(2.0, u.duration / 3.0)), 2)
    out_len = lambda u: u.duration / speed.get(id(u), 1.0)  # noqa: E731

    # 후킹
    hook = _peak_hook(analyses) if style.hook_mode == "peak" else None
    if hook is None:
        hook_pool = [u for u in units if 1.2 <= u.duration <= 5.0 and id(u) not in speed] or units
        hook = max(hook_pool, key=lambda u: u.score)
    hook_clip_end = min(hook.end, hook.start + 3.5)
    early = hook.source == units[0].source and hook.start <= analyses[hook.source].duration * 0.15
    # 결과·리액션 후킹은 '미리보기'다: 본문에서 원래 위치에 전체를 다시 보여준다
    preview_hook = style.hook_mode in ("result", "peak")
    if not preview_hook and hook_clip_end < hook.end - 0.5:  # 잘린 후킹 문장의 나머지는 본문 후보로
        rest = Unit(hook.source, round(hook_clip_end, 3), hook.end, "", hook.score - 1)
        units.append(rest)

    body: list[Unit] = []
    total = hook_clip_end - hook.start
    for u in sorted(units, key=lambda u: -u.score):
        if u is hook and not early and not preview_hook:
            continue
        if u.score < 0 and total >= lo * 0.6:  # 군더더기는 최소 길이를 채운 뒤엔 버림
            continue
        if total + out_len(u) > goal and body:
            continue
        body.append(u)
        total += out_len(u)
    body.sort(key=lambda u: (u.source, u.start))
    if early and hook in body and not preview_hook:
        body.remove(hook)

    reason = {"peak": "가장 큰 리액션", "result": "결과 장면", "sentence": "최고 후킹 점수"}[style.hook_mode]
    measure = f"음량 {hook.score}dB" if style.hook_mode == "peak" else f"점수 {hook.score}"
    clips = [Clip(source=hook.source, start=hook.start, end=round(hook_clip_end, 3), role="hook",
                  reason=f"{reason}({measure}) — {hook.text[:30] or '장면'}")]
    for u in body:
        role = "cta" if u is body[-1] and any(w in u.text for w in CTA_WORDS) else "body"
        sp = speed.get(id(u), 1.0)
        clips.append(Clip(source=u.source, start=u.start, end=u.end, role=role, speed=sp,
                          reason=f"점수 {u.score}" + (f" · {sp}배속(무발화 작업 구간)" if sp != 1 else "")))

    fx = style.sfx
    emphases: list[Emphasis] = []
    sfx: list[SfxCue] = [SfxCue(source=hook.source, t=hook.start, kind=fx["hook"])]
    inserts: list[Insert] = []
    step = 0
    for u in [hook] + body:
        a = analyses[u.source]
        hit = next((w for w in style.hook_words if len(w) > 1 and w in u.text), None)
        num = NUM.search(u.text) if u.text else None
        if num:
            t = _word_time(a, u, num.group(0).split()[0]) or u.start
            emphases.append(Emphasis(source=u.source, t=t, kind="zoom_punch", keyword=num.group(0)))
            sfx.append(SfxCue(source=u.source, t=t, kind=fx["number"]))
        elif hit:
            t = _word_time(a, u, hit) or u.start
            emphases.append(Emphasis(source=u.source, t=t, kind="zoom_punch", keyword=hit))
            sfx.append(SfxCue(source=u.source, t=t, kind=fx["emphasis"]))
        if id(u) not in speed:
            for p in a.peaks:
                if u.start + 0.3 < p < u.end and all(abs(p - e.t) > 2.0 for e in emphases if e.source == u.source):
                    emphases.append(Emphasis(source=u.source, t=p, kind="zoom_punch", keyword=""))
                    if style.flash_on_peak:
                        emphases.append(Emphasis(source=u.source, t=p, kind="flash", keyword=""))
                    sfx.append(SfxCue(source=u.source, t=p, kind=fx["emphasis"]))
        if u is hook:
            continue
        if style.card_prefix:  # 시연형: 단계 카드
            if step < 5 and (any(w in u.text for w in LIST_WORDS) or not u.text or id(u) in speed or step == 0):
                step += 1
                label = _short(u.text, 10) if u.text else ("작업 과정" if id(u) in speed else "")
                inserts.append(Insert(source=u.source, t=u.start, duration=min(2.0, u.duration),
                                      kind="text_card", text=f"{style.card_prefix} {step} {label}".strip(), asset=""))
        elif style.key != "funny" and u.text and (num or any(w in u.text for w in LIST_WORDS)) and len(inserts) < 2:
            inserts.append(Insert(source=u.source, t=u.start, duration=min(2.5, u.duration), kind="text_card",
                                  text=_short(u.text, 14), asset=""))
    for c in clips[1:]:
        sfx.append(SfxCue(source=c.source, t=c.start, kind=fx["transition"]))

    keywords = _keywords(analyses)
    topic = brief.topic or (" · ".join(keywords[:3]) if keywords else Path(analyses[0].path).stem)
    hook_text = brief.hook or (_short(hook.text) if hook.text and style.hook_mode != "peak" else topic[:15])
    tags = [f"#{k}" for k in keywords[:5]]
    tags += {"reels": ["#릴스"], "shorts": ["#shorts", "#쇼츠"], "tiktok": ["#틱톡", "#fyp"]}.get(platform.key, [])
    tags += {"explainer": ["#정보공유"], "funny": ["#웃긴영상"], "demo": ["#리뷰", "#사용법"]}[style.key]
    first_line = f"#유료광고\n{hook_text}" if brief.sponsored else hook_text
    return EditPlan(
        topic=topic, platform=platform.key, hook_text=hook_text, clips=clips,
        emphases=emphases, sfx=sfx, inserts=inserts, cta_text=brief.cta,
        title_candidates=[hook_text, f"{topic} 핵심만", f"{hook_text} (끝까지 보면 이해됨)"],
        sns_caption=f"{first_line}\n\n{topic}\n필요한 분께 공유해 주세요.",
        hashtags=tags, sponsored=brief.sponsored,
        rationale=f"규칙 기반 초안({style.label}): {reason}을 맨 앞에 두고, 점수순으로 목표 길이를 채운 뒤 시간순 재배열.",
        risk_notes=["규칙 기반 문구는 초안입니다. 제목·캡션은 사람이 다듬으세요."],
    )


def attach_assets(plan: EditPlan, analyses: list[Analysis], assets_dir: Path | None, max_inserts: int = 3) -> None:
    """자료 폴더의 파일명(예: 매출그래프.png, before_after.mp4)과 대사 단어를 매칭해 자료화면을 붙인다."""
    if not assets_dir or not assets_dir.is_dir():
        return
    files = [f for f in sorted(assets_dir.iterdir()) if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov"}]
    used = {i.asset for i in plan.inserts if i.asset}
    clip_ranges = [(c.source, c.start, c.end) for c in plan.clips]
    added = 0
    for f in files:
        if f.name in used or added >= max_inserts:
            continue
        tokens = [t for t in re.split(r"[_\-\s.]+", f.stem) if len(t) >= 2]
        for a in analyses:
            if not a.transcript:
                continue
            w = next((w for w in a.transcript.words()
                      if any(tok in w.text for tok in tokens)
                      and any(s == a.index and st <= w.start <= en for s, st, en in clip_ranges)), None)
            if w:
                plan.inserts.append(Insert(source=a.index, t=w.start, duration=2.0, kind="asset", text="", asset=f.name))
                plan.sfx.append(SfxCue(source=a.index, t=w.start, kind="whoosh"))
                added += 1
                break
