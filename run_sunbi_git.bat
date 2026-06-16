@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem ============================================================
rem  oldbaram_sunbi 다운로드 + 실행 (git 증분 방식)
rem  - 최초 1회만 전체(약 130MB) clone
rem  - 이후 실행 시 바뀐 파일만 받음 (모델 그대로면 거의 0)
rem  - 힐러/격수 통합 GUI(healer_gui) 실행. 역할은 창 안에서 선택.
rem ============================================================

set "REPO=https://github.com/kimjs3374/oldbaram_sunbi.git"
set "TARGET=C:\ob_sunbi"

where git >nul 2>&1
if errorlevel 1 (
    echo [에러] git 이 설치되어 있지 않습니다.
    echo        https://git-scm.com/download/win 에서 설치 후 다시 실행하세요.
    pause & exit /b 1
)

if exist "%TARGET%\.git" (
    echo [업데이트] 바뀐 파일만 받는 중...
    cd /d "%TARGET%"
    git fetch --depth 1 origin main
    if errorlevel 1 ( echo [에러] 업데이트 조회 실패. 인터넷 확인. & pause & exit /b 1 )
    git reset --hard origin/main
) else (
    echo [최초 다운로드] 모델 포함 약 130MB, 한 번만 받습니다...
    git clone --depth 1 "%REPO%" "%TARGET%"
    if errorlevel 1 ( echo [에러] 다운로드 실패. 인터넷 확인. & pause & exit /b 1 )
)

echo.
echo GUI 실행 중... (콘솔 없이 GUI 창만 뜹니다)
cd /d "%TARGET%"
start "" pyw -m src.app.healer_gui
endlocal
