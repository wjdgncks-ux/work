"""기획이 '영상을 보게' 하는 자료: 장면 시트(약 1초 간격 프레임을 4×3 격자로 붙인 이미지)와 움직임 곡선.

예전에는 원본 전체에서 프레임 6장만 보여 줘서, 말 없이 화면이 재미있는 영상(반려동물·브이로그·시연)은
기획이 어디가 재미있는지 몰랐다. 이제 모든 원본을 합쳐 최대 MAX_FRAMES장을 시트로 보여 주고,
프레임 차이로 잰 움직임이 큰 순간(motion peaks)을 함께 알려 준다. 결과는 분석 폴더에 캐시한다.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .media import ffmpeg

MAX_FRAMES = 96      # 모든 원본을 합친 시트 프레임 수 상한(토큰 비용)
COLS, ROWS = 4, 3
MOTION_FPS = 4       # 움직임은 초당 4번 잰다


def _workdir(a) -> Path | None:
    return Path(a.frames[0][1]).parent.parent if a.frames else None


def step_for(analyses) -> float:
    """시트 프레임 간격(초): 전체 길이를 MAX_FRAMES로 나눈 값, 최소 1초, 0.5초 단위."""
    total = sum(a.duration for a in analyses)
    return max(1.0, round((total / MAX_FRAMES) * 2 + 0.49) / 2)


def motion(a) -> list[float]:
    """0.25초마다 화면 변화량(이전 프레임과의 밝기 차이 평균). 분석 폴더에 캐시."""
    wd = _workdir(a)
    cache = wd / "motion.json" if wd else None
    if cache and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(a.path), "-an", "-vf",
                           f"fps={MOTION_FPS},scale=160:-2,signalstats,metadata=print:key=lavfi.signalstats.YDIF:file=-",
                           "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    vals = [round(float(x), 2) for x in re.findall(r"lavfi\.signalstats\.YDIF=([\d.]+)", proc.stdout)]
    if cache and vals:
        cache.write_text(json.dumps(vals), encoding="utf-8")
    return vals


def motion_peaks(vals: list[float], limit: int = 12, gap: float = 1.5) -> list[float]:
    """움직임이 큰 순간(초). 평소보다 확실히 큰 것만, 서로 gap초 이상 떨어지게."""
    if len(vals) < 4:
        return []
    srt = sorted(vals)
    base = srt[len(srt) // 2]                 # 평소 움직임(중앙값)
    thr = max(base * 1.8, base + 1.5)         # 평소의 1.8배 이상이면서 확실히 큰 변화
    cand = [i for i in sorted(range(len(vals)), key=lambda i: -vals[i]) if vals[i] >= thr]
    picked: list[float] = []
    for i in cand:
        t = i / MOTION_FPS
        if all(abs(t - p) >= gap for p in picked):
            picked.append(t)
        if len(picked) >= limit:
            break
    return sorted(round(t, 2) for t in picked)


def sheets(a, step: float) -> list[tuple[float, Path]]:
    """[(첫 칸 시각, 시트 이미지)] — 칸은 왼쪽 위부터 오른쪽으로, step초 간격."""
    wd = _workdir(a)
    if not wd:
        return []
    out_dir = wd / f"sheets_{step:g}s"
    per = COLS * ROWS
    if not out_dir.exists() or not any(out_dir.glob("s*.jpg")):
        out_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg("-i", str(a.path), "-an", "-vf", f"fps=1/{step:g},scale=240:-2,tile={COLS}x{ROWS}",
               "-fps_mode", "vfr", "-q:v", "5", str(out_dir / "s%03d.jpg"))
    files = sorted(out_dir.glob("s*.jpg"))
    return [(round(k * per * step, 2), f) for k, f in enumerate(files)]
