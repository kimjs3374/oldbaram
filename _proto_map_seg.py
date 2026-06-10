# -*- coding: utf-8 -*-
"""맵바 char 분할 prototype — 한글+숫자가 글자 단위로 잘리는지 검증.

map_crops 이미지에 세로투영 기반 분할 적용 → box overlay 저장 + 개수 출력.
"""
import sys
import pathlib
import cv2
import numpy as np

CROPS = pathlib.Path("D:/oldbaram/map_crops")
OUT = pathlib.Path("D:/oldbaram/_seg_out")
OUT.mkdir(exist_ok=True)


def segment_chars(bgr):
    """세로투영 기반 char 분할 (contour 아닌 column projection).

    한글 자모 분리 방지: 세로 프로파일의 빈 열(공백)로만 분할.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    if np.mean(bw) > 127:
        bw = 255 - bw
    # 좌우 장식(횃불) 잘라내기: 양끝 15% 마스킹 — 맵명은 중앙 정렬.
    H, W = bw.shape
    cut = int(W * 0.15)
    bw[:, :cut] = 0
    bw[:, W - cut:] = 0
    # contour 기반: 글자별 외곽선 → 글자 단위 box (공백 간격 무관).
    # 한글 자모 분리 방지: 글자 높이 0.4H 이상만 + x겹침 box 병합.
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    raw = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if h < H * 0.40 or w < 3:
            continue
        raw.append([x, y, w, h])
    raw.sort(key=lambda b: b[0])
    # x구간 겹치거나 매우 가까운(<6px) box 병합 → 자모/획 합치기.
    merged = []
    for b in raw:
        if merged and b[0] <= merged[-1][0] + merged[-1][2] + 6:
            m = merged[-1]
            x0 = min(m[0], b[0]); y0 = min(m[1], b[1])
            x1 = max(m[0] + m[2], b[0] + b[2])
            y1 = max(m[1] + m[3], b[1] + b[3])
            merged[-1] = [x0, y0, x1 - x0, y1 - y0]
        else:
            merged.append(b)
    return bw, [tuple(b) for b in merged]


def main():
    files = sorted(CROPS.glob("*.png"))[:8]
    for f in files:
        img = cv2.imread(str(f))
        if img is None:
            continue
        bw, boxes = segment_chars(img)
        vis = img.copy()
        for (x, y, w, h) in boxes:
            cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 1)
        cv2.imwrite(str(OUT / f.name), vis)
        # char 폭 분포
        ws = [b[2] for b in boxes]
        print(f"{f.name}: chars={len(boxes)} widths={ws}")


if __name__ == "__main__":
    main()
