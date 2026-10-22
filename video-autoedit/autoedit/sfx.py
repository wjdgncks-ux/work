"""효과음 생성. 저작권 이슈를 피하려고 ffmpeg 신호 합성으로 만든다.
--sfx-dir 폴더에 whoosh/pop/ding/boom/boing/riser(.wav/.mp3) 를 두면 그 파일을 우선 사용."""

from __future__ import annotations

from pathlib import Path

from .media import ffmpeg

RECIPES = {
    # 대역 통과 노이즈를 짧게 부풀렸다 줄여 '휙'
    "whoosh": ("anoisesrc=d=0.45:c=pink:a=0.6,highpass=f=500,lowpass=f=5000,"
               "afade=t=in:d=0.22,afade=t=out:st=0.22:d=0.23"),
    # 높은 음에서 빠르게 떨어지는 '뽁'
    "pop": "aevalsrc='0.7*sin(2*PI*(500+1800*exp(-t*35))*t)*exp(-t*28)':d=0.18",
    # 두 배음 종소리 '띵'
    "ding": "aevalsrc='0.5*sin(2*PI*1760*t)*exp(-t*5)+0.25*sin(2*PI*2637*t)*exp(-t*7)':d=0.9",
    # 저역 스윕 '쿵'
    "boom": "aevalsrc='0.9*sin(2*PI*(45+90*exp(-t*10))*t)*exp(-t*4.5)':d=0.8",
    # 스프링처럼 흔들리는 '띠용'(유머)
    "boing": "aevalsrc='0.6*sin(2*PI*(260+140*sin(2*PI*13*t)*exp(-t*3))*t)*exp(-t*4)':d=0.7",
    # 반전 직전 긴장을 올리는 상승음
    "riser": "aevalsrc='0.45*t*sin(2*PI*(180+700*t*t)*t)+0.08*t*sin(2*PI*(360+1400*t*t)*t)':d=1.0,afade=t=out:st=0.9:d=0.1",
}

GAIN_DB = {"whoosh": -10, "pop": -8, "ding": -12, "boom": -6, "boing": -9, "riser": -12}


def ensure_sfx(cache_dir: Path, user_dir: Path | None = None) -> dict[str, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for kind, recipe in RECIPES.items():
        if user_dir:
            for ext in (".wav", ".mp3", ".m4a"):
                cand = user_dir / f"{kind}{ext}"
                if cand.exists():
                    out[kind] = cand
                    break
        if kind in out:
            continue
        path = cache_dir / f"{kind}.wav"
        if not path.exists():
            ffmpeg("-f", "lavfi", "-i", recipe, "-ac", "2", "-ar", "48000", str(path))
        out[kind] = path
    return out
