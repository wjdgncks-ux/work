@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo   숏폼 편집기 설치 - 처음 한 번만
echo ================================================
rem 주의: if 블록 괄호 안의 echo 문구에 괄호를 쓰지 않는다(블록이 거기서 끝나 버림).
rem Python은 3.11~3.13만 쓴다. python.org 맨 위 버튼은 최신 버전(3.14 이상)을 주는데,
rem 음성 인식 구성요소(onnxruntime)가 아직 최신 버전용으로 나오지 않아 설치가 실패한다.
rem 'where python'은 Microsoft Store 바로가기(설치 안 된 가짜 python)도 찾으므로 실제로 실행해서 확인한다.
set "OKPY=import sys; sys.exit(0 if (3,11)<=sys.version_info[:2]<=(3,13) else 1)"
set "PY="
for %%V in (3.13 3.12 3.11) do (if not defined PY (py -%%V -c "%OKPY%" >nul 2>nul && set "PY=py -%%V"))
if not defined PY (python -c "%OKPY%" >nul 2>nul && set "PY=python")
if not defined PY call :getpython
if not defined PY (
  echo [!] Python 3.13을 자동으로 설치하지 못했습니다.
  echo     https://www.python.org/downloads/windows/ 목록에서 "Python 3.13.x" 의 "Windows installer 64-bit" 를 받아 설치하세요.
  echo     맨 위 노란 최신 버전 버튼은 누르지 마세요. 지금 있는 Python은 지우지 않아도 됩니다.
  echo     설치 후 이 파일을 다시 더블클릭하세요.
  pause
  exit /b 1
)
echo 사용할 Python: %PY%
rem 예전에 맞지 않는 Python으로 만든 설치 폴더가 있으면 지우고 다시 만든다.
if exist ".venv\Scripts\python.exe" (".venv\Scripts\python.exe" -c "%OKPY%" >nul 2>nul || (echo 이전 설치의 Python 버전이 맞지 않아 다시 만듭니다. & rmdir /s /q .venv))

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

:getpython
echo.
echo Python 3.13을 자동으로 설치합니다. 약 30MB, 1~3분. 지금 있는 다른 Python은 그대로 둡니다.
set "PYEXE=%TEMP%\autoedit-python313.exe"
set "PYURL="
del "%PYEXE%" >nul 2>nul
where curl >nul 2>nul
if errorlevel 1 goto :pyps
echo 최신 3.13 설치 파일을 찾는 중...
for /L %%N in (30,-1,0) do (if not defined PYURL (curl -sfIL -o nul "https://www.python.org/ftp/python/3.13.%%N/python-3.13.%%N-amd64.exe" && set "PYURL=https://www.python.org/ftp/python/3.13.%%N/python-3.13.%%N-amd64.exe"))
if not defined PYURL exit /b 1
echo 받는 중: %PYURL%
curl -L --fail -o "%PYEXE%" "%PYURL%"
goto :pyinstall
:pyps
echo 진행 표시 없이 내려받는 중입니다. 잠시 기다려 주세요...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; foreach ($n in 30..0) { $u='https://www.python.org/ftp/python/3.13.'+$n+'/python-3.13.'+$n+'-amd64.exe'; try { Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile $env:PYEXE; break } catch {} }"
:pyinstall
if not exist "%PYEXE%" exit /b 1
echo 설치 중입니다. 화면 변화 없이 1~2분 걸릴 수 있습니다...
rem 이 사용자 전용 조용한 설치: 관리자 권한 불필요, PATH는 건드리지 않음, py 실행기 포함. 설치 위치: %LocalAppData%\Programs\Python\Python313
"%PYEXE%" /quiet InstallAllUsers=0 PrependPath=0 Include_launcher=1 InstallLauncherAllUsers=0 Include_test=0
del "%PYEXE%" >nul 2>nul
set "P313=%LocalAppData%\Programs\Python\Python313\python.exe"
if exist "%P313%" ("%P313%" -c "%OKPY%" >nul 2>nul && set PY="%P313%")
if not defined PY (py -3.13 -c "%OKPY%" >nul 2>nul && set "PY=py -3.13")
if defined PY echo Python 3.13 설치 완료.
exit /b 0
