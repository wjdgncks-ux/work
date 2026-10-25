"""군말(음·어·그·저기…) 찾기: 자막에서 빼고, 무음 컷과 함께 잘라낸다.

- 언제나 군말: 음, 어, 으, 흠, 엄, 에 (늘인 소리 '으음', '어어' 포함)
- 상황에 따라 군말: 그, 저, 저기, 뭐, 막, 이제 — 뜻이 있는 말('그 사람', '저 갈게요')일 수 있으므로
  앞뒤에 쉼(PAUSE 이상)이 있어 따로 떨어져 있거나, 문장 전체가 군말일 때만 군말로 본다.
"""

from __future__ import annotations

import re

from .transcribe import Transcript, Word

ALWAYS = re.compile(r"^(음+|어+|으+|흠+|엄+|에+|으음+|어어+|음음+)$")
SOMETIMES = {"그", "저", "저기", "뭐", "막", "이제", "그니까", "약간"}
PAUSE = 0.25   # 이 이상 쉬고 홀로 나온 '그·저·뭐' 등은 군말
REACH = 0.6    # 군말 컷이 앞뒤 무음으로 넓어질 수 있는 최대 길이


def _bare(text: str) -> str:
    return re.sub(r"[.,!?…~\s]+", "", text)


def filler_flags(words: list[Word]) -> list[bool]:
    flags = [bool(ALWAYS.match(_bare(w.text))) for w in words]
    for i, w in enumerate(words):
        if flags[i] or _bare(w.text) not in SOMETIMES:
            continue
        before = w.start - words[i - 1].end if i > 0 else 9.0
        after = words[i + 1].start - w.end if i + 1 < len(words) else 9.0
        neighbours_filler = (i == 0 or flags[i - 1] or before >= PAUSE) and (i + 1 == len(words) or flags[i + 1]
                                                                              or after >= PAUSE)
        flags[i] = neighbours_filler
    return flags


def filler_words(tr: Transcript | None) -> set[tuple[float, float, str]]:
    """군말 단어의 (시작, 끝, 글자) 집합. 문장 단위로 판단한다(문장이 통째로 군말이면 모두)."""
    out: set[tuple[float, float, str]] = set()
    if not tr:
        return out
    for s in tr.sentences:
        flags = filler_flags(s.words)
        if s.words and all(flags[i] or _bare(w.text) in SOMETIMES for i, w in enumerate(s.words)):
            flags = [True] * len(s.words)  # '음 어 그'처럼 문장 전체가 군말
        out |= {(w.start, w.end, w.text) for w, f in zip(s.words, flags) if f}
    return out


def filler_spans(tr: Transcript | None) -> list[tuple[float, float]]:
    """잘라낼 구간: 이어진 군말 덩어리 + 앞뒤 쉼(최대 REACH)까지. 다음 말과 겹치지 않는다."""
    if not tr:
        return []
    words = tr.words()
    fill = filler_words(tr)
    spans, i = [], 0
    while i < len(words):
        if (words[i].start, words[i].end, words[i].text) not in fill:
            i += 1
            continue
        j = i
        while j + 1 < len(words) and (words[j + 1].start, words[j + 1].end, words[j + 1].text) in fill:
            j += 1
        lo = max(words[i - 1].end if i > 0 else 0.0, words[i].start - REACH)
        hi = min(words[j + 1].start if j + 1 < len(words) else words[j].end + REACH, words[j].end + REACH)
        spans.append((round(lo, 3), round(hi, 3)))
        i = j + 1
    return spans
