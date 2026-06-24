"""옛바 관리자 콘솔 — 회원/기기 관리 전용 GUI (사장님 PC 전용, 배포 금지).

⚠️ 배포 exe(build_nuitka.bat=src 만 빌드)에 절대 포함되지 않는다(루트 별도 파일
+ 전용 빌드). service_role 키(~/.oldbaram_cloud_admin.json)로 app_users/devices
를 직접 관리하므로 이 키가 없는 일반 사용자는 흉내낼 수 없다.

기능: 회원 추가 / 목록 / 차단·해제 / 기간 연장 / 접속 현황.
실행: py admin_gui.py  (또는 build_admin.bat 으로 만든 oldbaram_admin.exe)
"""
from __future__ import annotations

import datetime
import json
import pathlib
import sys

import requests
import bcrypt
from PyQt5 import QtCore, QtWidgets

_ADMIN_CFG = pathlib.Path.home() / ".oldbaram_cloud_admin.json"
_TIMEOUT = 20


def _load_admin() -> dict:
    adm = json.loads(_ADMIN_CFG.read_text(encoding="utf-8"))
    url = str(adm["url"]).rstrip("/")
    key = adm.get("service_key") or adm.get("service_role") or adm.get("secret_key")
    if not key:
        raise RuntimeError("admin 설정에 service_key 없음")
    return {"url": url, "key": key}


def _hash_pw(pw: str) -> str:
    # pgcrypto crypt 호환(bcrypt $2a$). 서버 app_login 의 crypt 검증과 일치.
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(prefix=b"2a")).decode()


class AdminConsole(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("옛바 관리자 콘솔")
        self.resize(720, 520)
        try:
            adm = _load_admin()
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "오류",
                f"관리자 설정(~/.oldbaram_cloud_admin.json)을 읽을 수 없습니다.\n{e}")
            QtCore.QTimer.singleShot(0, self.close)
            return
        self.url = adm["url"]
        self.H = {"apikey": adm["key"], "Authorization": f"Bearer {adm['key']}",
                  "Content-Type": "application/json"}

        lay = QtWidgets.QVBoxLayout(self)

        # ── 회원 추가 폼 ──
        box = QtWidgets.QGroupBox("회원 추가")
        form = QtWidgets.QGridLayout(box)
        self.ed_user = QtWidgets.QLineEdit()
        self.ed_pw = QtWidgets.QLineEdit()
        self.sp_dev = QtWidgets.QSpinBox(); self.sp_dev.setRange(0, 99); self.sp_dev.setValue(3)
        self.sp_con = QtWidgets.QSpinBox(); self.sp_con.setRange(0, 99); self.sp_con.setValue(1)
        self.sp_days = QtWidgets.QSpinBox(); self.sp_days.setRange(0, 3650); self.sp_days.setValue(30)
        self.cb_admin = QtWidgets.QCheckBox("관리자")
        form.addWidget(QtWidgets.QLabel("아이디"), 0, 0); form.addWidget(self.ed_user, 0, 1)
        form.addWidget(QtWidgets.QLabel("비밀번호"), 0, 2); form.addWidget(self.ed_pw, 0, 3)
        form.addWidget(QtWidgets.QLabel("등록기기"), 1, 0); form.addWidget(self.sp_dev, 1, 1)
        form.addWidget(QtWidgets.QLabel("동시실행"), 1, 2); form.addWidget(self.sp_con, 1, 3)
        form.addWidget(QtWidgets.QLabel("기간(일,0=무제한)"), 2, 0); form.addWidget(self.sp_days, 2, 1)
        form.addWidget(self.cb_admin, 2, 2)
        btn_add = QtWidgets.QPushButton("➕ 추가")
        btn_add.clicked.connect(self.add_user)
        form.addWidget(btn_add, 2, 3)
        lay.addWidget(box)

        # ── 회원 목록 ──
        bar = QtWidgets.QHBoxLayout()
        btn_refresh = QtWidgets.QPushButton("🔄 새로고침")
        btn_refresh.clicked.connect(self.refresh)
        btn_toggle = QtWidgets.QPushButton("⛔ 차단/해제")
        btn_toggle.clicked.connect(self.toggle_enabled)
        btn_extend = QtWidgets.QPushButton("📅 +30일 연장")
        btn_extend.clicked.connect(self.extend_30)
        btn_clear_dev = QtWidgets.QPushButton("🧹 기기 슬롯 비우기")
        btn_clear_dev.clicked.connect(self.clear_devices)
        btn_del = QtWidgets.QPushButton("🗑 삭제")
        btn_del.clicked.connect(self.delete_user)
        for b in (btn_refresh, btn_toggle, btn_extend, btn_clear_dev, btn_del):
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["아이디", "등록기기", "동시", "만료일", "활성", "온라인"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.table, 1)

        self.status = QtWidgets.QLabel("")
        lay.addWidget(self.status)
        self.refresh()

    # ── REST 헬퍼 ──
    def _get(self, path, params=None):
        r = requests.get(f"{self.url}/rest/v1/{path}", headers=self.H,
                         params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _say(self, t): self.status.setText(t)

    def _selected_user(self):
        row = self.table.currentRow()
        if row < 0:
            self._say("회원을 먼저 선택하세요.")
            return None
        return self.table.item(row, 0).text()

    # ── 동작 ──
    def refresh(self):
        try:
            users = self._get("app_users", {
                "select": "username,max_devices,max_concurrent,expires_at,enabled,is_admin",
                "order": "created_at.desc"})
            devs = self._get("devices", {"select": "username,last_seen"})
        except Exception as e:
            self._say(f"불러오기 실패: {e}")
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        online = {}
        for d in devs:
            ls = d.get("last_seen")
            if not ls:
                continue
            try:
                t = datetime.datetime.fromisoformat(ls.replace("Z", "+00:00"))
                if (now - t).total_seconds() <= 90:
                    online[d["username"]] = online.get(d["username"], 0) + 1
            except Exception:
                pass
        self.table.setRowCount(len(users))
        for i, u in enumerate(users):
            exp = (u.get("expires_at") or "")[:10] or "무제한"
            vals = [u["username"], str(u["max_devices"]), str(u["max_concurrent"]),
                    exp, "✅" if u["enabled"] else "⛔차단",
                    str(online.get(u["username"], 0))]
            if u.get("is_admin"):
                vals[0] += " (관리자)"
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QtWidgets.QTableWidgetItem(v))
        self._say(f"회원 {len(users)}명 · 온라인 {sum(online.values())}대")

    def add_user(self):
        u = self.ed_user.text().strip()
        pw = self.ed_pw.text()
        if not u or not pw:
            self._say("아이디와 비밀번호를 입력하세요.")
            return
        days = self.sp_days.value()
        exp = None
        if days > 0:
            exp = (datetime.datetime.now(datetime.timezone.utc)
                   + datetime.timedelta(days=days)).isoformat()
        payload = {"username": u, "password_hash": _hash_pw(pw),
                   "max_devices": self.sp_dev.value(),
                   "max_concurrent": self.sp_con.value(),
                   "expires_at": exp, "is_admin": self.cb_admin.isChecked()}
        try:
            r = requests.post(f"{self.url}/rest/v1/app_users", headers=self.H,
                             data=json.dumps(payload), timeout=_TIMEOUT)
            if r.status_code >= 400:
                self._say(f"추가 실패: {r.text[:120]}")
                return
        except Exception as e:
            self._say(f"추가 실패: {e}")
            return
        self.ed_user.clear(); self.ed_pw.clear()
        self._say(f"'{u}' 추가 완료")
        self.refresh()

    def _patch_user(self, username, body):
        r = requests.patch(f"{self.url}/rest/v1/app_users",
                          headers=self.H, params={"username": f"eq.{username}"},
                          data=json.dumps(body), timeout=_TIMEOUT)
        r.raise_for_status()

    def toggle_enabled(self):
        u = self._selected_user()
        if not u:
            return
        u = u.replace(" (관리자)", "")
        row = self.table.currentRow()
        cur = self.table.item(row, 4).text()
        new_en = (cur == "⛔차단")  # 차단이면 해제(true)
        try:
            self._patch_user(u, {"enabled": new_en})
            self._say(f"'{u}' {'해제' if new_en else '차단'} 완료")
            self.refresh()
        except Exception as e:
            self._say(f"실패: {e}")

    def extend_30(self):
        u = self._selected_user()
        if not u:
            return
        u = u.replace(" (관리자)", "")
        exp = (datetime.datetime.now(datetime.timezone.utc)
               + datetime.timedelta(days=30)).isoformat()
        try:
            self._patch_user(u, {"expires_at": exp})
            self._say(f"'{u}' 만료일 +30일(지금부터)")
            self.refresh()
        except Exception as e:
            self._say(f"실패: {e}")

    def clear_devices(self):
        u = self._selected_user()
        if not u:
            return
        u = u.replace(" (관리자)", "")
        try:
            r = requests.delete(f"{self.url}/rest/v1/devices", headers=self.H,
                              params={"username": f"eq.{u}"}, timeout=_TIMEOUT)
            r.raise_for_status()
            self._say(f"'{u}' 기기 슬롯 비움(재등록 가능)")
            self.refresh()
        except Exception as e:
            self._say(f"실패: {e}")

    def delete_user(self):
        u = self._selected_user()
        if not u:
            return
        u = u.replace(" (관리자)", "")
        if QtWidgets.QMessageBox.question(
                self, "삭제", f"'{u}' 회원을 삭제할까요?") != QtWidgets.QMessageBox.Yes:
            return
        try:
            requests.delete(f"{self.url}/rest/v1/devices", headers=self.H,
                          params={"username": f"eq.{u}"}, timeout=_TIMEOUT)
            r = requests.delete(f"{self.url}/rest/v1/app_users", headers=self.H,
                              params={"username": f"eq.{u}"}, timeout=_TIMEOUT)
            r.raise_for_status()
            self._say(f"'{u}' 삭제 완료")
            self.refresh()
        except Exception as e:
            self._say(f"실패: {e}")


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    w = AdminConsole()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
