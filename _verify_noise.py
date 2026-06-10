# -*- coding: utf-8 -*-
"""coord_jump_max=4 노이즈 클램프 실증 검증."""
import sys
sys.path.insert(0, ".")
from src.vision.ocr import Ocr as O

o = O.__new__(O)
o.coord_jump_max = 4
o.coord_reject_max = 3
o._reject_count = 0
o._last_coord = (19, 9)
o._last_map = "m"
o._pending_map = None
o._is_admissible_map = lambda x: False

fails = []
# 실증: raw 오독 (1,4) 1프레임 → 클램프 (19,9) 노이즈 제거
r1 = o._filter_coord_jump((1, 4), "m")
print("프레임1 (1,4) →", r1, " 기대 (19,9)")
if r1 != (19, 9):
    fails.append("프레임1")
o._last_coord = r1
r2 = o._filter_coord_jump((1, 4), "m"); o._last_coord = r2
r3 = o._filter_coord_jump((1, 4), "m"); o._last_coord = r3
print("프레임2/3 →", r2, r3, " 기대 (19,9) 유지")
if r2 != (19, 9) or r3 != (19, 9):
    fails.append("프레임2/3")
r4 = o._filter_coord_jump((1, 4), "m")
print("프레임4 →", r4, " 기대 (1,4) 강제수락(연속4=실제)")
if r4 != (1, 4):
    fails.append("프레임4")
# 정상 d=3 통과
o._last_coord = (19, 9); o._reject_count = 0
rn = o._filter_coord_jump((20, 6), "m")
print("정상 d=3 (20,6) →", rn, " 기대 통과")
if rn != (20, 6):
    fails.append("정상통과")
# 한 축만 노이즈: (19,9)→(19,4) jy=5 클램프 y
o._last_coord = (19, 9); o._reject_count = 0
ry = o._filter_coord_jump((19, 4), "m")
print("한축 (19,4) jy=5 →", ry, " 기대 (19,9)")
if ry != (19, 9):
    fails.append("한축클램프")

print()
print("RESULT:", "ALL PASS" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
