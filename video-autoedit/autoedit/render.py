"""렌더링: 조각별 리프레이밍·줌 펀치 → 무손실 이어붙이기 → 자막/자료화면/효과음/BGM/라우드니스 최종 합성."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from .analyze import Analysis
from .media import ffmpeg, probe
from .plan import EditPlan
from .platforms import Platform
from .sfx import GAIN_DB, ensure_sfx
from .subtitles import Overlay, build_ass
from .timeline import Caption, Piece, map_time, total_duration

@dataclass
class RenderOptions:
    font: str = "Noto Sans CJK KR"
    fonts_dir: Path | None = None
    reframe: str = "auto"  # auto / crop / blur
    sfx_dir: Path | None = None
    assets_dir: Path | None = None
    bgm: Path | None = None
    bgm_db: float = -20.0
    crf: int = 20
    preset: str = "medium"
    zoom: float = 0.14            # 줌 펀치 배율 증가분(스타일에서 결정)
    highlight: str = "#FFE500"    # 자막 강조 색(채널 프로필)
    handle: str = ""              # 채널 핸들 워터마크(예: @mychannel)
    preview: bool = False         # 저화질 빠른 미리보기(수작업 편집 확인용)


def preview_platform(p: Platform) -> Platform:
    return replace(p, width=p.width // 2 // 2 * 2, height=p.height // 2 // 2 * 2)


def _reframe_filter(a: Analysis, p: Platform, mode: str) -> str:
    W, H = p.width, p.height
    cover = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if mode == "auto":
        mode = "crop" if abs(a.info.aspect - W / H) < 0.25 or (a.info.aspect < 1) == p.vertical else "blur"
    if mode == "crop":
        return f"[0:v]{cover},setsar=1"
    return (f"[0:v]split[bg][fg];[bg]{cover},boxblur=24:2,eq=brightness=-0.08[bgb];"
            f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")


def _piece_effects(plan: EditPlan, piece: Piece, p: Platform, zoom: float) -> str:
    punches, flashes = [], []
    for e in plan.emphases:
        if e.source == piece.source and piece.start <= e.t < piece.end:
            (punches if e.kind == "zoom_punch" else flashes).append(round((e.t - piece.start) / piece.speed, 3))
    chain = f",setpts=(PTS-STARTPTS)/{piece.speed}" if piece.speed != 1.0 else ""
    chain += f",fps={p.fps}"
    if punches:
        terms = "+".join(
            f"{zoom}*min(1,max(0,(it-{a})/0.08))*min(1,max(0,({a}+1.0-it)/0.3))" for a in punches)
        chain += (f",zoompan=z='1+{terms}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                  f":d=1:s={p.width}x{p.height}:fps={p.fps}")
    for a in flashes:
        chain += f",eq=brightness=0.22:enable='between(t,{a},{a + 0.1})'"
    return chain + ",format=yuv420p[v]"


def render_pieces(plan: EditPlan, timeline: list[Piece], analyses: list[Analysis], p: Platform,
                  opts: RenderOptions, work: Path) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    listing = []
    for i, piece in enumerate(timeline):
        a = analyses[piece.source]
        out = work / f"piece_{i:03d}.mkv"
        dur = piece.duration  # 출력 길이(빨리감기 반영)
        graph = _reframe_filter(a, p, opts.reframe) + _piece_effects(plan, piece, p, opts.zoom)
        fade = min(0.02, dur / 4)
        args = ["-ss", f"{piece.start:.3f}", "-t", f"{piece.src_duration:.3f}", "-i", a.path]
        if a.info.has_audio and piece.speed == 1.0:
            graph += (f";[0:a]aresample=48000,aformat=channel_layouts=stereo,"
                      f"afade=t=in:d={fade},afade=t=out:st={dur - fade:.3f}:d={fade}[a]")
        else:  # 원음 없음 또는 빨리감기 구간 → 무음(효과음·BGM이 채움)
            args += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            graph += ";[1:a]anull[a]"
        ffmpeg(*args, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
               "-c:v", "libx264", "-preset", "ultrafast" if opts.preview else "veryfast",
               "-crf", "26" if opts.preview else "16", "-g", str(p.fps),
               "-c:a", "pcm_s16le", str(out))
        listing.append(f"file '{out.resolve()}'")
    concat = work / "concat.txt"
    concat.write_text("\n".join(listing) + "\n", encoding="utf-8")
    body = work / "body.mkv"
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(body))
    return body


def _overlays(plan: EditPlan, timeline: list[Piece], dur: float, handle: str = "") -> list[Overlay]:
    out: list[Overlay] = []
    hook_end = sum(x.duration for x in timeline if x.role == "hook") or 3.0
    if plan.hook_text:
        out.append(Overlay(0.0, min(max(hook_end, 2.0), 3.5, dur), plan.hook_text, "Hook"))
    for ins in plan.inserts:
        t = map_time(timeline, ins.source, ins.t)
        if t is not None and ins.kind == "text_card" and ins.text:
            out.append(Overlay(t, min(t + ins.duration, dur), ins.text, "Card"))
    if plan.cta_text:
        out.append(Overlay(max(dur - 3.0, 0), dur, plan.cta_text, "CTA"))
    if plan.sponsored:  # 영상 전체가 추천·보증이면 처음~끝 지속 표기
        out.append(Overlay(0.0, dur, "유료광고", "Badge"))
    if handle:
        out.append(Overlay(0.0, dur, handle, "Handle"))
    return out


def compose(plan: EditPlan, timeline: list[Piece], captions: list[Caption], analyses: list[Analysis],
            p: Platform, opts: RenderOptions, body: Path, out: Path, work: Path) -> Path:
    dur = total_duration(timeline)
    W, H = p.width, p.height
    marks = []
    for e in plan.emphases:
        t = map_time(timeline, e.source, e.t)
        if t is not None and e.keyword:
            marks.append((t, e.keyword))
    ass = work / "subs.ass"
    ass.write_text(build_ass(p, captions, _overlays(plan, timeline, dur, opts.handle), marks, opts.font,
                             opts.highlight), encoding="utf-8")

    inputs: list[str] = ["-i", str(body)]
    vchain: list[str] = []
    cur = "[0:v]"
    n = 1
    for k, ins in enumerate(plan.inserts):
        t = map_time(timeline, ins.source, ins.t)
        if t is None:
            continue
        d = min(ins.duration, dur - t)
        if d <= 0.2:
            continue
        if ins.kind == "text_card":
            vchain.append(f"{cur}drawbox=x=0:y=ih*0.30:w=iw:h=ih*0.16:color=black@0.45:t=fill"
                          f":enable='between(t,{t:.3f},{t + d:.3f})'[vb{k}]")
            cur = f"[vb{k}]"
            continue
        asset = (opts.assets_dir / ins.asset) if opts.assets_dir and ins.asset else None
        if not asset or not asset.exists():
            continue
        is_img = asset.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        inputs += ["-itsoffset", f"{t:.3f}"] + (["-loop", "1"] if is_img else []) + ["-t", f"{d:.3f}", "-i", str(asset)]
        bw, bh = round(W * 0.86) // 2 * 2, round(H * (0.40 if p.vertical else 0.55)) // 2 * 2
        y = round(H * (p.safe_top + 0.06)) if p.vertical else round(H * 0.08)
        vchain.append(f"[{n}:v]scale={bw}:{bh}:force_original_aspect_ratio=decrease,"
                      f"pad=iw+16:ih+16:8:8:color=white,format=yuva420p,"
                      f"fade=t=in:st={t:.3f}:d=0.15:alpha=1[ins{k}]")
        vchain.append(f"{cur}[ins{k}]overlay=x=(W-w)/2:y={y}:eof_action=pass"
                      f":enable='between(t,{t:.3f},{t + d:.3f})'[vo{k}]")
        cur = f"[vo{k}]"
        n += 1
    ass_arg = str(ass).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    fonts = f":fontsdir='{opts.fonts_dir}'" if opts.fonts_dir else ""
    vchain.append(f"{cur}ass='{ass_arg}'{fonts},format=yuv420p[vout]")

    # 오디오: 목소리 + 효과음 + (선택) BGM 덕킹 → 라우드니스 정규화
    sfx_files = ensure_sfx(work.parent / "_sfx", opts.sfx_dir)
    achain: list[str] = []
    mix = ["[voice]"]
    achain.append("[0:a]asplit=2[voice][key]" if opts.bgm else "[0:a]anull[voice]")
    for j, cue in enumerate(plan.sfx):
        t = map_time(timeline, cue.source, cue.t)
        if t is None:
            continue
        if cue.kind == "whoosh":  # 전환음은 컷 직전에 시작해야 자연스럽다
            t = max(0.0, t - 0.2)
        elif cue.kind == "riser":  # 상승음은 해당 시점에 '도달'해야 한다
            t = max(0.0, t - 1.0)
        inputs += ["-i", str(sfx_files[cue.kind])]
        ms = int(t * 1000)
        achain.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,"
                      f"volume={GAIN_DB[cue.kind]}dB,adelay={ms}|{ms}[s{j}]")
        mix.append(f"[s{j}]")
        n += 1
    if opts.bgm:
        inputs += ["-stream_loop", "-1", "-i", str(opts.bgm)]
        achain.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,volume={opts.bgm_db}dB[bgm]")
        achain.append("[bgm][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[bduck]")
        mix.append("[bduck]")
        n += 1
    achain.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first,"
                  f"loudnorm=I={p.loudness_lufs}:TP=-1.5:LRA=11,aresample=48000[aout]")

    graph = ";".join(vchain + achain)
    script = work / "graph.txt"
    script.write_text(graph, encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(*inputs, "-filter_complex_script", str(script), "-map", "[vout]", "-map", "[aout]",
           "-t", f"{dur:.3f}", "-r", str(p.fps),
           "-c:v", "libx264", "-preset", "ultrafast" if opts.preview else opts.preset,
           "-crf", "28" if opts.preview else str(opts.crf), "-profile:v", "high",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", str(out))
    return out


def measure_lufs(path: Path) -> float | None:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-vn",
                           "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", proc.stderr)
    return float(m[-1]) if m else None


def correct_loudness(out: Path, target: float, tolerance: float = 0.7) -> None:
    """1패스 loudnorm은 짧은 클립에서 1~2LU 빗나가므로 측정 후 고정 게인으로 2차 보정(영상은 재인코딩 없음)."""
    lufs = measure_lufs(out)
    if lufs is None or abs(lufs - target) <= tolerance:
        return
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    ffmpeg("-i", str(out), "-map", "0", "-c:v", "copy", "-af", f"volume={target - lufs:.2f}dB,alimiter=limit=0.84:level=0",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp))
    tmp.replace(out)


def verify(out: Path, p: Platform) -> dict:
    info = probe(out)
    lufs = measure_lufs(out)
    cover = out.with_suffix(".cover.jpg")
    ffmpeg("-ss", "0.6", "-i", str(out), "-frames:v", "1", "-q:v", "3", str(cover))
    return {
        "file": str(out), "duration": round(info.duration, 2), "size": f"{info.width}x{info.height}",
        "spec_ok": (info.width, info.height) == (p.width, p.height) and info.duration <= p.max_seconds,
        "loudness_lufs": lufs, "cover": str(cover),
    }


def render(plan: EditPlan, timeline: list[Piece], captions: list[Caption], analyses: list[Analysis],
           p: Platform, opts: RenderOptions, out: Path, work: Path) -> dict:
    if opts.preview:
        p = preview_platform(p)
    body = render_pieces(plan, timeline, analyses, p, opts, work / "pieces")
    compose(plan, timeline, captions, analyses, p, opts, body, out, work)
    correct_loudness(out, p.loudness_lufs)
    result = verify(out, p)
    result["preview"] = opts.preview
    (work / ("preview.json" if opts.preview else "render.json")).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
