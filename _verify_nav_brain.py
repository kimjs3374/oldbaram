# -*- coding: utf-8 -*-
r"""NavBrain 단위 회귀 — 좌표축/기권 게이트/flow 우회/히스테리시스/실데이터.

실행: py -3 _verify_nav_brain.py   (D:\oldbaram 루트, 게임 무접촉)
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent / "dist_dosa"))

from src.fsm.nav_features import (ACTIONS, DELTA, action_from_delta,
                                  encode_patch, encode_scalars, PATCH, CH)
from src.fsm.map_grid import MapGrid
from src.fsm import map_route
from src.fsm.nav_brain import NavBrain

FAIL = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name} {detail}")
    if not cond:
        FAIL.append(name)


# ── 1. 좌표축 단일 정본 (실측: D=y+, U=y-) ─────────────────────────────
check("축: D=y+", DELTA["D"] == (0, 1))
check("축: U=y-", DELTA["U"] == (0, -1))
check("축: map_route 동일 정본", map_route.DELTA == DELTA,
      f"map_route={map_route.DELTA}")
check("action_from_delta D", action_from_delta(0, 1) == "D")
check("action_from_delta U", action_from_delta(0, -1) == "U")
check("action_from_delta STAY", action_from_delta(0, 0) == "-")
check("action_from_delta 비단위=None", action_from_delta(2, 0) is None)

# ── 2. 합성 맵: ㄱ자 통로 + 직선 경로 중간 벽 ──────────────────────────
# 지형: y=5 가로 통로 x=0..10, x=10 세로 통로 y=0..5.
#       (5,5)에서 R 방향 간선에 blocked 3회 (직진 차단) → 우회 확인용.
tmp = tempfile.mkdtemp()
g = MapGrid(tmp)
M = "선비족1"  # 구조검증 통과 이름
for x in range(0, 11):
    for _ in range(40):
        g.add_walk(M, x, 5)
for y in range(0, 6):
    for _ in range(40):
        g.add_walk(M, 10, y)
ver0 = g.version()
check("grid version 증가", ver0 > 0, f"ver={ver0}")

nav = NavBrain(g)
# 데이터: 셀 16개 < MIN_CELLS=30 → 기권해야 정상.
d, conf, src = nav.suggest(M, (0, 5), (10, 0))
check("기권: MIN_CELLS 미달", d is None and src.startswith("sparse"), src)

# 셀 수 늘리기 (30개 이상): y=6 가로 통로 추가 + 세로 통로 x=0.
for x in range(0, 11):
    for _ in range(40):
        g.add_walk(M, x, 6)
for y in range(0, 6):
    for _ in range(40):
        g.add_walk(M, 0, y)
d, conf, src = nav.suggest(M, (0, 5), (10, 0))
check("충분 데이터: 제안 나옴", d in ("L", "R", "U", "D"), f"d={d} src={src}")

# blocked 간선: (5,5)→R 3회 (하드 페널티) → (0,5)에서 (10,5) 갈 때
# y=6 통로로 우회하는지 (첫 스텝이 D(아래 y=6) 또는 일단 R 후 우회).
for _ in range(3):
    g.add_blocked(M, 5, 5, "R")
nav2 = NavBrain(g)  # 캐시 없는 새 인스턴스
# (4,5)에서 목표 (10,5): 직진 R은 (5,5)R 벽을 곧 만남 → flow가 우회 반영.
d_at5, _, _ = nav2.suggest(M, (5, 5), (10, 5), hold=False)
check("blocked 간선 회피: (5,5)서 R 금지", d_at5 != "R", f"d={d_at5}")

# 시뮬레이션: (0,5)→(10,0) 도달 (blocked 우회 포함).
cur, goal, steps, ok = (0, 5), (10, 0), 0, False
while steps < 60:
    d, c, s = nav2.suggest(M, cur, goal, hold=False)
    if d is None:
        break
    dx, dy = DELTA[d]
    nxt = None
    for st in range(1, 4):
        cand = (cur[0] + dx * st, cur[1] + dy * st)
        sl = g.slot(M)
        if f"{cand[0]},{cand[1]}" in sl["cells"]:
            nxt = cand
            break
    if nxt is None:
        break
    cur = nxt
    steps += 1
    if cur == goal:
        ok = True
        break
check("합성맵 시뮬 도달", ok, f"steps={steps} 최종={cur}")

# ── 3. 온라인 학습(버전 캐시 무효화): 새 관측 후 flow 재계산 ───────────
v_before = g.version()
g.add_walk(M, 3, 4)
check("관측 추가 → 버전 증가", g.version() > v_before)

# ── 4. unstick: U 막힘 시 blocked_dir 제외 제안 ────────────────────────
du = nav2.unstick_dir(M, (5, 5), "R", (10, 5))
check("unstick: 막힌 방향 제외", du != "R" and du in ("L", "U", "D", None),
      f"du={du}")

# ── 5. 히스테리시스: HOLD_S 내 제안 유지 ───────────────────────────────
n3 = NavBrain(g)
d1, _, _ = n3.suggest(M, (0, 5), (10, 5), purpose="h")
d2, _, s2 = n3.suggest(M, (0, 5), (10, 5), purpose="h")
check("히스테리시스: 같은 입력 동일 제안", d1 == d2, f"{d1}=={d2} src={s2}")

# ── 6. 인코딩 shape/값 ─────────────────────────────────────────────────
sl = g.slot(M)
p = encode_patch(sl, 5, 5)
check("패치 shape", p.shape == (CH, PATCH, PATCH), str(p.shape))
check("패치 중심 observed=1", p[1, PATCH // 2, PATCH // 2] == 1.0)
# blocked R 채널(DIRS 순서 U,D,L,R → R=ch5) 값 1.0 (3회 포화).
check("패치 blocked R 채널", p[5, PATCH // 2, PATCH // 2] == 1.0,
      f"val={p[5, PATCH//2, PATCH//2]}")
sc = encode_scalars((5, 5), (10, 5), "R")
check("스칼라 shape/goal dx", sc.shape == (8,) and abs(sc[0] - 5 / 16) < 1e-6,
      f"sc[0]={sc[0]:.3f}")

# ── 7. 실데이터 (maps_cloud) ───────────────────────────────────────────
mc = pathlib.Path(__file__).parent / "maps_cloud"
if mc.is_dir() and any(mc.glob("*.json")):
    gr = MapGrid(mc)
    nr = NavBrain(gr)
    s3 = gr.slot("선비족3")
    if s3 and s3["cells"]:
        cells = sorted(
            ((int(k.split(",")[0]), int(k.split(",")[1]), c["walk"])
             for k, c in s3["cells"].items() if c["walk"] > 0),
            key=lambda t: -t[2])
        top = cells[0]
        far = max(cells, key=lambda t: abs(t[0] - top[0]) + abs(t[1] - top[1]))
        cur, goal, steps, ok = (far[0], far[1]), (top[0], top[1]), 0, False
        while steps < 200:
            d, c, s = nr.suggest("선비족3", cur, goal, hold=False)
            if d is None:
                break
            dx, dy = DELTA[d]
            nxt = None
            for st in range(1, 4):
                cand = (cur[0] + dx * st, cur[1] + dy * st)
                if f"{cand[0]},{cand[1]}" in s3["cells"]:
                    nxt = cand
                    break
            if nxt is None:
                break
            cur = nxt
            steps += 1
            if cur == goal:
                ok = True
                break
        check("실데이터 선비족3 시뮬 도달", ok, f"steps={steps}")
        d, conf, src = nr.suggest("2()", (1, 1), (5, 5))
        check("실데이터 노이즈맵 기권", d is None, src)
else:
    print("[SKIP] maps_cloud 없음 — 실데이터 검사 생략")

print()
if FAIL:
    print(f"FAIL {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("ALL PASS")
