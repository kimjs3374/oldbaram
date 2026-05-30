"""이미지의 수평 projection + threshold 값 여러 개 시도."""
import sys
import cv2
import numpy as np
from pathlib import Path

sys.path.insert(0, r"D:/oldbaram/dist_dosa")

IMG = r"C:/Users/ENG/.claude/image-cache/9d9315db-72e8-4c8f-83dd-ab97eee020cf/1.png"
img = cv2.imread(IMG)
h, w = img.shape[:2]
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
print(f"[IMG] {w}x{h}")
print(f"[GRAY] min={gray.min()} max={gray.max()} mean={gray.mean():.1f}")


def split_bands(bw, min_h=8):
    row_sum = bw.sum(axis=1)
    if row_sum.max() <= 0:
        return []
    thr = float(row_sum.max()) * 0.08
    H = bw.shape[0]
    bands = []
    i = 0
    while i < H:
        if row_sum[i] > thr:
            j = i
            while j < H and row_sum[j] > thr:
                j += 1
            if j - i >= min_h:
                bands.append((i, j))
            i = j
        else:
            i += 1
    return bands


# 여러 threshold 시도.
for name, bw in [
    ("T=170 (기존)", cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)[1]),
    ("T=OTSU", cv2.threshold(gray, 0, 255,
                             cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]),
    ("T=OTSU+INV", cv2.threshold(gray, 0, 255,
                                  cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]),
    ("T=120", cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY)[1]),
    ("T=200", cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)[1]),
]:
    bands = split_bands(bw)
    print(f"[{name:18s}] bands={bands}")

# PaddleOCR 풀 파이프라인으로 실측.
from paddleocr import TextRecognition

_MODEL_DIR = Path(r"D:/oldbaram/dist_dosa/models/korean_PP-OCRv5_mobile_rec")
rec = TextRecognition(
    model_name="korean_PP-OCRv5_mobile_rec",
    model_dir=str(_MODEL_DIR),
)


def rec_text(im):
    up = cv2.resize(im, (im.shape[1] * 3, im.shape[0] * 3),
                    interpolation=cv2.INTER_CUBIC)
    try:
        r = rec.predict(up)
        for it in r:
            t = it.get("rec_text", "")
            if t:
                return t
    except Exception as e:
        return f"ERR {e}"
    return ""


# OTSU INV 버전으로 band 잡아 recognition.
_, bw_otsu_inv = cv2.threshold(
    gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
)
bands = split_bands(bw_otsu_inv)
print(f"\n[OTSU+INV bands -> recognize]")
for idx, (y0, y1) in enumerate(bands):
    crop = img[max(0, y0 - 3):min(h, y1 + 3), :]
    t = rec_text(crop)
    print(f"  band{idx} ({y0}~{y1}): {t!r}")

# Otsu (보통) 로도 시도.
_, bw_otsu = cv2.threshold(gray, 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)
bands2 = split_bands(bw_otsu)
print(f"\n[OTSU bands -> recognize]")
for idx, (y0, y1) in enumerate(bands2):
    crop = img[max(0, y0 - 3):min(h, y1 + 3), :]
    t = rec_text(crop)
    print(f"  band{idx} ({y0}~{y1}): {t!r}")

# 전체 image 직접 recognize.
print("\n[FULL direct recognize]")
print(f"  full: {rec_text(img)!r}")
