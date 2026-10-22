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
command -v python3 >/dev/null 2>&1 || brew install python
command -v ffmpeg >/dev/null 2>&1 || brew install ffmpeg
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
if ! pip install -r requirements.txt; then
  echo "[!] 구성요소 설치 실패. 인터넷 연결을 확인하고 다시 실행하세요."
  read -r -p "엔터를 누르면 닫힙니다"; exit 1
fi
python -m autoedit doctor
echo
echo "설치가 끝났습니다. 이제 'start-mac.command'를 더블클릭해서 실행하세요."
read -r -p "엔터를 누르면 닫힙니다"
