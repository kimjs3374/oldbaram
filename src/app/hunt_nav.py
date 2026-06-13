# -*- coding: utf-8 -*-
"""선비족 사냥 순서 네비게이션 로직 (격수 전용, 2026-06-12 / 2026-06-13 개편).

설계 문서: D:\\oldbaram\\선비족 네비게이션 오버레이.md

2026-06-13 개편 (사용자 요청) — 우선순위: 수동 > 학습 확정 > 추천 안내.
- 맵명 `선비족x-y(z)` / `제2선비족x-y(z)` 에서 x(지역 1~5), y(굴 1~5),
  z(굴 내 층 1~7) 파싱. 비매칭 맵(입구 등)은 무시.
- **수동(적용)**: 사용자가 네비 x 를 직접 지정하면 그 굴 순서를 적용.
  순서 텍스트는 GUI 가 RECOMMEND5 로 자동 채우고, 4굴로 줄이면 즉시 반영
  (set_manual_text). x 미지정이면 순서 입력 불가(GUI 비활성).
- **학습(확정)**: 자동 모드에서 각 굴을 '완주'할 때 순서에 기록.
  **완주 판정 = x-y(z) 의 z 가 7 까지 갔다가 로비(허브 선비족x)로 나온 시점**
  (사용자 2026-06-13 정정). 한 바퀴(굴 재완주)에서 회전 규칙으로 순서
  확정, 매 바퀴 재확정. 사이클 최소 길이 3 미만은 확정 보류.
- **추천(안내)**: 학습 확정 전 / 미입력 시 RECOMMEND5[x] 안내 — 선비족x
  진입 즉시. 순서 밖 굴 진입은 직전 강조 유지 + out_of_order 표시.
- 네비 x/순서·학습값은 세션에 저장하지 않음 — GUI 재실행 시 항상 초기화.

UI/오버레이는 src/ui/hunt_nav_overlay.py, 배선은 main_window.
"""
from __future__ import annotations

import re
import threading
from typing import Callable, List, Optional

# 맵명 파싱: 선비족x-y / 제2선비족x-y (+ 뒤에 (z) — z는 굴 내 층/채널).
_MAP_RE = re.compile(r"^(제2)?선비족([1-5])-([1-5])(?:\((\d+)\))?")
# 허브(통로) 맵: 선비족x (x 뒤에 -y 없음. '선비족1방' 류 변형 허용).
_HUB_RE = re.compile(r"^(제2)?선비족([1-5])(?:$|[^-\d])")

# x별 슬롯 배치. label: 1~5=굴, 0=입구.
# 슬롯: TL(좌상) TR(우상) LM(좌중) RM(우중) BL(좌하) BR(우하).
LAYOUTS = {
    1: {"TL": 4, "TR": 3, "LM": 5, "RM": 1, "BL": 0, "BR": 2},
    2: {"TL": 5, "TR": 4, "LM": 0, "RM": 1, "BL": 2, "BR": 3},
    3: {"TL": 5, "TR": 4, "LM": 0, "RM": 1, "BL": 2, "BR": 3},
    4: {"TL": 4, "TR": 3, "LM": 5, "RM": 0, "BL": 1, "BR": 2},
    5: {"TL": 4, "TR": 3, "LM": 5, "RM": 0, "BL": 1, "BR": 2},
}
# 추천 순서 (사용자 제공, 하단 "입력된" 네굴/다섯굴 순서).
RECOMMEND4 = {1: [5, 4, 3, 2], 2: [5, 4, 3, 2], 3: [5, 4, 3, 2],
              4: [3, 4, 1, 2], 5: [3, 4, 1, 2]}
RECOMMEND5 = {1: [5, 4, 3, 1, 2], 2: [5, 4, 1, 3, 2], 3: [5, 4, 1, 3, 2],
              4: [3, 4, 5, 1, 2], 5: [3, 4, 5, 1, 2]}

_MIN_CYCLE = 3  # 세굴 미만 사이클은 확정 보류.


def parse_map(map_name: str):
    """맵명 → (base, x, y, z). 굴=(base,x,y,z|0), 허브=(base,x,0,0).
    비매칭(입구/타 사냥터)은 None.

    허브 = `선비족x` (x 뒤 -y 없음) — 굴(7)에서 나오면 도착하는 통로.
    """
    s = str(map_name or "").strip()
    m = _MAP_RE.match(s)
    if m is not None:
        base = ("제2선비족" if m.group(1) else "선비족")
        z = int(m.group(4)) if m.group(4) else 0
        return base, int(m.group(2)), int(m.group(3)), z
    h = _HUB_RE.match(s)
    if h is not None:
        base = ("제2선비족" if h.group(1) else "선비족")
        return base, int(h.group(2)), 0, 0
    return None


def parse_order_text(text) -> Optional[List[int]]:
    """'5,4,3,2' → [5,4,3,2]. 유효 = 1~5 중복 없는 3~5개. 아니면 None."""
    out: List[int] = []
    for tok in str(text or "").replace(" ", "").split(","):
        if not tok:
            continue
        if not tok.isdigit():
            return None
        v = int(tok)
        if not (1 <= v <= 5) or v in out:
            return None
        out.append(v)
    if not (3 <= len(out) <= 5):
        return None
    return out


class CaveOrderTracker:
    """굴 순서 네비게이션 (순수 로직, 스레드 안전). 2026-06-13 개편.

    우선순위: manual(수동 적용) > confirmed(학습 확정) > recommend(추천 안내).
      manual    — 사용자 네비 x 지정 + 굴 순서 적용 (set_manual_text).
      confirmed — 자동 모드 학습 확정 순서. **완주 = z7→로비** 시점에 굴 기록,
                  한 바퀴(굴 재완주) 회전 규칙으로 확정.
      recommend — 미입력/확정 전 RECOMMEND5[eff_x] 안내.
      idle      — 선비족 영역 밖 / x 미관측.

    x별 캐시·텍스트 자동입력·세션 저장 없음 (GUI 재실행 초기화).
    """

    def __init__(self, log_cb: Optional[Callable[[str], None]] = None):
        self._log = log_cb or (lambda s: None)
        self._lock = threading.RLock()
        self._base = ""
        self._x_ocr = 0          # OCR 관측 x (0=미관측)
        self._x_override = 0     # GUI 수동 x (0=자동)
        self._manual_order: List[int] = []   # 사용자 적용 순서 (유효 시)
        self._visited: List[int] = []        # 이번 바퀴 완주(z7→로비) 굴 시퀀스
        self._order: List[int] = []          # 학습 확정 순서
        self._cur_y = 0
        self._next_y = 0
        self._out_of_order = False
        self._notice = ""
        self._notice_seq = 0
        # 허브/완주 추적: 굴 z7 도달 후 허브(선비족x) 도착 = 완주 확정 + 강조.
        self._at_hub = False
        self._from_z7 = False
        self._last_z = 0                # 현재(직전) 굴에서 마지막 관측 (z).

    # ── 외부 API ──────────────────────────────────────────────────────────

    def eff_x(self) -> int:
        return self._x_override or self._x_ocr

    def _active_order(self) -> List[int]:
        """적용 순서: 수동 > 학습 확정 > 추천(RECOMMEND5[eff_x])."""
        if self._manual_order:
            return list(self._manual_order)
        if self._order:
            return list(self._order)
        x = self.eff_x()
        if x in RECOMMEND5:
            return list(RECOMMEND5[x])
        return []

    def _state(self) -> str:
        if self._manual_order:
            return "manual"
        if self._order:
            return "confirmed"
        if self.eff_x() in RECOMMEND5:
            return "recommend"
        return "idle"

    def set_x_override(self, x: int) -> None:
        with self._lock:
            old = self.eff_x()
            self._x_override = int(x) if 1 <= int(x) <= 5 else 0
            new = self.eff_x()
            if new != old:
                self._switch_x(new)

    def set_manual_text(self, text, user_edit: bool = True) -> None:
        """굴 순서 텍스트 반영 (항목 1·2·6).

        user_edit=True: 유효(3~5굴) → 수동 적용 즉시 반영. 빈 값 → 수동 해제
        (학습 확정 순서 / 추천 안내로 복귀). 무효 비공백 → 무시(이전 유지).
        user_edit=False: no-op (프로그램 echo 재진입 차단).
        """
        if not user_edit:
            return
        with self._lock:
            parsed = parse_order_text(text)
            if parsed:
                self._manual_order = parsed
                self._recalc_next()
                self._set_notice(
                    f"굴 순서 적용: {'→'.join(map(str, parsed))}")
            elif not str(text or "").strip():
                self._manual_order = []
                self._recalc_next()
                x = self.eff_x()
                if self._order:
                    self._set_notice("수동 해제 — 학습 순서로 복귀")
                elif x in RECOMMEND5:
                    self._set_notice(
                        "추천 순서 안내: "
                        f"{'→'.join(map(str, RECOMMEND5[x]))}")
                else:
                    self._set_notice("굴 순서 비움")
            # 무효 비공백은 무시.

    def observe(self, map_name: str) -> None:
        """맵 변경 시 호출 (격수 OCR 확정 맵명). 비매칭 맵은 무시.

        자동 모드 선비족x 최초 진입 시 즉시 추천 안내(항목5). 굴 내 z 진행을
        _last_z 로 추적 → z7 도달 후 허브(선비족x) 도착 시 그 굴 완주 학습.
        """
        p = parse_map(map_name)
        if p is None:
            return
        base, x, y, z = p
        with self._lock:
            self._base = base
            old_eff = self.eff_x()
            self._x_ocr = x
            new_eff = self.eff_x()
            if new_eff != old_eff:
                if old_eff != 0:
                    self._switch_x(new_eff)
                else:
                    # 최초 x 관측 — 자동 모드면 추천 즉시 안내 (항목5).
                    self._announce_recommend(new_eff)
            if y == 0:
                # 허브(로비) 도착 — 직전 굴 z7 완주면 학습 확정 + 강조 안내.
                self._at_hub = True
                self._from_z7 = (self._last_z == 7)
                if self._from_z7:
                    if not self._manual_order and self._cur_y:
                        self._learn_complete(self._cur_y)
                    if self._next_y:
                        self._set_notice(f"다음 굴: {self._next_y}굴로 이동")
                return
            self._at_hub = False
            self._from_z7 = False
            if y == self._cur_y:
                self._last_z = z or self._last_z  # 같은 굴 내 (z) 진행 갱신.
                return  # dedup
            self._cur_y = y
            self._last_z = z
            self._recalc_next()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "base": self._base or "선비족",
                "x": self.eff_x(),
                "cur_y": self._cur_y,
                "next_y": self._next_y,
                "order": self._active_order(),
                "state": self._state(),
                "out_of_order": self._out_of_order,
                "visited": list(self._visited),
                "auto_text": "",          # 호환 (자동입력 제거 — 항목4)
                "notice": self._notice,
                "notice_seq": self._notice_seq,
                "at_hub": self._at_hub,
                "from_z7": self._from_z7,
                "last_z": self._last_z,
            }

    # ── 내부 ─────────────────────────────────────────────────────────────

    def _announce_recommend(self, x: int) -> None:
        """자동 모드(수동 미입력) 첫 x 관측 → 다섯굴 추천 즉시 안내 (항목5)."""
        if not self._manual_order and not self._order and x in RECOMMEND5:
            self._set_notice(
                f"추천 순서 안내: {'→'.join(map(str, RECOMMEND5[x]))}")

    def _learn_complete(self, y: int) -> None:
        """굴 y 완주(z7→로비) 학습. 굴 재완주(바퀴 경계) 시 회전 확정."""
        if y in self._visited:
            idx = self._visited.index(y)
            cyc = self._visited[idx:] + self._visited[:idx]
            if len(cyc) >= _MIN_CYCLE:
                self._order = cyc
                self._set_notice(
                    f"굴 순서 확정: {'→'.join(map(str, cyc))}")
            self._visited = [y]
        else:
            self._visited.append(y)
            self._set_notice(
                f"굴 완주 학습: {y}굴 "
                f"(누적 {'→'.join(map(str, self._visited))})")
        self._recalc_next()

    def _switch_x(self, new_x: int) -> None:
        """지역 전환 — 바퀴/학습 리셋. 수동 순서는 override+main_window 가 관리."""
        self._cur_y = 0
        self._next_y = 0
        self._out_of_order = False
        self._at_hub = False
        self._from_z7 = False
        self._last_z = 0
        self._visited = []
        self._order = []
        self._recalc_next()
        if not self._manual_order and new_x in RECOMMEND5:
            self._set_notice(
                f"지역 {self._base or '선비족'}{new_x} — 추천 순서 안내: "
                f"{'→'.join(map(str, RECOMMEND5[new_x]))}")
        else:
            self._set_notice(f"지역 전환: {self._base or '선비족'}{new_x}")

    def _recalc_next(self) -> None:
        order = self._active_order()
        if order and self._cur_y in order:
            i = order.index(self._cur_y)
            self._next_y = order[(i + 1) % len(order)]
            self._out_of_order = False
        elif order and self._cur_y:
            # 순서 밖 굴 — 직전 강조 유지 + 표시만.
            self._out_of_order = True
        elif order:
            # 아직 굴 미진입 — 첫 굴을 다음으로 안내.
            self._next_y = order[0]
            self._out_of_order = False
        else:
            self._next_y = 0
            self._out_of_order = False

    def _set_notice(self, msg: str) -> None:
        self._notice = str(msg)
        self._notice_seq += 1
        try:
            self._log(f"[HUNT-NAV] {msg}")
        except Exception:
            pass
