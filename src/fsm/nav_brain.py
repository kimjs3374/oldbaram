"""NavBrain — 수집 데이터 기반 경로 두뇌 (경로 딥러닝 학습.md 정본).

2계층:
  1) NavGraph: 살아있는 MapGrid(walk/blocked/attempts) 위 Dijkstra flow field.
     세션 중 새 관측이 들어올 때마다(grid 버전) 자동 갱신 = 온라인 학습.
  2) NavNet:  격수 발자국 behavior cloning ONNX(nav_policy.onnx). 없으면
     그래프 단독 동작 (digit_cnn 의 sess=None + ready() 폴백 관례).

핵심 규율 (v113 B3D 3.5배 악화 교훈):
  - 데이터 부족하면 **기권(None)** — 호출측 기존 trail/직선 로직이 안전망.
  - 절대 예외를 밖으로 던지지 않음 (핫패스). 실패=기권.
  - 제안 히스테리시스(HOLD_S)로 want 급변 방지 (STUCK 감지 무력화 방지).

좌표축: nav_features.DELTA 단일 정본 (실측 D=y+, U=y-).
"""
from __future__ import annotations

import heapq
import os
import pathlib
import time

from .nav_features import (ACTIONS, ACTION_IDX, DELTA, NAV_ONNX,
                           blocked_evidence, encode_patch, encode_scalars)

_DIRS4 = ("L", "R", "U", "D")


def _intra_threads(default: int = 1) -> int:
    """ort 스레드 캡 — YOLO/OCR 3엔진 경합 회귀 방지 (실측 관례: 캡 필수)."""
    try:
        return max(1, int(os.environ.get("OB_NAV_INTRA_THREADS", default)))
    except Exception:
        return default


class NavBrain:
    # --- 튜닝 상수 (리플레이 평가 _nav_replay_eval.py 로 확정/조정) ---
    MIN_CELLS = 30      # 기권 게이트: 관측 셀 최소 (실측 맵 셀 중앙값 65)
    MIN_WALK = 300      # 기권 게이트: walk 총합 최소 (실측 맵 walk 중앙값 199)
    WALK_BONUS = 3.0    # 통행량 선호 계수 (_route_optimize.py 와 동일)
    BLK_SOFT = 4.0      # 차단 증거 1건당 소프트 페널티
    BLK_HARD = 50.0     # 차단 증거 포화(≥3회/막힘률≥0.7) 하드 페널티
    MAX_GAP = 3         # 좌표 OCR 누락 보간 (map_route 관례)
    HOLD_S = 0.4        # 제안 히스테리시스 (want 급변 → STUCK 감지 무력화 방지)
    NET_AGREE_BONUS = 0.35   # 그래프·정책망 일치 시 conf 가산
    NET_W = 10.0        # 정책망 prior 가중(알파고 결합) — 후보 비용에서 차감.
                        # 후보는 flow 연결 방향뿐이라 망이 벽으로 못 끔(안전,
                        # blocked 하드 페널티 50 > NET_W×1 이라 벽 회피 우선 유지).
                        # 2026-07-05 홀드아웃 스윕 실측: 1.5/3/6/10 중 10이
                        # 전체 일치율 64.8%·hard(직선오답) 28.1%로 최적.
    FLOW_CACHE_MAX = 8

    def __init__(self, grid, log=None, model_dir=None):
        self._grid = grid
        self._log = log
        self._flow_cache = {}   # (map, goal) -> (grid_ver, {cell: cost})
        self._stat_cache = {}   # map -> (grid_ver, n_cells, walk_total)
        self._hold = {}         # purpose_key -> (dir, ts)
        self._sess = None
        self._in_x = self._in_g = self._out_y = None
        self._pg_cache = None   # 맵간 포탈 그래프 캐시 (60s)
        self._pg_ts = 0.0
        base = pathlib.Path(model_dir) if model_dir else pathlib.Path(__file__).parent
        self._model_path = base / NAV_ONNX
        self._init_net()

    # ------------------------------------------------------------------ net
    def _init_net(self):
        try:
            if not self._model_path.is_file():
                return
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.intra_op_num_threads = _intra_threads(1)
            so.inter_op_num_threads = 1
            self._sess = ort.InferenceSession(
                str(self._model_path), sess_options=so,
                providers=["CPUExecutionProvider"])
            ins = self._sess.get_inputs()
            self._in_x, self._in_g = ins[0].name, ins[1].name
            self._out_y = self._sess.get_outputs()[0].name
        except Exception:
            self._sess = None  # 모델 문제 = 그래프 단독 (기권 폴백 관례)

    def ready_net(self) -> bool:
        return self._sess is not None

    def _net_probs(self, slot, cur, goal, last_dir):
        """정책망 5-way 확률. 실패 시 None."""
        if self._sess is None:
            return None
        try:
            import numpy as np
            x = encode_patch(slot, cur[0], cur[1])[None]
            g = encode_scalars(cur, goal, last_dir)[None]
            y = self._sess.run([self._out_y],
                               {self._in_x: x, self._in_g: g})[0][0]
            y = y - float(y.max())
            e = np.exp(y)
            return e / float(e.sum())
        except Exception:
            return None

    # ---------------------------------------------------------------- graph
    def _slot_stats(self, name, slot):
        ver = self._grid.version()
        st = self._stat_cache.get(name)
        if st is not None and st[0] == ver:
            return st[1], st[2]
        cells = slot.get("cells") or {}
        n = 0
        total = 0
        for c in cells.values():
            w = int(c.get("walk", 0))
            if w > 0:
                n += 1
                total += w
        self._stat_cache[name] = (ver, n, total)
        return n, total

    def _edge_cost(self, slot, x, y, d, step, walk_dst) -> float:
        """(x,y)에서 d 방향으로 step칸 이동해 walk_dst 셀 도달 비용."""
        ev = blocked_evidence(slot, x, y, d)
        pen = 0.0
        if ev >= 0.7:
            pen = self.BLK_HARD
        elif ev > 0.0:
            pen = self.BLK_SOFT * (ev * 3.0)  # cnt1≈1.33→5.3, cnt2≈2.67→10.7
        return step + self.WALK_BONUS / (1.0 + walk_dst) + pen

    def _neighbors_rev(self, slot, walk, bx, by):
        """flow(goal→바깥) 확장용: '어떤 셀 A가 d 방향으로 (bx,by)에 도달하나'."""
        for d, (dx, dy) in DELTA.items():
            for step in range(1, self.MAX_GAP + 1):
                ax, ay = bx - dx * step, by - dy * step
                w = walk.get((ax, ay))
                if w is not None:
                    yield ax, ay, d, step
                    break

    def _flow(self, name, slot, goal):
        """goal 기준 flow field {(x,y): 최소비용}. 캐시(grid 버전)."""
        ver = self._grid.version()
        key = (name, goal)
        hit = self._flow_cache.get(key)
        if hit is not None and hit[0] == ver:
            return hit[1]
        cells = slot.get("cells") or {}
        walk = {}
        for k, c in cells.items():
            w = int(c.get("walk", 0))
            if w >= 1:
                xs, ys = k.split(",")
                walk[(int(xs), int(ys))] = w
        if not walk:
            return None
        gx, gy = goal
        # goal 셀이 미관측이면 맨해튼 ≤2 내 관측 셀들을 시드로 (포탈/출구 인접 허용).
        seeds = []
        if (gx, gy) in walk:
            seeds.append(((gx, gy), 0.0))
        else:
            for (x, y), _w in walk.items():
                d0 = abs(x - gx) + abs(y - gy)
                if d0 <= 2:
                    seeds.append((((x, y)), float(d0)))
        if not seeds:
            return None
        cost = {}
        pq = []
        for (sx, sy), c0 in seeds:
            cost[(sx, sy)] = c0
            heapq.heappush(pq, (c0, sx, sy))
        while pq:
            c, x, y = heapq.heappop(pq)
            if c > cost.get((x, y), 1e18):
                continue
            for ax, ay, d, step in self._neighbors_rev(slot, walk, x, y):
                ec = self._edge_cost(slot, ax, ay, d, step, walk[(x, y)])
                nc = c + ec
                if nc < cost.get((ax, ay), 1e18):
                    cost[(ax, ay)] = nc
                    heapq.heappush(pq, (nc, ax, ay))
        if len(self._flow_cache) >= self.FLOW_CACHE_MAX:
            self._flow_cache.pop(next(iter(self._flow_cache)))
        self._flow_cache[key] = (ver, cost)
        return cost

    def _step_candidates(self, slot, walk_lookup, flow, cur, avoid):
        """cur에서 4방향 후보 (dir, 총비용). gap 보간 + avoid 회피."""
        cx, cy = cur
        out = []
        for d, (dx, dy) in DELTA.items():
            for step in range(1, self.MAX_GAP + 1):
                nx, ny = cx + dx * step, cy + dy * step
                if (nx, ny) in avoid:
                    break
                f = flow.get((nx, ny))
                if f is not None:
                    w = walk_lookup.get((nx, ny), 1)
                    out.append((d, self._edge_cost(slot, cx, cy, d, step, w) + f))
                    break
        return out

    # ------------------------------------------------------------------ API
    def suggest(self, map_name, cur, goal, avoid=(), last_dir="-",
                hold=True, purpose="follow"):
        """다음 한 칸 제안. 반환 (dir|None, conf 0..1, src).

        기권(None) 조건: 데이터 부족 맵 / cur·goal 연결 불가 / 내부 오류.
        기권 시 호출측은 기존 로직 그대로 → 악화 불가능.
        """
        try:
            if cur is None or goal is None or tuple(cur) == tuple(goal):
                return None, 0.0, "nogoal"
            slot = self._grid.slot(map_name)
            if slot is None:
                return None, 0.0, "nomap"
            name = map_name
            n_cells, walk_total = self._slot_stats(name, slot)
            if n_cells < self.MIN_CELLS or walk_total < self.MIN_WALK:
                return None, 0.0, f"sparse({n_cells}c/{walk_total}w)"
            flow = self._flow(name, slot, (int(goal[0]), int(goal[1])))
            if flow is None:
                return None, 0.0, "noflow"
            cells = slot.get("cells") or {}
            walk_lookup = {}
            for k, c in cells.items():
                w = int(c.get("walk", 0))
                if w >= 1:
                    xs, ys = k.split(",")
                    walk_lookup[(int(xs), int(ys))] = w
            cand = self._step_candidates(slot, walk_lookup, flow,
                                         (int(cur[0]), int(cur[1])),
                                         set(avoid or ()))
            if not cand:
                return None, 0.0, "nostep"
            cand.sort(key=lambda t: t[1])
            graph_d = cand[0][0]
            src = "G"
            probs = self._net_probs(slot, cur, goal, last_dir)
            if probs is not None:
                # 알파고식 결합: 탐색 비용 − 정책망 prior. 방향 자체가 바뀔
                # 수 있으나 후보는 flow 연결된 관측 통로뿐 → 벽 진입 불가.
                cand = sorted(
                    ((c - self.NET_W * float(probs[ACTION_IDX[d]]), d, c)
                     for d, c in cand))
                best_s, best_d, best_c = cand[0]
                margin = (cand[1][0] - best_s) if len(cand) > 1 else 2.0
                conf = 0.55 + min(0.25, 0.12 * max(0.0, margin))
                net_d = ACTIONS[int(probs.argmax())]
                if net_d == best_d:
                    conf = min(1.0, conf + self.NET_AGREE_BONUS
                               * float(probs.max()))
                    src = "G+N"
                elif best_d != graph_d:
                    src = f"N>{graph_d}"   # 망이 그래프 1순위를 뒤집음
                else:
                    conf *= 0.8
                    src = f"G/N:{net_d}"
                cand = [(d, c) for _s, d, c in cand]
            else:
                best_d, best_c = cand[0]
                margin = (cand[1][1] - best_c) if len(cand) > 1 else 2.0
                conf = 0.55 + min(0.25, 0.12 * margin)
            # 히스테리시스: 직전 제안 유지 (STUCK 감지 무력화 방지).
            if hold:
                hkey = (purpose, name)
                prev = self._hold.get(hkey)
                now = time.time()
                if (prev is not None and prev[0] != best_d
                        and now - prev[1] < self.HOLD_S
                        and any(d == prev[0] for d, _ in cand)):
                    return prev[0], conf * 0.9, src + "+hold"
                self._hold[hkey] = (best_d, now)
            return best_d, conf, src
        except Exception:
            return None, 0.0, "err"

    # ------------------------------------------------- 맵간 그래프 (portals)
    def _portal_graph(self):
        """{from맵: {to맵: (x,y,dir,n)}} — maps/*.json portals 전수 스캔.

        포탈은 드물게 변하므로 60s 캐시. 로드된 슬롯의 신규 관측도 반영.
        """
        now = time.time()
        g = getattr(self, "_pg_cache", None)
        if g is not None and now - self._pg_ts < 60.0:
            return g
        graph = {}
        try:
            import json as _json
            for fp in self._grid.root.glob("*.json"):
                try:
                    d = _json.loads(fp.read_text(encoding="utf-8"))
                except Exception:
                    continue
                ps = d.get("portals") or {}
                if not ps:
                    continue
                frm = d.get("map") or fp.stem
                graph[frm] = {
                    to: (int(p.get("x", 0)), int(p.get("y", 0)),
                         str(p.get("dir", "-")), int(p.get("n", 0)))
                    for to, p in ps.items()}
        except Exception:
            pass
        # 메모리 슬롯(이번 세션 신규 관측)이 디스크보다 최신 → 덮어씀.
        for name, s in self._grid._maps.items():
            for to, p in (s.get("portals") or {}).items():
                graph.setdefault(name, {})[to] = (
                    int(p["x"]), int(p["y"]),
                    str(p.get("dir", "-")), int(p.get("n", 0)))
        self._pg_cache = graph
        self._pg_ts = now
        return graph

    def portal_goal(self, from_map, to_map):
        """from맵에서 to맵으로 가는 출구좌표/방향 — 직결 없으면 BFS 첫 홉.

        반환 ((x,y), dir, 남은홉수) | (None, '-', -1). 기권=None (관례).
        """
        try:
            graph = self._portal_graph()
            src = graph.get(from_map) or {}
            if to_map in src:
                x, y, d, n = src[to_map]
                return (x, y), d, 1
            # BFS (최대 8홉) — 첫 홉 포탈 반환.
            from collections import deque as _dq
            q = _dq([(from_map, None, 0)])
            first = {}
            seen = {from_map}
            while q:
                cur, fh, hops = q.popleft()
                if hops >= 8:
                    continue
                for nxt, (x, y, d, n) in (graph.get(cur) or {}).items():
                    if nxt in seen:
                        continue
                    seen.add(nxt)
                    f = fh or (x, y, d)
                    if nxt == to_map:
                        return (f[0], f[1]), f[2], hops + 1
                    first[nxt] = f
                    q.append((nxt, f, hops + 1))
            return None, "-", -1
        except Exception:
            return None, "-", -1

    def unstick_dir(self, map_name, cur, blocked_dir, goal, avoid=()):
        """STUCK 우회 방향 제안 — blocked_dir 제외, flow 비용 최소 방향.

        반환 dir|None. 직교 후보 재배열용 (호출측이 후보 집합으로 제한).
        """
        try:
            if cur is None or goal is None:
                return None
            slot = self._grid.slot(map_name)
            if slot is None:
                return None
            n_cells, walk_total = self._slot_stats(map_name, slot)
            if n_cells < self.MIN_CELLS or walk_total < self.MIN_WALK:
                return None
            flow = self._flow(map_name, slot, (int(goal[0]), int(goal[1])))
            if flow is None:
                return None
            cells = slot.get("cells") or {}
            walk_lookup = {}
            for k, c in cells.items():
                w = int(c.get("walk", 0))
                if w >= 1:
                    xs, ys = k.split(",")
                    walk_lookup[(int(xs), int(ys))] = w
            cand = self._step_candidates(slot, walk_lookup, flow,
                                         (int(cur[0]), int(cur[1])),
                                         set(avoid or ()))
            cand = [(d, c) for d, c in cand if d != blocked_dir]
            if not cand:
                return None
            cand.sort(key=lambda t: t[1])
            return cand[0][0]
        except Exception:
            return None
