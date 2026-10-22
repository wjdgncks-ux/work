@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo   숏폼 편집기 설치 (처음 한 번만)
echo ================================================
where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python이 없습니다.
  echo     https://www.python.org/downloads/ 에서 설치하고
  echo     설치 첫 화면의 "Add python.exe to PATH" 를 꼭 체크하세요.
  echo     설치 후 이 파일을 다시 더블클릭하세요.
  pause
  exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo ffmpeg 설치 중... (윈도우 기본 앱 설치 도구 winget 사용)
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  echo.
  echo [!] ffmpeg 설치가 끝났으면 이 창을 닫고 이 파일을 한 번 더 더블클릭하세요.
  pause
  exit /b 0
)
if not exist ".venv" python -m venv .venv
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 (
  echo [!] 프로그램 구성요소 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행하세요.
  pause
  exit /b 1
)
echo.
python -m autoedit doctor
echo.
echo 설치가 끝났습니다. 이제 "start-windows.bat" 을 더블클릭해서 실행하세요.
echo (바탕화면에 바로가기를 만들어 두면 편합니다)
pause
