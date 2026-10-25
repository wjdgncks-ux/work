"""편집 기획서(EditPlan) 스키마.

모든 시각은 '원본 영상 기준 시각'(source, t)으로 기록한다. 그래야 사람이 plan.json을
원본 영상을 보며 직접 고칠 수 있고, 무음 컷·순서 변경 후 출력 시각은 timeline.py가 계산한다.
같은 스키마를 Claude 구조화 출력 스키마로도 사용한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .stylecard import StyleCard

SfxKind = Literal["whoosh", "pop", "ding", "boom", "boing", "riser"]
EmphasisKind = Literal["zoom_punch", "flash"]
InsertKind = Literal["text_card", "asset"]
ClipRole = Literal["hook", "body", "cta"]


class Clip(BaseModel):
    source: int = Field(description="원본 영상 번호(0부터)")
    start: float = Field(description="원본 기준 시작 초")
    end: float = Field(description="원본 기준 끝 초")
    role: ClipRole = Field(description="hook=맨 앞 0~3초 후킹 컷, body=본문, cta=마무리 행동 유도")
    reason: str = Field(description="이 구간을 고른 이유(한 문장)")
    speed: float = Field(default=1.0, description="재생 속도. 1=정상, 2~4=빨리감기(말 없는 시연 구간). 빨리감기 구간은 원음 대신 무음")


class Emphasis(BaseModel):
    source: int
    t: float = Field(description="원본 기준 강조 시각(초)")
    kind: EmphasisKind
    keyword: str = Field(description="자막에서 색으로 강조할 단어. 없으면 빈 문자열")


class SfxCue(BaseModel):
    source: int
    t: float
    kind: SfxKind


class Insert(BaseModel):
    source: int
    t: float = Field(description="자료화면이 시작될 원본 기준 시각")
    duration: float
    kind: InsertKind = Field(description="text_card=핵심 문구 카드, asset=자료 이미지/영상")
    text: str = Field(description="카드에 들어갈 짧은 문구(15자 이내 권장)")
    asset: str = Field(description="asset일 때 자료 폴더의 파일명. 없으면 빈 문자열")


class NarrationWord(BaseModel):
    start: float
    end: float
    text: str


class Narration(BaseModel):
    """나레이션(AI 음성/인물 지정 음성). 다른 효과처럼 원본 내용(source, t)에 붙어 클립과 함께 움직인다."""

    source: int
    t: float = Field(description="나레이션이 시작될 원본 기준 시각")
    text: str
    voice: str = Field(default="", description="'voicestudio:<목소리 id>' 형식")
    voice_name: str = ""
    engine: str = Field(default="", description="음성을 만든 엔진·모델(예: voicestudio/voxcpm2). 라이선스 점검용")
    audio: str = Field(default="", description="생성된 음성 파일(프로젝트 폴더 기준 상대 경로)")
    duration: float = 0.0
    words: list[NarrationWord] = Field(default_factory=list, description="생성 음성 기준 단어 타이밍(자막용)")
    original: Literal["keep", "duck", "mute"] = Field(default="duck", description="나레이션 동안 원본 소리: 유지/줄이기/끄기")
    volume_db: float = 0.0
    captions: bool = True


class EditPlan(BaseModel):
    topic: str = Field(description="영상의 핵심 주제 한 줄")
    platform: str
    hook_text: str = Field(description="첫 3초 화면 상단 타이틀(15자 이내)")
    clips: list[Clip] = Field(description="출력 순서대로. 첫 클립은 role=hook")
    emphases: list[Emphasis]
    sfx: list[SfxCue]
    inserts: list[Insert]
    cta_text: str = Field(description="마지막 2~3초 화면 문구(저장/공유/댓글 등 구체 행동)")
    title_candidates: list[str] = Field(description="업로드 제목 후보 3개")
    sns_caption: str = Field(description="게시글 본문. 첫 줄이 '더보기'를 누르게 만드는 문장")
    hashtags: list[str] = Field(description="대형+니치 혼합 5~10개, # 포함")
    sponsored: bool = Field(description="협찬/광고 여부. True면 #유료광고 표기 강제")
    rationale: str = Field(description="편집 의도 요약(3문장 이내)")
    risk_notes: list[str] = Field(description="과장·규제·사실 검증이 필요한 표현 목록")
    narrations: list[Narration] = Field(default_factory=list, description="편집기에서 음성 생성 후 채움. 기획 단계에서는 빈 목록")
    ai_voice_label: bool = Field(default=True, description="AI 음성이 있으면 화면에 'AI 음성' 표시(AI 기본법 생성물 표시)")


class Brief(BaseModel):
    """사용자가 주는 편집 의도. 비워두면 영상 내용에서 추론한다."""

    topic: str = ""
    audience: str = ""
    goal: Literal["reach", "conversion"] = "reach"  # 신규 유입(후킹) vs 전환(문의/구매)
    industry: str = "general"  # general/beauty/medical/supplement/local/realestate/saas
    style: Literal["explainer", "funny", "demo"] = "explainer"
    sponsored: bool = False
    cta: str = ""
    hook: str = ""
    notes: str = ""
    reference: StyleCard | None = Field(default=None, description="레퍼런스 영상에서 뽑은 스타일 카드(없으면 장르 기본값)")
