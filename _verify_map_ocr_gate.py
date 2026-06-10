# -*- coding: utf-8 -*-
"""맵 끝자리 OCR 오독 게이트 검증 — 격수 좌표 연속이면 맵명 변화 거부."""
import sys
sys.path.insert(0, ".")
from src.fsm.controller import Follower

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        fails.append(name)


class S:
    def __init__(self, x, y, m, valid=True):
        self.x = x; self.y = y; self.map_name = m
        self.coord_valid = valid; self.seq = 1; self.map_seq = 0


def new_f():
    f = Follower()
    f._last_map = "선비족3-3(3)"
    f._atk_prev_coord = (25, 6)
    f._map_ocr_reject = 0
    return f


# ① 격수 좌표 연속(d=1) + 맵명 끝자리 변화 → 오독 거부 (직전 맵 유지)
f = new_f()
s = S(26, 6, "선비족3-3(2)")   # (25,6)→(26,6) d=1, 맵 z3→z2
f._map_ocr_gate(s)
check("좌표연속 끝자리변화 → 거부", s.map_name == "선비족3-3(3)",
      f"got {s.map_name!r}")

# ② 격수 좌표 급변(맵전환 좌표계 변화) + 맵 변화 → 수용 (진짜 전환)
f = new_f()
s = S(2, 28, "선비족3-3(2)")   # (25,6)→(2,28) d=45, 진짜 전환
f._map_ocr_gate(s)
check("좌표급변 맵변화 → 수용", s.map_name == "선비족3-3(2)",
      f"got {s.map_name!r}")

# ③ 같은 맵명 → 무관 (거부 카운터 리셋)
f = new_f()
s = S(26, 6, "선비족3-3(3)")
f._map_ocr_gate(s)
check("같은맵 통과", s.map_name == "선비족3-3(3)")

# ④ 좌표 연속 오독 N프레임 연속 초과 → 실제 전환으로 수용
#    (update 흐름: 게이트 수용 시 641이 _last_map 갱신 → 다음은 같은맵)
f = new_f()
last = None
for i in range(5):
    s = S(26, 6, "선비족3-3(2)")
    f._map_ocr_gate(s)
    if s.map_name != f._last_map:
        f._last_map = s.map_name  # 641 맵변화 수용 시뮬
    last = s.map_name
check("연속 4프레임 초과 → 수용", last == "선비족3-3(2)",
      f"reject_max={f._map_ocr_reject_max} got {last!r}")

# ⑤ coord_valid=False → 게이트 미적용(좌표 신뢰불가라 거부판정 안함)
f = new_f()
s = S(26, 6, "선비족3-3(2)", valid=False)
f._map_ocr_gate(s)
check("coord_valid=False 통과", s.map_name == "선비족3-3(2)")

print()
print("RESULT:", "ALL PASS" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
