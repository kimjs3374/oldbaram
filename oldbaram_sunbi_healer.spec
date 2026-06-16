# -*- mode: python ; coding: utf-8 -*-
"""선비족 힐러 exe 빌드 스펙 (onedir, torch-free).

빌드:
  cd D:\oldbaram
  dist_dosa\.venv\Scripts\pyinstaller oldbaram_sunbi_healer.spec --noconfirm

산출물: D:\oldbaram\dist\oldbaram_sunbi_healer\
  - oldbaram_sunbi_healer.exe
  - _internal\ (python+libs + dataset/maps/config.yaml/vision 모델 동봉)
폴더 통째 zip 배포 → 압축풀고 exe 실행 (Python 설치 불필요).

데이터는 _internal 에 둔다: config.ROOT(=src 부모)와 다른 모듈의
Path(__file__).parents[2] 가 frozen 에서 모두 _internal 을 가리켜 경로가 일관됨.
"""
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = r'D:\oldbaram'

datas = [
    # vision 모델/사전 — 코드가 Path(__file__).parent 기준으로 찾음 → src/vision 에.
    (ROOT + r'\src\vision\korean_rec.onnx', 'src/vision'),
    (ROOT + r'\src\vision\digit_cnn.onnx', 'src/vision'),
    (ROOT + r'\src\vision\korean_dict.txt', 'src/vision'),
    # ROOT(=_internal) 기준 데이터/설정/학습파일.
    (ROOT + r'\config.yaml', '.'),
    (ROOT + r'\knownmaps.txt', '.'),
    (r'C:\ob_sunbi\portals_v2.json', '.'),
    # YOLO nano 가중치 — config.vision.weights(.pt) 의 .onnx 형제. onnx 만 동봉.
    (ROOT + r'\dataset\runs\full_v3_nano\weights\best.onnx',
     'dataset/runs/full_v3_nano/weights'),
    # 맵 데이터화 JSON 233개 (controller.MapGrid).
    (ROOT + r'\maps', 'maps'),
]
# rapidocr_onnxruntime 은 내부 config/yaml/모델을 동적 로드 → 데이터 동봉 필수.
datas += collect_data_files('rapidocr_onnxruntime')

hiddenimports = collect_submodules('rapidocr_onnxruntime')
# numpy 2.x 신구조(numpy._core.*)를 PyInstaller hook 이 못 따라가 누락 → 명시 수집.
hiddenimports += collect_submodules('numpy')

# torch/ultralytics 및 그 무거운 의존(코드에 try/except import torch 잔존)을 강제 제외.
excludes = [
    'torch', 'torchvision', 'ultralytics', 'ultralytics_thop',
    'matplotlib', 'scipy', 'pandas', 'sympy', 'polars', 'onnx',
    'tkinter', 'PySide2', 'PySide6', 'PyQt6', 'IPython', 'notebook',
    'pytest', 'pydoc',
]

block_cipher = None

a = Analysis(
    [ROOT + r'\run_sunbi_healer.py'],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='oldbaram_sunbi_healer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # 첫 빌드: import/부팅 에러 노출용. 안정화 후 False 재빌드 권장.
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='oldbaram_sunbi_healer',
)
