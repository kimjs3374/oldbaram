"""격수 화면에 띄우는 힐러 N대 실시간 미리보기 독립 창.

FrameReceiver 가 수신 스레드에서 on_frame(idx, nick, bgr) 을 호출 → 여기서
QImage 로 변환 후 signal 로 GUI 스레드에 넘긴다(스레드 경계 안전).
접속한 힐러 수만큼 셀이 동적으로 생기고 그리드가 재배치된다(하드코딩 칸 없음).
"""
from __future__ import annotations

import math
from typing import Dict

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

_CELL_W = 360   # 셀 표시 폭(px). 프레임은 들어온 비율 유지로 맞춤.
_MAX_COLS = 2   # 가로 최대 열 수. 3대 → 2×2(빈칸 1).


class HealerPreviewWindow(QtWidgets.QWidget):
    # idx, nick, QImage — 수신 스레드 → GUI 스레드 전달용.
    _frame_in = QtCore.pyqtSignal(int, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("힐러 미리보기")
        self.setWindowFlags(
            QtCore.Qt.Window | QtCore.Qt.WindowStaysOnTopHint
        )
        self.setStyleSheet("background:#12141a; color:#cfd3dc;")
        self._grid = QtWidgets.QGridLayout(self)
        self._grid.setContentsMargins(6, 6, 6, 6)
        self._grid.setSpacing(6)
        # idx -> {"box": QWidget, "img": QLabel, "nick": QLabel}
        self._cells: Dict[int, dict] = {}
        self._frame_in.connect(self._on_frame_gui)
        self.resize(_CELL_W + 24, _CELL_W)

    # FrameReceiver 콜백 (수신 스레드에서 호출).
    def on_frame(self, idx: int, nick: str, frame_bgr: np.ndarray) -> None:
        try:
            h, w = frame_bgr.shape[:2]
            rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
            qimg = QtGui.QImage(
                rgb.data, w, h, 3 * w, QtGui.QImage.Format_RGB888
            ).copy()  # 버퍼 소유권 복사 (스레드 경계 안전).
            self._frame_in.emit(int(idx), str(nick or ""), qimg)
        except Exception:
            pass

    @QtCore.pyqtSlot(int, str, object)
    def _on_frame_gui(self, idx: int, nick: str, qimg: QtGui.QImage) -> None:
        cell = self._cells.get(idx)
        if cell is None:
            cell = self._make_cell(idx)
            self._cells[idx] = cell
            self._relayout()
        title = nick.strip() or f"힐러{idx + 1}"
        cell["nick"].setText(title)
        pm = QtGui.QPixmap.fromImage(qimg).scaledToWidth(
            _CELL_W, QtCore.Qt.SmoothTransformation
        )
        cell["img"].setPixmap(pm)

    def _make_cell(self, idx: int) -> dict:
        box = QtWidgets.QWidget(self)
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        nick = QtWidgets.QLabel(f"힐러{idx + 1}", box)
        nick.setStyleSheet("font:bold 12px; color:#7fd1ff;")
        img = QtWidgets.QLabel(box)
        img.setFixedWidth(_CELL_W)
        img.setStyleSheet("background:#000;")
        img.setAlignment(QtCore.Qt.AlignCenter)
        img.setText("연결 대기…")
        v.addWidget(nick)
        v.addWidget(img)
        return {"box": box, "img": img, "nick": nick}

    def _relayout(self) -> None:
        order = sorted(self._cells.keys())
        cols = min(_MAX_COLS, max(1, len(order)))
        for pos, idx in enumerate(order):
            box = self._cells[idx]["box"]
            self._grid.removeWidget(box)
            self._grid.addWidget(box, pos // cols, pos % cols)
        rows = math.ceil(len(order) / cols)
        self.resize(cols * (_CELL_W + 12) + 12, rows * (_CELL_W + 28) + 12)

    def closeEvent(self, ev) -> None:
        # 창을 닫아도 수신은 계속 — 다음에 다시 show() 하면 됨. 숨김만.
        ev.ignore()
        self.hide()
