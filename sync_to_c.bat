@echo off
REM D:\oldbaram\dist_dosa -> C:\oldbaram\dist_dosa 동기화.
REM __pycache__ / .pyc / logs / models / frames / debug / dataset 는 제외.
REM 같은 기계 내 드라이브 간 복사 가정. 다른 기계면 이 파일 사용 불가.
echo [SYNC] D:\oldbaram\dist_dosa  ->  C:\oldbaram\dist_dosa
robocopy D:\oldbaram\dist_dosa C:\oldbaram\dist_dosa /E ^
  /XD __pycache__ logs frames debug dataset models .git ^
  /XF *.pyc *.log *.tmp ^
  /NFL /NDL /NJH /NJS /NC /NS
echo.
echo [SYNC] D:\oldbaram\src       ->  C:\oldbaram\src
robocopy D:\oldbaram\src C:\oldbaram\src /E ^
  /XD __pycache__ logs frames debug dataset models .git ^
  /XF *.pyc *.log *.tmp ^
  /NFL /NDL /NJH /NJS /NC /NS
echo.
echo [SYNC] pyc 캐시 삭제 (옛 바이트코드 무효화)
for /d /r C:\oldbaram %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
echo.
echo [DONE] 동기화 + pyc 캐시 정리 완료.
pause
