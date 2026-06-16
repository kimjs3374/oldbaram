"""격수 화면에 띄우는 힐러 N대 실시간 미리보기 독립 창.

FrameReceiver 가 수신 스레드에서 on_frame(src_ip, idx, nick, bgr) 을 호출 → 여기서
QImage 로 변환 후 signal 로 GUI 스레드에 넘긴다(스레드 경계 안전).
**셀 구분 키 = 송신 IP**. 여러 힐러의 healer_idx 가 전부 0이어도 IP는 PC마다
고유하므로 한 칸에 겹쳐 번갈아 그려지는 문제가 없다(v91 CooldownReport 동일 원칙).

기능:
- 동적 그리드: 접속한 힐러 수만큼 칸 자동 생성(하드코딩 칸 없음).
- 반응형: 창 크기에 따라 각 셀 픽스맵이 비율 유지로 확대/축소(마지막 프레임 캐시).
- 위치/크기 기억: 닫힐 때 geometry 를 ~/.oldbaram_preview.json 에 저장 → 재실행 복원.
- 메인 종료 동반: parent(메인 윈도우)가 _allow_close 후 close() 하면 진짜 닫힘.
"""
from __future__ import annotations

import json
import math
import pathlib
from typing import Dict, Optional

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

_GEO_PATH = pathlib.Path.home() / ".oldbaram_preview.json"
_DEFAULT_CELL_W = 720   # 기본 셀 폭(px). 실제 표시 폭은 창 크기에 따라 반응형.
_MIN_CELL_W = 160
_MAX_COLS = 2           # 가로 최대 열 수. 3대 → 2×2.
_MARGIN = 6
_SPACING = 6
_NICK_H = 22            # 닉 라벨 높이 추정(레이아웃 여백 계산용).


class HealerPreviewWindow(QtWidgets.QWidget):
    # src_ip, title, QImage — 수신 스레드 → GUI 스레드 전달용.
    _frame_in = QtCore.pyqtSignal(str, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("힐러 미리보기")
        self.setWindowFlags(
            QtCore.Qt.Window | QtCore.Qt.WindowStaysOnTopHint
        )
        self.setStyleSheet("background:#12141a; color:#cfd3dc;")
        self._grid = QtWidgets.QGridLayout(self)
        self._grid.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        self._grid.setSpacing(_SPACING)
        # src_ip -> {"box","img","nick","qimg"(마지막 프레임 캐시)}
        self._cells: Dict[str, dict] = {}
        self._allow_close = False   # 메인이 종료시 True 로 세팅 후 close().
        self._frame_in.connect(self._on_frame_gui)
        # geometry 저장 디바운스(이동/리사이즈 폭주 방지).
        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_geo)
        self._restore_geo()

    # ---- 수신 콜백 (수신 스레드) ----
    def on_frame(self, src_ip: str, idx: int, nick: str,
                 frame_bgr: np.ndarray) -> None:
        try:
            h, w = frame_bgr.shape[:2]
            rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
            qimg = QtGui.QImage(
                rgb.data, w, h, 3 * w, QtGui.QImage.Format_RGB888
            ).copy()  # 버퍼 소유권 복사 (스레드 경계 안전).
            title = (nick or "").strip() or f"힐러{int(idx) + 1}"
            title = f"{title}  ({src_ip})"
            self._frame_in.emit(str(src_ip or "?"), title, qimg)
        except Exception:
            pass

    @QtCore.pyqtSlot(str, str, object)
    def _on_frame_gui(self, key: str, title: str,
                      qimg: QtGui.QImage) -> None:
        cell = self._cells.get(key)
        if cell is None:
            cell = self._make_cell()
            self._cells[key] = cell
            self._relayout()
        cell["nick"].setText(title)
        cell["qimg"] = qimg            # 캐시 (리사이즈 시 재스케일용).
        self._rescale_cell(cell)

    # ---- 반응형 스케일 ----
    def _cell_w(self) -> int:
        cols = min(_MAX_COLS, max(1, len(self._cells)))
        avail = self.width() - 2 * _MARGIN - _SPACING * (cols - 1)
        return max(_MIN_CELL_W, avail // cols)

    def _rescale_cell(self, cell: dict) -> None:
        qimg = cell.get("qimg")
        if qimg is None:
            return
        pm = QtGui.QPixmap.fromImage(qimg).scaledToWidth(
            self._cell_w(), QtCore.Qt.SmoothTransformation
        )
        cell["img"].setPixmap(pm)

    def _make_cell(self) -> dict:
        box = QtWidgets.QWidget(self)
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        nick = QtWidgets.QLabel("연결됨", box)
        nick.setStyleSheet("font:bold 12px; color:#7fd1ff;")
        img = QtWidgets.QLabel(box)
        img.setMinimumWidth(_MIN_CELL_W)
        img.setStyleSheet("background:#000;")
        img.setAlignment(QtCore.Qt.AlignCenter)
        img.setText("연결 대기…")
        v.addWidget(nick)
        v.addWidget(img)
        return {"box": box, "img": img, "nick": nick, "qimg": None}

    def _relayout(self) -> None:
        order = sorted(self._cells.keys())
        cols = min(_MAX_COLS, max(1, len(order)))
        for pos, key in enumerate(order):
            box = self._cells[key]["box"]
            self._grid.removeWidget(box)
            self._grid.addWidget(box, pos // cols, pos % cols)
        for cell in self._cells.values():
            self._rescale_cell(cell)

    # ---- 이벤트 ----
    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        for cell in self._cells.values():
            self._rescale_cell(cell)
        self._save_timer.start()

    def moveEvent(self, ev) -> None:
        super().moveEvent(ev)
        self._save_timer.start()

    def closeEvent(self, ev) -> None:
        self._save_geo()
        if self._allow_close:
            ev.accept()           # 메인 종료 동반 → 진짜 닫힘.
        else:
            ev.ignore()           # 사용자가 X → 숨김만(수신 유지).
            self.hide()

    # ---- geometry 영속 ----
    def _save_geo(self) -> None:
        try:
            g = self.geometry()
            _GEO_PATH.write_text(json.dumps(
                {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()}
            ), encoding="utf-8")
        except Exception:
            pass

    def _restore_geo(self) -> None:
        try:
            if _GEO_PATH.is_file():
                d = json.loads(_GEO_PATH.read_text(encoding="utf-8"))
                self.setGeometry(int(d["x"]), int(d["y"]),
                                 int(d["w"]), int(d["h"]))
                return
        except Exception:
            pass
        # 기본: 셀 1칸 크기.
        self.resize(_DEFAULT_CELL_W + 2 * _MARGIN,
                    _DEFAULT_CELL_W + _NICK_H + 2 * _MARGIN)
