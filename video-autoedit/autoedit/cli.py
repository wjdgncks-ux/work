"""명령행 진입점.

  python -m autoedit run 원본1.mp4 원본2.mp4 --platform reels,shorts
  python -m autoedit run *.mp4 --plan-only        # 기획서·리포트만 → plan.json 수정
  python -m autoedit render out/원본1/reels/plan.json  # 수정한 기획서로 다시 렌더링
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .analyze import Analysis, analyze
from .media import require_ffmpeg
from .pacing import fill_static_gaps, sanitize
from .plan import Brief, EditPlan
from .planner_heuristic import attach_assets, plan_heuristic
from .platforms import Platform, get_platforms
from .render import RenderOptions, render
from .report import build_report, issues_for_llm
from .scorecard import Scorecard, autofix, evaluate
from .timeline import build_captions, build_timeline


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _llm_available(mode: str) -> bool:
    if mode == "off":
        return False
    if mode == "on":
        return True
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _assets(d: Path | None) -> list[str]:
    if not d or not d.is_dir():
        return []
    return sorted(f.name for f in d.iterdir() if f.is_file() and not f.name.startswith("."))


def _finalize(plan: EditPlan, analyses: list[Analysis], platform: Platform, brief: Brief) -> tuple[Scorecard, list, list]:
    sanitize(plan, analyses)
    fill_static_gaps(plan, analyses, platform)
    fixes = autofix(plan, platform)
    timeline = build_timeline(plan, analyses)
    captions = build_captions(timeline, analyses, platform.caption_chars)
    card = evaluate(plan, timeline, captions, analyses, platform, brief.industry)
    card.fixes = fixes
    return card, timeline, captions


def make_plan(analyses: list[Analysis], platform: Platform, brief: Brief, args) -> tuple[EditPlan, Scorecard, list, list, str, Scorecard | None]:
    plan, planner, messages = None, "규칙 기반(heuristic)", []
    if _llm_available(args.llm):
        from .planner_llm import MODEL, plan_llm
        log(f"  · Claude 기획 요청 ({MODEL}, effort={args.effort})")
        plan, messages = plan_llm(analyses, platform, brief, _assets(args.assets), args.effort)
        if plan:
            planner = f"Claude ({MODEL})"
    if plan is None:
        plan = plan_heuristic(analyses, platform, brief)
        attach_assets(plan, analyses, args.assets)
    card, timeline, captions = _finalize(plan, analyses, platform, brief)
    before = None
    for _ in range(args.revise if messages else 0):
        if not any(i.severity == "상" for i in card.issues):
            break
        from .planner_llm import revise_llm
        log(f"  · 진단 점수 {card.score} — '상' 이슈 수정 요청")
        revised = revise_llm(messages, issues_for_llm(card), args.effort)
        if not revised:
            break
        revised.platform, revised.sponsored = platform.key, revised.sponsored or brief.sponsored
        r_card, r_tl, r_caps = _finalize(revised, analyses, platform, brief)
        if r_card.score >= card.score:
            before = card
            plan, card, timeline, captions = revised, r_card, r_tl, r_caps
            planner += " + 자가 수정 1회"
    return plan, card, timeline, captions, planner, before


def _render_opts(ns: dict) -> RenderOptions:
    p = lambda k: Path(ns[k]) if ns.get(k) else None  # noqa: E731
    return RenderOptions(font=ns["font"], fonts_dir=p("fonts_dir"), reframe=ns["reframe"],
                         sfx_dir=p("sfx_dir"), assets_dir=p("assets"), bgm=p("bgm"), bgm_db=ns["bgm_db"])


def cmd_run(args) -> int:
    require_ffmpeg()
    platforms = get_platforms(args.platform)
    brief = Brief(**json.loads(Path(args.brief).read_text(encoding="utf-8"))) if args.brief else Brief()
    for k in ("topic", "audience", "goal", "industry", "cta", "hook"):
        if getattr(args, k):
            setattr(brief, k, getattr(args, k))
    brief.sponsored = brief.sponsored or args.sponsored

    videos = [Path(v) for v in args.videos]
    missing = [str(v) for v in videos if not v.exists()]
    if missing:
        log(f"파일 없음: {missing}")
        return 2
    groups = [[v] for v in videos] if args.mode == "each" else [videos]
    whisper = None if args.whisper == "none" else args.whisper
    out_root = Path(args.out)
    results = []
    for group in groups:
        name = group[0].stem if len(group) == 1 else "merged"
        root = out_root / name
        log(f"\n▶ {name}: 분석 중 ({len(group)}개 원본)")
        analyses = [analyze(v, i, root / "_analysis" / v.stem, whisper_model=whisper, language=args.language,
                            force=args.reanalyze) for i, v in enumerate(group)]
        for a in analyses:
            log(f"  · {Path(a.path).name}: {a.duration:.1f}s, 대사 {len(a.transcript.sentences) if a.transcript else 0}문장, "
                f"피크 {len(a.peaks)}, 장면전환 {len(a.scene_changes)}")
        for platform in platforms:
            pdir = root / platform.key
            pdir.mkdir(parents=True, exist_ok=True)
            log(f"  ▷ {platform.label} 기획")
            plan, card, timeline, captions, planner, before = make_plan(analyses, platform, brief, args)
            (pdir / "plan.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")
            project = {
                "sources": [str(v.resolve()) for v in group],
                "analysis_dirs": [str((root / "_analysis" / v.stem).resolve()) for v in group],
                "platform": platform.key, "brief": brief.model_dump(), "planner": planner,
                "render": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()
                           if k in ("font", "fonts_dir", "reframe", "sfx_dir", "assets", "bgm", "bgm_db")},
            }
            (pdir / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=1), encoding="utf-8")
            info = None
            if not args.plan_only:
                log(f"  ▷ 렌더링 ({len(timeline)}컷, {card.metrics['duration']}s)")
                info = render(plan, timeline, captions, analyses, platform, _render_opts(project["render"]),
                              pdir / f"{name}_{platform.key}.mp4", pdir / "work")
            (pdir / "report.md").write_text(build_report(plan, card, platform, planner, info, before), encoding="utf-8")
            log(f"  ✔ 진단 {card.score}/100 · {pdir / 'report.md'}")
            results.append({"name": name, "platform": platform.key, "score": card.score,
                            "output": info["file"] if info else None, "report": str(pdir / "report.md")})
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0


def cmd_render(args) -> int:
    """사람이 고친 plan.json으로 다시 진단·렌더링."""
    require_ffmpeg()
    plan_path = Path(args.plan)
    pdir = plan_path.parent
    project = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
    platform = get_platforms(project["platform"])[0]
    brief = Brief(**project["brief"])
    analyses = [analyze(src, i, Path(d), whisper_model=None)
                for i, (src, d) in enumerate(zip(project["sources"], project["analysis_dirs"]))]
    plan = EditPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    card, timeline, captions = _finalize(plan, analyses, platform, brief)
    plan_path.write_text(plan.model_dump_json(indent=1), encoding="utf-8")  # 실제 렌더링된 보정본으로 갱신
    name = Path(project["sources"][0]).stem if len(project["sources"]) == 1 else "merged"
    info = render(plan, timeline, captions, analyses, platform, _render_opts(project["render"]),
                  pdir / f"{name}_{platform.key}.mp4", pdir / "work")
    (pdir / "report.md").write_text(build_report(plan, card, platform, project["planner"] + " + 사람 수정", info),
                                    encoding="utf-8")
    print(json.dumps({"score": card.score, **info}, ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="autoedit", description="SNS 숏폼 자동 편집")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="분석→기획→진단→렌더링")
    r.add_argument("videos", nargs="+")
    r.add_argument("--platform", default="reels", help="reels,shorts,tiktok,youtube (쉼표로 여러 개)")
    r.add_argument("--mode", choices=["each", "merge"], default="each", help="each=영상마다 따로, merge=여러 원본을 한 편으로")
    r.add_argument("--out", default="out")
    r.add_argument("--brief", help="Brief JSON 파일 경로")
    r.add_argument("--topic"); r.add_argument("--audience"); r.add_argument("--cta"); r.add_argument("--hook")
    r.add_argument("--goal", choices=["reach", "conversion"])
    r.add_argument("--industry", choices=["general", "beauty", "medical", "supplement", "local", "realestate", "saas"])
    r.add_argument("--sponsored", action="store_true", help="협찬/광고 영상 → 유료광고 표기 강제")
    r.add_argument("--llm", choices=["auto", "on", "off"], default="auto")
    r.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default="high")
    r.add_argument("--revise", type=int, default=1, help="Claude 자가 수정 최대 횟수")
    r.add_argument("--whisper", default="small", help="faster-whisper 모델(tiny/base/small/medium/large-v3) 또는 none")
    r.add_argument("--language", default="ko")
    r.add_argument("--reanalyze", action="store_true")
    r.add_argument("--plan-only", action="store_true")
    r.add_argument("--assets", type=Path, help="자료화면 폴더(파일명을 대사 키워드와 매칭)")
    r.add_argument("--sfx-dir", type=Path)
    r.add_argument("--bgm", type=Path, help="배경음악(사용권 있는 파일만). 목소리 나올 때 자동으로 줄어듦")
    r.add_argument("--bgm-db", type=float, default=-20.0)
    r.add_argument("--reframe", choices=["auto", "crop", "blur"], default="auto")
    r.add_argument("--font", default="Noto Sans CJK KR")
    r.add_argument("--fonts-dir", type=Path)
    r.set_defaults(func=cmd_run)

    rr = sub.add_parser("render", help="수정한 plan.json으로 다시 렌더링")
    rr.add_argument("plan")
    rr.set_defaults(func=cmd_render)

    args = ap.parse_args(argv)
    return args.func(args)
