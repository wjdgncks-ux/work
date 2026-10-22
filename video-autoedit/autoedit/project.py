"""편집 프로젝트(out/<영상>/<플랫폼>/) 단위의 공통 처리. CLI와 수작업 편집기가 같이 쓴다.

폴더 구성
  project.json   원본 경로·분석 폴더·플랫폼·Brief·렌더 옵션·기획 엔진
  plan.json      편집 기획서(사람이 고치는 대상)
  report.md      진단 리포트
  <이름>_<플랫폼>.mp4 / preview.mp4
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .analyze import Analysis, analyze
from .pacing import fill_static_gaps, sanitize
from .plan import Brief, EditPlan
from .platforms import Platform, get_platforms
from .render import RenderOptions, render
from .report import build_report
from .scorecard import Scorecard, autofix, evaluate
from .styles import STYLES, Style
from .timeline import Caption, Piece, build_captions, build_timeline
from .transcribe import Transcript

RENDER_KEYS = ("font", "fonts_dir", "reframe", "sfx_dir", "assets", "bgm", "bgm_db", "highlight", "handle")


def finalize(plan: EditPlan, analyses: list[Analysis], platform: Platform, brief: Brief,
             *, auto_pace: bool = True) -> tuple[Scorecard, list[Piece], list[Caption]]:
    """검증·보정 → 타임라인·자막 → 진단. auto_pace=False면 사람이 넣은 강조를 건드리지 않는다."""
    style = STYLES[brief.style]
    sanitize(plan, analyses)
    if auto_pace:
        fill_static_gaps(plan, analyses, platform, style)
    fixes = autofix(plan, platform)
    timeline = build_timeline(plan, analyses, cut_silence=style.cut_silence)
    captions = build_captions(timeline, analyses, platform.caption_chars)
    card = evaluate(plan, timeline, captions, analyses, platform, brief.industry, style)
    card.fixes = fixes
    return card, timeline, captions


def render_options(render_cfg: dict, style: Style, *, preview: bool = False) -> RenderOptions:
    p = lambda k: Path(render_cfg[k]) if render_cfg.get(k) else None  # noqa: E731
    return RenderOptions(
        font=render_cfg.get("font") or "Noto Sans CJK KR", fonts_dir=p("fonts_dir"),
        reframe=render_cfg.get("reframe") or "auto", sfx_dir=p("sfx_dir"), assets_dir=p("assets"),
        bgm=p("bgm"), bgm_db=float(render_cfg.get("bgm_db") or -20.0),
        highlight=render_cfg.get("highlight") or "#FFE500", handle=render_cfg.get("handle") or "",
        zoom=style.zoom, preview=preview,
    )


@dataclass
class Project:
    pdir: Path
    meta: dict
    analyses: list[Analysis]

    @classmethod
    def load(cls, pdir: Path) -> "Project":
        pdir = Path(pdir)
        meta = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
        analyses = [analyze(src, i, Path(d), whisper_model=None)
                    for i, (src, d) in enumerate(zip(meta["sources"], meta["analysis_dirs"]))]
        return cls(pdir, meta, analyses)

    @property
    def platform(self) -> Platform:
        return get_platforms(self.meta["platform"])[0]

    @property
    def brief(self) -> Brief:
        return Brief(**self.meta["brief"])

    @property
    def style(self) -> Style:
        return STYLES[self.brief.style]

    @property
    def name(self) -> str:
        return Path(self.meta["sources"][0]).stem if len(self.meta["sources"]) == 1 else "merged"

    @property
    def plan_path(self) -> Path:
        return self.pdir / "plan.json"

    def load_plan(self) -> EditPlan:
        return EditPlan.model_validate_json(self.plan_path.read_text(encoding="utf-8"))

    def save_plan(self, plan: EditPlan) -> None:
        self.plan_path.write_text(plan.model_dump_json(indent=1), encoding="utf-8")

    def set_style(self, key: str) -> None:
        self.meta["brief"]["style"] = STYLES[key].key
        (self.pdir / "project.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=1), encoding="utf-8")

    def output_path(self, preview: bool = False) -> Path:
        return self.pdir / ("preview.mp4" if preview else f"{self.name}_{self.platform.key}.mp4")

    def save_transcript(self, index: int, transcript: Transcript) -> None:
        a = self.analyses[index]
        path = Path(self.meta["analysis_dirs"][index]) / "transcript.json"
        path.write_text(json.dumps(transcript.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        a.transcript = transcript

    def finalize(self, plan: EditPlan, *, auto_pace: bool = False):
        return finalize(plan, self.analyses, self.platform, self.brief, auto_pace=auto_pace)

    def render(self, plan: EditPlan, *, preview: bool = False, planner_suffix: str = "") -> tuple[dict, Scorecard]:
        card, timeline, captions = self.finalize(plan)
        self.save_plan(plan)  # 실제 렌더링한 보정본으로 갱신
        opts = render_options(self.meta.get("render", {}), self.style, preview=preview)
        info = render(plan, timeline, captions, self.analyses, self.platform, opts,
                      self.output_path(preview), self.pdir / ("work_preview" if preview else "work"))
        if not preview:
            planner = self.meta.get("planner", "") + planner_suffix
            (self.pdir / "report.md").write_text(build_report(plan, card, self.platform, planner, info),
                                                 encoding="utf-8")
        return info, card
