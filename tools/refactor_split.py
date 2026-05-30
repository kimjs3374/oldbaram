"""healer_gui.py 자동 분할기.

D:/oldbaram/dist_dosa/src/app/healer_gui.py 를 역할별 모듈로 찢어 동일 경로
하위에 생성한 workers/, ui/, utils/ 로 분산 배치한 뒤 healer_gui.py 자체는
엔트리(main)+MainWindow 임포트만 남기는 최종 파일로 교체한다.

src/ 는 dist_dosa/ 와 완전 동일 구조여야 하므로 두 루트에 모두 적용.

사용: py tools/refactor_split.py
"""
from __future__ import annotations
from pathlib import Path

ROOT_DIST = Path("D:/oldbaram/dist_dosa/src")
ROOT_SRC = Path("D:/oldbaram/src")


# (시작, 종료) 1-based inclusive→exclusive. 원본 healer_gui.py 기준.
SECTIONS = {
    "healer_worker":      (88,   1526),   # HealerWorker
    "attacker_worker":    (1526, 1679),   # AttackerWorker
    "dialogs":            (1690, 1834),   # SkillDialog + ParamDialog + NetworkDialog
    "overlay":            (1834, 1975),   # GameOverlay
    "control_listener":   (1975, 2070),   # ControlListener
    "attacker_heartbeat": (2070, 2197),   # AttackerHeartbeat
    "healer_heartbeat":   (2197, 2252),   # HealerHeartbeat
    "region_picker":      (2252, 2325),   # RegionPicker
    "main_window":        (2325, 3785),   # MainWindow
}

# frame_to_qpix: 1679~1688 → utils/win_helpers.py 로 이동
# _setup_logger, _is_fg_hwnd, _user32, LOG_DIR → utils/logger_setup.py / win_helpers.py


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def slice_lines(lines: list[str], start: int, end: int) -> str:
    # 1-based → 0-based
    return "".join(lines[start - 1:end - 1])


HEADER_COMMON_QT = """from __future__ import annotations
import ctypes
import logging
import os
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

from ..utils.logger_setup import _setup_logger
from ..utils.win_helpers import _user32, _is_fg_hwnd, frame_to_qpix
"""

HEADERS = {
    "healer_worker": HEADER_COMMON_QT + """
# HealerWorker 내부에서 런타임 import 되는 무거운 모듈은 그대로 유지.
""",
    "attacker_worker": HEADER_COMMON_QT + """
from ..app.attacker import Attacker
""",
    "dialogs": HEADER_COMMON_QT + """
""",
    "overlay": HEADER_COMMON_QT + """
""",
    "control_listener": HEADER_COMMON_QT + """
""",
    "attacker_heartbeat": HEADER_COMMON_QT + """
""",
    "healer_heartbeat": HEADER_COMMON_QT + """
""",
    "region_picker": HEADER_COMMON_QT + """
""",
    "main_window": HEADER_COMMON_QT + """
import json
import sys
import threading
from datetime import datetime

from ..config import load as load_cfg
from .overlay import GameOverlay
from .region_picker import RegionPicker
from .dialogs import SkillDialog, ParamDialog, NetworkDialog
from ..workers.healer_worker import HealerWorker
from ..workers.attacker_worker import AttackerWorker
from ..workers.heartbeat import HealerHeartbeat, AttackerHeartbeat
from ..workers.control_listener import ControlListener
""",
}


DEST = {
    "healer_worker":      ("workers", "healer_worker.py"),
    "attacker_worker":    ("workers", "attacker_worker.py"),
    "dialogs":            ("ui",      "dialogs.py"),
    "overlay":            ("ui",      "overlay.py"),
    "control_listener":   ("workers", "control_listener.py"),
    "attacker_heartbeat": ("workers", "_heartbeat_atk.py"),  # 임시, 이후 합침
    "healer_heartbeat":   ("workers", "_heartbeat_hlr.py"),
    "region_picker":      ("ui",      "region_picker.py"),
    "main_window":        ("ui",      "main_window.py"),
}


# utils/logger_setup.py 는 전용 내용으로 생성 (원본 55-70행 이관)
UTILS_LOGGER = '''"""로거 공용 셋업."""
from __future__ import annotations
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def _setup_logger() -> tuple[logging.Logger, Path]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOG_DIR / f"healer_{ts}.log"
    lg = logging.getLogger("healer")
    lg.setLevel(logging.DEBUG)
    for h in list(lg.handlers):
        lg.removeHandler(h)
    fh = RotatingFileHandler(path, maxBytes=20 * 1024 * 1024,
                             backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
        datefmt="%H:%M:%S"
    ))
    lg.addHandler(fh)
    return lg, path
'''

UTILS_WIN_HELPERS = '''"""Win32/이미지 공용 헬퍼.

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


def frame_to_qpix(frame: np.ndarray, max_w: int = 640) -> QtGui.QPixmap:
    H, W = frame.shape[:2]
    scale = min(1.0, max_w / W)
    if scale < 1.0:
        frame = cv2.resize(frame, (int(W * scale), int(H * scale)))
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    qimg = QtGui.QImage(rgb.data, w, h, ch * w, QtGui.QImage.Format_RGB888)
    return QtGui.QPixmap.fromImage(qimg.copy())
'''

# FSM 상태 한글 매핑 — GUI 표시 전용. 로그는 영어 유지.
UTILS_STATE_NAMES = '''"""FSM 상태·액션 한글 디스플레이 매핑 (UI 전용, 로그는 영어 유지)."""
from __future__ import annotations

FSM_STATE_KR = {
    "FOLLOW":       "따라가는 중",
    "COMBAT":       "사냥 중",
    "LOADING":      "맵 이동 중",
    "STUCK":        "멈춤 감지",
    "NEW_MAP":      "새 맵 진입",
    "ENTER_PORTAL": "포탈 진입",
    "IDLE":         "대기",
    "TAB_CONFIRM":  "빨탭 동기화 중",
}

TAB_ACTION_KR = {
    "send_home": "Home(셀프타겟) 송신",
    "send_tab":  "Tab(빨탭 확정) 송신",
    "wait_red":  "빨탭 확인 대기",
    "done_ok":   "동기화 완료",
    "retry_arm": "재시도",
}


def fsm_kr(state: str) -> str:
    if not state:
        return "-"
    return FSM_STATE_KR.get(state, state)


def tab_kr(action: str) -> str:
    if not action:
        return "-"
    return TAB_ACTION_KR.get(action, action)
'''

# 윈도우 좌표계 동기화 — 절대 픽셀 ↔ 창 상대 픽셀 변환. MoveEvent 추적.
UTILS_WINDOW_GEOM = '''"""게임창 절대좌표 ↔ 창 상대좌표 변환 헬퍼 (v5.17 영역 설정 용도)."""
from __future__ import annotations
import ctypes
from typing import Optional, Tuple

_user32 = ctypes.WinDLL("user32", use_last_error=True)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def get_window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    if not hwnd:
        return None
    r = _RECT()
    if not _user32.GetWindowRect(int(hwnd), ctypes.byref(r)):
        return None
    return (int(r.left), int(r.top), int(r.right), int(r.bottom))


def abs_to_rel(hwnd: int, x: int, y: int) -> Optional[Tuple[int, int]]:
    """절대 화면 좌표 → 게임창 좌상단 기준 상대 좌표."""
    wr = get_window_rect(hwnd)
    if wr is None:
        return None
    return (int(x - wr[0]), int(y - wr[1]))


def rel_to_abs(hwnd: int, x: int, y: int) -> Optional[Tuple[int, int]]:
    """창 상대 좌표 → 현재 창 위치 기준 절대 좌표."""
    wr = get_window_rect(hwnd)
    if wr is None:
        return None
    return (int(x + wr[0]), int(y + wr[1]))
'''

UTILS_INIT = '"""유틸 헬퍼 패키지."""\n'
WORKERS_INIT = '"""Qt 백그라운드 스레드 패키지."""\n'
UI_INIT = '"""PyQt5 위젯 패키지."""\n'


def create_init_files(root: Path) -> None:
    for sub, body in [
        ("utils", UTILS_INIT),
        ("workers", WORKERS_INIT),
        ("ui", UI_INIT),
    ]:
        (root / sub).mkdir(exist_ok=True)
        f = root / sub / "__init__.py"
        if not f.exists():
            f.write_text(body, encoding="utf-8")


def write_utils(root: Path) -> None:
    (root / "utils" / "logger_setup.py").write_text(UTILS_LOGGER, encoding="utf-8")
    (root / "utils" / "win_helpers.py").write_text(UTILS_WIN_HELPERS, encoding="utf-8")
    (root / "utils" / "state_names.py").write_text(UTILS_STATE_NAMES, encoding="utf-8")
    (root / "utils" / "window_geom.py").write_text(UTILS_WINDOW_GEOM, encoding="utf-8")


def combine_heartbeats(root: Path) -> None:
    """_heartbeat_atk.py + _heartbeat_hlr.py 를 workers/heartbeat.py 로 병합."""
    atk = (root / "workers" / "_heartbeat_atk.py").read_text(encoding="utf-8")
    hlr = (root / "workers" / "_heartbeat_hlr.py").read_text(encoding="utf-8")

    # 두 파일에서 클래스 정의 부분만 추출 (header 제외).
    def class_only(s: str) -> str:
        idx = s.find("\nclass ")
        return s[idx:] if idx >= 0 else s

    atk_body = class_only(atk).lstrip("\n")
    hlr_body = class_only(hlr).lstrip("\n")
    merged = HEADER_COMMON_QT + "\n\n" + atk_body + "\n\n" + hlr_body
    (root / "workers" / "heartbeat.py").write_text(merged, encoding="utf-8")
    (root / "workers" / "_heartbeat_atk.py").unlink()
    (root / "workers" / "_heartbeat_hlr.py").unlink()


ENTRY_HEALER_GUI = '''"""옛바 통합 GUI 엔트리포인트.

v5.17 리팩토링: 실제 구현은 ui.main_window + workers/* + ui/* 에 분산.
본 파일은 QApplication 생성과 main() 만 담당.

실행: py -m src.app.healer_gui
"""
from __future__ import annotations
import os
import sys

# torch/numpy를 PyQt5 전에 로드 (Windows MSVC 런타임 충돌 회피).
try:
    import torch  # noqa: F401
except Exception:
    pass
try:
    import PyQt5 as _pyqt5
    _qt_root = os.path.dirname(_pyqt5.__file__)
    for _sub in ("Qt5", "Qt"):
        _plugs = os.path.join(_qt_root, _sub, "plugins")
        if os.path.isdir(_plugs):
            os.environ["QT_PLUGIN_PATH"] = _plugs
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_plugs, "platforms")
            break
except Exception:
    pass

from PyQt5 import QtWidgets

from ..config import load as load_cfg
from ..ui.main_window import MainWindow
from ..ui.styles import APP_QSS


def main():
    cfg = load_cfg()
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    w = MainWindow(cfg)
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
'''


def split_one_root(root: Path) -> None:
    src_path = root / "app" / "healer_gui.py"
    lines = read_lines(src_path)

    create_init_files(root)
    write_utils(root)

    for name, (s, e) in SECTIONS.items():
        subdir, fname = DEST[name]
        body = slice_lines(lines, s, e)
        content = HEADERS[name] + "\n\n" + body
        (root / subdir / fname).write_text(content, encoding="utf-8")

    combine_heartbeats(root)

    # 원본 백업 후 엔트리만 남김.
    backup = src_path.with_suffix(".py.pre517.bak")
    if not backup.exists():
        backup.write_text("".join(lines), encoding="utf-8")
    src_path.write_text(ENTRY_HEALER_GUI, encoding="utf-8")


def main():
    for root in [ROOT_DIST, ROOT_SRC]:
        print(f"[SPLIT] {root}")
        split_one_root(root)
    print("[SPLIT] done")


if __name__ == "__main__":
    main()
