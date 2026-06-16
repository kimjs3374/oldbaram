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
atk = SimpleNamespace(x=9, y=8, coord_valid=True)
w, r = call(mk_self(9, 16, "U", 1.0), "U", atk, fol)
check("x정렬됨 → ALIGN 스킵", "ALIGN" not in r, f"got {w} ({r})")

# ③b STUCK-WAIT 정확칸: U막힘(위로)+격수같은x, 위칸(hy-1=15)이 trail이면 대기.
#    _wd 좌표축 수정(U=y-) 검증: 위로 가려면 다음칸 (9,15) 봐야(기존엔 9,17 오판).
fol_up = SimpleNamespace(_map_trail={"선비족3-2(4)": {(9, 15)}}, _grid=None)
atk = SimpleNamespace(x=9, y=8, coord_valid=True)
w, r = call(mk_self(9, 16, "U", 1.0), "U", atk, fol_up)
check("U막힘+위칸(9,15) trail → STUCK-WAIT 대기", w == "-" and "WAIT" in r,
      f"got {w} ({r})")

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

# ⑦ map_neq=True (맵 전환 중, 격수 새맵 좌표) → ALIGN 스킵, trail 추종 살림
atk = SimpleNamespace(x=7, y=8, coord_valid=True)
w, r = HealerWorker._apply_stuck_filter(
    mk_self(9, 16, "U", 1.0), "U", "r", atk, fol, True)
check("map_neq=True(맵전환) → ALIGN 스킵", "ALIGN" not in r, f"got {w} ({r})")

# ⑧ STUCK-HOLD: 격수 근접(추종축 거리≤2)+x정렬+세로(U)막힘 → 진동 대신 대기.
#    근접 미세정렬 진동(1737회 주범)만 막고, 추종은 안 끊음.
fol_nw = SimpleNamespace(_map_trail={}, _grid=None)
atk = SimpleNamespace(x=9, y=15, coord_valid=True)  # x정렬, y거리1(근접)
w, r = call(mk_self(9, 16, "U", 1.5), "U", atk, fol_nw)
check("격수근접 x정렬+U막힘 → STUCK-HOLD 대기", w == "-" and "HOLD" in r,
      f"got {w} ({r})")
# ⑧b 격수 x 다름 → HOLD 아님(ALIGN으로 정렬)
atk = SimpleNamespace(x=6, y=8, coord_valid=True)
w, r = call(mk_self(9, 16, "U", 1.5), "U", atk, fol_nw)
check("격수x다름+U막힘 → ALIGN(L 정렬, HOLD 아님)", w == "L" and "ALIGN" in r,
      f"got {w} ({r})")
# ⑧c 격수 원거리(추종축 거리>2)+x정렬+막힘 → HOLD 금지, 우회(추종 살림).
#    2026-06-16: v99 HOLD가 격수 4칸 거리에서도 대기시켜 추종실패하던 회귀 차단.
atk = SimpleNamespace(x=9, y=8, coord_valid=True)  # x정렬, y거리8(원거리)
w, r = call(mk_self(9, 16, "U", 1.5), "U", atk, fol_nw)
check("격수원거리 x정렬+U막힘 → HOLD 금지(우회)", "HOLD" not in r,
      f"got {w} ({r})")

# ⑩ 맵전환 출구 우회(163713 버그): map_neq=True, 출구(6,0) 위, 격수는 이미
#    다음맵 좌표(25,7). L막힘 → 격수기준이면 D(아래=출구반대로 처박힘),
#    출구기준이면 U. → U 여야(사용자 "(7)에서 위로 나가야는데 아래로").
fol_ex = SimpleNamespace(_map_trail={}, _grid=None, exit_coord=lambda: (6, 0))
atk = SimpleNamespace(x=25, y=7, coord_valid=True)
w, r = HealerWorker._apply_stuck_filter(
    mk_self(7, 2, "L", 1.0), "L", "r", atk, fol_ex, True)
check("map_neq 출구위(6,0)+L막힘 → 출구기준 U우회(D아님)", w == "U",
      f"got {w} ({r})")
# ⑩b 같은맵(map_neq=False)은 기존대로 격수 기준 (출구 무시)
atk = SimpleNamespace(x=7, y=20, coord_valid=True)  # 격수 아래(y20>hy2)
w, r = call(mk_self(7, 2, "L", 1.0), "L", atk, fol_ex)  # map_neq=False
check("같은맵 L막힘 → 격수기준 D우회(출구 무시)", w == "D", f"got {w} ({r})")

print()
print("RESULT:", "ALL PASS" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
