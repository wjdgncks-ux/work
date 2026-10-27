"""Claude 기반 편집 기획.

영상 신호(대사 타임코드·음량 피크·장면 전환)와 대표 프레임을 함께 보내 '주제 해석 → 후킹 → 컷 구성 →
강조·효과음·자료화면'을 EditPlan 스키마(구조화 출력)로 받는다. 진단표 '상' 이슈가 남으면 1회 수정 요청.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from .analyze import Analysis
from .plan import Brief, EditPlan
from .platforms import Platform
from .styles import STYLES

MODEL = "claude-opus-5-5"

# 고정 시스템 프롬프트(프롬프트 캐시 대상 — 실행마다 바뀌는 값을 넣지 말 것)
SYSTEM = """당신은 10년 차 숏폼 편집 감독이자 퍼포먼스 마케터다. 원본 촬영본의 신호 데이터와 프레임을 보고
SNS 알고리즘(시청 지속·공유·저장)에 맞는 편집 기획서(EditPlan)를 작성한다.

판단 기준 (huchan-content-feedback 진단 기준을 영상에 적용):
1. 3초 룰: 첫 클립(role=hook)은 결과·반전·질문·숫자가 담긴 1.5~3.5초. 원본 중간/끝에 있어도 맨 앞으로 가져온다(콜드 오픈).
   hook_text는 15자 이내, 숫자나 질문형. 영상 내용이 실제로 답하는 약속만 한다(낚시 금지 — 이탈·신뢰 하락).
2. 체류시간 설계: 화면 변화 없는 구간이 플랫폼 기준(max_shot_seconds)을 넘지 않게 클립 분할·줌 펀치·자료화면을 배치.
   군더더기(인사, 자기소개, 반복 설명, 말 더듬음)는 클립에서 제외한다. 클립 경계는 문장 경계에 맞춘다.
3. 정보 위계: 핵심 수치·체크리스트·단계는 text_card로 띄워 저장·공유 가치를 만든다(최대 3개, 15자 이내).
4. CTA: 목적(goal)에 맞춘다. reach=저장/공유/팔로우, conversion=프로필 링크/상담 등 구체 행동 하나.
5. 효과음: 편집 조건의 style.sfx 매핑(용도→종류)을 따른다. 종류: whoosh(전환) pop(강조) ding(수치·정답)
   boom(후킹) boing(유머 리액션) riser(반전 직전 긴장, 해당 시각에 도달하도록 자동 배치). 1.2초 이상 간격.
   장르별 판단은 편집 조건의 style.guide를 최우선으로 따른다(설명형/유머형/시연형).
   clip.speed: 말 없이 작업만 하는 긴 구간은 2~4배속으로(원음은 제거됨). 대사가 있는 구간은 1.
6. 자료화면(kind=asset)은 제공된 자료 파일명 목록에 있는 것만 사용한다. 없으면 만들지 않는다.
7. 규정: 근거 없는 최상급(최고·최초·1위·100%·완치·보장), 비교 광고, 효능 단정 표현을 hook_text·cta·카드·캡션에 쓰지 않는다.
   의료·화장품·건기식은 특히 엄격. 영상 대사에 위험 표현이 있으면 해당 구간을 빼거나 risk_notes에 기록한다.
   sponsored=true면 sns_caption 첫 줄을 단독 '#유료광고'로 시작한다.
8. 사실: 대사에 없는 수치·사례를 지어내지 않는다. 검증 안 된 수치는 risk_notes에 [사실 검증 필요]로 남긴다.

0. 편집 요청(brief.notes)은 편집자가 직접 적은 지시다. 길이·순서·강조·말투·뺄 구간 등은 이 요청을 다른 기준보다 먼저 따른다.
   단 규정(7)·사실(8)과 충돌하거나 원본에 없는 장면이 필요하면 따르지 말고 risk_notes에 '[요청 미반영] 이유'를 남긴다.
9. 레퍼런스 스타일(편집 조건의 reference_style)이 있으면 형식을 따른다: 후킹은 hook_templates 형식에 이 영상 내용을 채워 쓰고,
   tone·sentence_style·cta_style·title_style을 hook_text·카드·CTA·제목·캡션에 맞춘다. rhythm(평균 컷 길이·쉼)에 맞춰 클립을 나눈다.
   레퍼런스의 문장을 그대로 옮기지 않는다(형식만). avoid 항목은 하지 않는다. 장르(style.guide)·규정(7)·사실(8)이 우선한다.

시각 표기: 모든 t/start/end는 해당 원본(source)의 원본 기준 초. 출력 타임라인은 시스템이 계산한다.
clips는 출력 순서대로 나열하고 총 길이는 target_seconds 범위(무음 컷으로 10~20% 줄어드는 것 감안)."""


def _image_block(path: str) -> dict:
    data = base64.standard_b64encode(Path(path).read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def build_request(analyses: list[Analysis], platform: Platform, brief: Brief, assets: list[str]) -> list[dict]:
    content: list[dict] = []
    style = STYLES[brief.style]
    ctx = {
        "platform": {
            "key": platform.key, "label": platform.label, "target_seconds": platform.target_seconds,
            "max_seconds": platform.max_seconds, "max_shot_seconds": platform.max_shot_seconds,
            "notes": platform.notes,
        },
        "brief": brief.model_dump(exclude={"reference"}),
        "style": {"key": style.key, "label": style.label, "guide": style.llm_guide, "sfx": style.sfx,
                  "target_seconds": [round(x * style.length_mult) for x in platform.target_seconds]},
        "asset_files": assets,
    }
    if brief.reference:
        ctx["reference_style"] = brief.reference.for_prompt()
    content.append({"type": "text", "text": "## 편집 조건\n" + json.dumps(ctx, ensure_ascii=False, indent=1)})
    for a in analyses:
        lines = [f"## 원본 {a.index}: {Path(a.path).name}",
                 f"길이 {a.duration:.1f}s, {a.info.display_size[0]}x{a.info.display_size[1]}, 오디오 {'있음' if a.info.has_audio else '없음'}",
                 f"음량 피크(s): {a.peaks[:40]}",
                 f"장면 전환(s): {a.scene_changes[:40]}",
                 f"무음 구간 수: {len(a.silences)}"]
        if a.transcript:
            lines.append("대사(타임코드):")
            lines += [f"[{s.start:.2f}-{s.end:.2f}] {s.text}" for s in a.transcript.sentences]
        else:
            lines.append("대사: 없음(음성 인식 불가) — 프레임과 음량으로 판단")
        content.append({"type": "text", "text": "\n".join(lines)})
        for t, fp in a.frames:
            content.append({"type": "text", "text": f"원본 {a.index} 프레임 @ {t:.1f}s"})
            content.append(_image_block(fp))
    content.append({"type": "text", "text": "위 원본으로 EditPlan을 작성하라."})
    return [{"role": "user", "content": content}]


def structured_call(client, *, system: str, messages: list[dict], output, effort: str, fallback: str,
                    max_tokens: int = 16000):
    """구조화 출력(Pydantic 모델) 호출. 오류·거절·잘림이면 None을 돌려주고 이유를 알린다(fallback: 대신 할 일)."""
    import anthropic

    try:
        resp = client.beta.messages.parse(
            model=MODEL,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_format=output,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError as exc:
        print(f"  · Claude 요청 한도 초과({exc.status_code}) → {fallback}", file=sys.stderr)
        return None
    except anthropic.APIStatusError as exc:
        print(f"  · Claude API 오류 {exc.status_code}: {exc.message} → {fallback}", file=sys.stderr)
        return None
    except anthropic.APIConnectionError as exc:
        print(f"  · Claude 연결 실패({exc}) → {fallback}", file=sys.stderr)
        return None
    if resp.stop_reason == "refusal":
        print(f"  · Claude가 요청을 거절 → {fallback}", file=sys.stderr)
        return None
    if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
        print(f"  · 결과가 잘렸거나 파싱 실패 → {fallback}", file=sys.stderr)
        return None
    return resp.parsed_output


def _call(client, messages: list[dict], effort: str) -> EditPlan | None:
    return structured_call(client, system=SYSTEM, messages=messages, output=EditPlan, effort=effort,
                           fallback="규칙 기반 기획으로 대체")


def plan_llm(analyses: list[Analysis], platform: Platform, brief: Brief, assets: list[str],
             effort: str = "high") -> tuple[EditPlan | None, list[dict]]:
    """(기획서, 대화 기록) 반환. 대화 기록은 revise_llm에 그대로 이어 붙인다."""
    try:
        import anthropic
    except ImportError:
        print("  · anthropic 미설치 → 규칙 기반 기획 (pip install anthropic)", file=sys.stderr)
        return None, []
    client = anthropic.Anthropic()
    messages = build_request(analyses, platform, brief, assets)
    plan = _call(client, messages, effort)
    if plan:
        plan.platform = platform.key
        plan.narrations = []  # 음성은 편집기에서 생성한다
        plan.sponsored = plan.sponsored or brief.sponsored
        messages = messages + [{"role": "assistant", "content": plan.model_dump_json()}]
    return plan, messages


def revise_llm(messages: list[dict], issues_text: str, effort: str = "high") -> EditPlan | None:
    import anthropic

    client = anthropic.Anthropic()
    follow = messages + [{"role": "user", "content":
                          "자동 진단에서 아래 문제가 나왔다. 원인을 해결한 EditPlan 전체를 다시 작성하라. "
                          "근거 없이 문제를 숨기지 말고, 해결 불가능하면 risk_notes에 이유를 적어라.\n\n" + issues_text}]
    return _call(client, follow, effort)
