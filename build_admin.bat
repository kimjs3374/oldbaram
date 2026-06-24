@echo off
rem ============================================================
rem  옛바 관리자 콘솔 전용 빌드 (사장님 PC 전용 — 배포 금지).
rem  배포 exe(build_nuitka)와 완전 별개. service_role 키로 회원/기기 관리.
rem  산출물: nuitka_build\oldbaram_admin.exe
rem ============================================================
setlocal
set PY=D:\oldbaram\dist_dosa\.venv\Scripts\python.exe
cd /d D:\oldbaram

"%PY%" -m nuitka admin_gui.py ^
  --onefile ^
  --assume-yes-for-downloads ^
  --enable-plugin=pyqt5 ^
  --include-package=requests ^
  --include-package=urllib3 ^
  --include-package=certifi ^
  --include-package=charset_normalizer ^
  --include-package=idna ^
  --include-package=bcrypt ^
  --include-package-data=certifi ^
  --windows-console-mode=disable ^
  --output-dir=nuitka_build ^
  --output-filename=oldbaram_admin.exe

echo.
echo BUILD_DONE exit=%ERRORLEVEL%
endlocal
