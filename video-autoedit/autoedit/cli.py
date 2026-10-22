"""명령행 진입점.

  python -m autoedit run 원본.mp4 --profile company            # 채널 프로필 적용
  python -m autoedit run 원본.mp4 --style funny --platform reels,tiktok
  python -m autoedit run *.mp4 --plan-only                       # 기획서·리포트만
  python -m autoedit edit out/원본/reels                          # 수작업 편집기(브라우저)
  python -m autoedit render out/원본/reels/plan.json             # 수정한 기획서로 렌더링
  python -m autoedit profiles                                    # 프로필 목록
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .analyze import Analysis, analyze
from .media import require_ffmpeg
from .plan import Brief, EditPlan
from .planner_heuristic import attach_assets, plan_heuristic
from .platforms import Platform, get_platforms
from .profiles import list_profiles, load_profile
from .project import RENDER_KEYS, Project, finalize, render_options
from .render import render
from .report import build_report, issues_for_llm
from .scorecard import Scorecard
from .styles import STYLES

DEFAULT_RENDER = {"font": "Noto Sans CJK KR", "reframe": "auto", "bgm_db": -20.0, "highlight": "#FFE500"}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _llm_available(mode: str) -> bool:
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


def make_plan(analyses: list[Analysis], platform: Platform, brief: Brief, args, render_cfg: dict,
              extra_tags: list[str]) -> tuple[EditPlan, Scorecard, list, list, str, Scorecard | None]:
    plan, planner, messages = None, "규칙 기반(heuristic)", []
    if _llm_available(args.llm):
        from .planner_llm import MODEL, plan_llm
        log(f"  · Claude 기획 요청 ({MODEL}, effort={args.effort})")
        plan, messages = plan_llm(analyses, platform, brief, _assets(render_cfg.get("assets")), args.effort)
        if plan:
            planner = f"Claude ({MODEL})"
    if plan is None:
        plan = plan_heuristic(analyses, platform, brief)
        assets = render_cfg.get("assets")
        attach_assets(plan, analyses, Path(assets) if assets else None)
    _add_tags(plan, extra_tags)
    card, timeline, captions = finalize(plan, analyses, platform, brief)
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
        _add_tags(revised, extra_tags)
        r_card, r_tl, r_caps = finalize(revised, analyses, platform, brief)
        if r_card.score >= card.score:
            before = card
            plan, card, timeline, captions = revised, r_card, r_tl, r_caps
            planner += " + 자가 수정 1회"
    return plan, card, timeline, captions, planner, before


def _add_tags(plan: EditPlan, tags: list[str]) -> None:
    for t in tags:
        if t not in plan.hashtags:
            plan.hashtags.insert(0, t)


def cmd_run(args) -> int:
    require_ffmpeg()
    profile = load_profile(args.profile) if args.profile else None

    # 우선순위: 명령행 > 프로필 > 기본값
    brief_data = dict(profile.brief) if profile else {}
    if args.brief:
        brief_data.update(json.loads(Path(args.brief).read_text(encoding="utf-8")))
    if profile and profile.style:
        brief_data["style"] = profile.style
    for k in ("topic", "audience", "goal", "industry", "cta", "hook", "style"):
        if getattr(args, k):
            brief_data[k] = getattr(args, k)
    brief = Brief(**brief_data)
    brief.sponsored = brief.sponsored or args.sponsored or bool(profile and profile.sponsored)

    render_cfg = dict(DEFAULT_RENDER)
    if profile:
        render_cfg.update({k: v for k, v in profile.render.items() if v is not None})
        if profile.handle:
            render_cfg["handle"] = profile.handle
    for k in RENDER_KEYS:
        v = getattr(args, k, None)
        if v is not None:
            render_cfg[k] = str(Path(v).resolve()) if isinstance(v, Path) else v

    platforms = get_platforms(args.platform or (profile.platforms if profile and profile.platforms else "reels"))
    videos = [Path(v) for v in args.videos]
    missing = [str(v) for v in videos if not v.exists()]
    if missing:
        log(f"파일 없음: {missing}")
        return 2
    groups = [[v] for v in videos] if args.mode == "each" else [videos]
    whisper = None if args.whisper == "none" else args.whisper
    out_root = Path(args.out) / (profile.key if profile else "")
    log(f"채널: {profile.name if profile else '(프로필 없음)'} · 스타일: {STYLES[brief.style].label}")
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
            plan, card, timeline, captions, planner, before = make_plan(
                analyses, platform, brief, args, render_cfg, profile.hashtags if profile else [])
            (pdir / "plan.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")
            project = {
                "sources": [str(v.resolve()) for v in group],
                "analysis_dirs": [str((root / "_analysis" / v.stem).resolve()) for v in group],
                "platform": platform.key, "brief": brief.model_dump(), "planner": planner,
                "profile": profile.key if profile else None, "render": render_cfg,
            }
            (pdir / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=1), encoding="utf-8")
            info = None
            if not args.plan_only:
                log(f"  ▷ 렌더링 ({len(timeline)}컷, {card.metrics['duration']}s)")
                opts = render_options(render_cfg, STYLES[brief.style])
                info = render(plan, timeline, captions, analyses, platform, opts,
                              pdir / f"{name}_{platform.key}.mp4", pdir / "work")
            (pdir / "report.md").write_text(build_report(plan, card, platform, planner, info, before), encoding="utf-8")
            log(f"  ✔ 진단 {card.score}/100 · {pdir / 'report.md'}")
            results.append({"name": name, "platform": platform.key, "score": card.score, "project": str(pdir),
                            "output": info["file"] if info else None, "report": str(pdir / "report.md")})
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0


def cmd_render(args) -> int:
    """사람이 고친 plan.json으로 다시 진단·렌더링."""
    require_ffmpeg()
    proj = Project.load(Path(args.plan).parent)
    info, card = proj.render(proj.load_plan(), preview=args.preview, planner_suffix=" + 사람 수정")
    print(json.dumps({"score": card.score, **info}, ensure_ascii=False, indent=1))
    return 0


def cmd_edit(args) -> int:
    require_ffmpeg()
    from .editor import serve
    serve(Path(args.project), host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_profiles(_args) -> int:
    for p in list_profiles():
        prof = load_profile(str(p))
        print(f"{p.stem:<10} {prof.name} · 스타일 {prof.style or '-'} · 플랫폼 {prof.platforms or '-'} · {prof.handle}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="autoedit", description="SNS 숏폼 자동 편집")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="분석→기획→진단→렌더링")
    r.add_argument("videos", nargs="+")
    r.add_argument("--profile", help="채널 프로필 이름(profiles/<이름>.json) 또는 경로")
    r.add_argument("--style", choices=sorted(STYLES), help="explainer=설명형, funny=유머, demo=제품 시연")
    r.add_argument("--platform", help="reels,shorts,tiktok,youtube (쉼표로 여러 개)")
    r.add_argument("--mode", choices=["each", "merge"], default="each", help="each=영상마다 따로, merge=여러 원본을 한 편으로")
    r.add_argument("--out", default="out")
    r.add_argument("--brief", help="Brief JSON 파일 경로")
    r.add_argument("--topic"); r.add_argument("--audience"); r.add_argument("--cta"); r.add_argument("--hook")
    r.add_argument("--goal", choices=["reach", "conversion"])
    r.add_argument("--industry", choices=["general", "beauty", "medical", "supplement", "local", "realestate", "saas"])
    r.add_argument("--sponsored", action="store_true", help="협찬/광고·이해관계 영상 → 유료광고 표기 강제")
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
    r.add_argument("--bgm-db", type=float)
    r.add_argument("--reframe", choices=["auto", "crop", "blur"])
    r.add_argument("--font")
    r.add_argument("--fonts-dir", type=Path)
    r.add_argument("--highlight", help="자막 강조색 #RRGGBB")
    r.add_argument("--handle", help="채널 핸들 워터마크(예: @mychannel)")
    r.set_defaults(func=cmd_run)

    rr = sub.add_parser("render", help="수정한 plan.json으로 다시 렌더링")
    rr.add_argument("plan")
    rr.add_argument("--preview", action="store_true", help="저화질 빠른 미리보기")
    rr.set_defaults(func=cmd_render)

    e = sub.add_parser("edit", help="브라우저 수작업 편집기")
    e.add_argument("project", help="out/<영상>/<플랫폼> 폴더")
    e.add_argument("--host", default="127.0.0.1")
    e.add_argument("--port", type=int, default=8765)
    e.add_argument("--no-browser", action="store_true")
    e.set_defaults(func=cmd_edit)

    pr = sub.add_parser("profiles", help="채널 프로필 목록")
    pr.set_defaults(func=cmd_profiles)

    args = ap.parse_args(argv)
    return args.func(args)
