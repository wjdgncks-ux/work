"""레퍼런스 영상 → 스타일 카드.

1. 영상 확보: 올린 파일(권장) 또는 링크(yt-dlp, 내 채널·허락받은 영상만 — 플랫폼 약관상 무단 다운로드 금지)
2. 측정: 기존 분석기로 컷(장면 전환)·쉼·말 속도·소리 강조 빈도 → Rhythm(여러 개면 중앙값)
3. Claude: 대사(시각 포함)와 대표 프레임을 보고 후킹 형식·말투·CTA·자막 모양을 '형식'으로 정리(원문 복제 금지)
4. 저장: ~/.autoedit/styles/<id>.json. 채널별 기본 카드는 설정(channel_styles)에 둔다.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
import uuid
from pathlib import Path

from . import settings
from .analyze import Analysis, analyze, detect_scenes
from .media import extract_frame, probe
from .stylecard import Rhythm, StyleAnalysis, StyleCard, clean

MAX_SECONDS = 600
MAX_REFS = 8
FRAMES_PER_REF = 4
SCENE_THRESHOLD = 0.15  # 숏폼은 같은 구도에서 자르는 점프컷이 많아 원본 분석(0.32)보다 민감하게
CUT_FIELDS = ("avg_shot", "cuts_per_10s", "first_cut")

SYSTEM = """당신은 숏폼(릴스·쇼츠·틱톡) 편집 스타일 분석가다. 레퍼런스 영상들의 대사(타임코드)·측정값·대표 프레임을 보고,
다른 영상을 같은 '형식'으로 편집할 수 있게 스타일을 정리한다.

규칙:
- 여러 개면 공통 패턴만 뽑는다. 한 영상에만 있는 특징은 공통인 것처럼 쓰지 않는다. 1개면 summary에 표본 한계를 적는다.
- 문장을 그대로 옮기지 않는다. 후킹 문구는 형식 템플릿으로 일반화하고 내용 자리는 ○○로 비운다(예: "○○ 하는 사람 90%가 모르는 ○○").
  특정 인물·브랜드·상품 이름을 템플릿에 넣지 않는다.
- 자막 모양(위치·크기·글자색·강조색·배경·한 줄 글자 수·이모지)은 프레임에서 판단한다. 자막이 안 보이면 기본값(하단·흰색·외곽선·12자)을 쓰고 summary에 '자막 확인 불가'라고 적는다.
  색은 #RRGGBB.
- avoid에는 그대로 따라 하면 문제가 되는 것(원문·음악 재사용, 근거 없는 최상급·효능 단정, 낚시성 후킹 등)을 적는다.
- 측정값(rhythm)과 어긋나는 주장을 하지 않는다."""


class RefError(ValueError):
    pass


# ---------------- 저장소 ----------------
def store_dir() -> Path:
    d = settings.home() / "styles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def refs_dir() -> Path:
    d = settings.workspace() / "레퍼런스"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_cards() -> list[StyleCard]:
    cards = []
    for p in sorted(store_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            cards.append(StyleCard(**json.loads(p.read_text(encoding="utf-8"))))
        except (ValueError, TypeError):
            continue
    return cards


def load_card(card_id: str) -> StyleCard | None:
    p = store_dir() / f"{Path(card_id).name}.json"
    if not p.exists():
        return None
    return StyleCard(**json.loads(p.read_text(encoding="utf-8")))


def save_card(card: StyleCard) -> StyleCard:
    card = clean(card)
    card.id = card.id or uuid.uuid4().hex[:10]
    (store_dir() / f"{card.id}.json").write_text(card.model_dump_json(indent=1), encoding="utf-8")
    return card


def delete_card(card_id: str) -> None:
    (store_dir() / f"{Path(card_id).name}.json").unlink(missing_ok=True)
    defaults = {k: v for k, v in (settings.get("channel_styles") or {}).items() if v != card_id}
    settings.put(channel_styles=defaults)


def channel_default(profile: str | None) -> str | None:
    return (settings.get("channel_styles") or {}).get(profile or "") or None


def set_channel_default(profile: str, card_id: str | None) -> None:
    defaults = dict(settings.get("channel_styles") or {})
    if card_id:
        defaults[profile] = card_id
    else:
        defaults.pop(profile, None)
    settings.put(channel_styles=defaults)


# ---------------- 링크 받기 ----------------
def _fetch(url: str, dest: Path) -> Path:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RefError("링크 받기 기능(yt-dlp)이 설치되지 않았습니다. 영상 파일을 올려 주세요") from exc
    opts = {"outtmpl": str(dest / "%(id)s.%(ext)s"), "format": "best[ext=mp4]/best", "noplaylist": True,
            "quiet": True, "no_warnings": True, "max_filesize": 300 * 1024 * 1024}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))


def download_link(url: str, dest: Path) -> Path:
    url = url.strip()
    if not re.match(r"^https?://\S+$", url):
        raise RefError(f"링크 형식이 아닙니다: {url[:60]}")
    dest.mkdir(parents=True, exist_ok=True)
    try:
        path = _fetch(url, dest)
    except RefError:
        raise
    except Exception as exc:  # noqa: BLE001 — 사이트마다 오류가 제각각이라 이유만 보여 준다
        raise RefError(f"링크에서 영상을 받지 못했습니다({str(exc)[:120]}). 인스타그램은 로그인이 필요한 경우가 많습니다."
                             " 영상 파일을 올려 주세요") from exc
    if not path.exists():
        raise RefError("링크에서 받은 영상 파일을 찾지 못했습니다. 영상 파일을 올려 주세요")
    return path


# ---------------- 측정 ----------------
def _debounce(times: list[float], gap: float = 0.25) -> list[float]:
    out: list[float] = []
    for t in sorted(times):
        if not out or t - out[-1] >= gap:
            out.append(t)
    return out


def rhythm_of(a: Analysis) -> Rhythm:
    """컷이 하나도 감지되지 않으면 컷 관련 값은 0(=모름)으로 두어 편집에 반영하지 않는다."""
    dur = max(a.duration, 0.1)
    sc = _debounce(t for t in a.scene_changes if 0.2 < t < dur - 0.2)
    sil = [(s, e) for s, e in a.silences if e > s]
    sil_total = sum(e - s for s, e in sil)
    chars = sum(len(w.text.replace(" ", "")) for w in a.transcript.words()) if a.transcript else 0
    return Rhythm(
        duration=round(dur, 2), avg_shot=round(dur / (len(sc) + 1), 2) if sc else 0.0,
        cuts_per_10s=round(len(sc) / dur * 10, 2), first_cut=round(sc[0], 2) if sc else 0.0,
        speech_cps=round(chars / max(dur - sil_total, 1.0), 2) if chars else 0.0,
        silence_ratio=round(sil_total / dur, 3),
        median_pause=round(statistics.median(e - s for s, e in sil), 2) if sil else 0.0,
        peaks_per_10s=round(len(a.peaks) / dur * 10, 2),
    )


def combine(rhythms: list[Rhythm]) -> Rhythm:
    if not rhythms:
        return Rhythm()
    cut = [r for r in rhythms if r.avg_shot > 0] or rhythms  # 컷을 못 잰 영상은 컷 값 중앙값에서 뺀다
    return Rhythm(**{k: round(statistics.median(getattr(r, k) for r in (cut if k in CUT_FIELDS else rhythms)), 3)
                     for k in Rhythm.model_fields})


# ---------------- Claude 분석 ----------------
def _llm_ready() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def build_request(analyses: list[Analysis], rhythms: list[Rhythm], hook_frames: list[str]) -> list[dict]:
    from .planner_llm import _image_block
    content: list[dict] = [{"type": "text", "text": "## 측정값(중앙값)\n" + combine(rhythms).model_dump_json(indent=1)}]
    for i, (a, r) in enumerate(zip(analyses, rhythms)):
        lines = [f"## 레퍼런스 {i + 1}: 길이 {a.duration:.1f}s", "측정: " + r.model_dump_json()]
        if a.transcript and a.transcript.sentences:
            lines.append("대사(타임코드, 0~3초가 후킹):")
            lines += [f"[{s.start:.1f}-{s.end:.1f}] {s.text}" for s in a.transcript.sentences[:60]]
        else:
            lines.append("대사: 없음(음악·자막 위주 영상이거나 음성 인식 불가)")
        content.append({"type": "text", "text": "\n".join(lines)})
        frames = [(0.8, hook_frames[i])] if hook_frames[i] else []
        frames += a.frames[: FRAMES_PER_REF - len(frames)]
        for t, fp in frames:
            content.append({"type": "text", "text": f"레퍼런스 {i + 1} 프레임 @ {t:.1f}s"})
            content.append(_image_block(fp))
    content.append({"type": "text", "text": f"위 레퍼런스 {len(analyses)}개의 공통 편집 스타일을 StyleAnalysis로 정리하라."})
    return [{"role": "user", "content": content}]


def analyze_style(analyses: list[Analysis], rhythms: list[Rhythm], hook_frames: list[str]) -> tuple[StyleAnalysis | None, str]:
    if not _llm_ready():
        return None, "측정값만(Claude 키 없음)"
    try:
        import anthropic
    except ImportError:
        return None, "측정값만(anthropic 미설치)"
    from .planner_llm import MODEL, structured_call
    result = structured_call(anthropic.Anthropic(), system=SYSTEM, messages=build_request(analyses, rhythms, hook_frames),
                             output=StyleAnalysis, effort="medium", fallback="측정값만으로 스타일 카드 생성", max_tokens=12000)
    return (result, f"Claude ({MODEL})") if result else (None, "측정값만(Claude 분석 실패)")


# ---------------- 전체 ----------------
def build_card(videos: list[Path], *, name: str = "", links: list[str] = (), whisper_model: str | None = "small",
               progress=None) -> StyleCard:
    progress = progress or (lambda msg, frac: None)
    card_id = uuid.uuid4().hex[:10]
    root = refs_dir() / card_id
    paths = [Path(v) for v in videos]
    for k, url in enumerate(links):
        progress(f"레퍼런스 링크 받는 중 {k + 1}/{len(links)}", 0.05 + 0.15 * k / max(len(links), 1))
        paths.append(download_link(url, root / "links"))
    if not paths:
        raise RefError("레퍼런스 영상이 없습니다")
    if len(paths) > MAX_REFS:
        raise RefError(f"레퍼런스는 {MAX_REFS}개까지 넣을 수 있습니다")
    analyses, rhythms, hook_frames = [], [], []
    for i, p in enumerate(paths):
        progress(f"레퍼런스 분석 중 {i + 1}/{len(paths)} · {p.name}", 0.2 + 0.6 * i / len(paths))
        dur = probe(p).duration
        if dur > MAX_SECONDS:
            raise RefError(f"{p.name}: {dur / 60:.0f}분짜리입니다. 레퍼런스는 10분 이하 숏폼 영상을 넣어 주세요")
        work = root / "_analysis" / f"{i:02d}"
        a = analyze(p, i, work, whisper_model=whisper_model, n_frames=6)
        a.scene_changes = detect_scenes(str(p), SCENE_THRESHOLD)
        analyses.append(a)
        rhythms.append(rhythm_of(a))
        try:
            hook_frames.append(str(extract_frame(p, min(0.8, dur / 2), work / "hook.jpg")))
        except Exception:  # noqa: BLE001 — 첫 프레임이 없어도 분석은 계속
            hook_frames.append("")
    progress("스타일 정리 중(Claude)", 0.85)
    analysis, engine = analyze_style(analyses, rhythms, hook_frames)
    if not analysis:
        print(f"  · 스타일 카드: {engine}", file=sys.stderr)
    card = StyleCard(id=card_id, name=name.strip() or f"레퍼런스 {time.strftime('%m/%d %H:%M')}",
                     sources=[p.name for p in paths], samples=len(paths), rhythm=combine(rhythms), analysis=analysis,
                     analyzed_by=engine, created=time.strftime("%Y-%m-%d %H:%M"))
    return save_card(card)
