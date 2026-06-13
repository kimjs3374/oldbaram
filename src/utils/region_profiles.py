# -*- coding: utf-8 -*-
"""해상도별 OCR 영역 프로파일 (2026-06-13 항목10·11·12).

msw.exe 클라이언트 해상도(WxH)별로 모든 OCR 영역을 **창 상대좌표**로
저장/공유한다.
- 항목10: 최초 실행 시 해당 해상도 프로파일이 있으면 자동으로 영역 필드 설정.
- 항목11: 창 위치/크기 변경 감지 → 영역 좌표 자동 재지정(이동=delta shift,
  해상도 변경=프로파일 재적용). (이동 추적은 main_window._tick_msw_tracker.)
- 항목12: 해상도별 영역을 클라우드(app_settings DB row 'region_profiles')로
  수집·공유 → 좌표 최적화 작업의 입력.

좌표계: 영역은 화면 절대좌표로 보관(self._regions / cfg.cooldown). msw 클라이언트
원점(get_window_rect left/top)을 빼면 창 상대좌표. 같은 해상도면 인게임 UI
배치가 동일하므로 상대좌표는 PC 간 이식 가능.
"""
from __future__ import annotations

import json
import pathlib
from typing import Dict, Optional, Tuple

# 프로파일에 담는 영역 키. self._regions(앞 6종) + cfg.cooldown(cd/nick/buff).
_SELF_KEYS = ("game", "map", "coord", "xp", "hp", "mp")
_CFG_KEYS = ("cd", "nick", "buff")
REGION_KEYS = _SELF_KEYS + _CFG_KEYS

# cfg.cooldown 속성 prefix (cd 는 region_*, 나머지는 {k}_region_*).
_CFG_PREFIX = {"cd": "region", "nick": "nick_region", "buff": "buff_region"}

_LOCAL = pathlib.Path.home() / ".oldbaram_region_profiles.json"
# 클라우드 공유 행 id (app_settings DB — 한글 무관, read-modify-write 병합).
CLOUD_SID = "region_profiles"


def res_key(w: int, h: int) -> str:
    return f"{int(w)}x{int(h)}"


# ── 로컬 저장 ─────────────────────────────────────────────────────────────

def load_local() -> dict:
    try:
        return json.loads(_LOCAL.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save_local(profiles: dict) -> None:
    try:
        _LOCAL.write_text(
            json.dumps(profiles, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception:
        pass


# ── 좌표 변환 ─────────────────────────────────────────────────────────────

def to_rel(regions_abs: Dict[str, tuple],
           origin: Tuple[int, int]) -> Dict[str, list]:
    ox, oy = int(origin[0]), int(origin[1])
    out: Dict[str, list] = {}
    for k, v in regions_abs.items():
        x, y, w, h = (int(t) for t in v)
        out[k] = [x - ox, y - oy, w, h]
    return out


def to_abs(regions_rel: Dict[str, list],
           origin: Tuple[int, int]) -> Dict[str, tuple]:
    ox, oy = int(origin[0]), int(origin[1])
    out: Dict[str, tuple] = {}
    for k, v in regions_rel.items():
        rx, ry, w, h = (int(t) for t in v)
        out[k] = (rx + ox, ry + oy, w, h)
    return out


# ── main_window 영역 수집/적용 ───────────────────────────────────────────

def gather_regions_abs(mw) -> Dict[str, tuple]:
    """현재 GUI 에 설정된 영역(절대좌표)을 영역키→(x,y,w,h) 로 수집."""
    out: Dict[str, tuple] = {}
    regions = getattr(mw, "_regions", {}) or {}
    for k in _SELF_KEYS:
        v = regions.get(k)
        if v and int(v[2]) > 0 and int(v[3]) > 0:
            out[k] = (int(v[0]), int(v[1]), int(v[2]), int(v[3]))
    cd = getattr(mw, "cfg", None)
    cd = getattr(cd, "cooldown", None)
    if cd is not None:
        for k in _CFG_KEYS:
            p = _CFG_PREFIX[k]
            x = int(getattr(cd, f"{p}_x", -1))
            w = int(getattr(cd, f"{p}_w", 0))
            if x >= 0 and w > 0:
                out[k] = (x, int(getattr(cd, f"{p}_y", 0)), w,
                          int(getattr(cd, f"{p}_h", 0)))
    return out


def apply_regions_abs(mw, regions_abs: Dict[str, tuple]) -> int:
    """절대좌표 영역들을 GUI(_regions/cfg.cooldown)+워커에 적용. 반환=적용 개수."""
    n = 0
    regions = getattr(mw, "_regions", None)
    for k in _SELF_KEYS:
        if k not in regions_abs:
            continue
        x, y, w, h = regions_abs[k]
        if regions is not None:
            regions[k] = (int(x), int(y), int(w), int(h))
        try:
            mw._sync_region_to_cfg(k)
        except Exception:
            pass
        btn = getattr(mw, "_region_buttons", {}).get(k)
        if btn is not None:
            lb = getattr(mw, "_region_labels_kr", {}).get(k, k)
            btn.setText(f"{lb} ✓")
        n += 1
    cd = getattr(getattr(mw, "cfg", None), "cooldown", None)
    if cd is not None:
        for k in _CFG_KEYS:
            if k not in regions_abs:
                continue
            x, y, w, h = regions_abs[k]
            p = _CFG_PREFIX[k]
            setattr(cd, f"{p}_x", int(x))
            setattr(cd, f"{p}_y", int(y))
            setattr(cd, f"{p}_w", int(w))
            setattr(cd, f"{p}_h", int(h))
            n += 1
    # 워커 실행 중이면 즉시 반영.
    w_ = getattr(mw, "worker", None)
    if w_ is not None:
        try:
            mw._apply_all_regions_to_worker()
        except Exception:
            pass
        _inject_cfg_regions_to_worker(mw, cd)
    try:
        mw._refresh_region_overlay()
        mw._refresh_overlay_anchors()
    except Exception:
        pass
    return n


def _inject_cfg_regions_to_worker(mw, cd) -> None:
    """cd/nick/buff 영역을 워커 setter 로 주입 (실행 중 재적용용)."""
    if cd is None or mw.worker is None:
        return
    w = mw.worker
    try:
        if (int(getattr(cd, "region_w", 0)) > 0
                and int(getattr(cd, "region_x", -1)) >= 0
                and hasattr(w, "set_cooldown_region")):
            w.set_cooldown_region(int(cd.region_x), int(cd.region_y),
                                  int(cd.region_w), int(cd.region_h))
        if (int(getattr(cd, "nick_region_w", 0)) > 0
                and int(getattr(cd, "nick_region_x", -1)) >= 0
                and hasattr(w, "set_nick_region")):
            w.set_nick_region(int(cd.nick_region_x), int(cd.nick_region_y),
                              int(cd.nick_region_w), int(cd.nick_region_h))
        if (int(getattr(cd, "buff_region_w", 0)) > 0
                and int(getattr(cd, "buff_region_x", -1)) >= 0
                and hasattr(w, "set_buff_region")):
            w.set_buff_region(int(cd.buff_region_x), int(cd.buff_region_y),
                              int(cd.buff_region_w), int(cd.buff_region_h))
    except Exception:
        pass


# ── 프로파일 저장/적용 (해상도 기준) ──────────────────────────────────────

def save_current_profile(mw, origin: Tuple[int, int],
                         resolution: Tuple[int, int]) -> Optional[str]:
    """현재 영역들을 해상도 키로 로컬 프로파일에 저장. 반환=res_key 또는 None."""
    abs_r = gather_regions_abs(mw)
    if not abs_r:
        return None
    rel = to_rel(abs_r, origin)
    rk = res_key(resolution[0], resolution[1])
    prof = load_local()
    prof[rk] = rel
    save_local(prof)
    return rk


def apply_profile_for_resolution(mw, origin: Tuple[int, int],
                                 resolution: Tuple[int, int]) -> int:
    """해당 해상도 로컬 프로파일이 있으면 절대좌표로 변환해 적용. 반환=적용 개수."""
    rk = res_key(resolution[0], resolution[1])
    prof = load_local()
    rel = prof.get(rk)
    if not rel:
        return 0
    abs_r = to_abs(rel, origin)
    return apply_regions_abs(mw, abs_r)


def merge_into_local(remote_profiles: dict) -> int:
    """클라우드에서 받은 프로파일을 로컬에 병합(로컬 우선 — 사용자 보정 보존).

    로컬에 없는 해상도만 추가. 반환=새로 추가된 해상도 수.
    """
    if not remote_profiles:
        return 0
    prof = load_local()
    added = 0
    for rk, rel in remote_profiles.items():
        if rk not in prof and rel:
            prof[rk] = rel
            added += 1
    if added:
        save_local(prof)
    return added
