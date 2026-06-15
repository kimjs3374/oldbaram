# -*- coding: utf-8 -*-
r"""STUCK-ALIGN 통로 정렬 검증 (좌우 진동 근본해결).

120 4층 실사고 재현: 격수 x=7 통로로 위(U)로 갔는데 힐러 (9,16)에서
U 막힘 → 기존엔 ORTHO2(R)로 멀어지며 좌우 진동. 수정 후 격수 x로 L 정렬.

사용법: cd dist_dosa && py ..\_verify_stuck_align.py
"""
import sys
import time
from types import SimpleNamespace
sys.path.insert(0, ".")
from src.workers.healer_worker import HealerWorker

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        fails.append(name)


def mk_self(hx, hy, want, dur):
    """STUCK 누적 상태(dur초 진행 0)인 mock self."""
    return SimpleNamespace(
        healer_coord=(hx, hy), healer_map="선비족3-2(4)",
        _run_want=want, _run_start_ts=time.time() - dur,
        _run_start_pos=(hx, hy), _whitetab_confirm=0,
        _stuck_last_log=0.0,
        log=SimpleNamespace(warning=lambda *a, **k: None),
        _blacklist_remove_at=lambda *a, **k: None,
        _blacklist_add=lambda *a, **k: None,
    )


def call(mock, want, atk, fol):
    return HealerWorker._apply_stuck_filter(mock, want, "r", atk, fol, False)


fol = SimpleNamespace(_map_trail={"선비족3-2(4)": {(9, 17)}}, _grid=None)

# ① 핵심: 세로(U) 막힘 + 격수 x=7 < 힐러 x=9 → L 정렬 (멀어지는 R 금지)
atk = SimpleNamespace(x=7, y=8, coord_valid=True)
w, r = call(mk_self(9, 16, "U", 1.0), "U", atk, fol)
check("U막힘+격수x7<9 → L 정렬", w == "L", f"got {w} ({r})")

# ② 격수 x=11 > 힐러 x=9 → R 정렬
atk = SimpleNamespace(x=11, y=8, coord_valid=True)
w, r = call(mk_self(9, 16, "U", 1.0), "U", atk, fol)
check("U막힘+격수x11>9 → R 정렬", w == "R", f"got {w}")

# ③ x 도달(격수 x==힐러 x) → ALIGN 안 함 → 기존 로직(STUCK-WAIT/ORTHO)
#    (9,17)이 trail이라 STUCK-WAIT 대기('-') 또는 ORTHO
atk = SimpleNamespace(x=9, y=8, coord_valid=True)
w, r = call(mk_self(9, 16, "U", 1.0), "U", atk, fol)
check("x정렬됨 → ALIGN 스킵(L/R 아님)", w != "L" and w != "R", f"got {w} ({r})")

# ④ 가로(L) 막힘 + 격수 y 다름 → 수직 정렬
# 좌표축 D=y증가, U=y감소. 격수 y=20 > 힐러 y=16 = 격수가 아래 → D.
atk = SimpleNamespace(x=3, y=20, coord_valid=True)
w, r = call(mk_self(9, 16, "L", 1.0), "L", atk, fol)
check("L막힘+격수y20>16(아래) → D 정렬", w == "D", f"got {w}")

# ④b 격수 y 작음(위) → U 정렬
atk = SimpleNamespace(x=3, y=4, coord_valid=True)
w, r = call(mk_self(9, 16, "L", 1.0), "L", atk, fol)
check("L막힘+격수y4<16(위) → U 정렬", w == "U", f"got {w}")

# ⑤ 정렬 2.5s 초과 → ALIGN 안 함(폴백). 격수 x 달라도 ORTHO/WAIT로.
atk = SimpleNamespace(x=7, y=8, coord_valid=True)
w, r = call(mk_self(9, 16, "U", 3.0), "U", atk, fol)
check("dur 3.0s → ALIGN 폴백(ORTHO2 등)", "ALIGN" not in r, f"got {w} ({r})")

# ⑥ 격수 좌표 무효 → ALIGN 안 함
atk = SimpleNamespace(x=7, y=8, coord_valid=False)
w, r = call(mk_self(9, 16, "U", 1.0), "U", atk, fol)
check("격수 무효 → ALIGN 스킵", "ALIGN" not in r, f"got {w} ({r})")

print()
print("RESULT:", "ALL PASS" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
