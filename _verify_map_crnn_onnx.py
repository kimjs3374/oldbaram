# -*- coding: utf-8 -*-
r"""map_crnn.onnx (onnxruntime) 추론 검증 — 225장 전체 정확도."""
import sys
import pathlib
import cv2

sys.path.insert(0, "dist_dosa")
from src.vision.map_crnn import MapCrnn

SRC = pathlib.Path("D:/oldbaram/mapcrops_dl")
DS = pathlib.Path("D:/oldbaram/mapcrops_dataset.tsv")

m = MapCrnn("D:/oldbaram/dist_dosa/src/vision/map_crnn.onnx",
            "D:/oldbaram/dist_dosa/src/vision/map_crnn_charset.txt")
print("ready:", m.ready(), "charset:", len(m.charset))

labels = {}
for ln in DS.read_text(encoding="utf-8").splitlines():
    if "\t" in ln:
        fn, lab = ln.split("\t", 1)
        labels[fn.strip()] = lab.strip()

ok, tot = 0, 0
wrong = []
confs = []
for name, lab in labels.items():
    img = cv2.imread(str(SRC / name))
    if img is None:
        continue
    pred, conf = m.predict(img)
    tot += 1
    confs.append(conf)
    if pred == lab:
        ok += 1
    else:
        wrong.append((lab, pred, round(conf, 3)))

print(f"onnx 추론 정확도: {ok}/{tot} = {100 * ok / tot:.1f}%")
import numpy as np
confs = np.array(confs)
print(f"학습맵 conf: min={confs.min():.3f} p5={np.percentile(confs,5):.3f} "
      f"p50={np.percentile(confs,50):.3f} mean={confs.mean():.3f}")
print(f"  → 수집임계 후보: p5 미만이면 미학습/새맵 (학습맵 95%가 위)")
if wrong:
    print(f"틀린 {len(wrong)}개(앞15): {wrong[:15]}")
