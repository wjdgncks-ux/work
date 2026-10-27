@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo   숏폼 편집기 설치 - 처음 한 번만
echo ================================================
rem 주의: if 블록 괄호 안의 echo 문구에 괄호를 쓰지 않는다(블록이 거기서 끝나 버림).
rem 'where python'은 Microsoft Store 바로가기(설치 안 된 가짜 python)도 찾으므로, 실제로 실행되는지로 확인한다.
rem python.org 설치본에 딸린 py 실행기를 먼저 쓴다(PATH 체크를 빠뜨려도 동작).
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (python --version >nul 2>nul && set "PY=python")
if not defined PY (
  echo [!] Python이 없습니다.
  echo     https://www.python.org/downloads/ 에서 설치하고
  echo     설치 첫 화면의 "Add python.exe to PATH" 를 꼭 체크하세요.
  echo     설치 후 이 파일을 다시 더블클릭하세요.
  pause
  exit /b 1
)

rem ffmpeg: winget이 없는 PC가 많아 프로그램 폴더의 tools\ffmpeg 에 직접 받아 둔다(시스템 설정은 건드리지 않음).
set "APPDIR=%~dp0"
set "PATH=%~dp0tools\ffmpeg\bin;%PATH%"
set "FFURL=https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
where ffmpeg >nul 2>nul
if errorlevel 1 call :getffmpeg
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo.
  echo [!] ffmpeg 영상 처리 도구를 받지 못했습니다.
  echo     인터넷 연결을 확인하고 이 파일을 다시 더블클릭하세요.
  echo     회사 보안 프로그램이 github.com 내려받기를 막는 경우 담당자에게 문의하세요.
  pause
  exit /b 1
)
echo ffmpeg 확인 완료.

if not exist ".venv" %PY% -m venv .venv
call ".venv\Scripts\activate.bat"
echo.
echo 프로그램 구성요소를 설치합니다. 5~15분 걸릴 수 있습니다. 창을 닫지 마세요.
python -m pip install --upgrade pip
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
echo 바탕화면에 바로가기를 만들어 두면 편합니다.
pause
exit /b 0

:getffmpeg
echo.
echo ffmpeg 영상 처리 도구를 내려받습니다. 약 200MB, 인터넷 속도에 따라 1~10분.
set "FFZIP=%TEMP%\autoedit-ffmpeg.zip"
del "%FFZIP%" >nul 2>nul
where curl >nul 2>nul
if errorlevel 1 goto :ffps
curl -L --fail -o "%FFZIP%" "%FFURL%"
goto :ffunzip
:ffps
echo 진행 표시 없이 내려받는 중입니다. 잠시 기다려 주세요...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing -Uri $env:FFURL -OutFile $env:FFZIP"
:ffunzip
if not exist "%FFZIP%" exit /b 1
echo 압축을 푸는 중...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; $t=Join-Path $env:TEMP 'autoedit-ffmpeg'; if (Test-Path $t) { Remove-Item $t -Recurse -Force }; Expand-Archive -LiteralPath $env:FFZIP -DestinationPath $t -Force; $exe=Get-ChildItem $t -Recurse -Filter ffmpeg.exe | Select-Object -First 1; $dst=Join-Path $env:APPDIR 'tools\ffmpeg\bin'; New-Item -ItemType Directory -Force -Path $dst | Out-Null; Copy-Item -Path (Join-Path $exe.DirectoryName 'ffmpeg.exe'),(Join-Path $exe.DirectoryName 'ffprobe.exe') -Destination $dst -Force; Remove-Item $t -Recurse -Force"
del "%FFZIP%" >nul 2>nul
exit /b 0
