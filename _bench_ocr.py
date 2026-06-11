# -*- coding: utf-8 -*-
r"""맵 OCR 엔진 속도/메모리/용량 비교 — RapidOCR(det+rec / rec-only) vs PaddleOCR."""
import sys
import time
import re
import pathlib
import os
import numpy as np
import cv2

try:
    import psutil
    proc = psutil.Process(os.getpid())
    def mem():
        return proc.memory_info().rss / 1024 / 1024
except Exception:
    def mem():
        return -1

SRC = pathlib.Path("D:/oldbaram/mapcrops_dl")
TSV = pathlib.Path("D:/oldbaram/mapcrops_dataset.tsv")
labels = {}
for ln in TSV.read_text(encoding="utf-8").splitlines():
    if "\t" in ln:
        fn, lab = ln.split("\t", 1)
        labels[fn.strip()] = lab.strip()
imgs = []
for fn, lab in list(labels.items())[:40]:
    im = cv2.imread(str(SRC / fn))
    if im is not None:
        imgs.append((im, lab))
KEEP = re.compile(r"[^가-힣0-9()\-]")


def acc(get_text):
    ok = 0
    for im, lab in imgs:
        if KEEP.sub("", get_text(im)) == KEEP.sub("", lab):
            ok += 1
    return ok / len(imgs)


def bench(name, get_text, warmup=3):
    for im, _ in imgs[:warmup]:
        get_text(im)
    ts = []
    for im, _ in imgs:
        t0 = time.perf_counter()
        get_text(im)
        ts.append((time.perf_counter() - t0) * 1000)
    print(f"\n[{name}]")
    print(f"  정확도: {100*acc(get_text):.1f}%")
    print(f"  속도: 평균 {np.mean(ts):.0f}ms / 중앙 {np.median(ts):.0f}ms "
          f"/ 최대 {np.max(ts):.0f}ms")
    print(f"  메모리(RSS): {mem():.0f}MB")


print(f"기준 메모리: {mem():.0f}MB")

# 1) RapidOCR det+rec (현재 v41)
m0 = mem()
from rapidocr_onnxruntime import RapidOCR
eng_full = RapidOCR(rec_model_path="D:/oldbaram/korean_rec.onnx",
                    rec_keys_path="D:/oldbaram/korean_dict.txt")
def rapid_full(im):
    r, _ = eng_full(im)
    return "".join(x[1] for x in r) if r else ""
print(f"\nRapidOCR 로드 후 메모리: +{mem()-m0:.0f}MB")
bench("RapidOCR det+rec (v41)", rapid_full)

# 2) RapidOCR rec-only (det 끄기 — 맵바 위치 고정)
def rapid_rec(im):
    r, _ = eng_full(im, use_det=False, use_cls=False, use_rec=True)
    if not r:
        return ""
    out = []
    for x in r:
        if isinstance(x, (list, tuple)) and x and isinstance(x[0], str):
            out.append(x[0])
        elif isinstance(x, str):
            out.append(x)
    return "".join(out)
bench("RapidOCR rec-only", rapid_rec)

# 3) PaddleOCR TextRecognition (기존)
m1 = mem()
from paddleocr import TextRecognition
_KD = pathlib.Path("D:/oldbaram/dist_dosa/models/korean_PP-OCRv5_mobile_rec")
kw = dict(model_name="korean_PP-OCRv5_mobile_rec", enable_mkldnn=False)
if _KD.is_dir():
    kw["model_dir"] = str(_KD)
pp = None
for ex in [{"device": "cpu"}, {}]:
    try:
        pp = TextRecognition(**ex, **kw); break
    except Exception:
        continue
print(f"\nPaddleOCR 로드 후 메모리: +{mem()-m1:.0f}MB")
def paddle_rec(im):
    out = pp.predict(im)
    t = ""
    for o in out:
        tt = o.get("rec_text", "") if isinstance(o, dict) else getattr(o, "rec_text", "")
        if tt:
            t = tt; break
    return t
bench("PaddleOCR rec (기존)", paddle_rec)

# 용량
print("\n=== 디스크 용량 ===")
for p in ["D:/oldbaram/korean_rec.onnx"]:
    print(f"  RapidOCR korean_rec.onnx: {os.path.getsize(p)/1024/1024:.1f}MB")
import rapidocr_onnxruntime as r_
rdir = pathlib.Path(r_.__file__).parent / "models"
tot = sum(f.stat().st_size for f in rdir.glob("*.onnx"))
print(f"  RapidOCR 내장(det+cls): {tot/1024/1024:.1f}MB")
ppsz = sum(f.stat().st_size for f in _KD.glob("*")) if _KD.is_dir() else 0
print(f"  PaddleOCR korean 모델: {ppsz/1024/1024:.1f}MB")
