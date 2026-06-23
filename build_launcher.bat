@echo off
rem ============================================================
rem  옛바 런처 Nuitka onefile 빌드 (작은 단일 exe).
rem  표준 라이브러리만 사용 → 의존 거의 없음(PyQt/requests 불필요).
rem  산출물: nuitka_build\oldbaram_launcher.exe
rem  배포: oldbaram_launcher.exe + app\(메인 dist) 를 같은 폴더에 두고 zip.
rem ============================================================
setlocal
set PY=D:\oldbaram\dist_dosa\.venv\Scripts\python.exe
cd /d D:\oldbaram

"%PY%" -m nuitka launcher.py ^
  --onefile ^
  --assume-yes-for-downloads ^
  --windows-console-mode=force ^
  --output-dir=nuitka_build ^
  --output-filename=oldbaram_launcher.exe

echo.
echo BUILD_DONE exit=%ERRORLEVEL%
endlocal
