"""컨펌용 자료: 검수표(HTML 한 장, 이미지 포함)와 업로드 문구(txt).

제작자와 컨펌자가 다르고 완성본으로 소통하는 흐름을 전제로 한다.
완성본 mp4 + 검수표 + 업로드 문구를 메신저로 보내고, 컨펌자는 '0:12 자막 오타'처럼 시간과 함께 수정 요청을 보낸다.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path

from .plan import EditPlan
from .platforms import Platform
from .scorecard import Scorecard

CHECKLIST = [
    "첫 3초 문구가 영상 내용으로 실제로 회수되는가(낚시 아님)",
    "자막·화면 문구에 오타가 없는가",
    "수치·사례·효과 표현에 근거가 있는가(최고·1위·100%·완치 등 금지)",
    "브랜드 톤·채널 성격과 맞는가",
    "배경음악·자료 이미지의 사용권이 있는가",
    "협찬·광고면 '유료광고' 표시, AI 음성이면 'AI 음성' 표시가 보이는가",
    "업로드할 때 플랫폼의 협찬 표시·AI 생성 콘텐츠 표시를 체크했는가",
]


def upload_text(plan: EditPlan) -> str:
    lines = ["[제목 후보]"] + [f"{i}. {t}" for i, t in enumerate(plan.title_candidates, 1)]
    lines += ["", "[게시글]", plan.sns_caption.strip(), "", " ".join(plan.hashtags)]
    return "\n".join(lines) + "\n"


def _esc(s) -> str:
    return html.escape(str(s or ""))


def review_html(*, name: str, version: int, platform: Platform, plan: EditPlan, card: Scorecard, info: dict,
                creator: str, voices_used: list[dict]) -> str:
    cover = ""
    cp = Path(info.get("cover") or "")
    if cp.is_file():
        cover = "data:image/jpeg;base64," + base64.b64encode(cp.read_bytes()).decode()
    ai_voice = bool(plan.narrations and any(n.audio for n in plan.narrations))
    severe = [i for i in card.issues if i.severity in ("상", "중")]
    flags = [f for f in card.flags if f.severity == "상"]
    rows = lambda items: "".join(items) or "<li class='muted'>없음</li>"  # noqa: E731
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>검수표 · {_esc(name)} v{version}</title>
<style>
body{{margin:0;background:#f4f5f7;color:#1c1d21;font:15px/1.55 -apple-system,"Malgun Gothic","Noto Sans KR",sans-serif}}
main{{max-width:760px;margin:0 auto;padding:20px 16px 40px}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:22px 0 8px}}
.card{{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 3px #0001;margin-bottom:12px}}
.top{{display:grid;grid-template-columns:150px 1fr;gap:16px}} .top img{{width:150px;border-radius:8px;background:#000}}
.muted{{color:#6b6f7a}} .chip{{display:inline-block;padding:2px 8px;border-radius:99px;background:#eef0f3;margin:2px 4px 2px 0;font-size:13px}}
.warn{{background:#fff1f0;color:#b42318}} .ok{{background:#ecfdf3;color:#067647}}
pre{{white-space:pre-wrap;background:#f7f8fa;border-radius:8px;padding:10px;margin:6px 0;font:inherit}}
li{{margin:4px 0}} label{{display:flex;gap:8px;align-items:flex-start;margin:6px 0}}
.sev-상{{color:#b42318;font-weight:700}} .sev-중{{color:#b54708;font-weight:700}}
@media (max-width:520px){{.top{{grid-template-columns:1fr}} .top img{{width:60%}}}}
</style></head><body><main>
<div class="card top">
  {f'<img src="{cover}" alt="첫 화면">' if cover else '<div></div>'}
  <div>
    <h1>{_esc(name)} <span class="muted">v{version}</span></h1>
    <div class="muted">{_esc(platform.label)} · {_esc(info.get('size'))} · {info.get('duration')}초 · 음량 {info.get('loudness_lufs')} LUFS</div>
    <div class="muted">제작 {_esc(creator or '-')} · 파일 {_esc(Path(info.get('file', '')).name)}{''.join(f' · {_esc(k)}: {_esc(v)}' for k, v in (info.get('extra') or {}).items())}</div>
    <div style="margin-top:8px">
      <span class="chip {'warn' if plan.sponsored else ''}">유료광고 표시 {'있음' if plan.sponsored else '없음'}</span>
      <span class="chip {'warn' if ai_voice else ''}">AI 음성 {'사용' if ai_voice else '없음'}{' · 화면 표시 ' + ('켬' if plan.ai_voice_label else '<b>꺼짐</b>') if ai_voice else ''}</span>
      <span class="chip {'ok' if card.score >= 85 else 'warn'}">AI 진단 {card.score}/100</span>
    </div>
  </div>
</div>
<div class="card"><h2 style="margin-top:0">수정 요청 방법</h2>
  <div>영상 시간과 함께 보내 주세요. 예) <b>0:12 자막 '되요'→'돼요'</b>, <b>0:20~0:24 컷 빼기</b>. 시간은 <b>v{version}</b> 기준입니다.</div></div>
<div class="card"><h2 style="margin-top:0">컨펌 체크리스트</h2>
  {''.join(f'<label><input type="checkbox"> {_esc(c)}</label>' for c in CHECKLIST)}</div>
<div class="card"><h2 style="margin-top:0">업로드 문구</h2>
  <div class="muted">제목 후보</div><pre>{_esc(chr(10).join(plan.title_candidates))}</pre>
  <div class="muted">게시글</div><pre>{_esc(plan.sns_caption)}

{_esc(' '.join(plan.hashtags))}</pre></div>
<div class="card"><h2 style="margin-top:0">자동 점검에서 나온 것</h2>
  <ul>{rows(f"<li><span class='sev-{_esc(i.severity)}'>[{_esc(i.severity)}]</span> {_esc(i.where)}: {_esc(i.problem)}</li>" for i in severe)}</ul>
  <h2>규정 확인 필요 표현</h2>
  <ul>{rows(f"<li>{_esc(f.where)}: <b>{_esc(f.text)}</b> — {_esc(f.reason)}</li>" for f in flags)}</ul>
  {('<h2>사용한 AI 음성</h2><ul>' + ''.join(f"<li>{_esc(v['name'])} — {_esc(v.get('consent') or 'AI 기본 음성')}</li>" for v in voices_used) + '</ul>') if voices_used else ''}
  <div class="muted" style="margin-top:8px">자동 점검은 의심 표현을 찾아 줄 뿐 법률 판단이 아닙니다. 조문은 law.go.kr에서 확인하세요.</div></div>
</main></body></html>
"""
