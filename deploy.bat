@echo off
rem D:\oldbaram\dist_dosa → C:\oldbaram 전체 동기화.
rem 매번 한 줄씩 복사하지 않고 이 파일 더블클릭 or 터미널에서 실행하면 끝.
rem __pycache__ 및 *.pyc 는 제외.

echo [DEPLOY] dist_dosa -> C:\oldbaram 동기화 시작...

rem src 트리 전체 mirror (파일 변경/추가/삭제 반영). /MIR는 C 쪽 남는 파일 삭제.
robocopy "D:\oldbaram\dist_dosa\src" "C:\oldbaram\src" /MIR /XD __pycache__ /XF *.pyc /R:2 /W:1 /NFL /NDL /NJH

rem src_v2 트리 (빅뱅 재작성본) 전체 mirror.
robocopy "D:\oldbaram\dist_dosa\src_v2" "C:\oldbaram\src_v2" /MIR /XD __pycache__ /XF *.pyc /R:2 /W:1 /NFL /NDL /NJH

rem 루트 파일들 (knownmaps.txt, config.yaml 등). 추가/변경만.
robocopy "D:\oldbaram\dist_dosa" "C:\oldbaram" knownmaps.txt config.yaml requirements.txt /R:2 /W:1 /NFL /NDL /NJH

rem scripts / models / dataset 등 자주 안 바뀌는 건 제외. 필요 시 주석 해제:
rem robocopy "D:\oldbaram\dist_dosa\scripts" "C:\oldbaram\scripts" /MIR /XD __pycache__ /XF *.pyc /R:2 /W:1 /NFL /NDL /NJH

echo.
echo [DEPLOY] 완료.
rem robocopy 종료 코드 0~7 = 정상. 8+ = 오류.
if errorlevel 8 (
    echo [DEPLOY] 오류 발생 — errorlevel=%errorlevel%
    pause
    exit /b 1
)
echo [DEPLOY] 힐러 재시작하세요.
timeout /t 2 >nul
exit /b 0
