@echo off
chcp 65001 >nul
rem ============================================================
rem NavBrain 지속 학습 1커맨드 (경로 딥러닝 학습.md §7)
rem   ① 클라우드 로그 pull  ② 맵 grid 병합  ③ 데이터셋 재생성
rem   ④ NavNet 재학습+ONNX  ⑤ 리플레이 평가 게이트 (FAIL=정지)
rem PASS 시에만 배포 (cloud_uploader — src 동기 선행). 자동 배포 안 함.
rem ============================================================
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

echo [4/5] _nav_train.py (NavNet 재학습 + ONNX export + 패리티)
py -3 _nav_train.py
if errorlevel 1 (echo [FAIL] _nav_train.py & exit /b 1)

echo [5/5] _nav_replay_eval.py (배포 전 게이트)
py -3 _nav_replay_eval.py
if errorlevel 1 (echo [FAIL] _nav_replay_eval.py — 게이트 FAIL, 배포 금지 & exit /b 1)

echo.
echo 게이트 PASS — 배포 가능(cloud_uploader): src 동기 후 py -3 -m src.tools.cloud_uploader
echo (nav_policy.onnx 는 src/fsm/ 안이라 자동 포함. shadow 모드 기본으로 배포할 것)
exit /b 0
