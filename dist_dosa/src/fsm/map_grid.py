"""맵 데이터 grid — 좌표 occupancy(walk) + 사냥 스팟(tab) + 차단 간선(blocked).

S0 실시간 수집기와 S1 백필 파서가 **같은 누적 코드**를 쓴다 (맵 데이터화 로드맵.md
§1·§6). add_* 는 메모리 dict O(1) 라 controller 핫패스에서 호출해도 안전하고,
디스크 IO 는 flush() 에서만 발생한다.

데이터 모델: maps/<맵명>.json
  cells   "x,y" -> {walk, tab}   walk=방문 누적, tab=빨탭(사냥 명당) 관측
  blocked "x,y" -> {DIR: cnt}    STUCK 로그 기반 차단 간선 (장애물 1차 증거)

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
        "dirty": False,
    }


class MapGrid:
    """맵별 누적 데이터. lazy-load(맵 첫 등장 시 디스크에서 기존 누적 흡수)."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self._maps: dict[str, dict] = {}

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

    def add_blocked(self, name: str, x: int, y: int, d: str) -> None:
        if d not in ("L", "R", "U", "D"):
            return
        _, s = self._slot(name)
        if s is None or not self._valid(x, y):
            return
        s["blocked"][f"{x},{y}"][d] += 1
        s["dirty"] = True

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
            }
            tmp = self.root / f"{_safe(name)}.json.tmp"
            dst = self.root / f"{_safe(name)}.json"
            tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(dst)  # 원자적 교체 (쓰다 죽어도 기존 파일 보존)
            s["dirty"] = False
        return len(dirty)
