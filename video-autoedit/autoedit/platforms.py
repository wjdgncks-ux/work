"""플랫폼별 출력 규격과 편집 기준.

라벨 규칙 (huchan-content-feedback 스킬의 [원리]/[경험칙] 구분을 그대로 따름)
- [원리]  : 플랫폼 공식 발표·규격 등 검증된 사실
- [경험칙]: 현장에서 통용되는 추정치. 계정·업종마다 다르므로 A/B로 재검증할 값
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Platform:
    key: str
    label: str
    width: int
    height: int
    fps: int
    max_seconds: float          # [원리] 업로드 상한
    target_seconds: tuple[float, float]  # [경험칙] 완주율을 노리는 권장 길이
    max_shot_seconds: float     # [경험칙] 화면 변화 없이 버틸 수 있는 최대 구간
    caption_chars: int          # [경험칙] 자막 1줄 최대 글자수(한글 기준)
    safe_top: float             # 화면 비율. 상단 UI(계정명 등)에 가려지는 영역
    safe_bottom: float          # 하단 UI(캡션·버튼)에 가려지는 영역
    loudness_lufs: float = -14.0  # [경험칙] 스트리밍 플랫폼 정규화 기준으로 통용
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def vertical(self) -> bool:
        return self.height > self.width


PLATFORMS: dict[str, Platform] = {
    "reels": Platform(
        key="reels",
        label="Instagram Reels",
        width=1080, height=1920, fps=30,
        max_seconds=180,  # [원리] 2025-01 Mosseri 발표로 90초 → 3분
        target_seconds=(15, 45),
        max_shot_seconds=3.5,
        caption_chars=14,
        safe_top=0.12, safe_bottom=0.22,
        notes=(
            "[원리] Mosseri(2025-01): 핵심 지표는 시청시간, 도달 대비 좋아요, 도달 대비 공유(sends)",
            "[경험칙] 비팔로워 도달은 공유(DM 전송)를 유발하는 '저장·전달 가치' 구간이 좌우",
        ),
    ),
    "shorts": Platform(
        key="shorts",
        label="YouTube Shorts",
        width=1080, height=1920, fps=30,
        max_seconds=180,  # [원리] 2024-10-15부터 정사각/세로 3분 이하 = Shorts
        target_seconds=(20, 50),
        max_shot_seconds=4.0,
        caption_chars=14,
        safe_top=0.10, safe_bottom=0.20,
        notes=(
            "[경험칙] 반복 재생(loop)이 시청 지속률을 끌어올림 → 마지막 컷이 첫 컷으로 자연스럽게 이어지게",
        ),
    ),
    "tiktok": Platform(
        key="tiktok",
        label="TikTok",
        width=1080, height=1920, fps=30,
        max_seconds=600,  # 앱 내 업로드 기준. 계정 상태에 따라 다를 수 있음 → 업로드 전 확인
        target_seconds=(15, 40),
        max_shot_seconds=3.0,
        caption_chars=13,
        safe_top=0.10, safe_bottom=0.25,
        notes=("[경험칙] 우측 버튼 열이 넓어 자막을 좌측·중앙 상단으로 올리는 편이 안전",),
    ),
    "youtube": Platform(
        key="youtube",
        label="YouTube (가로 롱폼)",
        width=1920, height=1080, fps=30,
        max_seconds=12 * 3600,
        target_seconds=(180, 720),
        max_shot_seconds=8.0,
        caption_chars=22,
        safe_top=0.05, safe_bottom=0.12,
        notes=("[경험칙] 첫 30초 이탈 구간 관리가 핵심 → 도입부 예고(Preview) 컷 권장",),
    ),
}


def get_platforms(spec: str) -> list[Platform]:
    keys = [k.strip().lower() for k in spec.split(",") if k.strip()]
    unknown = [k for k in keys if k not in PLATFORMS]
    if unknown:
        raise ValueError(f"알 수 없는 플랫폼: {unknown}. 사용 가능: {sorted(PLATFORMS)}")
    return [PLATFORMS[k] for k in keys]
