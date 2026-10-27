#!/bin/bash
# 사용성 E2E 하네스 실행: 앱을 가짜 엔진으로 띄우고 여러 화면 크기로 사용자 흐름을 돌린 뒤 보고서를 만든다.
#   bash tests/e2e/run.sh [결과 폴더]
# 필요: node + playwright (tests/e2e 에서 `npm install` 또는 전역 설치), ffmpeg
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/e2e-report}"
WORK="$(mktemp -d)"
PORT="${E2E_PORT:-8790}"
rm -rf "$OUT"; mkdir -p "$OUT"
export NODE_PATH="${NODE_PATH:-$HERE/node_modules:$(npm root -g 2>/dev/null)}"

for VP in ${E2E_VPS:-"1366x768" "1920x1080"}; do
  rm -rf "$WORK"/*
  python3 "$HERE/harness.py" --root "$WORK" --port "$PORT" > "$OUT/server-$VP.log" 2>&1 &
  PID=$!
  for _ in $(seq 1 60); do curl -sf "http://127.0.0.1:$PORT/app/api/ping" >/dev/null && break; sleep 1; done
  node "$HERE/flows.js" "$WORK/harness.json" "$OUT" "${VP%x*}" "${VP#*x}" || true
  kill "$PID" 2>/dev/null || true; wait "$PID" 2>/dev/null || true
done
python3 "$HERE/report.py" "$OUT"
rm -rf "$WORK"
