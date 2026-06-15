# -*- coding: utf-8 -*-
r"""_boundary_exit 재작성 검증 (마지막 단발 전환 노이즈 강건).

사용법: cd dist_dosa && py ..\_verify_boundary.py
"""
import sys
sys.path.insert(0, ".")
from src.fsm.controller import Follower

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        fails.append(name)


be = Follower._boundary_exit

# ① 실사고 재현: 2-3(1)→(2). U로 길게 → R로 꺾음 → (19,4) 노이즈 1점.
trail = [(16,16),(16,15),(16,14),(16,13),(16,12),(16,11),(16,10),
         (16,9),(17,9),(18,9),(19,9),(19,4)]
coord, d, idx = be(trail)
check("2-3(1)→(2) dir=R (노이즈 (19,4) 무시)", d == "R", f"got {d} coord={coord}")
check("출구 x=19(오른쪽 끝)", coord[0] == 19, f"got {coord}")

# ② 노이즈 없이 R로 깔끔하게 나감
trail = [(10,9),(11,9),(12,9),(13,9),(14,9)]
c,d,i = be(trail)
check("깔끔한 R", d == "R", f"got {d}")

# ③ U로 나감 (마지막도 U)
trail = [(5,10),(5,8),(5,6),(5,4),(5,2)]
c,d,i = be(trail)
check("U로 나감", d == "U", f"got {d}")

# ④ 마지막 전환이 2점 이상 R (x폭 3+ 라 use_x 활성)
trail = [(5,10),(5,8),(5,6),(6,6),(7,6),(8,6)]  # U 길게 → R 3연속(x5~8)
c,d,i = be(trail)
check("R 연속은 진짜 R", d == "R", f"got {d}")

# ⑤ (7) 케이스류: R로 가다 끝 노이즈
trail = [(2,6),(3,6),(4,6),(5,6),(6,6),(7,6),(7,1)]  # R로 감 → (7,1) U노이즈1점
c,d,i = be(trail)
check("R 진행 + 끝 U노이즈1점 → R", d == "R", f"got {d} coord={c}")

# ⑥ 짧은 trail
check("1점 trail None", be([(5,5)]) == (None,None,None))
check("빈 trail None", be([]) == (None,None,None))

# ⑦ D로 나감
trail = [(5,2),(5,4),(5,6),(5,8),(5,10)]
c,d,i = be(trail)
check("D로 나감", d == "D", f"got {d}")

# ⑧ L로 나감 (U 길게 → L 꺾음 → 노이즈)
trail = [(10,10),(10,8),(10,6),(9,6),(8,6),(7,6),(7,9)]  # U→L→(7,9)노이즈
c,d,i = be(trail)
check("U→L꺾음+노이즈 → L", d == "L", f"got {d} coord={c}")

# ⑨ 실사고 2026-06-15 (1)→(2): 격수 (16,10)→...→(19,10) 순수 R 진출인데
#    (19,10)이 R·U 공유코너라 'U/D 우선'이 U로 덮던 회귀. 순수가로=R 존중.
trail = [(16,16),(16,14),(16,12),(16,11),(16,10),(17,10),(18,10),(19,10)]
c,d,i = be(trail)
check("(1)→(2) 순수R진출 코너 → R (U오판 금지)", d == "R", f"got {d} coord={c}")

print()
print("RESULT:", "ALL PASS" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
