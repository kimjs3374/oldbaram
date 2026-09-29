@echo off
rem ============================================================
rem  ob_macro 새 PC 개발환경 세팅 (1회 실행)
rem   [0] Python 3.12 확인
rem   [1] dist_dosa\.venv 생성 (exe 빌드 + 경량 실행, 현 PC 와 동일 버전 고정)
rem   [2] 글로벌 Python 개발/학습 패키지 (torch cu124 등)   - 인자 nodev 면 생략
rem   [3] 홈 폴더 설정 복원 (Supabase 키/GUI/영역 프로필) - 기존 파일은 안 덮어씀
rem   [4] Claude Code 메모리 복원                          - 기존 파일은 안 덮어씀
rem   [5] 스모크 테스트 (모델 4종 실추론 + GUI 생성)
rem  사용: _migration\setup_new_pc.bat          (전체)
rem        _migration\setup_new_pc.bat nodev    (GPU/학습 없는 PC)
rem  🔴 이 파일은 CRLF 필수
rem ============================================================
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0.."
set "ROOT=%CD%"
set "VPY=%ROOT%\dist_dosa\.venv\Scripts\python.exe"
set FAILS=0
echo ROOT = %ROOT%

echo.
echo === [0] Python 3.12 확인
py -3.12 -c "import struct,sys; assert struct.calcsize('P')==8; print(sys.version)"
if errorlevel 1 (
  echo [에러] Python 3.12 64bit 필요 - https://www.python.org/downloads/release/python-31210/
  echo        설치 시 "py launcher" 체크. 설치 후 다시 실행.
  pause & exit /b 1
)

echo.
echo === [1] dist_dosa\.venv  (빌드/경량실행 환경)
if not exist "%VPY%" py -3.12 -m venv "%ROOT%\dist_dosa\.venv"
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r "%ROOT%\_migration\requirements_venv_lock.txt"
if errorlevel 1 ( echo [에러] venv 패키지 설치 실패 & pause & exit /b 1 )

echo.
if /i "%~1"=="nodev" (
  echo === [2] 개발 패키지 생략 ^(nodev^)
) else (
  echo === [2] 글로벌 Python 개발/학습 패키지 ^(torch cu124 약 2.5GB^)
  py -3.12 -m pip install -r "%ROOT%\_migration\requirements_dev.txt" --extra-index-url https://download.pytorch.org/whl/cu124
  if errorlevel 1 ( echo [에러] 개발 패키지 설치 실패 & pause & exit /b 1 )
)

echo.
echo === [3] 홈 설정 복원 → %USERPROFILE%
for %%F in ("%ROOT%\_migration\home\.oldbaram_*.json") do (
  if exist "%USERPROFILE%\%%~nxF" (
    echo   유지^(이미 있음^): %%~nxF
  ) else (
    copy /y "%%~fF" "%USERPROFILE%\" >nul & echo   복원: %%~nxF
  )
)

echo.
rem Claude Code 프로젝트 키 = 경로의 ':' '\' 를 '-' 로 (D:\ob_macro → D--ob_macro)
set "PK=%ROOT::=-%"
set "PK=%PK:\=-%"
echo === [4] Claude 메모리 복원 → %USERPROFILE%\.claude\projects\%PK%\memory
robocopy "%ROOT%\_migration\claude_memory" "%USERPROFILE%\.claude\projects\%PK%\memory" /E /XC /XN /XO /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 ( echo   [경고] 메모리 복사 실패 ) else ( echo   완료 )

echo.
echo === [5] 스모크 테스트
"%VPY%" "%ROOT%\_migration\smoke_test.py"
if errorlevel 1 set /a FAILS+=1
"%VPY%" "%ROOT%\_migration\smoke_test.py" dist_dosa
if errorlevel 1 set /a FAILS+=1
if /i not "%~1"=="nodev" (
  py -3.12 "%ROOT%\_migration\smoke_test.py"
  if errorlevel 1 set /a FAILS+=1
)

echo.
if "%FAILS%"=="0" (
  echo ==== 세팅 완료: 스모크 테스트 전부 PASS ====
) else (
  echo ==== [실패] 스모크 테스트 %FAILS%건 FAIL - 위 [FAIL] 줄 확인 ====
)
echo 수동 남은 일: _migration\README_이전.md 의 "새 PC 수동 작업" 참고
pause
endlocal
