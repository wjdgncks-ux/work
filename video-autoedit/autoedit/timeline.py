"""기획서(원본 시각) → 출력 타임라인 변환, 무음 컷(점프컷), 자막 청크 생성."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .analyze import Analysis
from .plan import EditPlan

PAD = 0.08        # 무음 컷 앞뒤 여유
MIN_PIECE = 0.3   # 이보다 짧은 조각은 버림
MIN_CUT = 0.15    # 이보다 짧은 무음은 자르지 않음(미세 점프는 튐만 생김)


@dataclass
class Piece:
    source: int
    start: float
    end: float
    out_start: float
    role: str

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def out_end(self) -> float:
        return self.out_start + self.duration


def _subtract_silences(start: float, end: float, silences: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pieces = [(start, end)]
    for s, e in silences:
        cut_s, cut_e = s + PAD, e - PAD
        if cut_e - cut_s < MIN_CUT:
            continue
        nxt = []
        for a, b in pieces:
            if cut_e <= a or cut_s >= b:
                nxt.append((a, b))
                continue
            if cut_s > a:
                nxt.append((a, cut_s))
            if cut_e < b:
                nxt.append((cut_e, b))
        pieces = nxt
    return [(a, b) for a, b in pieces if b - a >= MIN_PIECE]


def build_timeline(plan: EditPlan, analyses: list[Analysis], *, jumpcut: bool = True) -> list[Piece]:
    out: list[Piece] = []
    cursor = 0.0
    for clip in plan.clips:
        a = analyses[clip.source]
        start, end = max(0.0, clip.start), min(a.duration, clip.end)
        if end - start < MIN_PIECE:
            continue
        ranges = _subtract_silences(start, end, a.silences) if jumpcut and a.info.has_audio else [(start, end)]
        if not ranges:  # 전부 무음(B-roll 등)이면 원본 유지
            ranges = [(start, end)]
        for s, e in ranges:
            out.append(Piece(clip.source, round(s, 3), round(e, 3), round(cursor, 3), clip.role))
            cursor += e - s
    return out


def map_time(timeline: list[Piece], source: int, t: float) -> float | None:
    """원본 시각 → 출력 시각. 같은 원본 구간이 두 번 쓰이면(후킹 프리뷰) 본문 쪽을 우선."""
    hits = [p for p in timeline if p.source == source and p.start <= t <= p.end]
    if not hits:
        return None
    body = [p for p in hits if p.role != "hook"]
    p = (body or hits)[0]
    return round(p.out_start + (t - p.start), 3)


def total_duration(timeline: list[Piece]) -> float:
    return timeline[-1].out_end if timeline else 0.0


@dataclass
class Caption:
    start: float
    end: float
    text: str


_BREAK = re.compile(r"[.!?,。…~]$")


def build_captions(timeline: list[Piece], analyses: list[Analysis], max_chars: int, max_dur: float = 1.8) -> list[Caption]:
    """단어 타임스탬프를 출력 타임라인으로 옮겨 짧은 자막 덩어리로 묶는다."""
    caps: list[Caption] = []
    for p in timeline:
        tr = analyses[p.source].transcript
        if not tr:
            continue
        words = [w for w in tr.words() if w.start >= p.start - 0.05 and w.end <= p.end + 0.15]
        buf: list[str] = []
        c_start = c_end = None
        for w in words:
            ws = p.out_start + max(w.start - p.start, 0)
            we = min(p.out_start + (w.end - p.start), p.out_end)
            candidate = " ".join(buf + [w.text])
            if buf and (len(candidate.replace(" ", "")) > max_chars or we - c_start > max_dur):
                caps.append(Caption(c_start, c_end, " ".join(buf)))
                buf, c_start = [], None
            if c_start is None:
                c_start = ws
            buf.append(w.text)
            c_end = we
            if _BREAK.search(w.text):
                caps.append(Caption(c_start, c_end, " ".join(buf)))
                buf, c_start = [], None
        if buf:
            caps.append(Caption(c_start, c_end, " ".join(buf)))
    # 다음 자막 직전까지 늘려 깜빡임 방지(최대 0.4초)
    for i in range(len(caps) - 1):
        gap = caps[i + 1].start - caps[i].end
        if 0 < gap < 0.4:
            caps[i].end = caps[i + 1].start
    return [c for c in caps if c.end - c.start > 0.05]
