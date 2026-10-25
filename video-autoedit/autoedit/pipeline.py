"""자동 편집 파이프라인(분석 → 기획 → 진단 → [렌더링]). 명령행과 시작 화면이 같이 쓴다."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import reference
from .analyze import Analysis, analyze
from .plan import Brief, EditPlan
from .planner_heuristic import attach_assets, plan_heuristic
from .platforms import Platform, get_platforms
from .profiles import Profile, load_profile
from .project import RENDER_KEYS, Project, finalize
from .report import build_report, issues_for_llm
from .scorecard import Scorecard
from .styles import STYLES

DEFAULT_RENDER = {"font": "auto", "reframe": "auto", "bgm_db": -20.0, "highlight": "#FFE500"}
Progress = Callable[[str, float], None]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


@dataclass
class RunOptions:
    profile: str | None = None
    style: str | None = None
    platform: str | None = None
    mode: str = "each"                 # each=영상마다 따로, merge=여러 원본을 한 편으로
    out: Path = Path("out")
    brief_path: str | None = None
    brief: dict = field(default_factory=dict)      # topic/audience/goal/industry/cta/hook 덮어쓰기
    sponsored: bool = False
    llm: str = "auto"
    effort: str = "high"
    revise: int = 1
    whisper: str | None = "small"
    language: str = "ko"
    reanalyze: bool = False
    plan_only: bool = False
    render: dict = field(default_factory=dict)     # font/reframe/assets/bgm… 덮어쓰기
    reference: str | None = None                    # 스타일 카드 id. None=채널 기본 카드, ""=쓰지 않음


def llm_available(mode: str) -> bool:
    if mode == "off":
        return False
    if mode == "on":
        return True
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _assets(d: str | None) -> list[str]:
    p = Path(d) if d else None
    if not p or not p.is_dir():
        return []
    return sorted(f.name for f in p.iterdir() if f.is_file() and not f.name.startswith("."))


def _add_tags(plan: EditPlan, tags: list[str]) -> None:
    for t in tags:
        if t not in plan.hashtags:
            plan.hashtags.insert(0, t)


def make_plan(analyses: list[Analysis], platform: Platform, brief: Brief, opts: RunOptions, render_cfg: dict,
              extra_tags: list[str]) -> tuple[EditPlan, Scorecard, list, list, str, Scorecard | None]:
    plan, planner, messages = None, "규칙 기반(heuristic)", []
    if llm_available(opts.llm):
        from .planner_llm import MODEL, plan_llm
        log(f"  · Claude 기획 요청 ({MODEL}, effort={opts.effort})")
        plan, messages = plan_llm(analyses, platform, brief, _assets(render_cfg.get("assets")), opts.effort)
        if plan:
            planner = f"Claude ({MODEL})"
    if plan is None:
        plan = plan_heuristic(analyses, platform, brief)
        assets = render_cfg.get("assets")
        attach_assets(plan, analyses, Path(assets) if assets else None)
    _add_tags(plan, extra_tags)
    card, timeline, captions = finalize(plan, analyses, platform, brief)
    before = None
    for _ in range(opts.revise if messages else 0):
        if not any(i.severity == "상" for i in card.issues):
            break
        from .planner_llm import revise_llm
        log(f"  · 진단 점수 {card.score} — '상' 이슈 수정 요청")
        revised = revise_llm(messages, issues_for_llm(card), opts.effort)
        if not revised:
            break
        revised.platform, revised.sponsored = platform.key, revised.sponsored or brief.sponsored
        _add_tags(revised, extra_tags)
        r_card, r_tl, r_caps = finalize(revised, analyses, platform, brief)
        if r_card.score >= card.score:
            before = card
            plan, card, timeline, captions = revised, r_card, r_tl, r_caps
            planner += " + 자가 수정 1회"
    return plan, card, timeline, captions, planner, before


def resolve(opts: RunOptions) -> tuple[Profile | None, Brief, dict, list[Platform]]:
    """우선순위: 명시 옵션 > 프로필 > 기본값."""
    profile = load_profile(opts.profile) if opts.profile else None
    brief_data = dict(profile.brief) if profile else {}
    if opts.brief_path:
        brief_data.update(json.loads(Path(opts.brief_path).read_text(encoding="utf-8")))
    if profile and profile.style:
        brief_data["style"] = profile.style
    brief_data.update({k: v for k, v in opts.brief.items() if v})
    if opts.style:
        brief_data["style"] = opts.style
    brief = Brief(**brief_data)
    ref_id = opts.reference if opts.reference is not None else reference.channel_default(profile.key if profile else None)
    if ref_id:
        brief.reference = reference.load_card(ref_id)
        if brief.reference is None:
            raise ValueError(f"스타일 카드를 찾지 못했습니다: {ref_id}")
    brief.sponsored = brief.sponsored or opts.sponsored or bool(profile and profile.sponsored)

    render_cfg = dict(DEFAULT_RENDER)
    if profile:
        render_cfg.update({k: v for k, v in profile.render.items() if v is not None})
        if profile.handle:
            render_cfg["handle"] = profile.handle
    for k in RENDER_KEYS:
        v = opts.render.get(k)
        if v is not None:
            render_cfg[k] = str(Path(v).resolve()) if isinstance(v, Path) else v
    platforms = get_platforms(opts.platform or (profile.platforms if profile and profile.platforms else "reels"))
    return profile, brief, render_cfg, platforms


def run_pipeline(videos: list[Path], opts: RunOptions, progress: Progress | None = None) -> list[dict]:
    """progress(설명, 0~1) 콜백으로 진행 상황을 알린다. 결과: 프로젝트별 요약 목록."""
    progress = progress or (lambda msg, frac: None)
    profile, brief, render_cfg, platforms = resolve(opts)
    missing = [str(v) for v in videos if not Path(v).exists()]
    if missing:
        raise FileNotFoundError(f"파일 없음: {missing}")
    groups = [[Path(v)] for v in videos] if opts.mode == "each" else [[Path(v) for v in videos]]
    out_root = Path(opts.out) / (profile.key if profile else "")
    log(f"채널: {profile.name if profile else '(프로필 없음)'} · 스타일: {STYLES[brief.style].label}"
        + (f" · 레퍼런스: {brief.reference.name}" if brief.reference else ""))
    # 세로 플랫폼(Reels·Shorts·TikTok)은 한 번 편집하고 내보낼 때 플랫폼별 파일을 만든다(같은 편집 두 번 하지 않게).
    vertical = [p for p in platforms if p.vertical]
    heads = ([(vertical[0], vertical[1:])] if vertical else []) + [(p, []) for p in platforms if not p.vertical]
    steps = sum(len(g) for g in groups) + len(heads) * len(groups)
    done = 0
    results = []
    for group in groups:
        name = group[0].stem if len(group) == 1 else "merged"
        root = out_root / name
        log(f"\n▶ {name}: 분석 중 ({len(group)}개 원본)")
        analyses = []
        for i, v in enumerate(group):
            progress(f"{v.name} 분석 중" + (" (자동 캡션 포함 — 영상 길이에 따라 몇 분)" if opts.whisper else ""), done / steps)
            analyses.append(analyze(v, i, root / "_analysis" / v.stem, whisper_model=opts.whisper,
                                    language=opts.language, force=opts.reanalyze))
            done += 1
        for a in analyses:
            log(f"  · {Path(a.path).name}: {a.duration:.1f}s, 대사 {len(a.transcript.sentences) if a.transcript else 0}문장, "
                f"피크 {len(a.peaks)}, 장면전환 {len(a.scene_changes)}")
        for platform, extras in heads:
            label = " + ".join(p.label for p in [platform, *extras])
            progress(f"{name} · {label} 편집 기획 중", done / steps)
            pdir = root / platform.key
            pdir.mkdir(parents=True, exist_ok=True)
            plan, card, timeline, captions, planner, before = make_plan(
                analyses, platform, brief, opts, render_cfg, profile.hashtags if profile else [])
            (pdir / "plan.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")
            project = {
                "sources": [str(v.resolve()) for v in group],
                "analysis_dirs": [str((root / "_analysis" / v.stem).resolve()) for v in group],
                "platform": platform.key, "platforms": [platform.key] + [e.key for e in extras],
                "brief": brief.model_dump(), "planner": planner,
                "profile": profile.key if profile else None, "render": render_cfg,
                "created": time.strftime("%Y-%m-%d %H:%M"), "language": opts.language,
                "review": {"status": "편집 중", "memo": "", "done": []}, "versions": [],
            }
            (pdir / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=1), encoding="utf-8")
            info = None
            if not opts.plan_only:
                progress(f"{name} · {label} 완성본 만드는 중", done / steps)
                log(f"  ▷ 렌더링 ({len(timeline)}컷, {card.metrics['duration']}s)")
                from . import settings
                info, _ = Project(pdir, project, analyses).render(plan, creator=settings.get("user_name", ""))
            (pdir / "report.md").write_text(build_report(plan, card, platform, planner, info, before), encoding="utf-8")
            log(f"  ✔ 진단 {card.score}/100 · {pdir / 'report.md'}")
            done += 1
            results.append({"name": name, "platform": platform.key, "score": card.score, "project": str(pdir),
                            "output": info["file"] if info else None, "report": str(pdir / "report.md")})
    progress("완료", 1.0)
    return results
