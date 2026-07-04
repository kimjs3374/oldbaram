@echo off
rem ============================================================
rem  선비족 힐러 Nuitka standalone 빌드 (소스 C 컴파일 = 디컴파일 불가)
rem  - torch/ultralytics 제거판(onnxruntime 직접추론) 그대로 사용.
rem  - 데이터(모델/maps/config)는 dist 루트 기준 배치 → 경로 일관.
rem  실행: cd D:\oldbaram & build_nuitka.bat
rem  산출물: D:\oldbaram\nuitka_build\run_sunbi_healer.dist\
rem ============================================================
setlocal
set PY=D:\oldbaram\dist_dosa\.venv\Scripts\python.exe
cd /d D:\oldbaram

"%PY%" -m nuitka run_sunbi_healer.py ^
  --standalone ^
  --assume-yes-for-downloads ^
  --enable-plugin=pyqt5 ^
  --include-package=src ^
  --include-package=requests ^
  --include-package=urllib3 ^
  --include-package=certifi ^
  --include-package=charset_normalizer ^
  --include-package=idna ^
  --include-package-data=certifi ^
  --include-package-data=rapidocr_onnxruntime ^
  --include-data-files=src/vision/korean_rec.onnx=src/vision/korean_rec.onnx ^
  --include-data-files=src/vision/digit_cnn.onnx=src/vision/digit_cnn.onnx ^
  --include-data-files=src/fsm/nav_policy.onnx=src/fsm/nav_policy.onnx ^
  --include-data-files=src/vision/korean_dict.txt=src/vision/korean_dict.txt ^
  --include-data-files=config.yaml=config.yaml ^
  --include-data-files=knownmaps.txt=knownmaps.txt ^
  --include-data-files=C:/ob_sunbi/portals_v2.json=portals_v2.json ^
  --include-data-files=dataset/runs/full_v3_nano/weights/best.onnx=dataset/runs/full_v3_nano/weights/best.onnx ^
  --include-data-dir=maps=maps ^
  --nofollow-import-to=torch ^
  --nofollow-import-to=ultralytics ^
  --nofollow-import-to=torchvision ^
  --nofollow-import-to=matplotlib ^
  --nofollow-import-to=scipy ^
  --nofollow-import-to=tkinter ^
  --windows-console-mode=disable ^
  --output-dir=nuitka_build ^
  --output-filename=oldbaram_sunbi_healer.exe

echo.
echo BUILD_DONE exit=%ERRORLEVEL%
endlocal
