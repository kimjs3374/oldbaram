# -*- coding: utf-8 -*-
r"""좌표 점프 축별 클램프 필터 선검증.

healer-37 2026-06-10 15:20 사고 재현: 같은맵(선비족2)에서 x가 1↔18 진동,
y는 11→10→9→8 정상 단조. 기존 jump_max=60이라 d=17 진동이 통과해 16s 정체.
사용법: cd dist_dosa && py ..\_verify_coord_jump.py
"""
import sys

sys.path.insert(0, ".")
import src.vision.ocr as ocrmod

cls = None
for name in dir(ocrmod):
    obj = getattr(ocrmod, name)
    if isinstance(obj, type) and hasattr(obj, "_filter_coord_jump"):
        cls = obj
        break
assert cls is not None

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        fails.append(name)


def new_ocr(last_coord, last_map="M", jmax=10, rmax=3, known=("M",)):
    o = cls.__new__(cls)
    o._last_coord = last_coord
    o._last_map = last_map
    o._pending_map = ""
    o._known_maps = set(known)
    o._reject_count = 0
    o.coord_jump_max = jmax
    o.coord_reject_max = rmax
    return o


# ① 핵심: x축만 진동 → x 클램프, y 수락 (15:20 사고)
o = new_ocr((1, 11))
r = o._filter_coord_jump((18, 10), "M")   # x 1→18 노이즈, y 11→10 정상
check("x진동 클램프 y수락", r == (1, 10), f"got {r}")
o._last_coord = r
r = o._filter_coord_jump((1, 9), "M")      # x 정상복귀, y 계속감소
check("정상복귀 통과", r == (1, 9), f"got {r}")
o._last_coord = r
r = o._filter_coord_jump((18, 8), "M")     # 또 x 튐
check("x재진동도 클램프", r == (1, 8), f"got {r}")

# ② 15:20 전체 시퀀스 — x는 항상 1 근처로 수렴, y 단조
o = new_ocr((3, 11))
seq = [(3, 11), (1, 11), (18, 10), (1, 10), (1, 9), (18, 8), (17, 8)]
out = []
for c in seq:
    r = o._filter_coord_jump(c, "M")
    if r is not None:
        o._last_coord = r
    out.append(r)
xs = [c[0] for c in out]
check("시퀀스 x 진동제거(전부 ≤3)", all(x <= 3 for x in xs), f"xs={xs}")
check("시퀀스 y 단조추종 보존",
      [c[1] for c in out] == [11, 11, 10, 10, 9, 8, 8],
      f"ys={[c[1] for c in out]}")

# ③ 정상 빠른 이동(축당 ≤7) — 통과
o = new_ocr((5, 5))
check("정상 이동 d=7 통과", o._filter_coord_jump((12, 5), "M") == (12, 5))
o = new_ocr((5, 5))
check("정상 대각 (3,4) 통과", o._filter_coord_jump((8, 9), "M") == (8, 9))

# ④ 맵 전환(다른 맵명) — 필터 스킵, 새좌표 그대로 + last 리셋
o = new_ocr((20, 1), last_map="M")
r = o._filter_coord_jump((2, 25), "N")     # cmp_m='N' ≠ last 'M'
check("맵전환 새좌표 통과", r == (2, 25), f"got {r}")
check("맵전환 last_coord 리셋", o._last_coord is None)

# ⑤ 맵 OCR 지연: 같은맵명 + pending 새known맵 → 양축점프여도 통과
o = new_ocr((22, 1), last_map="M", known=("M", "N"))
o._pending_map = "N"
r = o._filter_coord_jump((9, 28), "M")     # 양축 큰 점프지만 맵전환 신호
check("맵지연 양축점프 보존", r == (9, 28), f"got {r}")

# ⑥ 양축 동시 노이즈(pending 없음) → 직전값 클램프(추종 끊김 방지),
#    reject_max 후 강제수락. 2026-06-11: None reject → 클램프로 변경.
o = new_ocr((5, 5))
r1 = o._filter_coord_jump((25, 25), "M")
r2 = o._filter_coord_jump((25, 25), "M")
r3 = o._filter_coord_jump((25, 25), "M")
r4 = o._filter_coord_jump((25, 25), "M")   # 4번째 = reject_max(3) 초과
check("양축노이즈 초기 클램프(직전5,5)",
      r1 == (5, 5) and r2 == (5, 5) and r3 == (5, 5),
      f"got {r1},{r2},{r3}")
check("양축노이즈 연속시 강제수락", r4 == (25, 25), f"got {r4}")

# ⑦ coord/last None 가드
o = new_ocr((5, 5))
check("coord None 통과", o._filter_coord_jump(None, "M") is None)
o2 = cls.__new__(cls)
o2._last_coord = None
o2._last_map = "M"; o2._pending_map = ""; o2._known_maps = {"M"}
o2._reject_count = 0; o2.coord_jump_max = 10; o2.coord_reject_max = 3
check("last None 통과", o2._filter_coord_jump((9, 9), "M") == (9, 9))

print()
print("RESULT:", "ALL PASS" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
