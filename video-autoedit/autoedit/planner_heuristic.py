"""규칙 기반 편집 기획(API 키 없이 동작하는 기본값).

전략
1) 후보 단위 만들기: 대사가 있으면 문장, 없으면 발화 구간/장면 단위 3초 창
2) 점수: 음성 에너지 + 후킹 키워드(질문·반전·숫자) + 발화 밀도 + 음량 피크
3) 최고점 단위를 맨 앞으로(결론 먼저/콜드 오픈), 나머지는 점수순으로 목표 길이까지 채운 뒤 시간순 정렬
4) 숫자·키워드 지점에 줌 펀치+효과음, 클립 전환에 휙 효과음, 나열/수치 문장은 텍스트 카드
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .analyze import Analysis
from .plan import Brief, Clip, EditPlan, Emphasis, Insert, SfxCue
from .platforms import Platform

HOOK_WORDS = ["?", "왜", "어떻게", "진짜", "사실", "비밀", "절대", "꿀팁", "방법", "이유", "실수", "결과",
              "비교", "전후", "드디어", "처음", "몰랐", "반전", "주의", "망", "후회", "제일", "핵심", "딱"]
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


def _units(a: Analysis) -> list[Unit]:
    if a.transcript and a.transcript.sentences:
        units: list[Unit] = []
        for s in a.transcript.sentences:
            if units and units[-1].duration < 1.2 and s.start - units[-1].end < 0.5:
                units[-1].end, units[-1].text = s.end, units[-1].text + " " + s.text
            else:
                units.append(Unit(a.index, s.start, s.end, s.text))
        return units
    # 대사 없음: 발화 구간(무음의 여집합) 또는 장면 경계 기준 3초 창
    bounds = sorted({0.0, a.duration, *a.scene_changes})
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


def _score(u: Unit, a: Analysis, med_energy: float) -> float:
    score = (a.energy_at(u.start, u.end) - med_energy) / 6.0
    score += a.speech_ratio(u.start, u.end)
    score += 0.5 * sum(1 for p in a.peaks if u.start <= p <= u.end)
    if u.text:
        score += min(3, sum(1 for w in HOOK_WORDS if w in u.text)) * 1.2
        score += 1.0 if NUM.search(u.text) else 0
        toks = u.text.split()
        if toks and all(t.strip(".,!?") in FILLERS for t in toks):
            score -= 3
        if u.text.startswith(GREETING):  # 인사·자기소개는 군더더기
            score -= 1.5
    if u.duration < 0.8:
        score -= 1
    return round(score, 3)


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


def plan_heuristic(analyses: list[Analysis], platform: Platform, brief: Brief) -> EditPlan:
    units: list[Unit] = []
    for a in analyses:
        med = float(np.median(a.energy_db)) if a.energy_db else -40.0
        for u in _units(a):
            u.score = _score(u, a, med)
            units.append(u)
    if not units:
        a = analyses[0]
        units = [Unit(a.index, 0.0, min(a.duration, platform.target_seconds[1]), "", 0.0)]

    lo, hi = platform.target_seconds
    goal = (lo + hi) / 2 * 1.15  # 무음 컷으로 약 15% 줄어드는 것을 감안

    # 후킹: 1.2~5초 단위 중 최고점
    hook_pool = [u for u in units if 1.2 <= u.duration <= 5.0] or units
    hook = max(hook_pool, key=lambda u: u.score)
    hook_clip_end = min(hook.end, hook.start + 3.5)
    early = hook.source == units[0].source and hook.start <= analyses[hook.source].duration * 0.15

    body: list[Unit] = []
    total = hook_clip_end - hook.start
    for u in sorted(units, key=lambda u: -u.score):
        if u is hook and not early:
            continue
        if u.score < 0 and total >= lo * 0.6:  # 군더더기는 최소 길이를 채운 뒤엔 버림
            continue
        if total + u.duration > goal and body:
            continue
        body.append(u)
        total += u.duration
    body.sort(key=lambda u: (u.source, u.start))
    if early and hook in body:
        body.remove(hook)

    clips = [Clip(source=hook.source, start=hook.start, end=round(hook_clip_end, 3), role="hook",
                  reason=f"최고 후킹 점수 {hook.score} — {hook.text[:30] or '에너지 피크'}")]
    for u in body:
        role = "cta" if u is body[-1] and any(w in u.text for w in CTA_WORDS) else "body"
        clips.append(Clip(source=u.source, start=u.start, end=u.end, role=role, reason=f"점수 {u.score}"))

    emphases: list[Emphasis] = []
    sfx: list[SfxCue] = [SfxCue(source=hook.source, t=hook.start, kind="boom")]
    inserts: list[Insert] = []
    chosen = [hook] + body
    for u in chosen:
        a = analyses[u.source]
        hit = next((w for w in HOOK_WORDS[1:] if w in u.text), None)
        num = NUM.search(u.text) if u.text else None
        if num:
            t = _word_time(a, u, num.group(0).split()[0]) or u.start
            emphases.append(Emphasis(source=u.source, t=t, kind="zoom_punch", keyword=num.group(0)))
            sfx.append(SfxCue(source=u.source, t=t, kind="ding"))
        elif hit:
            t = _word_time(a, u, hit) or u.start
            emphases.append(Emphasis(source=u.source, t=t, kind="zoom_punch", keyword=hit))
            sfx.append(SfxCue(source=u.source, t=t, kind="pop"))
        for p in a.peaks:
            if u.start + 0.3 < p < u.end and all(abs(p - e.t) > 2.0 for e in emphases if e.source == u.source):
                emphases.append(Emphasis(source=u.source, t=p, kind="zoom_punch", keyword=""))
                sfx.append(SfxCue(source=u.source, t=p, kind="pop"))
        if u.text and (num or any(w in u.text for w in LIST_WORDS)) and len(inserts) < 2 and u is not hook:
            inserts.append(Insert(source=u.source, t=u.start, duration=min(2.5, u.duration), kind="text_card",
                                  text=_short(u.text, 14), asset=""))
    for c in clips[1:]:
        sfx.append(SfxCue(source=c.source, t=c.start, kind="whoosh"))

    keywords = _keywords(analyses)
    topic = brief.topic or (" · ".join(keywords[:3]) if keywords else Path(analyses[0].path).stem)
    hook_text = brief.hook or (_short(hook.text) if hook.text else topic[:15])
    tags = [f"#{k}" for k in keywords[:5]]
    tags += {"reels": ["#릴스", "#정보공유"], "shorts": ["#shorts", "#쇼츠"], "tiktok": ["#틱톡", "#fyp"]}.get(platform.key, [])
    first_line = f"#유료광고\n{hook_text}" if brief.sponsored else hook_text
    return EditPlan(
        topic=topic, platform=platform.key, hook_text=hook_text, clips=clips,
        emphases=emphases, sfx=sfx, inserts=inserts, cta_text=brief.cta,
        title_candidates=[hook_text, f"{topic} 핵심만", f"{hook_text} (끝까지 보면 이해됨)"],
        sns_caption=f"{first_line}\n\n{topic}에 대해 정리했습니다.\n필요한 분께 공유해 주세요.",
        hashtags=tags, sponsored=brief.sponsored,
        rationale="규칙 기반 초안: 최고 점수 구간을 맨 앞에 두고, 점수순으로 목표 길이를 채운 뒤 시간순 재배열.",
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
