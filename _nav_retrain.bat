@echo off
rem ============================================================
rem  NavBrain 수동 재학습 (배포 없음 - 결과 확인용)
rem  자동 반영은 _nav_auto.bat (작업 스케줄러 oldbaram_nav_auto 매일 실행)
rem  train 은 nav_dataset\staging 에만 출력 -> 게이트 FAIL 시 실모델 무접촉
rem  (CRLF 필수: LF 저장 시 cmd 한글 파싱 깨짐)
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

echo [1/5] cloud_logs --pull (최신 수집분)
py -3 -m src.tools.cloud_logs --pull
if errorlevel 1 (echo [FAIL] cloud_logs --pull & exit /b 1)

echo [2/5] _cloud_maps.py (맵 grid 병합)
py -3 _cloud_maps.py
if errorlevel 1 (echo [FAIL] _cloud_maps.py & exit /b 1)

echo [3/5] _nav_dataset.py (시퀀스 데이터셋 재생성)
py -3 _nav_dataset.py
if errorlevel 1 (echo [FAIL] _nav_dataset.py & exit /b 1)

echo [4/5] _nav_train.py (NavNet 재학습 - staging 출력)
py -3 _nav_train.py --weights sqrt --patience 8 --epochs 60
if errorlevel 1 (echo [FAIL] _nav_train.py & exit /b 1)

echo [5/5] _nav_replay_eval.py (배포 게이트 - staging 모델 평가)
py -3 _nav_replay_eval.py --model-dir nav_dataset\staging
if errorlevel 1 (echo [FAIL] 게이트 FAIL - 이전 모델 유지 & exit /b 1)

echo.
echo 게이트 PASS - 반영/배포는 _nav_auto.bat (스케줄러가 매일 자동 실행 중)
exit /b 0
