# -*- coding: utf-8 -*-
r"""포탈(출구) 백필 — 로그의 [CTRL-MAPCHG] 전이를 maps grid portals 로 통합.

로그가 영구 정본이므로 재실행 멱등 (기존 portals 를 로그 재집계로 덮어씀).
_cloud_maps.py 가 maps_cloud/ 를 재생성한 직후 돌리는 것이 정위치
(_nav_auto.bat / _nav_retrain.bat 단계에 포함).

사용: py -3 _portal_backfill.py [--maps maps_cloud] [--logs logs_cloud]
"""
import argparse
import hashlib
import json
import pathlib
import re
import statistics
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT / "dist_dosa"))

from src.fsm.map_grid import MapGrid, canon_map_name          # noqa: E402
from src.fsm.controller import Follower                        # noqa: E402

RE_CHG = re.compile(
    r"\[CTRL-MAPCHG\] '([^']*)'→'([^']*)' "
    r"exit_coord=\((-?\d+), (-?\d+)\) exit_dir='([^']*)'")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--maps", default=str(ROOT / "maps_cloud"))
    ap.add_argument("--logs", default=str(ROOT / "logs_cloud"))
    args = ap.parse_args()

    edges = defaultdict(list)   # (from,to) -> [(x,y,dir)]
    seen = set()
    n_files = 0
    for fp in pathlib.Path(args.logs).glob("*.log"):
        h = hashlib.md5(fp.read_bytes()).hexdigest()
        if h in seen:
            continue        # 동일 세션 sid 이중 업로드 제거 (실측 7그룹)
        seen.add(h)
        n_files += 1
        try:
            txt = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in RE_CHG.finditer(txt):
            a = canon_map_name(m.group(1))
            b = canon_map_name(m.group(2))
            if not (Follower._is_valid_sunbi_map(a)
                    and Follower._is_valid_sunbi_map(b)):
                continue
            edges[(a, b)].append(
                (int(m.group(3)), int(m.group(4)), m.group(5)))

    grid = MapGrid(args.maps)
    n_write = 0
    for (a, b), obs in edges.items():
        xs = sorted(o[0] for o in obs)
        ys = sorted(o[1] for o in obs)
        dirs = [o[2] for o in obs if o[2] in "LRUD"]
        d = Counter(dirs).most_common(1)[0][0] if dirs else "-"
        _, s = grid._slot(a)
        if s is None:
            continue
        # 로그 재집계가 정본 — 기존 값 덮어씀 (멱등)
        s["portals"][b] = {"dir": d, "n": len(obs),
                           "x": xs[len(xs) // 2], "y": ys[len(ys) // 2]}
        s["dirty"] = True
        n_write += 1
    n_flush = grid.flush()
    outdeg = defaultdict(set)
    for (a, b) in edges:
        outdeg[a].add(b)
    print(f"[portal-backfill] 로그 {n_files}개(md5 유니크) → "
          f"전이 {len(edges)}종/{sum(len(v) for v in edges.values())}회 → "
          f"portals {n_write}건 기록, 맵파일 {n_flush}개 갱신, "
          f"분기맵(출구2+) {sum(1 for v in outdeg.values() if len(v) >= 2)}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
