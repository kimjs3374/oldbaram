"""S1 백필 파서 — 이미 쌓인 로그를 맵 데이터(maps/<맵명>.json)로 환생.

게임/앱을 건드리지 않는 순수 오프라인 분석. logs/ + dist_dosa/logs_cloud/ 의
모든 .log 를 읽어 맵별 occupancy(walk) + 스팟(tab) + 차단 간선(blocked) 누적.

데이터 모델: 맵 데이터화 로드맵.md §1.

추출 소스(실측 라인):
  [attacker] ... map='M' coord=(x,y) valid=1 ... red_tab=R   → walk(+tab if R)
  [TRAIL-PUSH] map='M' coord=(x, y)                          → walk
  [MOVE] ... reason='...STUCK...h=(x, y)...blocked=D...' ... h_map='M' a_map='M2' map_neq=B
                                                            → blocked 간선

  py _backfill_maps.py            # 전체 파싱 → maps/ 생성 + 리포트
  py _backfill_maps.py --dry      # 파일에 안 쓰고 리포트만
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent
LOG_DIRS = [ROOT / "logs", ROOT / "dist_dosa" / "logs_cloud"]
OUT_DIR = ROOT / "maps"

# --- 라인 패턴 (attacker: 공백없는 coord, healer: 공백있는 coord 둘 다 커버) ---
RE_ATK = re.compile(
    r"\[attacker\].*?map='([^']*)'.*?coord=\((\d+),\s*(\d+)\)"
    r".*?valid=(\d).*?red_tab=(\d)"
)
RE_PUSH = re.compile(r"\[TRAIL-PUSH\] map='([^']*)' coord=\((\d+),\s*(\d+)\)")
RE_MOVE_CTX = re.compile(r"h_map='([^']*)' a_map='([^']*)'")
RE_MOVE_NEQ = re.compile(r"map_neq=(True|False)")
RE_STUCK_IN_REASON = re.compile(
    r"reason='[^']*?STUCK[^']*?h=\((\d+),\s*(\d+)\)[^']*?blocked=([UDLR])"
)
# STAT 라인도 컨텍스트(h_map/a_map) 갱신엔 쓰되 blocked 중복 카운트는 MOVE만.
RE_STAT_CTX = re.compile(r"\[STAT\].*h_map='([^']*)' a_map='([^']*)'")


def _new_map():
    return {
        "cells": defaultdict(lambda: {"walk": 0, "tab": 0}),
        "blocked": defaultdict(lambda: defaultdict(int)),
        "sessions": set(),
    }


def _clean_map_name(m: str) -> str:
    """맵명 canonical 정규화.

    이 로그들(6/9~10)은 RapidOCR(v42, 6/11) 이전이라 닫는 괄호 ')' 를 자주
    떨어뜨려 같은 맵이 '선비족3-2(6' / '선비족3-2(6)' 로 파편화된다.
    여는 괄호 수 > 닫는 괄호 수면 부족분을 ')' 로 보정해 합친다.
    """
    m = m.strip().rstrip(" .,")
    if not m:
        return ""
    nopen = m.count("(")
    nclose = m.count(")")
    if nopen > nclose:
        m = m + ")" * (nopen - nclose)
    return m


def main() -> None:
    dry = "--dry" in sys.argv
    maps: dict[str, dict] = defaultdict(_new_map)

    log_files = []
    for d in LOG_DIRS:
        if d.is_dir():
            log_files += sorted(d.glob("*.log"))

    stat = {
        "files": len(log_files), "atk_walk": 0, "push_walk": 0,
        "tab": 0, "blocked": 0, "blocked_unknown": 0,
    }

    for fp in log_files:
        sid = fp.name
        # 파일 순회 중 유지되는 힐러 맵 컨텍스트.
        ctx_hmap = ""
        ctx_amap = ""
        ctx_neq = False
        last_good_hmap = ""  # 비어있지 않았던 최근 h_map

        try:
            lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue

        for ln in lines:
            # 1) 격수 좌표 = walk / tab
            m = RE_ATK.search(ln)
            if m:
                name, x, y, valid, red = m.groups()
                name = _clean_map_name(name)
                if name and valid == "1" and not (x == "0" and y == "0"):
                    md = maps[name]
                    md["cells"][f"{x},{y}"]["walk"] += 1
                    md["sessions"].add(sid)
                    stat["atk_walk"] += 1
                    if red == "1":
                        md["cells"][f"{x},{y}"]["tab"] += 1
                        stat["tab"] += 1
                continue

            # 2) 힐러 trail = walk
            m = RE_PUSH.search(ln)
            if m:
                name, x, y = m.groups()
                name = _clean_map_name(name)
                if name:
                    md = maps[name]
                    md["cells"][f"{x},{y}"]["walk"] += 1
                    md["sessions"].add(sid)
                    stat["push_walk"] += 1
                continue

            # 3) 컨텍스트 갱신 (STAT 또는 MOVE 라인)
            cm = RE_MOVE_CTX.search(ln) or RE_STAT_CTX.search(ln)
            if cm:
                ctx_hmap, ctx_amap = cm.groups()
                if ctx_hmap:
                    last_good_hmap = ctx_hmap
                nq = RE_MOVE_NEQ.search(ln)
                if nq:
                    ctx_neq = (nq.group(1) == "True")

            # 4) MOVE 라인 reason 내 STUCK → blocked 간선
            if "[MOVE]" in ln:
                sm = RE_STUCK_IN_REASON.search(ln)
                if sm:
                    x, y, d = sm.groups()
                    # 힐러가 막힌 맵 = 힐러 맵. 비었으면 같은맵일 때 a_map,
                    # 다른맵이면 최근 신뢰 h_map. 그래도 없으면 unknown.
                    if ctx_hmap:
                        name = ctx_hmap
                    elif not ctx_neq and ctx_amap:
                        name = ctx_amap
                    elif last_good_hmap:
                        name = last_good_hmap
                    else:
                        name = ""
                    name = _clean_map_name(name)
                    if name:
                        maps[name]["blocked"][f"{x},{y}"][d] += 1
                        maps[name]["sessions"].add(sid)
                        stat["blocked"] += 1
                    else:
                        stat["blocked_unknown"] += 1

    # --- 직렬화 + 리포트 ---
    if not dry:
        OUT_DIR.mkdir(exist_ok=True)
    rows = []
    for name, md in maps.items():
        cells = md["cells"]
        if not cells and not md["blocked"]:
            continue
        xs = [int(k.split(",")[0]) for k in cells]
        ys = [int(k.split(",")[1]) for k in cells]
        bounds = {
            "x": [min(xs), max(xs)] if xs else [0, 0],
            "y": [min(ys), max(ys)] if ys else [0, 0],
        }
        spot_cells = sum(1 for c in cells.values() if c["tab"] > 0)
        blocked_edges = sum(len(v) for v in md["blocked"].values())
        rows.append((name, len(cells), spot_cells, blocked_edges,
                     len(md["sessions"])))

        if not dry:
            out = {
                "map": name,
                "bounds": bounds,
                "cells": {k: dict(v) for k, v in cells.items()},
                "blocked": {k: dict(v) for k, v in md["blocked"].items()},
                "sessions": len(md["sessions"]),
            }
            safe = re.sub(r'[\\/:*?"<>|]', "_", name)
            (OUT_DIR / f"{safe}.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    rows.sort(key=lambda r: -r[1])
    print(f"\n로그 {stat['files']}개 파싱 | walk(격수 {stat['atk_walk']} + "
          f"trail {stat['push_walk']}) | 스팟관측 {stat['tab']} | "
          f"blocked {stat['blocked']} (귀속실패 {stat['blocked_unknown']})")
    print(f"맵 {len(rows)}개 생성 → {OUT_DIR}\n")
    print(f"{'맵명':<16}{'셀수':>6}{'스팟셀':>7}{'막힌간선':>9}{'세션':>6}")
    print("-" * 46)
    for name, nc, ns, nb, sess in rows:
        print(f"{name:<16}{nc:>6}{ns:>7}{nb:>9}{sess:>6}")


if __name__ == "__main__":
    main()
