"""말 없는 영상에 화면을 보고 분위기에 맞는 자막을 단다(Claude 비전).

- 대상: 소리가 없거나, 음성 인식 결과 말이 거의 없는 원본(is_speechless).
- 원본 전체를 약 2초 간격 프레임(최대 MAX_FRAMES장)으로 보여 주고, 장면별 자막(시작·끝·문구)을 받는다.
- 결과는 그 원본의 transcript.json에 kind="visual"로 저장한다. 그래서 대사 자막과 똑같이 화면에 나오고,
  편집기 '캡션' 탭에서 고칠 수 있고, 다시 기획해도 유지된다. 군말 컷 대상이 아니며 빨리감기 구간에도 표시된다.
- 화면에 보이는 것만 근거로 쓴다(수치·효능·사람의 속사정 지어내기 금지). Claude 키가 없으면 만들지 않는다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from .analyze import Analysis
from .media import extract_frame
from .plan import Brief
from .styles import STYLES
from .transcribe import Sentence, Transcript, Word

MAX_FRAMES = 36
EVERY = 2.0          # 초: 프레임 간격(길면 MAX_FRAMES에 맞춰 넓어짐)
MAX_CHARS = 20       # 자막 한 개 최대 글자 수(띄어쓰기 제외)


class SceneCaption(BaseModel):
    start: float = Field(description="원본 기준 시작 초")
    end: float = Field(description="원본 기준 끝 초(시작보다 1.2~4초 뒤)")
    text: str = Field(description=f"화면 자막 한 개. 띄어쓰기 제외 {MAX_CHARS}자 이내")


class SceneCaptions(BaseModel):
    mood: str = Field(description="영상 분위기 한 줄(예: 잔잔한 아침 루틴, 정신없는 육아 현장)")
    captions: list[SceneCaption] = Field(description="시간 순서. 장면이 바뀌거나 볼거리가 생길 때마다, 자막끼리 겹치지 않게")
    unsure: list[str] = Field(description="화면만으로 확실하지 않아 일부러 쓰지 않은 것(예: 제품명, 장소, 사람 관계)")


SYSTEM = """당신은 10년 차 숏폼 예능·브랜드 영상 자막 작가다. 말이 없는(또는 거의 없는) 촬영본의 프레임을 시간 순서로 보고,
영상 분위기에 맞는 화면 자막을 쓴다. 시청자가 소리를 끄고 봐도 흐름과 재미가 전달되게 하는 것이 목적이다.

규칙:
1. 근거: 프레임에 실제로 보이는 것(행동·사물·표정·장소 분위기·화면 속 글자)만 근거로 쓴다. 보이지 않는 사실,
   수치, 제품 효능·성분, 가격, 사람의 이름·관계·속사정은 지어내지 않는다. 확실하지 않으면 쓰지 말고 unsure에 남긴다.
2. 분위기·말투: 편집 조건의 style.guide와 reference_style(있으면 tone·sentence_style)을 따른다.
   - 유머형: 예능 자막처럼 상황 한마디·속마음·의성어(예: "오늘도 시작된 전쟁", "(멍…)").
   - 설명형: 지금 무엇을 하는지·포인트를 짧게(예: "먼저 물기부터 닦기").
   - 시연형: 단계·결과 중심(예: "1단계 바르기", "전후 차이 보이시나요?").
   - 잔잔한 장면은 담백하게, 빠른 장면은 짧고 리듬감 있게. 이모지는 reference_style.caption.emoji가 true일 때만.
3. 길이·간격: 한 자막은 띄어쓰기 제외 20자 이내, 화면에 1.2~4초. 모든 순간을 채우지 않는다 — 장면이 바뀌거나
   볼거리가 생기는 곳에 2~5초 간격으로 둔다. 첫 자막은 0~1.5초 안에 시작해 시선을 잡는다. 자막끼리 겹치지 않는다.
4. 규정: 최상급·단정(최고·최초·100%·완벽·보장), 비교·효능 표현, 비하·혐오 표현을 쓰지 않는다.
   편집 요청(brief.notes)이 있으면 위 규칙 안에서 먼저 따른다.
5. 시각: 프레임에 붙은 시각(원본 기준 초) 사이를 보간해 start/end를 정한다. 영상 길이를 넘지 않는다."""


def is_speechless(a: Analysis) -> bool | None:
    """True=말 없음, False=말 있음, None=모름(음성 인식을 안 해서 판단 불가)."""
    if not a.info.has_audio:
        return True
    tr = a.transcript
    if tr is None:
        return None
    if tr.kind == "visual":
        return True
    words = tr.words()
    spoken = sum(s.end - s.start for s in tr.sentences)
    return len(words) < 8 and spoken < 0.15 * max(a.duration, 1.0)


def dense_frames(a: Analysis, workdir: Path) -> list[tuple[float, Path]]:
    n = int(min(MAX_FRAMES, max(4, a.duration // EVERY)))
    step = a.duration / (n + 1)
    out = []
    for k in range(1, n + 1):
        t = round(step * k, 2)
        f = workdir / "vframes" / f"v{k:02d}.jpg"
        if not f.exists():
            extract_frame(a.path, t, f, width=384)
        out.append((t, f))
    return out


def build_request(a: Analysis, frames: list[tuple[float, Path]], brief: Brief) -> list[dict]:
    import json

    from .planner_llm import _image_block
    style = STYLES[brief.style]
    ctx = {"style": {"key": style.key, "label": style.label, "guide": style.llm_guide},
           "brief": brief.model_dump(exclude={"reference"}), "duration": round(a.duration, 2),
           "scene_changes": a.scene_changes[:60], "loud_moments": a.peaks[:40]}
    if brief.reference:
        ctx["reference_style"] = brief.reference.for_prompt()
    content: list[dict] = [{"type": "text", "text": "## 편집 조건\n" + json.dumps(ctx, ensure_ascii=False, indent=1)}]
    if a.transcript and a.transcript.kind == "speech" and a.transcript.sentences:
        content.append({"type": "text", "text": "들린 짧은 말(참고): " + " / ".join(
            f"[{s.start:.1f}] {s.text}" for s in a.transcript.sentences[:10])})
    for t, f in frames:
        content.append({"type": "text", "text": f"@ {t:.1f}s"})
        content.append(_image_block(str(f)))
    content.append({"type": "text", "text": "위 프레임으로 SceneCaptions를 작성하라."})
    return [{"role": "user", "content": content}]


def to_transcript(result: SceneCaptions, duration: float, language: str = "ko") -> Transcript:
    """모델 출력 정리: 범위·길이 보정, 겹침 제거, 단어 타이밍 균등 배분."""
    sents: list[Sentence] = []
    last_end = 0.0
    for c in sorted(result.captions, key=lambda c: c.start):
        text = " ".join(c.text.split())
        if not text:
            continue
        start = max(float(c.start), last_end, 0.0)
        end = min(max(float(c.end), start + 1.2), start + 4.0, duration)
        if end - start < 0.6:
            continue
        toks = text.split()
        span = (end - start) / len(toks)
        words = [Word(round(start + i * span, 3), round(start + (i + 1) * span - 0.02, 3), t) for i, t in enumerate(toks)]
        sents.append(Sentence(round(start, 3), round(end, 3), text, words))
        last_end = end
    return Transcript(language, sents, kind="visual")


def generate(a: Analysis, workdir: Path, brief: Brief, *, language: str = "ko",
             log=lambda m: print(m, file=sys.stderr)) -> tuple[Transcript | None, SceneCaptions | None]:
    """화면을 보고 자막을 만든다. Claude 키가 없거나 실패하면 (None, None)."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        log("  · 말 없는 영상 자막은 Claude 키가 있어야 만들 수 있습니다(설정·점검)")
        return None, None
    try:
        import anthropic
    except ImportError:
        log("  · anthropic 미설치 → 말 없는 영상 자막 생략")
        return None, None
    from . import planner_llm
    frames = dense_frames(a, workdir)
    result = planner_llm.structured_call(anthropic.Anthropic(), system=SYSTEM, messages=build_request(a, frames, brief),
                                         output=SceneCaptions, effort="medium", fallback="화면 자막 없이 진행")
    if not result or not result.captions:
        return None, result
    return to_transcript(result, a.duration, language), result
