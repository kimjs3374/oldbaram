"""S2 경로 최적화 — 맵 데이터(maps/<맵명>.json) 위에서 A* 최단·고신뢰 경로.

walkable 셀을 노드로, 4방향 인접을 간선으로 격자 그래프 구성.
- blocked 간선(STUCK 로그)은 통과 금지 → 장애물 자동 회피.
- walk(방문수) 진한 셀로 가는 비용을 낮춰 검증된 통로 선호.
목표는 tab(빨탭 관측) 최대 셀 = 사냥 명당. 자동사냥은 이 경로 위를 이동·전투.

좌표축(실측): 격수 L 이동 시 coord x 감소 → L=(x-1), R=(x+1).
            U/D는 y축(화면 위=y작음 관례로 U=y-1, D=y+1 가정. blocked 적용용).

  py _route_optimize.py 선비족입구          # 입구→명당 최단경로 ASCII
  py _route_optimize.py 선비족입구 3 9 20 24 # start(3,9)→goal(20,24) 지정
"""
from __future__ import annotations

import heapq
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
MAPS = ROOT / "maps"

# 방향 → 좌표 델타 (L=x감소 실측 확정)
DELTA = {"L": (-1, 0), "R": (1, 0), "U": (0, -1), "D": (0, 1)}
WALK_MIN = 1          # 이 값 이상 방문해야 walkable 노드로 인정
WALK_BONUS = 3.0      # 진한 통로 선호 강도 (walk 클수록 진입비용↓)
MAX_GAP = 3           # 좌표 OCR 누락 보간: 같은 축 이 거리 이내 walk셀 연결


def _key(x, y):
    return f"{x},{y}"


def load_map(name):
    fp = MAPS / f"{name}.json"
    if not fp.is_file():
        sys.exit(f"맵 없음: {fp}")
    return json.loads(fp.read_text(encoding="utf-8"))


def build_graph(md):
    cells = md["cells"]
    blocked = md.get("blocked", {})
    walk = {k: c["walk"] for k, c in cells.items() if c["walk"] >= WALK_MIN}
    return walk, blocked


def neighbors(x, y, walk, blocked):
    """(x,y)에서 갈 수 있는 walkable 셀 + 진입비용.

    좌표 OCR 누락으로 1칸 인접이 비어도, 같은 축 MAX_GAP 이내 첫 walk셀을
    이어준다(보간). 단 출발셀의 그 방향이 STUCK이면 벽이므로 차단.
    """
    bset = blocked.get(_key(x, y), {})
    for d, (dx, dy) in DELTA.items():
        if d in bset:                 # 이 방향 STUCK 관측 → 벽. 회피.
            continue
        for step in range(1, MAX_GAP + 1):
            nx, ny = x + dx * step, y + dy * step
            nk = _key(nx, ny)
            if nk in walk:
                # 진입비용: 점프 거리 + 통로 약할수록 패널티
                cost = step + WALK_BONUS / walk[nk]
                yield nx, ny, cost
                break                 # 그 방향 첫 walk셀만 연결


def astar(start, goal, walk, blocked):
    sx, sy = start
    gx, gy = goal
    pq = [(0.0, sx, sy)]
    g = {_key(sx, sy): 0.0}
    came = {}
    while pq:
        _, x, y = heapq.heappop(pq)
        if (x, y) == (gx, gy):
            path = [(x, y)]
            while _key(x, y) in came:
                x, y = came[_key(x, y)]
                path.append((x, y))
            return path[::-1]
        for nx, ny, cost in neighbors(x, y, walk, blocked):
            nk = _key(nx, ny)
            ng = g[_key(x, y)] + cost
            if ng < g.get(nk, 1e18):
                g[nk] = ng
                came[nk] = (x, y)
                h = abs(nx - gx) + abs(ny - gy)  # 맨해튼 휴리스틱
                heapq.heappush(pq, (ng + h, nx, ny))
    return None


def pick_goal(md):
    """명당 = tab 최대 셀."""
    best, bt = None, -1
    for k, c in md["cells"].items():
        if c["tab"] > bt:
            bt, best = c["tab"], k
    x, y = map(int, best.split(","))
    return (x, y), bt


def pick_start(md, goal, walk, blocked):
    """입구 후보 = 명당에서 실제 도달 가능한 셀 중 가장 먼 곳.

    고립 노이즈 셀을 피하려고 BFS reachable 안에서만 고른다 → 경로 존재 보장.
    """
    from collections import deque
    gx, gy = goal
    seen = {(gx, gy)}
    q = deque([(gx, gy)])
    far, fd = (gx, gy), -1
    while q:
        x, y = q.popleft()
        d = abs(x - gx) + abs(y - gy)
        if d > fd:
            fd, far = d, (x, y)
        for nx, ny, _ in neighbors(x, y, walk, blocked):
            if (nx, ny) not in seen:
                seen.add((nx, ny))
                q.append((nx, ny))
    return far, len(seen)


def render(md, walk, blocked, path):
    cells = md["cells"]
    pset = set(path or [])
    xs = [int(k.split(",")[0]) for k in cells]
    ys = [int(k.split(",")[1]) for k in cells]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    print(f"  (S=start E=goal명당  +경로  @명당 *통로진함 .통로 #막힘)")
    for y in range(y0, y1 + 1):
        row = ""
        for x in range(x0, x1 + 1):
            k = _key(x, y)
            if path and (x, y) == path[0]:
                row += "S"
            elif path and (x, y) == path[-1]:
                row += "E"
            elif (x, y) in pset:
                row += "+"
            elif k in blocked:
                row += "#"
            elif k in cells:
                c = cells[k]
                row += "@" if c["tab"] >= 15 else "*" if c["tab"] > 0 \
                    else "o" if c["walk"] >= 20 else "."
            else:
                row += " "
        print(f"{y:2} |{row}|")


def main():
    if len(sys.argv) < 2:
        sys.exit("사용: py _route_optimize.py <맵명> [sx sy gx gy]")
    name = sys.argv[1]
    md = load_map(name)
    walk, blocked = build_graph(md)

    if len(sys.argv) >= 6:
        start = (int(sys.argv[2]), int(sys.argv[3]))
        goal = (int(sys.argv[4]), int(sys.argv[5]))
        gt = md["cells"].get(_key(*goal), {}).get("tab", "?")
        reach = None
    else:
        goal, gt = pick_goal(md)
        start, reach = pick_start(md, goal, walk, blocked)

    path = astar(start, goal, walk, blocked)
    print(f"\n[{name}] walkable {len(walk)}셀 · blocked {len(blocked)}좌표"
          + (f" · 명당 도달가능 {reach}셀" if reach else ""))
    print(f"start={start}  goal(명당 tab={gt})={goal}")
    if path:
        print(f"경로 길이 {len(path)}칸\n")
    else:
        print("경로 없음 (그래프 단절 — 데이터 부족 구간)\n")
    render(md, walk, blocked, path)


if __name__ == "__main__":
    main()
