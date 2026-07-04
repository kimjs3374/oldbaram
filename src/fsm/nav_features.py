"""NavBrain 공용 피처 인코딩 — 학습(_nav_train.py)·추론(nav_brain.py) 단일 정본.

digit_cnn 관례(coord_template._normalize 공유)와 동일하게, 패치/스칼라 인코딩을
여기 한 곳에 두고 학습 스크립트가 import 한다. 학습/추론 인코딩이 갈라지면
모델이 조용히 엉뚱한 답을 내므로 **이 파일 수정 시 재학습 필수**.

좌표축 실측 정본 (메모리 project_coord_axis_ud, 2026-06-15 실측):
    R = x증가, L = x감소, D = y증가(아래), U = y감소(위)
map_route.py 의 옛 DELTA(U/D 반대)는 2026-07-05 이 정본으로 정정됨.
"""
from __future__ import annotations

import numpy as np

# --- 좌표축 단일 정본 (실측: D=y+, U=y-) ---
DELTA = {"L": (-1, 0), "R": (1, 0), "U": (0, -1), "D": (0, 1)}
DIRS = ("U", "D", "L", "R")            # blocked 채널 순서 (ch2..ch5)
ACTIONS = ("U", "D", "L", "R", "-")    # 모델 출력 5-way ("-"=STAY)
ACTION_IDX = {a: i for i, a in enumerate(ACTIONS)}
REVERSE = {"L": "R", "R": "L", "U": "D", "D": "U"}

# --- 패치 스펙 (변경 시 재학습 필수) ---
PATCH = 15                 # 패치 한 변 (홀수, 중심=현재 셀)
HALF = PATCH // 2
CH = 6                     # 0=walk, 1=observed, 2..5=blocked U/D/L/R
GOAL_CLAMP = 16            # 목표 Δ 클램프 (칸)
DIST_CLAMP = 32            # 목표 거리 클램프 (칸)
WALK_LOG_NORM = float(np.log1p(500.0))  # walk 정규화 상수 (셀 walk p99 실측 규모)
N_SCALAR = 8               # [gdx, gdy, dist, last_dir onehot(5)]

# 모델 파일명 (src/fsm/ 코드 옆 — cloud_uploader src 재귀 포함 + Nuitka include)
NAV_ONNX = "nav_policy.onnx"


def action_from_delta(dx: int, dy: int):
    """단위 스텝 델타 → 액션. 단위 스텝이 아니면 None (호출측이 분해/보간)."""
    if dx == 0 and dy == 0:
        return "-"
    for d, (mx, my) in DELTA.items():
        if (dx, dy) == (mx, my):
            return d
    return None


def _cell(cells: dict, x: int, y: int) -> dict:
    return cells.get(f"{x},{y}") or {}


def blocked_evidence(slot: dict, x: int, y: int, d: str) -> float:
    """(x,y)에서 d 방향 차단 증거율 0..1.

    blocked cnt(힐러 STUCK 3.5s 확정)는 3회에서 포화, attempts 막힘률
    (격수 시도 통계, try>=3)은 비율 그대로. 둘 중 큰 값.
    """
    k = f"{x},{y}"
    ev = 0.0
    cnt = (slot.get("blocked") or {}).get(k, {}).get(d, 0)
    if cnt:
        ev = min(1.0, float(cnt) / 3.0)
    a = (slot.get("attempts") or {}).get(k, {}).get(d)
    if a:
        t = int(a.get("try", 0))
        b = int(a.get("block", 0))
        if t >= 3:
            ev = max(ev, b / float(t))
    return ev


def encode_patch(slot: dict, cx: int, cy: int) -> np.ndarray:
    """맵 슬롯(cells/blocked/attempts) → (CH, PATCH, PATCH) float32.

    slot 은 MapGrid 런타임 슬롯과 maps/<맵>.json 로드본 둘 다 수용
    (키 구조 동일: cells/blocked/attempts).
    """
    out = np.zeros((CH, PATCH, PATCH), dtype=np.float32)
    cells = slot.get("cells") or {}
    for py in range(PATCH):
        wy = cy + (py - HALF)
        for px in range(PATCH):
            wx = cx + (px - HALF)
            c = _cell(cells, wx, wy)
            w = int(c.get("walk", 0))
            if w > 0:
                out[0, py, px] = min(1.0, float(np.log1p(w)) / WALK_LOG_NORM)
                out[1, py, px] = 1.0
            for di, d in enumerate(DIRS):
                ev = blocked_evidence(slot, wx, wy, d)
                if ev > 0.0:
                    out[2 + di, py, px] = ev
    return out


def encode_scalars(cur, goal, last_dir: str) -> np.ndarray:
    """(현재셀, 목표셀, 직전방향) → (N_SCALAR,) float32."""
    g = np.zeros(N_SCALAR, dtype=np.float32)
    dx = max(-GOAL_CLAMP, min(GOAL_CLAMP, goal[0] - cur[0]))
    dy = max(-GOAL_CLAMP, min(GOAL_CLAMP, goal[1] - cur[1]))
    g[0] = dx / float(GOAL_CLAMP)
    g[1] = dy / float(GOAL_CLAMP)
    dist = abs(goal[0] - cur[0]) + abs(goal[1] - cur[1])
    g[2] = min(dist, DIST_CLAMP) / float(DIST_CLAMP)
    ld = last_dir if last_dir in ACTION_IDX else "-"
    g[3 + ACTION_IDX[ld]] = 1.0
    return g
