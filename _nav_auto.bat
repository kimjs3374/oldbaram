@echo off
rem ============================================================
rem  NavBrain 완전자동 학습+배포 (작업 스케줄러가 매일 실행)
rem  pull -> dataset -> train(staging) -> 게이트 -> PASS시 승격+배포
rem  게이트 FAIL = 이전 모델 유지 (실모델/배포 무접촉)
rem  인자: force = 새 데이터 없어도 강제 재학습
rem  기록: nav_auto_log.txt  /  🔴 이 파일은 CRLF 필수
rem ============================================================
chcp 65001 >nul
cd /d D:\oldbaram
set LOG=D:\oldbaram\nav_auto_log.txt
echo [%date% %time%] === NavBrain 자동학습 시작 === >> "%LOG%"

py -3 -m src.tools.cloud_logs --pull
if errorlevel 1 (echo [%date% %time%] FAIL cloud_logs pull >> "%LOG%" & exit /b 1)
py -3 _cloud_maps.py
if errorlevel 1 (echo [%date% %time%] FAIL cloud_maps pull >> "%LOG%" & exit /b 1)
py -3 _nav_dataset.py
if errorlevel 1 (echo [%date% %time%] FAIL dataset >> "%LOG%" & exit /b 1)

rem -- 새 데이터 없으면 스킵 (train.npz MD5 비교). force 로 우회 --
if /i "%~1"=="force" goto TRAIN
certutil -hashfile nav_dataset\train.npz MD5 | findstr /v ":" > nav_dataset\cur.md5
if exist nav_dataset\last.md5 (
  fc /b nav_dataset\cur.md5 nav_dataset\last.md5 >nul 2>&1
  if not errorlevel 1 (
    echo [%date% %time%] SKIP 새 데이터 없음 >> "%LOG%"
    exit /b 0
  )
)

:TRAIN
py -3 _nav_train.py --weights sqrt --patience 8 --epochs 60
if errorlevel 1 (echo [%date% %time%] FAIL train >> "%LOG%" & exit /b 1)

rem -- 배포 게이트 (staging 모델 평가 — 실모델 아직 무접촉) --
py -3 _nav_replay_eval.py --model-dir nav_dataset\staging
if errorlevel 1 (
  echo [%date% %time%] GATE FAIL - 이전 모델 유지, 배포 안 함 >> "%LOG%"
  exit /b 1
)

rem -- 승격: staging -> 양트리 + exe dist 폴더 --
copy /y nav_dataset\staging\nav_policy.onnx dist_dosa\src\fsm\nav_policy.onnx >nul
copy /y nav_dataset\staging\nav_policy.onnx src\fsm\nav_policy.onnx >nul
if exist nuitka_build\run_sunbi_healer.dist\src\fsm copy /y nav_dataset\staging\nav_policy.onnx nuitka_build\run_sunbi_healer.dist\src\fsm\nav_policy.onnx >nul
certutil -hashfile nav_dataset\train.npz MD5 | findstr /v ":" > nav_dataset\last.md5

rem -- 배포 1: exe 채널 (dist 폴더는 빌드시에만 변함 = 모델 1파일 증분, 안전) --
py -3 -m src.tools.cloud_uploader_dist --changelog "NavNet 자동 재학습 (게이트 PASS)"
if errorlevel 1 (echo [%date% %time%] FAIL exe채널 업로드 >> "%LOG%" & exit /b 1)

rem -- 배포 2: .py 채널 — dist_dosa 에 커밋 안 된 코드 수정분 있으면 미완성
rem    코드가 딸려가므로 스킵 (모델만 배포하는 안전장치) --
git status --porcelain -- dist_dosa/src dist_dosa/config.yaml > nav_dataset\tree.tmp
set TREESZ=1
for %%A in (nav_dataset\tree.tmp) do set TREESZ=%%~zA
if "%TREESZ%"=="0" (
  pushd dist_dosa
  py -3 -m src.tools.cloud_uploader --changelog "NavNet 자동 재학습 (게이트 PASS)"
  popd
) else (
  echo [%date% %time%] .py채널 스킵 - dist_dosa 작업트리 수정분 존재 >> "%LOG%"
)

echo [%date% %time%] OK 재학습+게이트PASS+배포 완료 >> "%LOG%"
exit /b 0
