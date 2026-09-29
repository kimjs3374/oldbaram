"""새 PC 이전 검증용 스모크 테스트.

사용 (프로젝트 루트 또는 dist_dosa 에서):
    python _migration\\smoke_test.py            # 루트 src 트리
    python _migration\\smoke_test.py dist_dosa  # dist_dosa(.py 배포채널) 트리

검사: ① 필수 데이터 파일 ② src 전 모듈 import ③ 모델 4종 실추론
      (YOLO onnx / digit_cnn / RapidOCR / NavBrain) ④ Qt offscreen MainWindow 생성.
로그인 게이트(서버 RPC)는 호출하지 않는다. 하나라도 실패하면 exit 1.
"""
import importlib
import os
import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent.parent
ROOT = HERE / sys.argv[1] if len(sys.argv) > 1 else HERE
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

fails = []
_REPORT = HERE / "_migration" / f"smoke_result_{ROOT.name}.txt"
_lines = []


def print(*a):  # noqa: A001 — stdout 교체 모듈/파이프 버퍼 유실 대비: 즉시 flush + 파일 기록
    s = " ".join(str(x) for x in a)
    _lines.append(s)
    sys.__stdout__.write(s + "\n")
    sys.__stdout__.flush()


def check(name, fn):
    try:
        r = fn()
        print(f"[OK]   {name}" + (f"  ({r})" if r is not None else ""))
    except Exception as e:
        fails.append(name)
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        print(traceback.format_exc(limit=3))


def need(rel):
    p = ROOT / rel
    if not p.exists():
        raise FileNotFoundError(p)
    return p


print(f"ROOT = {ROOT}\nPython = {sys.version.split()[0]} ({sys.executable})\n")

# ① 데이터 파일
for rel in ["config.yaml", "knownmaps.txt",
            "src/vision/korean_rec.onnx", "src/vision/korean_dict.txt",
            "src/vision/digit_cnn.onnx", "src/fsm/nav_policy.onnx",
            "dataset/runs/full_v3_nano/weights/best.onnx"]:
    check(f"file {rel}", lambda rel=rel: f"{need(rel).stat().st_size:,} B")

# ② 모듈 import (src 전체)
skip_mods = set()
mods = sorted(".".join(p.relative_to(ROOT).with_suffix("").parts)
              for p in (ROOT / "src").rglob("*.py") if "__pycache__" not in p.parts)
bad, legacy = [], []
_GPU_STACK = ("ultralytics", "torch")   # 경량 venv 엔 의도적으로 없음 (requirements-build.txt)
for m in mods:
    m = m.removesuffix(".__init__")
    try:
        importlib.import_module(m)
    except ModuleNotFoundError as e:
        (legacy if e.name in _GPU_STACK else bad).append(f"{m}: {e}")
    except Exception as e:
        bad.append(f"{m}: {type(e).__name__}: {e}")
check(f"import src.* ({len(mods)} modules)",
      lambda: (_ for _ in ()).throw(RuntimeError("\n  " + "\n  ".join(bad))) if bad
      else (f"{len(mods) - len(legacy)} ok, torch/ultralytics 필요로 건너뜀 {len(legacy)}: "
            + ", ".join(x.split(":")[0] for x in legacy) if legacy else "all"))

import numpy as np  # noqa: E402

# ③ 모델 실추론
def t_yolo():
    from src.config import load as load_cfg
    cfg = load_cfg()
    if not (ROOT / "src/vision/yolo_onnx.py").exists():
        # dist_dosa(.py 채널) 구트리: YOLO 가 ultralytics 경로(src/vision/yolo.py) — 원본도 동일.
        # 가중치 파일 자체 유효성만 onnxruntime 으로 확인.
        import onnxruntime as ort
        w = need("dataset/runs/full_v3_nano/weights/best.onnx")
        s = ort.InferenceSession(str(w), providers=["CPUExecutionProvider"])
        i = s.get_inputs()[0]
        sz = i.shape[2] if isinstance(i.shape[2], int) else 416
        s.run(None, {i.name: np.zeros((1, 3, sz, sz), np.float32)})
        return f"구트리(yolo_onnx 없음) → best.onnx 직접 sess.run ok, imgsz={sz}"
    from src.vision.yolo_onnx import OnnxYolo
    y = OnnxYolo(cfg.vision.weights, imgsz=cfg.vision.imgsz, conf=cfg.vision.conf)
    img = np.zeros((720, 1280, 3), np.uint8)
    for meth in ("detect", "predict", "infer", "__call__"):
        if hasattr(y, meth):
            out = getattr(y, meth)(img)
            return f"{pathlib.Path(cfg.vision.weights).name} .{meth}() -> {type(out).__name__}"
    inp = y.sess.get_inputs()[0]
    y.sess.run(None, {inp.name: np.zeros((1, 3, y.imgsz, y.imgsz), np.float32)})
    return "sess.run ok"


def t_digit():
    from src.vision.digit_cnn import DigitCnn
    d = DigitCnn(need("src/vision/digit_cnn.onnx"))
    assert d.ready(), "not ready"
    return f"predict -> {d.predict([np.full((28, 18), 255, np.uint8)])}"


def t_rapid():
    from src.vision import map_rapidocr as mr
    assert mr.ready(), "RapidOCR not ready"
    return f"read_text -> {mr.read_text(np.full((28, 220, 3), 30, np.uint8))!r}"


def t_nav():
    from src.fsm.map_grid import MapGrid
    from src.fsm.nav_brain import NavBrain
    nb = NavBrain(MapGrid(ROOT / "maps"))
    assert nb.ready_net(), "nav_policy.onnx 로드 실패"
    return "net ready"


check("YOLO onnx 추론", t_yolo)
check("digit_cnn 추론", t_digit)
check("RapidOCR 추론", t_rapid)
check("NavBrain onnx", t_nav)


# ④ Qt MainWindow (로그인 게이트 제외)
def t_gui():
    from PyQt5 import QtWidgets
    from src.config import load as load_cfg
    from src.app import healer_gui as hg
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    w = hg.MainWindow(load_cfg())
    w.show()
    for _ in range(20):
        app.processEvents()
    title = w.windowTitle()
    w.close()
    app.processEvents()
    return f"title={title!r}"


check("Qt MainWindow 생성", t_gui)

print("\n" + ("PASS - 전 항목 통과" if not fails else f"FAIL {len(fails)}건: {fails}"))
_REPORT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
os._exit(1 if fails else 0)  # Qt/워커 스레드 잔존으로 인한 종료 hang 방지
