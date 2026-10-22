"""편집 리포트(Markdown): huchan-content-feedback 출력 형식을 따른다."""

from __future__ import annotations

from .plan import EditPlan
from .platforms import Platform
from .scorecard import Scorecard


def build_report(plan: EditPlan, card: Scorecard, platform: Platform, planner: str,
                 render_info: dict | None = None, before: Scorecard | None = None) -> str:
    L: list[str] = []
    L.append(f"# 편집 리포트 — {platform.label}")
    L.append(f"\n- 주제: **{plan.topic}**\n- 기획 엔진: {planner}\n- 구조 진단 점수: **{card.score}/100**"
             + (f" (수정 전 {before.score})" if before else "")
             + "  ※ 조회수·전환 예측이 아니라 편집 구조 결함 진단 점수")
    m = card.metrics
    L.append(f"- 길이 {m['duration']}s (권장 {m['target']}), 컷 {m['pieces']}개, 평균 컷 {m['avg_shot']}s, "
             f"최장 정지 {m['longest_static']}s, 자막 {m['captions']}개, 강조 {m['emphases']}개, "
             f"효과음 {m['sfx']}개, 자료화면 {m['inserts']}개")
    if render_info:
        L.append(f"- 출력: `{render_info['file']}` · {render_info['size']} · {render_info['duration']}s · "
                 f"라우드니스 {render_info['loudness_lufs']} LUFS · 규격 {'통과' if render_info['spec_ok'] else '불일치'}")

    L.append("\n## 1. 종합 진단\n")
    L.append(f"- {card.headline}")
    L.append(f"- 편집 의도: {plan.rationale}")

    L.append("\n## 2. 상세 이슈\n")
    if card.issues:
        L.append("| 위치 | 문제 | 심각도 | 원인 | 개선 방향 |\n|---|---|---|---|---|")
        for i in card.issues:
            L.append(f"| {i.where} | {i.problem} | {i.severity} | {i.cause} | {i.fix} |")
    else:
        L.append("자동 진단 기준 내 이슈 없음.")

    if card.fixes:
        L.append("\n## 3. 자동 수정 (Before / After)\n")
        L.append("| 항목 | Before | After |\n|---|---|---|")
        for item, b, a in card.fixes:
            L.append(f"| {item} | {b} | {a} |")

    L.append("\n## 4. 규정·사실 검증 플래그\n")
    if card.flags or plan.risk_notes:
        for f in card.flags:
            L.append(f"- [{f.severity}] {f.where}: `{f.text}` — {f.reason}")
        for r in plan.risk_notes:
            L.append(f"- [기획 메모] {r}")
        L.append("- 조문 원문 확인: https://www.law.go.kr · 협찬 표기 지침: https://www.ftc.go.kr")
    else:
        L.append("- 탐지된 의심 표현 없음 (자동 탐지는 누락 가능 — 사람 검수 필수)")

    L.append("\n## 5. 업로드 패키지\n")
    L.append("**제목 후보**\n" + "\n".join(f"{n}. {t}" for n, t in enumerate(plan.title_candidates, 1)))
    L.append(f"\n**캡션**\n```\n{plan.sns_caption}\n\n{' '.join(plan.hashtags)}\n```")
    L.append(f"\n**화면 문구**: 후킹 `{plan.hook_text}` · CTA `{plan.cta_text}`")

    L.append("\n## 6. 사람 검수 체크리스트 (자동화가 판단 못 하는 영역)\n")
    L.append("- [ ] 후킹 문구가 영상 내용으로 실제 회수되는가 (낚시성 약속 → 이탈·신고)")
    L.append("- [ ] 이 영상만 보고 '왜 이 계정/제품이어야 하는가'가 한 문장으로 설명되는가")
    L.append("- [ ] 타겟이 실제로 이 말투·속도에 반응하는가")
    L.append("- [ ] 대사 속 수치·사례의 출처 확인")
    L.append("- [ ] 세로 변환 시 얼굴·제품이 잘리지 않았는가 (cover.jpg 확인)")
    L.append("- [ ] 배경음악·자료 이미지의 사용권 확보")
    L.append("\n## 플랫폼 메모\n" + "\n".join(f"- {n}" for n in platform.notes))
    return "\n".join(L) + "\n"


def issues_for_llm(card: Scorecard) -> str:
    return "\n".join(f"- [{i.severity}] {i.where}: {i.problem} / 원인: {i.cause} / 개선: {i.fix}"
                     for i in card.issues if i.severity in ("상", "중"))
