"""스타일 카드: 레퍼런스 영상에서 뽑은 '편집 형식'. 문장·음악은 가져오지 않고 형식만 담는다.

- Rhythm(측정값): 컷 길이, 말 속도, 쉼, 효과음 빈도 → 무음 컷·줌 간격 기준을 바꾼다
- 문구·톤(Claude 분석): 후킹 형식 템플릿, 말투, 문장 길이, CTA 화법 → 기획·대본 프롬프트에 들어간다
- CaptionLook: 자막 위치·크기·색·배경·한 줄 글자 수 → 자막 렌더링과 편집기 미리보기에 쓴다
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, Field

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class CaptionLook(BaseModel):
    position: Literal["bottom", "middle", "top"] = Field("bottom", description="대사 자막 세로 위치")
    size: Literal["small", "medium", "large"] = "medium"
    text_color: str = Field("#FFFFFF", description="자막 글자 색 #RRGGBB")
    highlight_color: str = Field("#FFE500", description="강조 단어 색 #RRGGBB")
    background: Literal["outline", "box", "none"] = Field("outline", description="외곽선 / 반투명 박스 / 없음")
    max_chars: int = Field(12, description="자막 한 줄 최대 글자 수(띄어쓰기 제외), 6~24")
    emoji: bool = Field(False, description="자막·문구에 이모지를 쓰는가")


class Rhythm(BaseModel):
    """레퍼런스들에서 잰 값(여러 개면 중앙값)."""
    duration: float = 0.0
    avg_shot: float = 0.0          # 평균 컷 길이(초)
    cuts_per_10s: float = 0.0
    first_cut: float = 0.0         # 첫 화면 전환까지(초)
    speech_cps: float = 0.0        # 초당 글자 수(띄어쓰기 제외)
    silence_ratio: float = 0.0     # 무음 비율
    median_pause: float = 0.0      # 남겨 둔 쉼의 중앙값(초)
    peaks_per_10s: float = 0.0     # 소리 강조(효과음·리액션) 빈도


class StyleAnalysis(BaseModel):
    """Claude가 레퍼런스 대사·프레임을 보고 채우는 부분."""
    summary: str = Field(description="이 스타일을 한두 문장으로. 표본이 1개면 그 한계를 함께 적는다")
    hook_pattern: str = Field(description="첫 3초 후킹 방식(예: 질문형, 숫자 제시형, 결과 먼저, 공감 상황극)")
    hook_templates: list[str] = Field(description="후킹 문구 형식 2~4개. 내용 자리는 ○○로 비우고 원문 문장을 그대로 쓰지 않는다")
    tone: str = Field(description="말투(반말/존댓말/혼합)와 성격(예: 친한 언니 같은 수다, 단호한 전문가)")
    sentence_style: str = Field(description="문장 길이·리듬·자주 쓰는 표현 유형(특정 문장 인용 금지)")
    cta_style: str = Field(description="마지막 행동 유도 방식(저장 유도, 댓글 질문, 프로필 링크 등)")
    title_style: str = Field(description="업로드 제목·캡션 형식")
    caption: CaptionLook
    do: list[str] = Field(description="따라 할 편집 습관 3~6개(컷 타이밍, 강조 방식, 효과음 사용 등)")
    avoid: list[str] = Field(description="그대로 따라 하면 안 되는 것(원문 복제, 음악, 과장·단정 표현 등)")


class StyleCard(BaseModel):
    id: str = ""
    name: str = ""
    sources: list[str] = Field(default_factory=list)   # 레퍼런스 파일 이름(표시용)
    samples: int = 0
    rhythm: Rhythm = Field(default_factory=Rhythm)
    analysis: StyleAnalysis | None = None              # Claude 키가 없으면 None(측정값만)
    analyzed_by: str = ""
    created: str = ""

    @property
    def caption(self) -> CaptionLook:
        return self.analysis.caption if self.analysis else CaptionLook()

    def warnings(self) -> list[str]:
        out = []
        if self.rhythm.duration > 0 and self.rhythm.avg_shot <= 0:
            out.append("레퍼런스에서 화면 전환(컷)을 감지하지 못해 컷 길이는 반영하지 않습니다(쉼·말 속도만 반영)")
        if self.samples <= 1:
            out.append("레퍼런스가 1개라 그 영상만의 우연한 특징이 섞일 수 있습니다. 같은 크리에이터 영상 3~5개를 권장합니다")
        if not self.analysis:
            out.append("Claude 키가 없어 문구·톤·자막 모양은 분석하지 못했습니다(컷 리듬만 반영)")
        return out

    def for_prompt(self) -> dict:
        """기획·대본 프롬프트에 넣는 형태(측정값 + 분석, 원문 없음)."""
        d = {"rhythm": self.rhythm.model_dump(), "samples": self.samples}
        if self.analysis:
            d.update(self.analysis.model_dump())
        return d


def clean(card: StyleCard) -> StyleCard:
    """사람이 고친 값·모델 출력의 범위를 정리한다(색 형식, 글자 수 범위)."""
    if card.analysis:
        c = card.analysis.caption
        c.max_chars = min(max(int(c.max_chars or 12), 6), 24)
        if not HEX.match(c.text_color or ""):
            c.text_color = "#FFFFFF"
        if not HEX.match(c.highlight_color or ""):
            c.highlight_color = "#FFE500"
        a = card.analysis
        a.hook_templates = [t.strip() for t in a.hook_templates if t.strip()][:6]
        a.do = [t.strip() for t in a.do if t.strip()][:8]
        a.avoid = [t.strip() for t in a.avoid if t.strip()][:8]
    return card


# ---------------- 편집에 반영 ----------------
def effective_style(style, card: StyleCard | None, platform):
    """레퍼런스 리듬으로 무음 컷 기준과 '최대 정지 구간'(줌·컷 간격)을 바꾼 스타일."""
    if not card or card.rhythm.duration <= 0:
        return style
    r = card.rhythm
    changes = {}
    if r.median_pause > 0:
        # 레퍼런스가 남겨 둔 쉼보다 긴 무음만 자른다(너무 촘촘하거나 느슨하지 않게 범위 제한)
        changes["cut_silence"] = round(min(max(r.median_pause * 1.2, 0.25), 1.0), 2)
    if r.avg_shot > 0:
        changes["pace_mult"] = round(min(max(r.avg_shot * 1.3 / platform.max_shot_seconds, 0.5), 2.0), 2)
    return replace(style, **changes)


def effective_platform(platform, card: StyleCard | None):
    """자막 한 줄 글자 수를 레퍼런스에 맞춘 플랫폼."""
    if not card or not card.analysis:
        return platform
    return replace(platform, caption_chars=card.caption.max_chars)
