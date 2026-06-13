"""옛바 통합 GUI 엔트리포인트.

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


import json
import pathlib

_SESSION_FILE = pathlib.Path.home() / ".oldbaram_session.json"


def _load_last() -> dict:
    try:
        return json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_last(nick: str, role: str) -> None:
    try:
        _SESSION_FILE.write_text(
            json.dumps({"nick": nick, "role": role}, ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass


def main():
    # 2026-06-13 항목8: 시작 시 닉네임/역할 선택 다이얼로그 제거 →
    #   GUI 메인창의 닉네임 필드 + 역할 라디오에서 직접 설정.
    #   직전 세션 값(.oldbaram_session.json)을 필드/라디오에 복원만 한다.
    cfg = load_cfg()
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    last = _load_last()
    from ..utils import logger_setup
    logger_setup.set_session(last.get("nick", ""), last.get("role", ""))
    w = MainWindow(cfg)
    # 직전 역할 복원 (라디오 토글 → self.role 반영). 쩔캐=healer+jjeol.
    try:
        _role = last.get("role", "")
        if _role == "attacker":
            w.rb_attacker.setChecked(True)
        elif _role == "jjeol" and hasattr(w, "rb_jjeol"):
            w.rb_jjeol.setChecked(True)
        else:
            w.rb_healer.setChecked(True)
    except Exception:
        pass
    # 직전 닉네임을 메인창 닉 필드에 복원.
    try:
        if hasattr(w, "nick_edit"):
            w.nick_edit.setText(str(last.get("nick", "") or ""))
    except Exception:
        pass
    w._session_nick = str(last.get("nick", "") or "")
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
