@echo off
chcp 65001 >nul
rem 업데이트가 이 파일을 바꿔도 안전하도록, 아래 괄호 블록 전체를 한 번에 읽어 실행한다(마지막 exit /b).
rem 블록 안 echo 문구에는 괄호를 쓰지 않는다.
(
cd /d "%~dp0"
set "PATH=%~dp0tools\ffmpeg\bin;%PATH%"
if not exist ".venv\Scripts\activate.bat" (
  echo 먼저 install-windows.bat 을 실행해 설치하세요.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
echo 숏폼 편집기를 여는 중입니다. 이 창을 닫으면 프로그램이 종료됩니다.
python -m autoedit app
pause
exit /b
)
