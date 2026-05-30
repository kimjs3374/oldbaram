"""whitetab_suspect 덤프 이미지들에 대해 YOLO 추론 실행 후
WHITE 클래스 bbox 크기/conf 분포를 측정한다.

목적: 현재 `min_w=25, min_h=40, conf=0.25` 필터가 실제 흰탭을 얼마나
놓치는지 실증. 덤프 이미지는 이미 `block_by_white=True`가 트리거된
(=YOLO가 감지한) 프레임이지만, 원본 raw 감지 중 sub-threshold로
필터링돼서 `det_white is None`이 되는 경우가 있는지 보는 게 핵심.

사용:
  python tools/probe_whitetab.py [--weights PATH] [--conf 0.05]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

DEFAULT_WEIGHTS = r"D:\oldbaram\dist_dosa\dataset\runs\full_v3\weights\best.pt"
DEFAULT_DIR = r"D:\oldbaram\logs\whitetab_suspect"
CLS_RED = 0
CLS_WHITE = 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=DEFAULT_WEIGHTS)
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--conf", type=float, default=0.05,
                    help="추론 최소 conf (기본 0.05, 실운영 0.25와 대조)")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--limit", type=int, default=0, help="0=전체")
    args = ap.parse_args()

    try:
        from ultralytics import YOLO
    except Exception as e:
        print(f"ultralytics import 실패: {e}")
        sys.exit(1)
    import cv2

    wp = Path(args.weights)
    if not wp.exists():
        print(f"weights not found: {wp}")
        sys.exit(1)
    model = YOLO(str(wp))

    d = Path(args.dir)
    pngs = sorted(d.glob("whitetab_*.png"))
    if args.limit > 0:
        pngs = pngs[:args.limit]
    print(f"samples: {len(pngs)} weights: {wp.name} conf: {args.conf}")

    white_sizes = []
    white_confs = []
    red_sizes = []
    red_confs = []
    missed_white_files = []

    for p in pngs:
        frame = cv2.imread(str(p))
        if frame is None:
            continue
        r = model.predict(frame, imgsz=args.imgsz, conf=args.conf,
                          iou=0.5, device=0, half=True, verbose=False)[0]
        if r.boxes is None or len(r.boxes) == 0:
            missed_white_files.append(p.name)
            continue
        xyxy = r.boxes.xyxy.cpu().numpy()
        confs = r.boxes.conf.cpu().numpy()
        clses = r.boxes.cls.cpu().numpy().astype(int)
        has_white = False
        for (x1, y1, x2, y2), c, k in zip(xyxy, confs, clses):
            w = int(x2 - x1); h = int(y2 - y1); cf = float(c)
            if int(k) == CLS_WHITE:
                white_sizes.append((w, h))
                white_confs.append(cf)
                has_white = True
            elif int(k) == CLS_RED:
                red_sizes.append((w, h))
                red_confs.append(cf)
        if not has_white:
            missed_white_files.append(p.name)

    def stats(arr, name):
        if not arr:
            print(f"  {name}: NONE")
            return
        a = np.array(arr, dtype=float)
        if a.ndim == 2:
            ws = a[:, 0]; hs = a[:, 1]
            print(f"  {name}: n={len(arr)} "
                  f"w[min/med/max]={ws.min():.0f}/{np.median(ws):.0f}/{ws.max():.0f} "
                  f"h[min/med/max]={hs.min():.0f}/{np.median(hs):.0f}/{hs.max():.0f}")
        else:
            print(f"  {name}: n={len(arr)} "
                  f"[min/med/mean/max]={a.min():.3f}/{np.median(a):.3f}/"
                  f"{a.mean():.3f}/{a.max():.3f}")

    print("\n=== WHITE ===")
    stats(white_sizes, "sizes")
    stats(white_confs, "confs")
    print("\n=== RED (공존 프레임) ===")
    stats(red_sizes, "sizes")
    stats(red_confs, "confs")

    # 필터 통과율: 현재 운영값 vs 낮춘 값
    def pass_rate(sizes, confs, min_w, min_h, min_conf):
        if not sizes:
            return "n/a"
        n = len(sizes)
        passed = sum(
            1 for (w, h), cf in zip(sizes, confs)
            if w >= min_w and h >= min_h and cf >= min_conf
        )
        return f"{passed}/{n} ({100*passed/n:.1f}%)"

    print("\n=== WHITE 필터 통과율 ===")
    print(f"  운영값 min_w=25 min_h=40 conf=0.25: "
          f"{pass_rate(white_sizes, white_confs, 25, 40, 0.25)}")
    print(f"  완화    min_w=15 min_h=25 conf=0.15: "
          f"{pass_rate(white_sizes, white_confs, 15, 25, 0.15)}")
    print(f"  완화    min_w=10 min_h=20 conf=0.10: "
          f"{pass_rate(white_sizes, white_confs, 10, 20, 0.10)}")

    print(f"\nWHITE 미검출 프레임: {len(missed_white_files)}/{len(pngs)}")
    for n in missed_white_files[:10]:
        print(f"  - {n}")


if __name__ == "__main__":
    main()
