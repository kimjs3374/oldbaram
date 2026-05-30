@echo off
rem D:\oldbaram\src_v2 → D:\oldbaram\dist_dosa\src_v2 복사 (배포 정본 위치).
rem dist_dosa 가 배포 정본이므로 src_v2 변경 후 이 스크립트로 동기화 → 그 다음 deploy.bat 실행.

echo [SYNC] src_v2 -> dist_dosa\src_v2 ...
robocopy "D:\oldbaram\src_v2" "D:\oldbaram\dist_dosa\src_v2" /MIR /XD __pycache__ /XF *.pyc /R:2 /W:1 /NFL /NDL /NJH

if errorlevel 8 (
    echo [SYNC] 오류 발생 — errorlevel=%errorlevel%
    pause
    exit /b 1
)
echo [SYNC] 완료. 이제 deploy.bat 실행하세요.
exit /b 0
