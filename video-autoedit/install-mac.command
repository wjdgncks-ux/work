#!/bin/bash
# 숏폼 편집기 설치(처음 한 번만). Finder에서 더블클릭.
cd "$(dirname "$0")" || exit 1
echo "================================================"
echo "  숏폼 편집기 설치"
echo "================================================"
if ! command -v brew >/dev/null 2>&1; then
  echo "[!] Homebrew가 필요합니다. https://brew.sh 의 설치 명령을 '터미널'에 붙여 넣어 설치한 뒤 다시 실행하세요."
  read -r -p "엔터를 누르면 닫힙니다"; exit 1
fi
# Python은 3.11~3.13만 쓴다(최신 3.14 이상은 음성 인식 구성요소 onnxruntime이 아직 없음)
OKPY='import sys; sys.exit(0 if (3,11)<=sys.version_info[:2]<=(3,13) else 1)'
PY=""
for v in 3.13 3.12 3.11; do
  if command -v "python$v" >/dev/null 2>&1 && "python$v" -c "$OKPY" 2>/dev/null; then PY="python$v"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.13 설치 중(Homebrew)..."
  brew install python@3.13 && PY="$(brew --prefix python@3.13)/bin/python3.13"
fi
if [ -z "$PY" ] || ! "$PY" -c "$OKPY" 2>/dev/null; then
  echo "[!] Python 3.13을 준비하지 못했습니다. 터미널에서 'brew install python@3.13' 을 실행한 뒤 다시 실행하세요."
  read -r -p "엔터를 누르면 닫힙니다"; exit 1
fi
command -v ffmpeg >/dev/null 2>&1 || brew install ffmpeg
if [ -x .venv/bin/python ] && ! .venv/bin/python -c "$OKPY" 2>/dev/null; then
  echo "이전 설치의 Python 버전이 맞지 않아 다시 만듭니다."; rm -rf .venv
fi
[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
echo "프로그램 구성요소를 설치합니다(5~15분). 창을 닫지 마세요."
python -m pip install --upgrade pip
if ! pip install -r requirements.txt; then
  echo "[!] 구성요소 설치 실패. 인터넷 연결을 확인하고 다시 실행하세요."
  read -r -p "엔터를 누르면 닫힙니다"; exit 1
fi
python -m autoedit doctor
echo
echo "설치가 끝났습니다. 이제 'start-mac.command'를 더블클릭해서 실행하세요."
read -r -p "엔터를 누르면 닫힙니다"
