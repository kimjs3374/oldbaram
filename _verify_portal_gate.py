# -*- coding: utf-8 -*-
"""맵전환 전 빨탭(red & !white) 확인 게이트 검증."""
import sys
import logging
sys.path.insert(0, ".")
from src.workers.healer_worker import HealerWorker

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        fails.append(name)


class FakeFol:
    def __init__(self):
        self._tab_confirm_active = False
        self._exit_coord = (19, 9)
    def force_exit_active(self): return True
    def cancel_force_exit(self): pass
    def exit_dir(self): return "R"
    def force_exit_remaining(self): return 5.0
    def direction(self): return "-"


def new_w(red, white, follow_only=False):
    w = HealerWorker.__new__(HealerWorker)
    w.coord_tol = 1
    w.healer_coord = (10, 9)
    w.follow_only = follow_only
    w._cur_red_raw = red
    w._cur_white_raw = white
    w._portal_enter_logged = False
    w.log = logging.getLogger("t")
    return w


atk = type("A", (), {"coord_valid": True, "x": 19, "y": 9,
                     "map_change_pending": False})()

# ① red=False (타겟 없음) → 진입 보류 (맵 안 넘어감)
w = new_w(False, False)
want, reason = w._decide_move_raw(atk, FakeFol(), True)
check("빨탭없음 → 진입보류", want == "-" and "PORTAL-WAIT" in reason, reason)

# ② white=True (흰탭) → 진입 보류
w = new_w(False, True)
want, reason = w._decide_move_raw(atk, FakeFol(), True)
check("흰탭 → 진입보류", want == "-" and "PORTAL-WAIT" in reason, reason)

# ③ red=True & white=True (공존, 미확정) → 보류
w = new_w(True, True)
want, reason = w._decide_move_raw(atk, FakeFol(), True)
check("red+white 공존 → 진입보류", want == "-" and "PORTAL-WAIT" in reason, reason)

# ④ red=True & white=False (빨탭 확정) → 진입 (방향 반환)
w = new_w(True, False)
want, reason = w._decide_move_raw(atk, FakeFol(), True)
check("빨탭확정 → 진입(방향)", want in ("L", "R", "U", "D"), f"{want} {reason}")
check("진입 로그 게이트 set", w._portal_enter_logged is True)

# ⑤ follow_only → 게이트 면제(red 무시 진입)
w = new_w(False, False, follow_only=True)
want, reason = w._decide_move_raw(atk, FakeFol(), True)
check("follow_only 게이트면제 진입", want in ("L", "R", "U", "D"), f"{want} {reason}")

print()
print("RESULT:", "ALL PASS" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
