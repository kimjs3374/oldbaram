"""런타임 A* 우회 (§2 S3 2026-06-13): 수집된 maps(walk/blocked) + peers(다른
캐릭 일시 장애물)로 STUCK 탈출 다음 방향을 계산.

_route_optimize.py(오프라인 분석)의 A* 를 런타임용으로 압축. healer_worker 가
STUCK 시 현재→목표(격수/포탈) 경로의 첫 방향을 받아 최적 우회.

좌표축(실측 정정 2026-07-05, 메모리 project_coord_axis_ud): R=x+, L=x-,
**D=y+(아래), U=y-(위)**. x·y 는 0 포함 자연수.
🔴 옛 주석 "U=y+ (2026-06-13 확정)" 은 사용자 멘탈모델이었고 실측(2026-06-15,
STUCK-ALIGN v90/_PEER_DXY v94 수정 근거)과 반대라 blocked 간선 방향이
뒤집혀 해석되는 버그였음. dormant(호출부 0)라 무사고였을 뿐. NavBrain 연결
전 nav_features.DELTA 단일 정본으로 통일.
- walk: {"x,y": walk횟수} (검증된 통행 칸. 격수·힐러가 밟은 곳).
- blocked: {"x,y": {DIR: ..}} (STUCK 학습 벽 — 그 칸의 그 방향 간선 차단).
- avoid: set((x,y)) (peers = 다른 캐릭 현재 칸. 겹칠 수 없어 일시 장애물).
"""
from __future__ import annotations

import heapq

DELTA = {"L": (-1, 0), "R": (1, 0), "U": (0, -1), "D": (0, 1)}
MAX_GAP = 3  # 좌표 OCR 누락 보간: 같은 축 이 거리 내 walk칸 연결.


def _neighbors(x, y, walk, blocked, avoid):
    """(x,y)에서 갈 수 있는 walk칸 + 진입 방향/비용. peer칸/blocked간선 회피."""
    bset = blocked.get(f"{x},{y}", {})
    for d, (dx, dy) in DELTA.items():
        if d in bset:                       # STUCK 학습 벽 → 회피
            continue
        for step in range(1, MAX_GAP + 1):
            nx, ny = x + dx * step, y + dy * step
            if (nx, ny) in avoid:           # 다른 캐릭 칸 → 그 축 진행 차단
                break
            if f"{nx},{ny}" in walk:
                yield nx, ny, d, step
                break


def astar_next_dir(walk, blocked, start, goal, avoid=None):
    """start→goal 최적 경로의 **첫 이동 방향** 'L/R/U/D' 반환. 경로 없으면 None.

    walk 데이터가 비었거나 start==goal 이면 None(호출측 fallback).
    """
    if not walk or start == goal:
        return None
    avoid = avoid or set()
    sx, sy = start
    gx, gy = goal
    pq = [(0.0, sx, sy)]
    gcost = {(sx, sy): 0.0}
    first = {}          # (x,y) → 출발점에서 그 칸까지의 첫 스텝 방향
    seen = set()
    while pq:
        _, x, y = heapq.heappop(pq)
        if (x, y) == (gx, gy):
            return first.get((x, y))
        if (x, y) in seen:
            continue
        seen.add((x, y))
        for nx, ny, d, step in _neighbors(x, y, walk, blocked, avoid):
            ng = gcost[(x, y)] + step
            if ng < gcost.get((nx, ny), 1e18):
                gcost[(nx, ny)] = ng
                first[(nx, ny)] = first.get((x, y), d)  # 첫 방향 전파
                h = abs(nx - gx) + abs(ny - gy)
                heapq.heappush(pq, (ng + h, nx, ny))
    return None
