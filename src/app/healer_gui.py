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


def _auto_update():
    """2026-06-15: 앱 시작 시 자동 업데이트 (install.bat 우회 직접 실행 대비).

    updater 모듈은 완비돼 있었으나 어떤 진입점도 호출하지 않아 → 사용자가
    install.bat 을 안 거치면 옛 버전에 고착(v89 등). "고쳐도 반영 안 됨"의
    근본. healer_gui 가 healer/attacker/jjeol 통합 진입점이라 여기 한 곳이면
    전 역할 커버. 오프라인/설정없음/실패는 전부 스킵하고 앱은 정상 실행.
    """
    try:
        from ..net.cloud_sync import CloudClient
        from ..net import updater
    except Exception:
        return
    try:
        client = CloudClient()           # ~/.oldbaram_cloud.json 자동 로드
    except Exception:
        return                            # 설정 없음/오프라인 → 스킵
    try:
        rel = updater.check(client)       # 최신 version > 로컬이면 release
        if not rel:
            return
        ver = int(rel.get("version", 0))
        print(f"[AUTO-UPDATE] v{ver} 발견 → 변경 파일 다운로드...")
        got = updater.download_updates(client, rel)
        if got:
            print(f"[AUTO-UPDATE] {len(got)}개 파일 적용 후 재시작합니다.")
            updater.launch_apply_and_exit(ver)
            sys.exit(0)                   # _apply_update.bat 이 복사+재시작
        else:
            updater.write_version(ver)    # 변경 없음 → 버전만 갱신(재시작 불필요)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[AUTO-UPDATE] 스킵(실패해도 앱 실행): {e}")


def main():
    # pythonw(콘솔 없는 실행)에서 sys.stdout/stderr 가 None → print 크래시 방지.
    # 로그는 logger_setup 의 FileHandler 로 남으므로 콘솔 출력만 devnull 로 흘림.
    if sys.stdout is None or sys.stderr is None:
        _devnull = open(os.devnull, "w")
        if sys.stdout is None:
            sys.stdout = _devnull
        if sys.stderr is None:
            sys.stderr = _devnull
    # 2026-06-15: 무엇보다 먼저 자동 업데이트 (옛 버전 고착 방지).
    _auto_update()
    # 2026-06-13 항목8: 시작 시 닉네임/역할 선택 다이얼로그 제거 →
    #   GUI 메인창의 닉네임 필드 + 역할 라디오에서 직접 설정.
    #   직전 세션 값(.oldbaram_session.json)을 필드/라디오에 복원만 한다.
    cfg = load_cfg()
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    # 2026-06-23: 라이선스 게이트(로그인+기기등록+동시실행+사용기간+킬스위치+
    # 강제버전). 서버 RPC(app_login)가 판정. 통과 못하면 앱 미실행. QApplication
    # 이 있어야 다이얼로그를 띄울 수 있어 여기서 호출.
    from ..ui.login_gate import run_login_gate
    from ..version import BUILD_VERSION
    auth = run_login_gate(BUILD_VERSION)
    if not auth:
        sys.exit(0)
    last = _load_last()
    from ..utils import logger_setup
    # 로그 식별자 = 로그인 아이디(요구: 아이디+구분자). 역할(구분자)은 라디오 복원.
    logger_setup.set_session(
        auth.get("username") or last.get("nick", ""), last.get("role", ""))
    w = MainWindow(cfg)
    w._auth = auth                    # 하트비트/주기로그/로그아웃에 사용(Phase C)
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
    # 닉네임 = 로그인 아이디 고정(로그 식별자 일관). 라디오(역할)만 사용자 선택.
    _nick = auth.get("username") or str(last.get("nick", "") or "")
    try:
        if hasattr(w, "nick_edit"):
            w.nick_edit.setText(_nick)
    except Exception:
        pass
    w._session_nick = _nick
    # 라이선스 하트비트(동시실행 슬롯 유지/감시) + 주기 로그 업로드 시작.
    try:
        w.start_license_monitors()
    except Exception:
        pass
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
