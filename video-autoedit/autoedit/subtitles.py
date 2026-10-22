"""ASS 자막 파일 생성: 대사 자막(키워드 색 강조), 후킹 타이틀, 텍스트 카드, CTA, 유료광고 배지."""

from __future__ import annotations

from dataclasses import dataclass

from .platforms import Platform
from .timeline import Caption


def ass_color(hex_rgb: str) -> str:
    """'#RRGGBB' → ASS '&H00BBGGRR&'(ASS는 BGR 순서)."""
    h = hex_rgb.lstrip("#").upper()
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&"


@dataclass
class Overlay:
    start: float
    end: float
    text: str
    style: str  # Hook / Card / CTA / Badge


def _ts(t: float) -> str:
    t = max(t, 0.0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "＼").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def _highlight(text: str, keywords: list[str], color: str) -> str:
    out = _esc(text)
    for kw in sorted({k for k in keywords if k}, key=len, reverse=True):
        k = _esc(kw)
        if k in out:
            out = out.replace(k, f"{{\\c{color}}}{k}{{\\r}}", 1)
    return out


def build_ass(platform: Platform, captions: list[Caption], overlays: list[Overlay],
              emphasis_marks: list[tuple[float, str]], font: str, highlight: str = "#FFE500") -> str:
    color = ass_color(highlight)
    W, H = platform.width, platform.height
    base = round(H * (0.042 if platform.vertical else 0.055))
    cap_margin = round(H * (platform.safe_bottom + 0.10))
    hook_margin = round(H * (platform.safe_top + 0.04))
    side = round(W * 0.07)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{base},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,{max(4, base // 12)},2,2,{side},{side},{cap_margin},1
Style: Hook,{font},{round(base * 1.35)},&H00FFFFFF,&H00FFFFFF,&H00111111,&H00111111,1,0,0,0,100,100,0,0,3,{round(base * 0.35)},0,8,{side},{side},{hook_margin},1
Style: Card,{font},{round(base * 1.2)},&H00111111,&H00111111,{color.rstrip("&")},{color.rstrip("&")},1,0,0,0,100,100,0,0,3,{round(base * 0.45)},0,5,{side},{side},0,1
Style: CTA,{font},{round(base * 1.1)},&H00FFFFFF,&H00FFFFFF,&H00E06A2A,&H00E06A2A,1,0,0,0,100,100,0,0,3,{round(base * 0.4)},0,2,{side},{side},{round(H * (platform.safe_bottom + 0.22))},1
Style: Handle,{font},{round(base * 0.5)},&H60FFFFFF,&H60FFFFFF,&H90000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,9,{side},{side},{round(H * platform.safe_top)},1
Style: Badge,{font},{round(base * 0.55)},&H00FFFFFF,&H00FFFFFF,&H80000000,&H80000000,1,0,0,0,100,100,0,0,3,{round(base * 0.2)},0,7,{side},{side},{round(H * platform.safe_top)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    pop_in = "{\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)}"
    for c in captions:
        kws = [kw for t, kw in emphasis_marks if c.start - 0.3 <= t <= c.end + 0.3]
        lines.append(f"Dialogue: 1,{_ts(c.start)},{_ts(c.end)},Caption,,0,0,0,,{pop_in}{_highlight(c.text, kws, color)}")
    card_y = round(H * 0.38)
    for o in overlays:
        text = _esc(o.text)
        if o.style == "Card":
            text = f"{{\\pos({W // 2},{card_y})\\fad(120,120)}}{pop_in}{text}"
        elif o.style in ("Hook", "CTA"):
            text = f"{{\\fad(80,150)}}{pop_in}{text}"
        layer = 3 if o.style in ("Badge", "Handle") else 2
        lines.append(f"Dialogue: {layer},{_ts(o.start)},{_ts(o.end)},{o.style},,0,0,0,,{text}")
    return header + "\n".join(lines) + "\n"
