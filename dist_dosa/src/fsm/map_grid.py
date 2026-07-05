"""맵 데이터 grid — 좌표 occupancy(walk) + 사냥 스팟(tab) + 차단 간선(blocked).

S0 실시간 수집기와 S1 백필 파서가 **같은 누적 코드**를 쓴다 (맵 데이터화 로드맵.md
§1·§6). add_* 는 메모리 dict O(1) 라 controller 핫패스에서 호출해도 안전하고,
디스크 IO 는 flush() 에서만 발생한다.

데이터 모델: maps/<맵명>.json
  cells   "x,y" -> {walk, tab}   walk=방문 누적, tab=빨탭(사냥 명당) 관측
  blocked "x,y" -> {DIR: cnt}    STUCK 로그 기반 차단 간선 (장애물 1차 증거)
  portals "<도착맵>" -> {dir, n, x, y}  출구(포탈) — 로드맵 §1 portals_v2 흡수
          (2026-07-05). x,y = PORTAL-DB 중앙값 보정된 출구좌표, n = 관측수.
          맵끼리 그래프 연결 → NavBrain 맵간 재합류/자율사냥 토대.

호출 측 책임: 좌표 dedup(이동 시에만 add_walk), STUCK edge(진입 시 1회 add_blocked).
중복 호출하면 카운트가 부풀려져 상대 빈도가 왜곡된다.
"""
from __future__ import annotations

import json
import pathlib
import re
from collections import defaultdict


def canon_map_name(m: str) -> str:
    """맵명 canonical 정규화 — OCR 닫는 괄호 ')' 누락 보정.

    '선비족3-2(6' / '선비족3-2(6)' 파편을 합친다 (백필·런타임 공용).
    """
    m = (m or "").strip().rstrip(" .,")
    if not m:
        return ""
    nopen, nclose = m.count("("), m.count(")")
    if nopen > nclose:
        m += ")" * (nopen - nclose)
    return m


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def _new_slot():
    return {
        "cells": defaultdict(lambda: {"walk": 0, "tab": 0}),
        "blocked": defaultdict(lambda: defaultdict(int)),
        # 격수 막힘률(§6.5): "x,y" → {DIR: {try, block}}. 막힘률=block/try.
        "attempts": defaultdict(
            lambda: defaultdict(lambda: {"try": 0, "block": 0})),
        # 출구: "<도착맵>" → {dir, n, x, y} (2026-07-05 portals_v2 흡수).
        "portals": {},
        "dirty": False,
    }


class MapGrid:
    """맵별 누적 데이터. lazy-load(맵 첫 등장 시 디스크에서 기존 누적 흡수)."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self._maps: dict[str, dict] = {}
        # 데이터 버전 — 관측이 추가될 때마다 증가. NavBrain flow field 캐시
        # 무효화 키 (세션 중 온라인 학습 반영).
        self._ver = 0

    # --- 내부 ---
    @staticmethod
    def _valid(x, y) -> bool:
        return (isinstance(x, int) and isinstance(y, int)
                and 0 <= x < 10000 and 0 <= y < 10000)

    def _slot(self, name: str):
        name = canon_map_name(name)
        if not name:
            return None, None
        s = self._maps.get(name)
        if s is None:
            s = self._load_or_new(name)
            self._maps[name] = s
        return name, s

    def _load_or_new(self, name: str) -> dict:
        s = _new_slot()
        fp = self.root / f"{_safe(name)}.json"
        if fp.is_file():
            try:
                d = json.loads(fp.read_text(encoding="utf-8"))
                for k, c in d.get("cells", {}).items():
                    s["cells"][k] = {"walk": int(c.get("walk", 0)),
                                     "tab": int(c.get("tab", 0))}
                for k, bd in d.get("blocked", {}).items():
                    for dd, cnt in bd.items():
                        s["blocked"][k][dd] = int(cnt)
                for k, dd in d.get("attempts", {}).items():
                    for di, tb in dd.items():
                        s["attempts"][k][di] = {
                            "try": int(tb.get("try", 0)),
                            "block": int(tb.get("block", 0)),
                        }
                for to, p in d.get("portals", {}).items():
                    s["portals"][to] = {
                        "dir": str(p.get("dir", "-")),
                        "n": int(p.get("n", 0)),
                        "x": int(p.get("x", 0)), "y": int(p.get("y", 0)),
                    }
            except Exception:
                pass  # 손상 파일이면 새로 시작 (수집은 멈추면 안 됨)
        return s

    # --- 수집 API (핫패스, O(1)) ---
    def add_walk(self, name: str, x: int, y: int, tab: bool = False) -> None:
        _, s = self._slot(name)
        if s is None or not self._valid(x, y):
            return
        c = s["cells"][f"{x},{y}"]
        c["walk"] += 1
        if tab:
            c["tab"] += 1
        s["dirty"] = True
        self._ver += 1

    def add_blocked(self, name: str, x: int, y: int, d: str) -> None:
        if d not in ("L", "R", "U", "D"):
            return
        _, s = self._slot(name)
        if s is None or not self._valid(x, y):
            return
        s["blocked"][f"{x},{y}"][d] += 1
        s["dirty"] = True
        self._ver += 1

    def add_attempt(self, name: str, x: int, y: int, d: str,
                    passed: bool) -> None:
        """격수가 (x,y)에서 d 방향 시도 결과 누적 (§6.5 막힘률).

        passed=True: 좌표가 d로 변함(통행 성공). False: 안 변함(몹 or 벽).
        막힘률=block/try → 항상 막힘=진짜 벽, 가끔=몹/일시. 누적 통계로 구분.
        """
        if d not in ("L", "R", "U", "D"):
            return
        _, s = self._slot(name)
        if s is None or not self._valid(x, y):
            return
        a = s["attempts"][f"{x},{y}"][d]
        a["try"] += 1
        if not passed:
            a["block"] += 1
        s["dirty"] = True
        self._ver += 1

    def add_portal(self, name: str, to_map: str, x: int, y: int,
                   d: str) -> None:
        """출구 관측 누적 — controller CTRL-MAPCHG 최종확정값을 받는다.

        좌표/방향은 이미 PORTAL-DB 중앙값+고정규칙 override 를 거친 값이라
        최신값으로 갱신(n만 누적). 호출측이 맵명 구조검증 책임.
        """
        to_map = canon_map_name(to_map)
        _, s = self._slot(name)
        if s is None or not to_map or not self._valid(x, y):
            return
        e = s["portals"].get(to_map) or {"dir": "-", "n": 0, "x": x, "y": y}
        e["n"] = int(e.get("n", 0)) + 1
        e["x"], e["y"] = int(x), int(y)
        if d in ("L", "R", "U", "D"):
            e["dir"] = d
        s["portals"][to_map] = e
        s["dirty"] = True
        self._ver += 1

    def portal_to(self, name: str, to_map: str):
        """직결 출구 조회 → ((x,y), dir, n) | (None, '-', 0).

        is_wall(핫패스, 메모리만)과 달리 lazy-load 포함 — 맵전환 시에만
        불리므로 디스크 1회 허용.
        """
        _, s = self._slot(name)
        if s is None:
            return None, "-", 0
        e = s["portals"].get(canon_map_name(to_map))
        if not e:
            return None, "-", 0
        return (int(e["x"]), int(e["y"])), str(e.get("dir", "-")), \
            int(e.get("n", 0))

    def import_bundle(self, bundle: dict) -> None:
        """클라우드/타 PC 묶음({맵명:{cells,blocked,...}})을 카운트 합산.

        교환·결합법칙이 성립(누적 카운트)이라 여러 PC 묶음을 순서 무관 병합 가능.
        D 작업기에서 빈 grid 에 여러 sid 묶음을 import → flush 하면 통합본.
        """
        if not isinstance(bundle, dict):
            return
        for name, md in bundle.items():
            _, s = self._slot(name)
            if s is None or not isinstance(md, dict):
                continue
            for k, c in md.get("cells", {}).items():
                cur = s["cells"][k]
                cur["walk"] += int(c.get("walk", 0))
                cur["tab"] += int(c.get("tab", 0))
            for k, bd in md.get("blocked", {}).items():
                for dd, cnt in bd.items():
                    if dd in ("L", "R", "U", "D"):
                        s["blocked"][k][dd] += int(cnt)
            for k, dd in md.get("attempts", {}).items():
                for di, tb in dd.items():
                    if di in ("L", "R", "U", "D"):
                        cur = s["attempts"][k][di]
                        cur["try"] += int(tb.get("try", 0))
                        cur["block"] += int(tb.get("block", 0))
            for to, p in md.get("portals", {}).items():
                cur = s["portals"].get(to)
                pn = int(p.get("n", 0))
                cn = int(cur.get("n", 0)) if cur else 0
                # 관측 많은 쪽 좌표/방향 채택, n 은 합산 (병합 순서 무관)
                if cur is None or pn >= cn:
                    s["portals"][to] = {
                        "dir": str(p.get("dir", "-")),
                        "x": int(p.get("x", 0)), "y": int(p.get("y", 0)),
                        "n": cn + pn}
                else:
                    cur["n"] = cn + pn
            s["dirty"] = True
            self._ver += 1

    def version(self) -> int:
        """관측 누적 버전 (NavBrain 캐시 무효화용)."""
        return self._ver

    def slot(self, name):
        """맵 슬롯 읽기 조회 (NavBrain 용, **읽기 전용으로만 쓸 것**).

        디스크 lazy-load 포함. 맵명 무효면 None.
        """
        _, s = self._slot(name)
        return s

    def is_wall(self, name, x, y, d, block_rate: float = 0.7,
                min_try: int = 3) -> bool:
        """학습된 벽 조회 — A* 없이 단순 멤버십 조회라 폭주 위험 0.

        (x,y)에서 d 방향이: ①STUCK 학습 벽(blocked, 힐러 3.5s 막힘) 또는
        ②격수 막힘률(attempts block/try ≥ block_rate, 시도 min_try+) 이면 벽.
        데이터 쌓일수록 신뢰도↑ (없으면 False → 호출측 기존 동작).
        """
        s = self._maps.get(canon_map_name(name))
        if s is None:
            return False
        k = f"{x},{y}"
        if d in s["blocked"].get(k, {}):
            return True
        a = s["attempts"].get(k, {}).get(d)
        if a:
            t = int(a.get("try", 0))
            b = int(a.get("block", 0))
            if t >= min_try and b / max(1, t) >= block_rate:
                return True
        return False

    def route_next_dir(self, map_name, start, goal, avoid=None):
        """§2 S3: 수집된 maps(walk/blocked)로 start→goal A* 첫 방향.

        avoid=peers(다른 캐릭 칸). 데이터 부족/경로 없으면 None(호출측 fallback).
        """
        from .map_route import astar_next_dir
        name = canon_map_name(map_name)
        s = self._maps.get(name)
        if s is None:
            return None
        walk = {k: c["walk"] for k, c in s["cells"].items()
                if c.get("walk", 0) >= 1}
        if not walk:
            return None
        blocked = {k: dict(v) for k, v in s["blocked"].items()}
        return astar_next_dir(walk, blocked, start, goal, avoid)

    # --- 영속화 ---
    def flush(self) -> int:
        """dirty 맵만 디스크에 저장. 저장한 맵 수 반환. (메모리가 누적본)."""
        dirty = [(n, s) for n, s in self._maps.items() if s["dirty"]]
        if not dirty:
            return 0
        self.root.mkdir(parents=True, exist_ok=True)
        for name, s in dirty:
            cells = s["cells"]
            xs = [int(k.split(",")[0]) for k in cells]
            ys = [int(k.split(",")[1]) for k in cells]
            out = {
                "map": name,
                "bounds": {
                    "x": [min(xs), max(xs)] if xs else [0, 0],
                    "y": [min(ys), max(ys)] if ys else [0, 0],
                },
                "cells": {k: dict(v) for k, v in cells.items()},
                "blocked": {k: dict(v) for k, v in s["blocked"].items()},
                "attempts": {k: {di: dict(tb) for di, tb in dd.items()}
                             for k, dd in s["attempts"].items()},
                "portals": {to: dict(p) for to, p in s["portals"].items()},
            }
            tmp = self.root / f"{_safe(name)}.json.tmp"
            dst = self.root / f"{_safe(name)}.json"
            tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(dst)  # 원자적 교체 (쓰다 죽어도 기존 파일 보존)
            s["dirty"] = False
        return len(dirty)
