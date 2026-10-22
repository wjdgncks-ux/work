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
    speed: float = 1.0

    @property
    def src_duration(self) -> float:
        return self.end - self.start

    @property
    def duration(self) -> float:
        """출력 타임라인상의 길이(빨리감기 반영)."""
        return (self.end - self.start) / self.speed

    def to_out(self, t: float) -> float:
        return self.out_start + (t - self.start) / self.speed

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


def build_timeline(plan: EditPlan, analyses: list[Analysis], *, cut_silence: float = 0.35) -> list[Piece]:
    """cut_silence 이상 지속된 무음만 잘라낸다(0이면 무음 컷 끔). 빨리감기 클립은 자르지 않는다."""
    out: list[Piece] = []
    cursor = 0.0
    for clip in plan.clips:
        a = analyses[clip.source]
        start, end = max(0.0, clip.start), min(a.duration, clip.end)
        speed = min(max(clip.speed, 0.25), 8.0)
        if end - start < MIN_PIECE:
            continue
        ranges = [(start, end)]
        if cut_silence > 0 and speed == 1.0 and a.info.has_audio:
            long_silences = [(s, e) for s, e in a.silences if e - s >= cut_silence]
            ranges = _subtract_silences(start, end, long_silences) or [(start, end)]  # 전부 무음이면 원본 유지
        for s, e in ranges:
            piece = Piece(clip.source, round(s, 3), round(e, 3), round(cursor, 3), clip.role, speed)
            out.append(piece)
            cursor += piece.duration
    return out


def map_time(timeline: list[Piece], source: int, t: float) -> float | None:
    """원본 시각 → 출력 시각. 같은 원본 구간이 두 번 쓰이면(후킹 프리뷰) 본문 쪽을 우선."""
    hits = [p for p in timeline if p.source == source and p.start <= t <= p.end]
    if not hits:
        return None
    body = [p for p in hits if p.role != "hook"]
    p = (body or hits)[0]
    return round(p.to_out(t), 3)


def locate_out(timeline: list[Piece], t_out: float) -> tuple[Piece, float] | None:
    """출력 시각 → (조각, 원본 시각)."""
    if not timeline:
        return None
    t_out = min(max(t_out, 0.0), total_duration(timeline) - 1e-3)
    p = next((p for p in timeline if p.out_start <= t_out < p.out_end), timeline[-1])
    return p, round(p.start + (t_out - p.out_start) * p.speed, 3)


def total_duration(timeline: list[Piece]) -> float:
    return timeline[-1].out_end if timeline else 0.0


@dataclass
class Caption:
    start: float
    end: float
    text: str


_BREAK = re.compile(r"[.!?,。…~]$")


def _chunk(words: list[tuple[float, float, str]], max_chars: int, max_dur: float) -> list[Caption]:
    """(출력 시작, 출력 끝, 단어) 목록을 짧은 자막 덩어리로 묶는다."""
    caps: list[Caption] = []
    buf: list[str] = []
    c_start = c_end = None
    for ws, we, text in words:
        candidate = " ".join(buf + [text])
        if buf and (len(candidate.replace(" ", "")) > max_chars or we - c_start > max_dur):
            caps.append(Caption(c_start, c_end, " ".join(buf)))
            buf, c_start = [], None
        if c_start is None:
            c_start = ws
        buf.append(text)
        c_end = we
        if _BREAK.search(text):
            caps.append(Caption(c_start, c_end, " ".join(buf)))
            buf, c_start = [], None
    if buf:
        caps.append(Caption(c_start, c_end, " ".join(buf)))
    return caps


def narration_spans(timeline: list[Piece], narrations) -> list[tuple[float, float, object]]:
    """음성이 생성된 나레이션의 출력 구간 (시작, 끝, 나레이션). 영상 끝을 넘는 부분은 잘린다."""
    total = total_duration(timeline)
    out = []
    for n in narrations:
        if not n.audio or n.duration <= 0:
            continue
        t0 = map_time(timeline, n.source, n.t)
        if t0 is None or t0 >= total:
            continue
        out.append((t0, min(t0 + n.duration, total), n))
    return out


def build_captions(timeline: list[Piece], analyses: list[Analysis], max_chars: int, max_dur: float = 1.8,
                   narrations=()) -> list[Caption]:
    """원본 대사(단어 타임스탬프)와 나레이션을 출력 타임라인 자막으로 만든다.
    자막을 켠 나레이션 구간에서는 원본 대사 자막을 숨긴다(두 줄이 겹치지 않게)."""
    caps: list[Caption] = []
    for p in timeline:
        tr = analyses[p.source].transcript
        if not tr or p.speed != 1.0:  # 빨리감기 구간은 원음이 없으므로 자막도 없음
            continue
        words = [(p.out_start + max(w.start - p.start, 0), min(p.out_start + (w.end - p.start), p.out_end), w.text)
                 for w in tr.words() if w.start >= p.start - 0.05 and w.end <= p.end + 0.15]
        caps += _chunk(words, max_chars, max_dur)
    spans = [(a, b, n) for a, b, n in narration_spans(timeline, narrations) if n.captions]
    if spans:
        caps = [c for c in caps if not any(c.start < b and c.end > a for a, b, _ in spans)]
        for a, b, n in spans:
            words = [(a + w.start, min(a + w.end, b), w.text) for w in n.words if a + w.start < b]
            caps += _chunk(words, max_chars, max_dur)
        caps.sort(key=lambda c: c.start)
    # 다음 자막 직전까지 늘려 깜빡임 방지(최대 0.4초)
    for i in range(len(caps) - 1):
        gap = caps[i + 1].start - caps[i].end
        if 0 < gap < 0.4:
            caps[i].end = caps[i + 1].start
        elif gap < 0:  # 겹치면 앞 자막을 줄인다
            caps[i].end = caps[i + 1].start
    return [c for c in caps if c.end - c.start > 0.05]
