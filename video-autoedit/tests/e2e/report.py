"""E2E 결과(result-*.json) → e2e-report/report.md"""
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
results = [json.loads(p.read_text()) for p in sorted(out.glob("result-*.json"))]
lines = ["# 사용성 E2E 보고서", ""]
total = sum(len(r["checks"]) for r in results)
failed = [(r["viewport"], c) for r in results for c in r["checks"] if not c["ok"]]
lines.append(f"- 검사 {total}개 · 실패 **{len(failed)}개** · 화면 크기 {', '.join(r['viewport'] for r in results)}")
lines.append("")
if failed:
    lines += ["## 실패", "", "| 화면 | 항목 | 내용 |", "|---|---|---|"]
    lines += [f"| {vp} | {c['name']} | {c['detail']} |" for vp, c in failed]
    lines.append("")
lines += ["## 단계별 소요 시간(초)", "", "| 단계 | " + " | ".join(r["viewport"] for r in results) + " |",
          "|---|" + "---|" * len(results)]
keys = sorted({k for r in results for k in r["timings"]})
names = {"home_load_ms": "시작 화면 열기", "upload_ms": "영상 올리기", "auto_edit_and_open_ms": "자동 편집 + 편집기 열기",
         "narration_ms": "나레이션 1개 생성", "export_final_ms": "최종 내보내기"}
for k in keys:
    lines.append(f"| {names.get(k, k)} | " + " | ".join(f"{r['timings'].get(k, 0) / 1000:.1f}" for r in results) + " |")
lines += ["", "## 전체 항목", ""]
for r in results:
    lines.append(f"### {r['viewport']}")
    lines += [f"- {'✅' if c['ok'] else '❌'} {c['name']} {('— ' + c['detail']) if c['detail'] and not c['ok'] else ''}" for c in r["checks"]]
    lines.append("")
(out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines[:4]))
for vp, c in failed:
    print(f"  ❌ [{vp}] {c['name']} — {c['detail']}")
sys.exit(1 if failed else 0)
