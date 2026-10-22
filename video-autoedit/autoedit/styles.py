"""영상 장르별 편집 스타일.

같은 '하이라이트'라도 장르마다 기준이 다르다.
- 설명형: 정보 밀도. 질문·숫자 문장을 후킹으로, 무음은 짧게 잘라 속도감.
- 유머형: 타이밍. 펀치라인 앞의 '숨'은 살리고(긴 정적만 컷), 리액션 피크를 후킹으로, 효과음·줌을 과감하게.
- 시연형: 결과. 완성/전후 장면을 먼저 보여주고, 말 없는 작업 구간은 빨리감기, 단계 카드로 순서 안내.
가중치·길이 배수는 모두 [경험칙] — 채널 성과 데이터로 보정할 대상.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Style:
    key: str
    label: str
    hook_mode: str            # sentence(키워드 문장) / peak(리액션 피크) / result(결과 장면)
    hook_words: tuple[str, ...]
    cut_silence: float        # 이 길이 이상인 무음만 잘라낸다
    zoom: float               # 줌 펀치 배율 증가분
    w_energy: float
    w_speech: float
    w_keyword: float
    w_peak: float
    w_scene: float
    w_position: float = 0.0   # +면 영상 뒤쪽(결과)일수록 가산
    speedup_silent: bool = False
    pace_mult: float = 1.0    # 플랫폼 '최대 정지 구간' 배수
    length_mult: float = 1.0  # 플랫폼 권장 길이 배수
    flash_on_peak: bool = False
    card_prefix: str = ""     # 단계 카드 접두어(시연형: STEP)
    sfx: dict[str, str] = field(default_factory=dict)  # 용도 → 효과음 종류
    llm_guide: str = ""


STYLES: dict[str, Style] = {
    "explainer": Style(
        key="explainer", label="설명형(정보·노하우)",
        hook_mode="sentence",
        hook_words=("?", "왜", "어떻게", "진짜", "사실", "비밀", "절대", "꿀팁", "방법", "이유", "실수", "결과",
                    "비교", "드디어", "처음", "몰랐", "반전", "주의", "후회", "제일", "핵심", "딱"),
        cut_silence=0.35, zoom=0.14,
        w_energy=1.0, w_speech=1.0, w_keyword=1.2, w_peak=0.5, w_scene=0.0,
        sfx={"hook": "boom", "transition": "whoosh", "emphasis": "pop", "number": "ding", "reveal": "ding"},
        llm_guide=("설명형: 질문·숫자·반전 문장을 후킹으로. 군더더기(인사·반복)는 과감히 삭제. "
                   "핵심 수치·체크리스트는 text_card로. 줌 펀치는 핵심 단어에만."),
    ),
    "funny": Style(
        key="funny", label="유머·재미",
        hook_mode="peak",
        hook_words=("ㅋㅋ", "ㅎㅎ", "대박", "미쳤", "헐", "아니", "잠깐", "뭐야", "레전드", "실화", "어떡해",
                    "망했", "왜", "?", "!"),
        cut_silence=0.8, zoom=0.26,
        w_energy=2.2, w_speech=0.4, w_keyword=0.8, w_peak=1.2, w_scene=0.3,
        pace_mult=1.0, length_mult=0.8, flash_on_peak=True,
        sfx={"hook": "boom", "transition": "whoosh", "emphasis": "boing", "number": "pop", "reveal": "riser"},
        llm_guide=("유머형: 웃음은 '셋업→정적→펀치라인' 타이밍이 생명. 펀치라인 직전의 짧은 정적은 자르지 말 것. "
                   "후킹은 가장 큰 리액션(웃음·비명) 1.5~2.5초를 맨 앞에 두되 펀치라인 대사 자체는 스포일러하지 않는다. "
                   "리액션 지점에 줌 펀치+boing/flash, 반전 직전에 riser. 텍스트 카드는 최소화(자막 리액션으로 대체)."),
    ),
    "demo": Style(
        key="demo", label="제품 시연·리뷰",
        hook_mode="result",
        hook_words=("완성", "짠", "결과", "보시면", "전후", "비교", "바로", "한 번에", "이렇게", "끝", "사용법", "차이"),
        cut_silence=0.6, zoom=0.10,
        w_energy=0.6, w_speech=0.5, w_keyword=1.2, w_peak=0.4, w_scene=1.0, w_position=1.0,
        speedup_silent=True, pace_mult=1.4, length_mult=1.2, card_prefix="STEP",
        sfx={"hook": "riser", "transition": "whoosh", "emphasis": "pop", "number": "ding", "reveal": "ding"},
        llm_guide=("시연형: 완성·결과·전후 비교 장면을 후킹으로 먼저 보여준다(결과 먼저, 과정은 뒤). "
                   "말 없이 손으로 작업하는 긴 구간은 clip.speed 2~4로 빨리감기. 단계마다 text_card('STEP 1 ○○'). "
                   "제품 효능을 단정하는 표현은 규정 리스크로 risk_notes에 기록."),
    ),
}


def get_style(key: str) -> Style:
    if key not in STYLES:
        raise ValueError(f"알 수 없는 스타일: {key}. 사용 가능: {sorted(STYLES)}")
    return STYLES[key]
