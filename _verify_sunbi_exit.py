# -*- coding: utf-8 -*-
r"""선비족 z(층) 전환 고정 출구 방향 검증 (사용자 2026-06-15 규칙).

사용법: cd dist_dosa && py ..\_verify_sunbi_exit.py
"""
import sys
sys.path.insert(0, ".")
from src.fsm.controller import Follower

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        fails.append(name)


sd = Follower._sunbi_exit_dir

# 사용자 규칙: 굴/지역 무관 z 전환 방향 고정
# 1→2 R, 2→3 U, 3→4 U, 4→5 U, 5→6 L, 6→7 L, 7→로비 U
cases = [
    ("선비족3-4(1)", "선비족3-4(2)", "R"),
    ("선비족3-4(2)", "선비족3-4(3)", "U"),
    ("선비족3-4(3)", "선비족3-4(4)", "U"),
    ("선비족3-4(4)", "선비족3-4(5)", "U"),
    ("선비족3-4(5)", "선비족3-4(6)", "L"),
    ("선비족3-4(6)", "선비족3-4(7)", "L"),
    ("선비족3-4(7)", "선비족3", "U"),       # 7→허브(로비)
    ("선비족3-4(7)", "선비족입구", "U"),    # 7→입구
]
for prev, new, exp in cases:
    got = sd(prev, new)
    check(f"{prev}→{new} = {exp}", got == exp, f"got {got}")

# 굴(y) 무관 — 다른 굴도 동일
check("굴2 1→2 = R", sd("선비족1-2(1)", "선비족1-2(2)") == "R")
check("굴5 5→6 = L", sd("제2선비족2-5(5)", "제2선비족2-5(6)") == "L")

# 역방향(데리러 복귀)·동일·비선비족 → None (기존 로직 유지)
check("역방향 6→5 = None", sd("선비족3-4(6)", "선비족3-4(5)") is None)
check("동일 z = None", sd("선비족3-4(3)", "선비족3-4(3)") is None)
check("점프 1→3 = None", sd("선비족3-4(1)", "선비족3-4(3)") is None)
check("비선비족 = None", sd("전미국54(2)예공", "전미국54(3)예공") is None)
check("입구→굴 = None", sd("선비족입구", "선비족3-4(1)") is None)
check("None 입력 = None", sd(None, "선비족3-4(2)") is None)

print()
print("RESULT:", "ALL PASS" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
