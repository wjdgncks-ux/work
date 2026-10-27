"""편집 프로젝트(out/<영상>/<플랫폼>/) 단위의 공통 처리. CLI와 수작업 편집기가 같이 쓴다.

폴더 구성
  project.json   원본 경로·분석 폴더·플랫폼·Brief·렌더 옵션·기획 엔진·컨펌 상태·버전 기록
  plan.json      편집 기획서(사람이 고치는 대상), .history/에 자동 백업
  report.md      진단 리포트
  <이름>_<플랫폼>_v<N>.mp4 + 검수표_v<N>.html + 업로드문구_v<N>.txt   완성본(버전마다 남김)
  preview.mp4    빠른 미리보기
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .analyze import Analysis, analyze
from .pacing import fill_static_gaps, sanitize
from .plan import Brief, Clip, EditPlan
from .platforms import Platform, get_platforms
from .render import RenderOptions, render
from .report import build_report
from .scorecard import Scorecard, autofix, evaluate
from .styles import STYLES, Style
from .stylecard import StyleCard, effective_platform, effective_style
from .timeline import Caption, Piece, build_captions, build_timeline
from .transcribe import Transcript

RENDER_KEYS = ("font", "fonts_dir", "reframe", "sfx_dir", "assets", "bgm", "bgm_db", "highlight", "handle")
REVIEW_STATES = ("편집 중", "컨펌 요청", "수정 요청", "승인", "업로드 완료")
HISTORY_KEEP = 30
HISTORY_MIN_GAP = 60  # 초: 자동 저장이 잦아도 백업은 1분에 하나


def next_version_path(pdir: Path, name: str, platform_key: str, versions: list[dict]) -> Path:
    n = max((v["v"] for v in versions), default=0) + 1
    return pdir / f"{name}_{platform_key}_v{n}.mp4"


def record_version(pdir: Path, meta: dict, info: dict, score: int) -> dict:
    """완성본 기록을 project.json에 남긴다(파일 이름의 v번호 기준)."""
    file = Path(info["file"]).name
    n = int(file.rsplit("_v", 1)[1].split(".")[0])
    entry = {"v": n, "file": file, "at": time.strftime("%Y-%m-%d %H:%M"), "score": score,
             "duration": info.get("duration"), "cover": Path(info.get("cover") or "").name,
             "extra": info.get("extra") or {}}
    meta.setdefault("versions", []).append(entry)
    (pdir / "project.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return entry


def finalize(plan: EditPlan, analyses: list[Analysis], platform: Platform, brief: Brief,
             *, auto_pace: bool = True, manual: bool = False) -> tuple[Scorecard, list[Piece], list[Caption]]:
    """검증·보정 → 타임라인·자막 → 진단. auto_pace=False면 사람이 넣은 강조를 건드리지 않는다.
    manual=True(편집기에서 컷을 클립으로 확정한 프로젝트)면 무음 컷·후킹 재정렬 없이 클립을 보이는 그대로 쓴다."""
    style = effective_style(STYLES[brief.style], brief.reference, platform)
    platform = effective_platform(platform, brief.reference)
    sanitize(plan, analyses, keep_order=manual)
    if auto_pace:
        fill_static_gaps(plan, analyses, platform, style)
    fixes = autofix(plan, platform)
    timeline = build_timeline(plan, analyses, cut_silence=0.0 if manual else style.cut_silence)
    captions = build_captions(timeline, analyses, platform.caption_chars, narrations=plan.narrations)
    card = evaluate(plan, timeline, captions, analyses, platform, brief.industry, style)
    card.fixes = fixes
    return card, timeline, captions


def render_options(render_cfg: dict, style: Style, *, preview: bool = False, reference: StyleCard | None = None) -> RenderOptions:
    from .doctor import resolve_font
    p = lambda k: Path(render_cfg[k]) if render_cfg.get(k) else None  # noqa: E731
    return RenderOptions(
        font=resolve_font(render_cfg.get("font") or "auto"), fonts_dir=p("fonts_dir"),
        reframe=render_cfg.get("reframe") or "auto", sfx_dir=p("sfx_dir"), assets_dir=p("assets"),
        bgm=p("bgm"), bgm_db=float(render_cfg.get("bgm_db") or -20.0),
        highlight=render_cfg.get("highlight") or "#FFE500", handle=render_cfg.get("handle") or "",
        zoom=style.zoom, preview=preview,
        caption_look=reference.caption.model_dump() if reference and reference.analysis else None,
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
        return effective_style(STYLES[self.brief.style], self.brief.reference, self.platform)

    @property
    def name(self) -> str:
        return Path(self.meta["sources"][0]).stem if len(self.meta["sources"]) == 1 else "merged"

    @property
    def plan_path(self) -> Path:
        return self.pdir / "plan.json"

    def load_plan(self) -> EditPlan:
        return EditPlan.model_validate_json(self.plan_path.read_text(encoding="utf-8"))

    def save_plan(self, plan: EditPlan) -> None:
        text = plan.model_dump_json(indent=1)
        if self.plan_path.exists():
            old = self.plan_path.read_text(encoding="utf-8")
            if old != text:
                self._backup(old)
        self.plan_path.write_text(text, encoding="utf-8")

    @property
    def history_dir(self) -> Path:
        return self.pdir / ".history"

    def _backup(self, old_text: str) -> None:
        """자동 저장 이전 상태를 1분에 하나씩 보관(최근 30개). 잘못 지웠을 때 되돌리는 안전망."""
        h = self.history_dir
        h.mkdir(exist_ok=True)
        files = sorted(h.glob("plan-*.json"))
        if files and time.time() - files[-1].stat().st_mtime < HISTORY_MIN_GAP:
            return
        (h / f"plan-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(old_text, encoding="utf-8")
        for f in files[: max(0, len(files) + 1 - HISTORY_KEEP)]:
            f.unlink(missing_ok=True)

    def history(self) -> list[dict]:
        out = []
        for f in sorted(self.history_dir.glob("plan-*.json"), reverse=True):
            ts = f.stem[5:]
            out.append({"file": f.name, "at": f"{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}"})
        return out

    def load_history(self, file: str) -> EditPlan:
        path = self.history_dir / Path(file).name
        return EditPlan.model_validate_json(path.read_text(encoding="utf-8"))

    @property
    def review(self) -> dict:
        return self.meta.setdefault("review", {"status": "편집 중", "memo": "", "done": []})

    def set_review(self, *, status: str | None = None, memo: str | None = None, done: list[int] | None = None) -> dict:
        r = self.review
        if status is not None:
            if status not in REVIEW_STATES:
                raise ValueError(f"알 수 없는 상태: {status}")
            r["status"] = status
        if memo is not None:
            r["memo"] = memo
        if done is not None:
            r["done"] = sorted({int(i) for i in done})
        r["updated"] = time.strftime("%Y-%m-%d %H:%M")
        self.save_meta()
        return r

    @property
    def versions(self) -> list[dict]:
        return self.meta.setdefault("versions", [])

    def latest_output(self) -> Path | None:
        if self.versions:
            return self.pdir / self.versions[-1]["file"]
        legacy = self.pdir / f"{self.name}_{self.platform.key}.mp4"
        return legacy if legacy.exists() else None

    def save_meta(self) -> None:
        (self.pdir / "project.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=1), encoding="utf-8")

    def set_reference(self, card: StyleCard | None) -> None:
        self.meta["brief"]["reference"] = card.model_dump() if card else None
        self.save_meta()

    def set_style(self, key: str) -> None:
        self.meta["brief"]["style"] = STYLES[key].key
        self.save_meta()

    @property
    def baked(self) -> bool:
        return bool(self.meta.get("baked"))

    def bake(self) -> EditPlan:
        """자동 무음 컷 결과를 실제 클립으로 확정한다(보이는 타임라인 = 출력 결과).
        CapCut처럼 타임라인에서 직접 자르고 지우는 편집기는 이 상태를 전제로 한다."""
        plan = self.load_plan()
        if self.baked:
            return plan
        sanitize(plan, self.analyses)
        timeline = build_timeline(plan, self.analyses, cut_silence=self.style.cut_silence)
        reasons = {(c.source, c.start, c.end): c.reason for c in plan.clips}
        new = []
        for p in timeline:
            reason = next((r for (s, a, b), r in reasons.items() if s == p.source and a <= p.start and p.end <= b), "")
            new.append(Clip(source=p.source, start=p.start, end=p.end, role=p.role, reason=reason, speed=p.speed))
        plan.clips = new
        self.save_plan(plan)
        self.meta["baked"] = True
        self.save_meta()
        return plan

    def add_source(self, path: Path, whisper_model: str | None) -> Analysis:
        """편집기에서 가져온 원본을 프로젝트에 추가하고 분석한다."""
        i = len(self.analyses)
        adir = self.pdir.parent / "_analysis" / f"{path.stem}_{i}"
        a = analyze(path, i, adir, whisper_model=whisper_model)
        self.analyses.append(a)
        self.meta["sources"].append(str(path.resolve()))
        self.meta["analysis_dirs"].append(str(adir.resolve()))
        self.save_meta()
        return a

    def output_path(self, preview: bool = False) -> Path | None:
        return self.pdir / "preview.mp4" if preview else self.latest_output()

    def save_transcript(self, index: int, transcript: Transcript) -> None:
        a = self.analyses[index]
        path = Path(self.meta["analysis_dirs"][index]) / "transcript.json"
        path.write_text(json.dumps(transcript.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        a.transcript = transcript

    def finalize(self, plan: EditPlan, *, auto_pace: bool = False):
        return finalize(plan, self.analyses, self.platform, self.brief, auto_pace=auto_pace, manual=self.baked)

    @property
    def extra_platforms(self) -> list[Platform]:
        """같은 편집으로 함께 내보낼 다른 세로 플랫폼(예: Reels 프로젝트의 TikTok 파일)."""
        return [get_platforms(k)[0] for k in (self.meta.get("platforms") or [])[1:]]

    def render(self, plan: EditPlan, *, preview: bool = False, planner_suffix: str = "", progress=None,
               creator: str = "") -> tuple[dict, Scorecard]:
        """미리보기는 preview.mp4를 덮어쓰고, 최종본은 v번호를 올려 새 파일로 남기며 검수표·업로드 문구를 만든다.
        다른 세로 플랫폼도 지정돼 있으면 자막·문구 위치만 그 플랫폼 안전영역에 맞춰 파일을 하나씩 더 만든다."""
        card, timeline, captions = self.finalize(plan)
        self.save_plan(plan)  # 실제 렌더링한 보정본으로 갱신
        opts = render_options(self.meta.get("render", {}), self.style, preview=preview, reference=self.brief.reference)
        extras = [] if preview else self.extra_platforms
        parts = 1 + 0.3 * len(extras)  # 추가 플랫폼은 본편을 재사용해 자막 합성만 → 약 30% 시간
        report = progress or (lambda f: None)
        out = self.pdir / "preview.mp4" if preview else next_version_path(self.pdir, self.name, self.platform.key, self.versions)
        info = render(plan, timeline, captions, self.analyses, self.platform, opts, out,
                      self.pdir / ("work_preview" if preview else "work"), progress=lambda f: report(f / parts))
        extra_files = {}
        body = self.pdir / "work" / "pieces" / "body.mkv"
        for k, pf in enumerate(extras, 1):
            caps = build_captions(timeline, self.analyses, effective_platform(pf, self.brief.reference).caption_chars,
                                  narrations=plan.narrations)
            path = out.with_name(out.name.replace(f"_{self.platform.key}_v", f"_{pf.key}_v"))
            same = (pf.width, pf.height, pf.fps) == (self.platform.width, self.platform.height, self.platform.fps)
            render(plan, timeline, caps, self.analyses, pf, opts, path, self.pdir / f"work_{pf.key}",
                   progress=lambda f, k=k: report((1 + 0.3 * (k - 1 + f)) / parts),
                   body=body if same and body.exists() else None)
            extra_files[pf.key] = path.name
        if not preview:
            info["extra"] = extra_files
            entry = record_version(self.pdir, self.meta, info, card.score)
            planner = self.meta.get("planner", "") + planner_suffix
            (self.pdir / "report.md").write_text(build_report(plan, card, self.platform, planner, info),
                                                 encoding="utf-8")
            self.write_review_package(plan, card, info, entry["v"], creator)
            info["version"] = entry["v"]
        return info, card

    def _reference_label(self) -> str:
        ref = self.brief.reference
        return f"{ref.name} (영상 {ref.samples}개 분석)" if ref else ""

    def write_review_package(self, plan: EditPlan, card: Scorecard, info: dict, version: int, creator: str) -> dict:
        from .review import review_html, upload_text
        from .voice import VoiceStore, engine_info
        voices = {}
        mine = {v.voice: v for v in VoiceStore().list()}
        for n in plan.narrations:
            if n.audio and (n.voice, n.engine) not in voices:
                prof = mine.get(n.voice)
                voices[(n.voice, n.engine)] = {"name": n.voice_name or (prof.name if prof else n.voice),
                                               "consent": prof.consent if prof and prof.kind == "clone" else "",
                                               **({"engine": engine_info(n.engine)} if n.engine else {})}
        sheet = self.pdir / f"검수표_v{version}.html"
        sheet.write_text(review_html(name=self.name, version=version, platform=self.platform, plan=plan, card=card,
                                     info=info, creator=creator, voices_used=list(voices.values()),
                                     reference=self._reference_label(), notes=self.brief.notes), encoding="utf-8")
        text = self.pdir / f"업로드문구_v{version}.txt"
        text.write_text(upload_text(plan), encoding="utf-8")
        return {"sheet": str(sheet), "text": str(text)}
