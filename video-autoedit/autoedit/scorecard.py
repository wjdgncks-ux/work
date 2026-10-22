"""편집 결과 진단표.

huchan-content-feedback 스킬의 진단 기준을 숏폼 영상용으로 옮긴 것:
- 이미지/썸네일 '3초 룰'      → 첫 3초 후킹(타이틀·즉시 발화·에너지)
- 블로그 '체류시간 설계'       → 컷 리듬(화면 변화 없는 최대 구간), 길이
- 텍스트 가독성/정보 위계       → 자막 글자수·커버리지, 후킹 문구 길이
- CTA / 참여 유도              → 마지막 CTA, 저장·공유 가치 장치(핵심 카드)
- 플랫폼 규격                  → 비율·길이 상한
- 규정 리스크 / 사실 검증       → compliance.py
점수는 '구조 진단 점수'이며 조회수·전환 예측이 아니다(스킬 주의사항).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .analyze import Analysis
from .compliance import Flag, check_disclosure, scan_text
from .plan import EditPlan
from .platforms import Platform
from .styles import STYLES, Style
from .timeline import Caption, Piece, map_time, narration_spans, total_duration

WEIGHT = {"상": 15, "중": 6, "하": 2}


@dataclass
class Issue:
    where: str
    problem: str
    severity: str
    cause: str
    fix: str


@dataclass
class Scorecard:
    score: int
    issues: list[Issue]
    flags: list[Flag]
    metrics: dict
    fixes: list[tuple[str, str, str]] = field(default_factory=list)  # (항목, before, after)

    @property
    def headline(self) -> str:
        top = [i for i in self.issues if i.severity == "상"]
        if not top:
            return "구조상 치명적 결함 없음 — 남은 과제는 소재 자체의 매력과 사람 검수"
        return " / ".join(f"{i.where}: {i.problem}" for i in top[:2])


def _visual_events(plan: EditPlan, timeline: list[Piece], analyses: list[Analysis]) -> list[float]:
    ev = {0.0}
    for p in timeline:
        ev.add(round(p.out_start, 2))
        a = analyses[p.source]
        for sc in a.scene_changes:
            if p.start < sc < p.end:
                ev.add(round(p.to_out(sc), 2))
    for e in plan.emphases:
        t = map_time(timeline, e.source, e.t)
        if t is not None:
            ev.add(round(t, 2))
    for ins in plan.inserts:
        t = map_time(timeline, ins.source, ins.t)
        if t is not None:
            ev.update({round(t, 2), round(t + ins.duration, 2)})
    ev.add(round(total_duration(timeline), 2))
    return sorted(ev)


def autofix(plan: EditPlan, platform: Platform) -> list[tuple[str, str, str]]:
    """규칙으로 확실히 고칠 수 있는 것만 자동 수정하고 Before/After를 남긴다."""
    fixes = []
    if not plan.hook_text.strip():
        new = plan.topic[:15]
        fixes.append(("후킹 타이틀", "(없음)", new))
        plan.hook_text = new
    elif len(plan.hook_text.replace(" ", "")) > 18:
        new = plan.hook_text[:16].rstrip() + "…"
        fixes.append(("후킹 타이틀 길이", plan.hook_text, new))
        plan.hook_text = new
    if not plan.cta_text.strip():
        new = "저장해두고 필요할 때 꺼내보세요" if platform.key == "reels" else "구독하고 다음 편 받아보기"
        fixes.append(("CTA", "(없음)", new))
        plan.cta_text = new
    if plan.sponsored:
        first = (plan.sns_caption.strip().splitlines() or [""])[0]
        if check_disclosure(True, plan.sns_caption):
            new = "#유료광고\n" + plan.sns_caption.strip()
            fixes.append(("협찬 표기", first[:30], "#유료광고 (첫 줄 단독 표기)"))
            plan.sns_caption = new
    return fixes


def evaluate(plan: EditPlan, timeline: list[Piece], captions: list[Caption],
             analyses: list[Analysis], platform: Platform, industry: str = "general",
             style: Style = STYLES["explainer"]) -> Scorecard:
    issues: list[Issue] = []
    dur = total_duration(timeline)
    lo, hi = (x * style.length_mult for x in platform.target_seconds)
    max_shot = platform.max_shot_seconds * style.pace_mult

    # 1. 길이 / 규격
    if dur > platform.max_seconds:
        issues.append(Issue("전체 길이", f"{dur:.0f}초 — {platform.label} 상한 {platform.max_seconds:.0f}초 초과", "상",
                            "[원리] 상한 초과 시 숏폼으로 분류되지 않거나 업로드 불가", "본문 클립을 줄이거나 2편으로 분할"))
    elif dur > hi * 1.3:
        issues.append(Issue("전체 길이", f"{dur:.0f}초 — 권장 {lo:.0f}~{hi:.0f}초보다 김", "중",
                            "[경험칙] 길이가 늘수록 완주율(시청시간 신호) 하락 위험", "중복 설명 구간 삭제, 예시 1개만 남기기"))
    elif dur < lo * 0.7:
        issues.append(Issue("전체 길이", f"{dur:.0f}초 — 권장보다 짧음", "하",
                            "[경험칙] 정보량 부족 시 저장·공유 동기 약함", "핵심 근거 컷 1개 추가"))

    # 2. 후킹(3초 룰)
    first = timeline[0] if timeline else None
    if not first or first.role != "hook":
        issues.append(Issue("0~3초", "후킹 컷이 맨 앞에 없음", "상",
                            "스크롤을 멈출 이유가 첫 화면에 없으면 이후 편집은 노출되지 않음",
                            "가장 강한 결과/반전 장면 2~3초를 맨 앞으로(콜드 오픈)"))
    else:
        hook_len = sum(p.duration for p in timeline if p.role == "hook")
        if hook_len > 4.0:
            issues.append(Issue("0~3초", f"후킹 구간 {hook_len:.1f}초로 김", "중",
                                "[경험칙] 후킹은 3초 안에 '왜 봐야 하는지'를 끝내야 함", "후킹 컷을 핵심 한 문장 단위로 축소"))
        a = analyses[first.source]
        if a.energy_db:
            med = float(np.median(a.energy_db))
            if a.energy_at(first.start, first.end) < med - 3:
                issues.append(Issue("0~3초", "후킹 구간 음성 에너지가 영상 평균보다 낮음", "중",
                                    "도입부 톤이 처지면 첫 인상이 약함", "목소리 톤이 올라간 구간이나 리액션 구간으로 교체"))
    if len(plan.hook_text.replace(" ", "")) > 15:
        issues.append(Issue("후킹 타이틀", f"'{plan.hook_text}' — {len(plan.hook_text)}자", "하",
                            "정보 위계: 1초 안에 읽히지 않으면 타이틀 역할 못함", "15자 이내, 숫자·질문형 권장"))
    if captions and captions[0].start > 0.8:
        issues.append(Issue("0~1초", f"첫 발화가 {captions[0].start:.1f}초에 시작", "중",
                            "무음 도입은 이탈 구간", "첫 컷 앞 무음 제거 또는 효과음·타이틀로 공백 채우기"))

    # 3. 컷 리듬(체류시간 설계)
    ev = _visual_events(plan, timeline, analyses)
    gaps = [(ev[i], ev[i + 1] - ev[i]) for i in range(len(ev) - 1)]
    long_gaps = [(t, g) for t, g in gaps if g > max_shot * 1.5]
    avg_shot = dur / max(len(gaps), 1)
    for t, g in long_gaps[:3]:
        issues.append(Issue(f"{t:.1f}초~", f"{g:.1f}초 동안 화면 변화 없음", "중",
                            f"[경험칙] {platform.label}·{style.label} 기준 {max_shot:.1f}초 이상 정지 화면에서 이탈 증가",
                            "줌 펀치·자료화면·컷 분할 중 하나 추가"))

    # 4. 자막
    speech = any(a.transcript for a in analyses)
    if not speech and style.key == "demo":
        if not any(i.kind == "text_card" for i in plan.inserts):
            issues.append(Issue("자막", "대사·단계 카드 모두 없음", "상",
                                "무음 시연은 화면만으로 '무엇을 하는지' 전달 안 됨", "단계마다 STEP 카드 추가"))
    elif not speech:
        issues.append(Issue("자막", "음성 인식 결과 없음 → 자막 미삽입", "상",
                            "음소거 시청 환경에서 내용 전달 불가, 자막 키워드 강조도 불가",
                            "faster-whisper 설치 후 재실행하거나 transcript.json 직접 작성"))
    else:
        over = [c for c in captions if len(c.text.replace(" ", "")) > platform.caption_chars + 2]
        if over:
            issues.append(Issue("자막", f"{len(over)}개 자막이 {platform.caption_chars}자 초과", "하",
                                "모바일 한 줄 가독성 저하", "transcript.json에서 문장 분리"))

    # 5. CTA / 참여 유도
    if not plan.cta_text.strip():
        issues.append(Issue("마지막 3초", "CTA 없음", "상", "유입이 다음 행동으로 이어지지 않는 구조",
                            "저장/공유/댓글 중 하나를 구체적으로 지시"))
    if style.key != "funny" and not any(i.kind == "text_card" for i in plan.inserts):
        issues.append(Issue("본문", "핵심 정보 카드(저장 가치 장치) 없음", "하",
                            "[원리] Mosseri: 비연결 도달은 sends(공유) 비중이 큼 → 저장·전달할 정보 요소 필요",
                            "핵심 수치·체크리스트를 텍스트 카드로 1~2개 삽입"))
    if not (3 <= len(plan.hashtags) <= 12):
        issues.append(Issue("해시태그", f"{len(plan.hashtags)}개", "하",
                            "대형 태그만 쓰거나 과다 사용 시 타겟 신호 흐려짐", "대형 2~3 + 니치 3~5 조합"))

    # 5-1. 나레이션(AI 음성)
    spans = narration_spans(timeline, plan.narrations)
    missing = [nr for nr in plan.narrations if not nr.audio]
    if missing:
        issues.append(Issue("나레이션", f"음성이 아직 생성되지 않은 나레이션 {len(missing)}개", "상",
                            "음성 파일이 없으면 내보낼 때 빠진다", "편집기에서 '음성 생성'을 누르거나 해당 줄 삭제"))
    for (a1, b1, _), (a2, b2, _) in zip(spans, spans[1:]):
        if a2 < b1 - 0.05:
            issues.append(Issue(f"{a2:.1f}초~", "나레이션 두 개가 겹침", "중",
                                "두 목소리가 동시에 들리면 내용 전달이 안 됨", "뒤 나레이션을 옮기거나 문장을 줄이기"))
            break
    over = [nr for nr in plan.narrations if nr.audio and (t0 := map_time(timeline, nr.source, nr.t)) is not None
            and t0 + nr.duration > dur + 0.1]
    if over:
        issues.append(Issue("마지막 나레이션", "영상 끝을 넘어 잘림", "하", "끝까지 읽히지 않으면 CTA가 묻힘",
                            "문장을 줄이거나 앞으로 옮기기"))
    if spans and not plan.ai_voice_label:
        issues.append(Issue("AI 음성 표시", "AI 음성을 쓰면서 화면 표시를 껐음", "중",
                            "[원리] 인공지능기본법(2026-01-22 시행): 생성형 AI 결과물 표시 의무(현재 과태료 계도기간)",
                            "'AI 음성' 표시를 켜고, 업로드 시 플랫폼의 AI 생성 콘텐츠 표시도 체크"))

    # 6. 규정 / 사실 검증
    flags: list[Flag] = []
    flags += scan_text("후킹 타이틀", plan.hook_text, industry)
    flags += scan_text("CTA", plan.cta_text, industry)
    flags += scan_text("SNS 캡션", plan.sns_caption, industry)
    for ins in plan.inserts:
        flags += scan_text(f"자료카드 '{ins.text[:10]}'", ins.text, industry)
    for nr in plan.narrations:
        flags += scan_text(f"나레이션 '{nr.text[:10]}'", nr.text, industry)
    for c in captions:
        flags += [f for f in scan_text(f"자막 {c.start:.1f}초", c.text, industry) if f.severity == "상"]
    flags += check_disclosure(plan.sponsored, plan.sns_caption)
    for f in flags:
        if f.severity == "상":
            issues.append(Issue(f.where, f"'{f.text}' — {f.reason}", "상",
                                "표시광고법/플랫폼 심사 반려 리스크", "근거 병기 또는 완화 표현으로 교체, law.go.kr 원문 확인"))

    # 7. 레이아웃(원본 비율)
    for a in analyses:
        if platform.vertical and a.info.aspect > 1.1:
            issues.append(Issue(f"원본 {a.index}", "가로 원본을 세로로 변환", "하",
                                "피사체가 화면 밖으로 잘리거나 위아래 여백 발생", "--reframe crop/blur 결과를 눈으로 확인"))
            break

    order = {"상": 0, "중": 1, "하": 2}
    issues.sort(key=lambda i: order[i.severity])
    score = max(0, 100 - sum(WEIGHT[i.severity] for i in issues))
    metrics = {
        "duration": round(dur, 1),
        "target": f"{lo:.0f}~{hi:.0f}s",
        "style": style.label,
        "pieces": len(timeline),
        "avg_shot": round(avg_shot, 2),
        "longest_static": round(max((g for _, g in gaps), default=0), 2),
        "captions": len(captions),
        "emphases": len(plan.emphases),
        "sfx": len(plan.sfx),
        "inserts": len(plan.inserts),
    }
    return Scorecard(score, issues, flags, metrics)
