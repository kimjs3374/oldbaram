@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem ============================================================
rem  oldbaram_sunbi 다운로드 + 실행 (zip 방식 / git 불필요)
rem  - GitHub main.zip 을 받아 C:\ob_sunbi 에 풉니다.
rem  - 이미 설치돼 있으면 업데이트할지 물어보고, 아니면 바로 실행.
rem  - 업데이트는 전체(약 130MB) 재다운로드입니다 (zip 한계).
rem    매번 조금씩만 받고 싶으면 run_sunbi_git.bat 을 쓰세요.
rem  - 힐러/격수 통합 GUI(healer_gui) 실행. 역할은 창 안에서 선택.
rem ============================================================

set "REPO=https://github.com/kimjs3374/oldbaram_sunbi/archive/refs/heads/main.zip"
set "TARGET=C:\ob_sunbi"
set "TMP=%TEMP%\ob_sunbi_dl"

if not exist "%TARGET%\src" goto DOWNLOAD

echo 기존 설치가 있습니다.
set "UP="
set /p "UP=최신으로 업데이트할까요? (Y=다시받기 / 그외=바로실행): "
if /i "%UP%"=="Y" goto DOWNLOAD
goto RUN

:DOWNLOAD
echo.
echo [다운로드] 최신 소스 받는 중... (모델 포함 약 130MB, 잠시 걸립니다)
if exist "%TMP%" rmdir /s /q "%TMP%"
mkdir "%TMP%"
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%REPO%' -OutFile '%TMP%\src.zip'"
if errorlevel 1 ( echo [에러] 다운로드 실패. 인터넷 확인. & pause & exit /b 1 )

echo [압축 해제] ...
powershell -NoProfile -Command "Expand-Archive -Path '%TMP%\src.zip' -DestinationPath '%TMP%' -Force"
if errorlevel 1 ( echo [에러] 압축 해제 실패. & pause & exit /b 1 )

echo [적용] %TARGET% 에 복사 중...
if not exist "%TARGET%" mkdir "%TARGET%"
xcopy /e /y /i /q "%TMP%\oldbaram_sunbi-main\*" "%TARGET%\" >nul
if errorlevel 1 ( echo [에러] 파일 복사 실패. & pause & exit /b 1 )
rmdir /s /q "%TMP%"

:RUN
echo.
echo GUI 실행 중... (콘솔 없이 GUI 창만 뜹니다)
cd /d "%TARGET%"
start "" pyw -m src.app.healer_gui
endlocal
