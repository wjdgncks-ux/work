"""나레이션: 대본 초안 작성 → 음성 생성 → 타임라인 배치.

대본 초안은 Claude가 있으면 영상 내용(클립별 대사)과 스타일을 보고 쓰고, 없으면 화면 문구(후킹·카드·CTA)를 엮는다.
음성은 voice.synthesize로 만들고, 단어 타이밍을 Narration.words에 담아 자막과 맞춘다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from .analyze import Analysis
from .plan import Brief, EditPlan, Narration, NarrationWord
from .styles import STYLES
from .timeline import Piece, locate_out, map_time, total_duration
from .voice import synthesize

CHARS_PER_SEC = 6.0  # [경험칙] 한국어 나레이션이 또렷하게 들리는 속도(띄어쓰기 제외 글자 수)


class ScriptLine(BaseModel):
    at: float = Field(description="나레이션 시작 시각(완성 영상 기준 초)")
    text: str = Field(description="읽을 문장. 한국어 구어체")


class Script(BaseModel):
    lines: list[ScriptLine]


def create_narration(pdir: Path, text: str, voice: str, voice_name: str, source: int, t: float,
                     original: str = "duck", speed: float | None = None, engine=None) -> Narration:
    info = synthesize(text, voice, pdir / "narration", speed=speed, engine=engine)
    return Narration(source=source, t=round(t, 3), text=text, voice=voice, voice_name=voice_name,
                     engine=info.get("engine", ""), audio=f"narration/{info['file']}", duration=info["duration"],
                     words=[NarrationWord(**w) for w in info["words"]], original=original)


def place(timeline: list[Piece], at: float) -> tuple[int, float] | None:
    """완성 영상 시각 → (원본 번호, 원본 시각)."""
    hit = locate_out(timeline, at)
    return (hit[0].source, hit[1]) if hit else None


def _fallback(plan: EditPlan, timeline: list[Piece]) -> list[ScriptLine]:
    lines = []
    if plan.hook_text:
        lines.append(ScriptLine(at=0.2, text=plan.hook_text))
    for ins in plan.inserts:
        t = map_time(timeline, ins.source, ins.t)
        if t is not None and ins.kind == "text_card" and ins.text:
            lines.append(ScriptLine(at=round(t, 2), text=ins.text))
    if plan.cta_text:
        lines.append(ScriptLine(at=max(0.0, round(total_duration(timeline) - 3.0, 2)), text=plan.cta_text))
    return lines


def draft_script(plan: EditPlan, timeline: list[Piece], analyses: list[Analysis], brief: Brief,
                 instruction: str = "") -> tuple[list[ScriptLine], str]:
    """(대본 줄, 작성 엔진) 반환."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return _fallback(plan, timeline), "화면 문구 기반(규칙)"
    try:
        import anthropic
    except ImportError:
        return _fallback(plan, timeline), "화면 문구 기반(규칙)"
    from .planner_llm import MODEL

    scenes = []
    for p in timeline:
        tr = analyses[p.source].transcript
        said = tr.text_between(p.start, p.end) if tr else ""
        scenes.append({"from": round(p.out_start, 2), "to": round(p.out_end, 2), "role": p.role,
                       "speed": p.speed, "said": said})
    style = STYLES[brief.style]
    prompt = {
        "goal": "완성 영상 위에 얹을 나레이션 대본을 쓴다",
        "topic": plan.topic, "style": style.label, "style_guide": style.llm_guide,
        "audience": brief.audience, "hook": plan.hook_text, "cta": plan.cta_text,
        "total_seconds": round(total_duration(timeline), 2), "scenes": scenes,
        "rules": [
            f"각 줄은 다음 줄 시작 전에 끝나야 한다. 1초에 띄어쓰기 제외 {CHARS_PER_SEC:.0f}글자 이내로 길이를 맞춘다",
            "원본 대사(said)가 있는 구간은 대사를 되풀이하지 말고 비워 두거나 보충만 한다",
            "근거 없는 최상급·효능 단정(최고, 1위, 100%, 완치 등) 금지. 영상에 없는 수치를 지어내지 않는다",
            "첫 줄은 0~3초 안에 시작해 스크롤을 멈추게 한다. 마지막 줄은 CTA로 끝낸다",
        ],
        "user_request": instruction,
        "editor_notes": brief.notes,
    }
    if brief.reference and brief.reference.analysis:
        a = brief.reference.analysis
        prompt["reference_style"] = {"tone": a.tone, "sentence_style": a.sentence_style, "hook_templates": a.hook_templates,
                                     "cta_style": a.cta_style, "avoid": a.avoid}
        prompt["rules"].append("reference_style의 말투·문장 형식을 따르되 레퍼런스 문장을 그대로 쓰지 않는다(형식만)")
    try:
        resp = anthropic.Anthropic().beta.messages.parse(
            model=MODEL, max_tokens=8000, output_format=Script,
            thinking={"type": "adaptive"}, output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        )
    except anthropic.APIError as exc:
        print(f"  · 대본 작성 실패({exc}) → 화면 문구 기반", file=sys.stderr)
        return _fallback(plan, timeline), "화면 문구 기반(규칙)"
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        return _fallback(plan, timeline), "화면 문구 기반(규칙)"
    return resp.parsed_output.lines, f"Claude ({MODEL})"
