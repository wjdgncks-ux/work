@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo 먼저 install-windows.bat 을 실행해 설치하세요.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
echo 숏폼 편집기를 여는 중입니다. 이 창을 닫으면 프로그램이 종료됩니다.
python -m autoedit app
pause
