#!/bin/bash
# 숏폼 편집기 실행. Finder에서 더블클릭(처음엔 우클릭 → 열기).
# 업데이트가 이 파일을 바꿔도 안전하도록 { } 블록 전체를 한 번에 읽어 실행한다(마지막 exit).
{
cd "$(dirname "$0")" || exit 1
if [ ! -f .venv/bin/activate ]; then
  echo "먼저 install-mac.command 를 실행해 설치하세요."; read -r -p "엔터를 누르면 닫힙니다"; exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "숏폼 편집기를 여는 중입니다. 이 창을 닫으면 프로그램이 종료됩니다."
python -m autoedit app
exit
}
