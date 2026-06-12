# -*- coding: utf-8 -*-
"""선비족 사냥 순서 네비게이션 오버레이 (격수 전용, 2026-06-12).

설계: D:\\oldbaram\\선비족 네비게이션 오버레이.md §5.
- x별 굴 배치 지도(슬롯 6개 고정 격자 — 오와열 정렬)를 그리고
  다음 굴 강조(노랑) + 현재 굴(녹색 테두리) 표시.
- 데이터: MainWindow._tick_analytics 가 worker.get_hunt_nav_snapshot()
  폴링 → update_nav(snap). 키 입력 없음(표시 전용).
- 위치 키 "huntnav" — 드래그/저장/투명도/편집모드는 _ScaledOverlay 공통.
"""
from __future__ import annotations

from typing import Optional

from PyQt5 import QtCore, QtGui

from .overlay import _ScaledOverlay, ACCENT_NAV
from ..app.hunt_nav import LAYOUTS

_STATE_KR = {
    "idle": "대기",
    "recommend": "추천",
    "learning": "학습중",
    "confirmed": "확정",
    "manual": "수동",
}

# 슬롯 → 격자 좌표 (col 비율, row 인덱스 0~2).
_SLOT_GRID = {
    "TL": (0.33, 0), "TR": (0.67, 0),
    "LM": (0.10, 1), "RM": (0.90, 1),
    "BL": (0.33, 2), "BR": (0.67, 2),
}


class HuntNavOverlay(_ScaledOverlay):
    """선비족 굴 네비게이션 지도 오버레이."""

    def __init__(self):
        super().__init__()
        self._snap: dict = {}
        # 컴팩트 기본(2026-06-12): 화면 중앙 가림 최소화. 더 키우려면 크기조절.
        self._base_w = 182
        self._relayout()

    def update_nav(self, snap: Optional[dict]) -> None:
        s = dict(snap or {})
        if s == self._snap:
            return
        self._snap = s
        self._relayout()
        self.update()

    def _on_scale_changed(self) -> None:
        self._relayout()
        self._reanchor()

    def _reanchor(self) -> None:
        """우선순위: 수동 위치 > game_rect 우상단 > 유지."""
        if self._manual_pos is not None:
            mx, my = self._manual_pos
            cx, cy = self._clamp_to_bound(mx, my)
            self.move(cx, cy)
            return
        if self._game_rect:
            gx, gy, gw, _ = self._game_rect
            cx, cy = self._clamp_to_bound(
                int(gx + gw - self.width() - self._px(8)),
                int(gy) + self._px(8))
            self.move(cx, cy)

    # ── 지오메트리 ───────────────────────────────────────────────────────

    def _grid_metrics(self):
        # 제목 제거(2026-06-12) + 컴팩트 — 중앙 가림 최소화.
        w = self.width()
        top = self._px(16)
        row_h = self._px(29)
        return w, top, row_h

    def _slot_center(self, slot: str):
        w, top, row_h = self._grid_metrics()
        fx, r = _SLOT_GRID[slot]
        return int(w * fx), int(top + row_h * r + row_h // 2)

    def _relayout(self) -> None:
        w = self._px(self._base_w)
        top = self._px(16)
        row_h = self._px(29)
        grid_h = row_h * 3
        bottom_h = self._px(16) * 2 + self._px(8)
        self.setFixedSize(w, top + grid_h + bottom_h)

    # ── 렌더 ────────────────────────────────────────────────────────────

    def _map_chip(self, qp, label: str) -> None:
        """우상단 미니 맵 식별 칩 (큰 제목 대신, 작게 — 입체 칩)."""
        ar, ag, ab = ACCENT_NAV
        qp.setFont(self._font(8))
        fm = qp.fontMetrics()
        cw = fm.horizontalAdvance(label) + self._px(14)
        ch = self._px(14)
        x0 = self.width() - cw - self._px(7)
        y0 = self._px(1)
        rect = QtCore.QRectF(x0, y0, cw, ch)
        qp.setPen(QtCore.Qt.NoPen)
        qp.setBrush(QtGui.QColor(ar, ag, ab, 42))
        qp.drawRoundedRect(rect, ch / 2, ch / 2)
        qp.setPen(QtGui.QPen(QtGui.QColor(ar, ag, ab, 175), self._px(1)))
        qp.setBrush(QtCore.Qt.NoBrush)
        qp.drawRoundedRect(rect, ch / 2, ch / 2)
        self._text_rect(qp, QtCore.QRect(int(x0), int(y0), int(cw), int(ch)),
                        int(QtCore.Qt.AlignCenter), label,
                        QtGui.QColor(255, 216, 132))

    def paintEvent(self, _ev):
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self._draw_panel_bg(qp, ACCENT_NAV)

        s = self._snap or {}
        base = str(s.get("base") or "선비족")
        x = int(s.get("x") or 0)
        cur_y = int(s.get("cur_y") or 0)
        next_y = int(s.get("next_y") or 0)
        order = [int(v) for v in (s.get("order") or [])]
        state = str(s.get("state") or "idle")
        out_of_order = bool(s.get("out_of_order"))
        # 강조 타이밍: 굴(7)에서 나와 허브(선비족x) 도착 = 강한 강조.
        hub_alert = bool(s.get("at_hub")) and bool(s.get("from_z7"))

        left_pad = self._px(13)
        _, top, row_h = self._grid_metrics()
        grid_bottom_y = top + row_h * 3

        if x in LAYOUTS:
            self._map_chip(qp, f"{base}{x}")
            layout = LAYOUTS[x]
            # 링(통로) — 슬롯 중심을 지나는 라운드 사각.
            lm = self._slot_center("LM")
            tl = self._slot_center("TL")
            ring = QtCore.QRectF(
                lm[0], tl[1],
                self._slot_center("RM")[0] - lm[0],
                self._slot_center("BL")[1] - tl[1],
            )
            # 링(통로) — 깔끔한 단선 (flat).
            qp.setBrush(QtCore.Qt.NoBrush)
            qp.setPen(QtGui.QPen(QtGui.QColor(96, 112, 146, 210), self._px(2)))
            qp.drawRoundedRect(ring, self._px(13), self._px(13))

            # 노드 박스 (flat — 채움+테두리, 투명도 비의존).
            bw, bh = self._px(38), self._px(21)
            for slot, label in layout.items():
                cx, cy = self._slot_center(slot)
                rect = QtCore.QRectF(cx - bw / 2, cy - bh / 2, bw, bh)
                is_entry = (label == 0)
                is_next = (not is_entry and next_y and label == next_y)
                is_cur = (not is_entry and cur_y and label == cur_y)
                in_order = (not is_entry and label in order)
                nr = self._px(6)
                halo = True
                if is_next and hub_alert:
                    fill = QtGui.QColor(250, 196, 56)
                    pen = QtGui.QPen(QtGui.QColor(255, 255, 255), self._px(2))
                    txt_c = QtGui.QColor(44, 30, 6)
                    halo = False
                elif is_next:
                    fill = QtGui.QColor(46, 50, 62, 228)
                    pen = QtGui.QPen(QtGui.QColor(255, 200, 60), self._px(2))
                    txt_c = QtGui.QColor(255, 214, 110)
                elif is_entry:
                    fill = QtGui.QColor(34, 44, 60, 228)
                    pen = QtGui.QPen(QtGui.QColor(96, 120, 160), self._px(1))
                    txt_c = QtGui.QColor(160, 186, 216)
                elif in_order:
                    fill = QtGui.QColor(44, 48, 60, 228)
                    pen = QtGui.QPen(QtGui.QColor(104, 116, 142), self._px(1))
                    txt_c = QtGui.QColor(226, 228, 238)
                else:
                    fill = QtGui.QColor(40, 44, 55, 228)
                    pen = QtGui.QPen(QtGui.QColor(88, 96, 116), self._px(1))
                    txt_c = QtGui.QColor(140, 144, 156)
                if is_cur:
                    pen = QtGui.QPen(QtGui.QColor(118, 240, 142), self._px(2))
                # 다음 굴(허브 강조) 옅은 글로우.
                if is_next and hub_alert:
                    qp.setPen(QtCore.Qt.NoPen)
                    qp.setBrush(QtGui.QColor(255, 210, 80, 55))
                    qp.drawRoundedRect(
                        rect.adjusted(-self._px(3), -self._px(3),
                                      self._px(3), self._px(3)),
                        nr + self._px(3), nr + self._px(3))
                qp.setBrush(fill)
                qp.setPen(pen)
                qp.drawRoundedRect(rect, nr, nr)
                text = "입구" if is_entry else (
                    f"▶{label}" if (is_next and hub_alert) else str(label))
                qp.setFont(self._font(9))
                self._text_rect(qp, rect.toRect(),
                                int(QtCore.Qt.AlignCenter), text, txt_c,
                                halo=halo)
        else:
            qp.setFont(self._font(9, bold=False))
            self._text(qp, left_pad, top + row_h,
                       "선비족x-y 맵 진입 시 표시",
                       QtGui.QColor(150, 156, 168))

        # 하단 2줄: 순서/상태 + 다음 굴.
        y_txt = grid_bottom_y + self._px(13)
        qp.setFont(self._font(8, bold=False))
        if order:
            self._text(qp, left_pad, y_txt,
                       f"순서  {'→'.join(map(str, order))}   "
                       f"({_STATE_KR.get(state, state)})",
                       QtGui.QColor(202, 208, 220))
        else:
            self._text(qp, left_pad, y_txt,
                       f"순서  미정  ({_STATE_KR.get(state, state)})",
                       QtGui.QColor(150, 156, 168))
        y_txt += self._px(16)
        if next_y and hub_alert:
            qp.setFont(self._font(10))
            self._text(qp, left_pad, y_txt, f"▶  {next_y}굴로 이동!",
                       QtGui.QColor(255, 206, 70))
        elif next_y:
            qp.setFont(self._font(9))
            line = f"다음  {next_y}굴"
            if out_of_order and cur_y:
                line += f"   (현재 {cur_y}굴 순서밖)"
            self._text(qp, left_pad, y_txt, line, QtGui.QColor(255, 216, 110))
        else:
            qp.setFont(self._font(9))
            self._text(qp, left_pad, y_txt, "다음  -",
                       QtGui.QColor(150, 156, 168))
        self._draw_edit_hint(qp)
