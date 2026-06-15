# -*- coding: utf-8 -*-
r"""지폭 쓴 그 굴 파력 스킵 검증 (cast_z 추적).

사용자: 지폭 쓴 그 굴(z)에서만 파력 스킵, 다음 굴은 시전.
cast_z 로직만 단위 검증 (mock self).

사용법: cd dist_dosa && py ..\_verify_jipok_castgul.py
"""
import sys
from types import SimpleNamespace
sys.path.insert(0, ".")
from src.app.attacker import Attacker

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        fails.append(name)


def mk(map_name, jipok_seq, cast_seq, cast_z):
    return SimpleNamespace(
        _last=SimpleNamespace(map_name=map_name),
        _jipok_seq=jipok_seq, _jipok_cast_seq=cast_seq,
        _jipok_cast_z=cast_z, _jjeol_jipok_ready=False)


f = Attacker.set_jjeol_jipok_ready

# ① 5층에서 지폭 시전(seq 0→1), cd_ready=False(쿨 254) → 같은 굴이라 스킵 True
s = mk("선비족3-5(5)", 1, 0, None)
f(s, False)
check("5층 지폭시전 직후 cd=False여도 스킵 True", s._jjeol_jipok_ready is True,
      f"cast_z={s._jipok_cast_z} ready={s._jjeol_jipok_ready}")
check("  cast_z=5 기록", s._jipok_cast_z == 5)

# ② 같은 5층 계속, cd_ready=False → 여전히 스킵(cast_z=5 유지)
s = mk("선비족3-5(5)", 1, 1, 5)
f(s, False)
check("5층 계속 머무름 → 스킵 유지", s._jjeol_jipok_ready is True)

# ③ 6층 이동(다음 굴), cd_ready=False → cast_z 해제 → 파력 시전(스킵 False)
s = mk("선비족3-5(6)", 1, 1, 5)
f(s, False)
check("6층 이동 → cast_z 해제, 파력 허용(False)", s._jjeol_jipok_ready is False,
      f"cast_z={s._jipok_cast_z}")
check("  cast_z=None 해제", s._jipok_cast_z is None)

# ④ 6층에서 cd_ready=True(지폭 곧 또 쏨) → 스킵 True (cd 기반 유지)
s = mk("선비족3-5(6)", 1, 1, None)
f(s, True)
check("6층 cd_ready=True(지폭 준비) → 스킵", s._jjeol_jipok_ready is True)

# ⑤ 지폭 안 쓴 5층(cast_z=None) + cd_ready=False → 파력 시전
s = mk("선비족3-5(5)", 0, 0, None)
f(s, False)
check("지폭 안쓴 5층 cd=False → 파력 허용", s._jjeol_jipok_ready is False)

# ⑥ 다음 바퀴 5층 재방문: 지폭 또 시전(seq 1→2) → cast_z=5 재기록 스킵
s = mk("선비족3-5(5)", 2, 1, None)
f(s, False)
check("다음바퀴 5층 지폭 재시전 → 스킵", s._jjeol_jipok_ready is True)
check("  cast_z=5 재기록", s._jipok_cast_z == 5)

print()
print("RESULT:", "ALL PASS" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
