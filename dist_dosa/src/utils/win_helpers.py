"""Win32/이미지 공용 헬퍼.

원본 healer_gui.py 상단에서 분리. 행동 변경 없음.
"""
from __future__ import annotations
import ctypes

import cv2
import numpy as np
from PyQt5 import QtGui

_user32 = ctypes.WinDLL("user32", use_last_error=True)


def _is_fg_hwnd(hwnd) -> bool:
    if not hwnd:
        return False
    try:
        return int(_user32.GetForegroundWindow()) == int(hwnd)
    except Exception:
        return False


def detect_arrow_dir(hwnd=None) -> str:
    """msw 가 foreground 일 때만 격수 방향키(↑↓←→) 1개 반환. 아니면 '-'.

    격수가 키 눌렀는데 좌표가 안 변하면 '막힘'(몹/벽). 이 키 신호를 UDP 로
    실어 controller 가 막힘률(try/block)을 누적한다 (맵 데이터화 로드맵.md §6.5).
    WASD 는 옛바 방향키가 아니므로 제외 (방향은 방향키만, 메모리 규율).
    """
    try:
        if hwnd and int(_user32.GetForegroundWindow()) != int(hwnd):
            return "-"

        def _down(vk: int) -> bool:
            return (_user32.GetAsyncKeyState(int(vk)) & 0x8000) != 0

        if _down(0x26):
            return "U"
        if _down(0x28):
            return "D"
        if _down(0x25):
            return "L"
        if _down(0x27):
            return "R"
    except Exception:
        pass
    return "-"


def frame_to_qpix(frame: np.ndarray, max_w: int = 640) -> QtGui.QPixmap:
    H, W = frame.shape[:2]
    scale = min(1.0, max_w / W)
    if scale < 1.0:
        frame = cv2.resize(frame, (int(W * scale), int(H * scale)))
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    qimg = QtGui.QImage(rgb.data, w, h, ch * w, QtGui.QImage.Format_RGB888)
    return QtGui.QPixmap.fromImage(qimg.copy())
