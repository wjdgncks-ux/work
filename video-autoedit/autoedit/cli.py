"""명령행 진입점.

  python -m autoedit app                                         # 시작 화면(일반 사용자는 이것만)
  python -m autoedit doctor                                      # 설치 점검
  python -m autoedit run 원본.mp4 --profile company            # 채널 프로필 적용
  python -m autoedit run 원본.mp4 --style funny --platform reels,tiktok
  python -m autoedit run *.mp4 --plan-only                       # 기획서·리포트만
  python -m autoedit edit out/원본/reels                          # 수작업 편집기(브라우저)
  python -m autoedit render out/원본/reels/plan.json             # 수정한 기획서로 렌더링
  python -m autoedit profiles                                    # 프로필 목록
  python -m autoedit voices clone "와이프 목소리" a.m4a b.m4a --consent "동의: 본인"
  python -m autoedit settings --elevenlabs-key <키>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .media import require_ffmpeg
from .pipeline import RunOptions, run_pipeline
from .profiles import list_profiles, load_profile
from .project import RENDER_KEYS, Project
from .styles import STYLES


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def cmd_run(args) -> int:
    require_ffmpeg()
    opts = RunOptions(
        profile=args.profile, style=args.style, platform=args.platform, mode=args.mode, out=Path(args.out),
        brief_path=args.brief, sponsored=args.sponsored, llm=args.llm, effort=args.effort, revise=args.revise,
        whisper=None if args.whisper == "none" else args.whisper, language=args.language,
        reanalyze=args.reanalyze, plan_only=args.plan_only,
        brief={k: getattr(args, k) for k in ("topic", "audience", "goal", "industry", "cta", "hook")},
        render={k: getattr(args, k, None) for k in RENDER_KEYS},
    )
    try:
        results = run_pipeline([Path(v) for v in args.videos], opts)
    except FileNotFoundError as exc:
        log(str(exc))
        return 2
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0


def cmd_render(args) -> int:
    """사람이 고친 plan.json으로 다시 진단·렌더링."""
    require_ffmpeg()
    proj = Project.load(Path(args.plan).parent)
    info, card = proj.render(proj.load_plan(), preview=args.preview, planner_suffix=" + 사람 수정")
    print(json.dumps({"score": card.score, **info}, ensure_ascii=False, indent=1))
    return 0


def cmd_doctor(_args) -> int:
    from .doctor import main as doctor_main
    return doctor_main()


def cmd_app(args) -> int:
    from .app import serve_app
    return serve_app(port=args.port, open_browser=not args.no_browser)


def cmd_edit(args) -> int:
    require_ffmpeg()
    from .editor import serve
    serve(Path(args.project), host=args.host, port=args.port, open_browser=not args.no_browser,
          whisper_model=None if args.whisper == "none" else args.whisper)
    return 0


def cmd_voices(args) -> int:
    """인물 지정 음성(목소리 복제)·AI 음성 관리. 엔진 키: autoedit settings --elevenlabs-key"""
    from . import voice
    store = voice.VoiceStore()
    try:
        if args.action == "list":
            for v in store.list():
                print(f"{v.id}  {v.name:<16} {v.kind:<6} {v.voice}  {v.note}  [{v.consent}]")
        elif args.action == "stock":
            for v in voice.get_engine().stock_voices():
                print(f"{v['voice']:<40} {v['name']:<16} {' · '.join(v['labels'].values())}")
        elif args.action == "clone":
            if not args.consent:
                log("목소리 주인의 동의가 필요합니다: --consent \"동의: 홍길동(2026-10-22)\"")
                return 2
            paths = [store.save_sample(Path(f).read_bytes(), Path(f).suffix)[0] for f in args.files]
            prof = voice.create_clone(args.name, paths, args.consent, store=store)
            print(f"만들었습니다: {prof.id} {prof.name} ({prof.note})")
        elif args.action == "say":
            info = voice.synthesize(args.text, args.voice, Path(args.out))
            print(json.dumps(info, ensure_ascii=False))
        elif args.action == "delete":
            prof = store.remove(args.id)
            print("삭제했습니다" if prof else "없는 목소리입니다")
    except voice.VoiceError as exc:
        log(str(exc))
        return 1
    return 0


def cmd_settings(args) -> int:
    from . import settings
    if args.elevenlabs_key is not None:
        settings.set_key("elevenlabs", args.elevenlabs_key)
    print(f"설정 위치: {settings.home() / 'config.json'}")
    print(f"ElevenLabs 키: {'설정됨' if settings.get_key('elevenlabs') else '없음'}")
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
    e.add_argument("--whisper", default="small", help="편집기에서 가져온 영상의 자동 캡션 모델 또는 none")
    e.set_defaults(func=cmd_edit)

    v = sub.add_parser("voices", help="AI 음성·인물 지정 음성(내 목소리) 관리")
    vs = v.add_subparsers(dest="action", required=True)
    vs.add_parser("list", help="내 목소리 목록")
    vs.add_parser("stock", help="AI 음성 목록(엔진 제공)")
    vc = vs.add_parser("clone", help="녹음 파일로 목소리 만들기")
    vc.add_argument("name")
    vc.add_argument("files", nargs="+", help="녹음 파일(총 1분 이상 권장)")
    vc.add_argument("--consent", default="", help="목소리 주인 동의 기록(필수)")
    vy = vs.add_parser("say", help="문장을 음성 파일로")
    vy.add_argument("voice", help="'elevenlabs:<voice_id>'")
    vy.add_argument("text")
    vy.add_argument("--out", default="voice_out")
    vd = vs.add_parser("delete")
    vd.add_argument("id")
    v.set_defaults(func=cmd_voices)

    st = sub.add_parser("settings", help="API 키 등 사용자 설정(~/.autoedit)")
    st.add_argument("--elevenlabs-key")
    st.set_defaults(func=cmd_settings)

    dr = sub.add_parser("doctor", help="설치 점검(ffmpeg·자막 기능·한글 글꼴·키·디스크)")
    dr.set_defaults(func=cmd_doctor)

    ap_ = sub.add_parser("app", help="시작 화면(새 프로젝트·최근 프로젝트·설정) — 바탕화면 아이콘이 실행하는 것")
    ap_.add_argument("--port", type=int, default=8765)
    ap_.add_argument("--no-browser", action="store_true")
    ap_.set_defaults(func=cmd_app)

    pr = sub.add_parser("profiles", help="채널 프로필 목록")
    pr.set_defaults(func=cmd_profiles)

    args = ap.parse_args(argv)
    return args.func(args)
