"""로그인 게이트 — 앱 시작 시 라이선스 인증(아이디/비번 + 기기 + 라이선스).

흐름(run_login_gate):
1. CloudClient 생성(설정 없음/오프라인 → 차단). HWID/기기명 수집.
2. 저장된 토큰 있으면 heartbeat 로 자동 로그인 시도.
3. 실패/없음 → 로그인 다이얼로그 → app_login RPC(서버 검증) → 사유별 안내.
4. 성공 → 토큰만 로컬 저장(비번 평문 저장 금지) + auth dict 반환.

반환 dict: {username, token, is_admin, expires_at, client, hwid} 또는 None(차단).
QApplication 이 이미 생성돼 있어야 한다(호출 측 healer_gui.main 에서 선생성).
"""
from __future__ import annotations

import json
import pathlib
import socket

from PyQt5 import QtWidgets

_AUTH_FILE = pathlib.Path.home() / ".oldbaram_auth.json"

_REASON_MSG = {
    "bad_credentials":  "아이디 또는 비밀번호가 올바르지 않습니다.",
    "killswitch":       "관리자가 서비스를 일시 중지했습니다.\n잠시 후 다시 시도하세요.",
    "disabled":         "사용이 중지된 계정입니다.\n관리자에게 문의하세요.",
    "expired":          "사용 기간이 만료되었습니다.\n관리자에게 문의하세요.",
    "update_required":  "최신 버전으로 업데이트가 필요합니다.\n프로그램을 다시 시작하면 자동 업데이트됩니다.",
    "device_limit":     "등록 가능한 기기 수를 초과했습니다.\n관리자에게 문의하세요.",
    "concurrent_limit": "동시 실행 가능한 기기 수를 초과했습니다.\n다른 기기에서 프로그램을 종료한 뒤 다시 시도하세요.",
}


def _load_auth() -> dict:
    try:
        return json.loads(_AUTH_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_auth(username: str, token: str) -> None:
    try:
        _AUTH_FILE.write_text(
            json.dumps({"username": username, "token": token},
                       ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass


def _clear_auth() -> None:
    try:
        _AUTH_FILE.unlink(missing_ok=True)
    except Exception:
        pass


class _LoginDialog(QtWidgets.QDialog):
    def __init__(self, last_username: str = ""):
        super().__init__()
        self.setWindowTitle("옛바 로그인")
        self.setModal(True)
        lay = QtWidgets.QFormLayout(self)
        self.ed_user = QtWidgets.QLineEdit(last_username)
        self.ed_pw = QtWidgets.QLineEdit()
        self.ed_pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.cb_remember = QtWidgets.QCheckBox("로그인 정보 저장")
        self.cb_remember.setChecked(True)
        lay.addRow("아이디", self.ed_user)
        lay.addRow("비밀번호", self.ed_pw)
        lay.addRow("", self.cb_remember)
        self.lbl = QtWidgets.QLabel("")
        self.lbl.setStyleSheet("color:#e06c75;")
        self.lbl.setWordWrap(True)
        lay.addRow(self.lbl)
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.button(QtWidgets.QDialogButtonBox.Ok).setText("로그인")
        btns.button(QtWidgets.QDialogButtonBox.Cancel).setText("종료")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addRow(btns)
        if last_username:
            self.ed_pw.setFocus()
        else:
            self.ed_user.setFocus()

    def set_error(self, msg: str) -> None:
        self.lbl.setText(msg)

    def values(self):
        return (self.ed_user.text().strip(),
                self.ed_pw.text(),
                self.cb_remember.isChecked())


def run_login_gate(build_version: str):
    """라이선스 인증 게이트. 통과 시 auth dict, 차단/취소 시 None."""
    # 1) 클라우드 클라이언트 (설정 없음/오프라인 → 차단)
    try:
        from ..net.cloud_sync import CloudClient
        client = CloudClient()
    except Exception:
        QtWidgets.QMessageBox.critical(
            None, "옛바",
            "서버 설정을 찾을 수 없습니다.\ninstall.bat 으로 설치 후 실행하세요.")
        return None

    from ..utils.hwid import machine_id
    hwid = machine_id()
    try:
        dev_name = socket.gethostname()
    except Exception:
        dev_name = "PC"

    saved = _load_auth()

    # 2) 저장된 토큰으로 자동 로그인 시도
    tok = saved.get("token")
    if tok:
        try:
            r = client.heartbeat(tok)
            if isinstance(r, dict) and r.get("ok"):
                return {"username": saved.get("username", ""), "token": tok,
                        "client": client, "hwid": hwid}
        except Exception:
            pass  # 오프라인이면 아래 수동 로그인에서도 실패 → 차단

    # 3) 로그인 다이얼로그 루프
    dlg = _LoginDialog(saved.get("username", ""))
    while True:
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return None  # 종료 버튼/창 닫기 → 앱 미실행
        username, password, remember = dlg.values()
        if not username or not password:
            dlg.set_error("아이디와 비밀번호를 입력하세요.")
            continue
        try:
            res = client.login(username, password, hwid, dev_name, build_version)
        except Exception:
            dlg.set_error("서버에 연결할 수 없습니다. 인터넷 연결을 확인하세요.")
            continue
        if isinstance(res, dict) and res.get("ok"):
            token = res.get("token", "")
            if remember:
                _save_auth(username, token)
            else:
                _clear_auth()
            return {
                "username": username, "token": token,
                "is_admin": res.get("is_admin", False),
                "expires_at": res.get("expires_at"),
                "client": client, "hwid": hwid,
            }
        reason = (res or {}).get("reason", "unknown") if isinstance(res, dict) else "unknown"
        dlg.set_error(_REASON_MSG.get(reason, f"로그인 실패: {reason}"))
        dlg.ed_pw.clear()
        dlg.ed_pw.setFocus()
