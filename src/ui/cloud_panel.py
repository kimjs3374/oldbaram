"""healer_gui 클라우드 패널: 설정 sync(올리기/내리기) + 업데이트 알림/적용.

main_window.__init__ 에서 attach(self, root_layout) 1회 호출.
클라우드 설정(~/.oldbaram_cloud.json) 이 없으면 조용히 비활성(에러 팝업 없음).

설정 동기화 id = role + healer_idx:
  격수 → "attacker", 힐러 → "healer-0", "healer-1" ...
  (IP/닉이 아니라 idx 기준 → IP 바뀌어도 설정 복원)
"""
from __future__ import annotations

import json
import pathlib

from PyQt5 import QtWidgets

from . import settings_io
from ..net import cloud_sync, updater

_LOG_DIR = pathlib.Path(__file__).resolve().parents[2] / "logs"
_MAPS_DIR = pathlib.Path(__file__).resolve().parents[2] / "maps"


def make_sid(role: str, idx: int) -> str:
    return "attacker" if role == "attacker" else f"healer-{int(idx)}"


def _log_sid(mw) -> str:
    """로그 storage 경로 = 로그인계정/역할-IP끝자리.

    라이선스 로그인 계정(mw._auth['username'])이 있으면 계정을 **상위 폴더**로
    두어 버킷에서 계정별로 정리됨(sunbi-logs/{계정}/{역할-IP끝자리}/...).
    storage key 는 ASCII 만 허용 → 계정명 비ASCII 문자는 제거. 게이트
    미사용(계정 없음)이면 기존 '역할-IP끝자리' 단일 폴더 그대로.
    """
    import re
    from ..utils.logger_setup import local_ip_suffix
    base = f"{mw.role}-{local_ip_suffix()}"
    auth = getattr(mw, "_auth", None) or {}
    user = re.sub(r"[^A-Za-z0-9._-]+", "", str(auth.get("username") or ""))
    return f"{user}/{base}" if user else base


def _latest_log():
    if not _LOG_DIR.is_dir():
        return None
    logs = sorted(_LOG_DIR.glob("*.log"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _upload_current_log(mw):
    """현재 세션(가장 최근) 로그를 sunbi-logs/{계정}_{role}-{ip끝자리}/ 로 업로드.

    storage 경로는 ASCII 만 가능 → 로그인계정 + role + IP 끝자리(PC 구분)로.
    닉은 로그 헤더에도 남음.
    """
    c = cloud_sync.CloudClient()
    sid = _log_sid(mw)
    p = _latest_log()
    if p is None:
        return None
    return c.upload_log(sid, str(p))


def auto_upload_log(mw) -> None:
    """closeEvent 등에서 조용히 호출 (실패 무시)."""
    try:
        _upload_current_log(mw)
    except Exception:
        pass


def _upload_maps(mw):
    """수집된 maps/ 전체를 maps/{role}-{ip끝자리}.json 묶음 1파일로 업로드.

    storage key 는 ASCII 만 가능 → 한글 맵명은 묶음 JSON **내용**에만 둔다.
    D 작업기는 _cloud_maps.py 로 pull+merge 해서 분석. 비어 있으면 skip.
    """
    import tempfile
    import os
    from ..utils.logger_setup import local_ip_suffix
    if not _MAPS_DIR.is_dir():
        return None
    bundle = {}
    for fp in _MAPS_DIR.glob("*.json"):
        try:
            bundle[fp.stem] = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            pass  # 손상 파일 건너뛰고 나머지 업로드
    if not bundle:
        return None
    sid = f"{mw.role}-{local_ip_suffix()}"
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    tmp = tmp_dir / f"{sid}.json"   # 파일명이 곧 storage key basename
    tmp.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    try:
        return cloud_sync.CloudClient().upload_log("maps", str(tmp))
    finally:
        try:
            tmp.unlink()
            os.rmdir(tmp_dir)
        except Exception:
            pass


def auto_upload_maps(mw) -> None:
    """정지 버튼 등에서 조용히 호출 (실패 무시). 반환 key 또는 None.

    2026-06-13 진단: maps 미업로드 원인 추적용 로그(로그파일에 [MAPS-UPLOAD]).
    """
    import logging
    _lg = logging.getLogger(getattr(mw, "role", "healer") or "healer")
    try:
        exists = _MAPS_DIR.is_dir()
        n = len(list(_MAPS_DIR.glob("*.json"))) if exists else 0
        key = _upload_maps(mw)
        _lg.info(f"[MAPS-UPLOAD] dir_exists={exists} files={n} key={key}")
        return key
    except Exception as e:  # noqa: BLE001
        _lg.warning(f"[MAPS-UPLOAD] 실패: {e}")
        return None


# ── 해상도별 OCR 영역 프로파일 (2026-06-13 항목10·12) ─────────────────────

def pull_region_profiles(mw) -> int:
    """클라우드 region_profiles(DB row) → 로컬 병합. 반환 = 새 해상도 수.

    공유 row(app_settings id='region_profiles') data={"profiles": {WxH: {...}}}.
    로컬에 없는 해상도만 추가 (사용자 보정 보존).
    """
    from ..utils import region_profiles as rp
    try:
        c = cloud_sync.CloudClient()
        data = c.pull_settings(rp.CLOUD_SID)
        if not data:
            return 0
        return rp.merge_into_local(data.get("profiles", data) or {})
    except cloud_sync.CloudConfigError:
        return 0
    except Exception:
        return 0


def upload_region_profiles(mw) -> bool:
    """로컬 region_profiles 를 클라우드 공유 row 에 병합 push (항목12 수집).

    read-modify-write: 기존 클라우드 + 로컬 합쳐 push (로컬 보정값 우선).
    해상도별 좌표 최적화의 입력 — 다중 표본 평균화는 D측 후속 작업.
    """
    from ..utils import region_profiles as rp
    try:
        c = cloud_sync.CloudClient()
        local = rp.load_local()
        if not local:
            return False
        remote = {}
        try:
            d = c.pull_settings(rp.CLOUD_SID)
            if d:
                remote = d.get("profiles", d) or {}
        except Exception:
            remote = {}
        remote.update(local)
        c.push_settings(rp.CLOUD_SID, "regions", {"profiles": remote})
        return True
    except cloud_sync.CloudConfigError:
        return False
    except Exception:
        return False


def auto_upload_region_profiles(mw) -> None:
    """정지 등에서 조용히 호출 (실패 무시)."""
    try:
        upload_region_profiles(mw)
    except Exception:
        pass


def attach(mw, root_layout) -> None:
    """클라우드 버튼 행을 root_layout 에 추가하고 핸들러를 연결."""
    import os
    _launcher = bool(os.environ.get("OB_LAUNCHER"))
    # 줄1: 설정/로그 버튼 3개 — 균등 폭(stretch=1)으로 좁은 창 텍스트 잘림 방지.
    row = QtWidgets.QHBoxLayout()
    row.setSpacing(4)
    btn_up = QtWidgets.QPushButton("☁ 설정 올리기")
    btn_down = QtWidgets.QPushButton("☁ 설정 내리기")
    btn_log = QtWidgets.QPushButton("☁ 로그 올리기")
    for b in (btn_up, btn_down, btn_log):
        b.setMinimumHeight(26)
    row.addWidget(btn_up, 1)
    row.addWidget(btn_down, 1)
    row.addWidget(btn_log, 1)
    root_layout.addLayout(row)
    # 줄2: 업데이트 알림/버튼 — 런처 배포(exe, OB_LAUNCHER)는 런처가 자동
    # 업데이트를 전담하므로 이 행을 숨긴다(중복 알림 혼란 방지). 개발(py -m)
    # 에서만 표시(레거시 소스 업데이트 경로).
    # 🔴 부모를 mw 로 지정 — 레이아웃에 안 넣어도 떠다니는 top-level 창이 되지
    # 않게(런처 환경에서 setVisible 시 별도 창으로 뜨던 버그 차단).
    lbl = QtWidgets.QLabel("", mw)
    btn_update = QtWidgets.QPushButton("⬇ 업데이트", mw)
    btn_update.setVisible(False)
    if not _launcher:
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(4)
        btn_update.setMinimumHeight(26)
        row2.addWidget(lbl, 1)
        row2.addWidget(btn_update)
        root_layout.addLayout(row2)
    else:
        lbl.hide()
        btn_update.hide()
    mw._cloud_lbl = lbl
    mw._cloud_btn_update = btn_update

    def _say(t: str) -> None:
        lbl.setText(t)

    def _sid() -> str:
        # 설정 sync 는 app_settings(DB 테이블) → 한글 닉 id 가능.
        # 시작 시 입력한 닉이 있으면 닉을 id 로(직관적), 없으면 role+idx.
        # (로그 업로드는 storage 라 한글 불가 → 별도로 role+idx 사용)
        nick = getattr(mw, "_session_nick", "")
        return nick if nick else make_sid(mw.role, mw.healer_idx_spin.value())

    def on_up() -> None:
        try:
            c = cloud_sync.CloudClient()
            sid = _sid()
            c.push_settings(sid, mw.role, settings_io.collect(mw))
            # 항목12: 해상도별 영역 프로파일도 공유 row 에 병합 업로드.
            try:
                upload_region_profiles(mw)
            except Exception:
                pass
            _say(f"올림 완료 ({sid})")
        except cloud_sync.CloudConfigError:
            _say("클라우드 미설정 (~/.oldbaram_cloud.json)")
        except Exception as e:  # noqa: BLE001 — UI 라벨로만 보고
            _say(f"올리기 실패: {e}")

    def on_down() -> None:
        try:
            c = cloud_sync.CloudClient()
            sid = _sid()
            data = c.pull_settings(sid)
            if not data:
                _say(f"클라우드에 설정 없음 ({sid})")
                return
            mw._settings_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            settings_io.load(mw)
            # 항목10: 영역 프로파일도 pull → 현재 해상도 프로파일 자동 적용.
            try:
                pull_region_profiles(mw)
                mw._auto_apply_region_profile(pull_cloud=False)
            except Exception:
                pass
            _say(f"내림 완료 ({sid})")
        except cloud_sync.CloudConfigError:
            _say("클라우드 미설정 (~/.oldbaram_cloud.json)")
        except Exception as e:  # noqa: BLE001
            _say(f"내리기 실패: {e}")

    def on_update() -> None:
        try:
            c = cloud_sync.CloudClient()
            rel = updater.check(c)
            if not rel:
                _say("이미 최신")
                btn_update.setVisible(False)
                return
            _say("업데이트 다운로드 중...")
            QtWidgets.QApplication.processEvents()
            updater.download_updates(c, rel)
            updater.launch_apply_and_exit(int(rel["version"]))
            mw.close()
        except Exception as e:  # noqa: BLE001
            _say(f"업데이트 실패: {e}")

    def on_log() -> None:
        try:
            key = _upload_current_log(mw)
            _say(f"로그 올림: {key}" if key else "로그 파일 없음")
        except cloud_sync.CloudConfigError:
            _say("클라우드 미설정 (~/.oldbaram_cloud.json)")
        except Exception as e:  # noqa: BLE001
            _say(f"로그 올리기 실패: {e}")

    btn_up.clicked.connect(on_up)
    btn_down.clicked.connect(on_down)
    btn_log.clicked.connect(on_log)
    btn_update.clicked.connect(on_update)

    # 시작 시 업데이트 체크 (빠른 단일 쿼리; 미설정/실패는 조용히).
    # 런처 배포(exe)는 런처가 업데이트를 전담하므로 이 레거시 소스 체크를 스킵
    # (스킵 안 하면 부모 없는 btn_update 가 별도 창으로 떴음 + 중복 알림).
    if not _launcher:
        try:
            c = cloud_sync.CloudClient()
            rel = updater.check(c)
            if rel:
                cl = (rel.get("changelog") or "").strip()
                _say(f"새 버전 v{rel['version']}" + (f": {cl}" if cl else " 있음"))
                btn_update.setVisible(True)
            else:
                _say(f"최신 (v{updater.local_version()})")
        except cloud_sync.CloudConfigError:
            _say("클라우드 미설정")
        except Exception:  # noqa: BLE001 — 시작 시 네트워크 실패 무시
            _say("")
