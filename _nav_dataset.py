"""NavBrain 데이터셋 빌더 — logs_cloud TRAIL-PUSH → goal-conditioned BC 샘플.

경로 딥러닝 학습.md §3 정본 구현. 격수 발자국(TRAIL-PUSH, 힐러 로그에 기록)을
세션·맵 구간별 순서 보존 시퀀스로 추출해 hindsight goal 샘플로 인코딩한다.

파이프라인:
  ① logs_cloud/*.log md5 중복 제거 (동일 sid 이중 업로드 실측 7그룹)
  ② 'SESSION START' 배너로 세션 분리 (배너 없으면 파일=1세션)
  ③ [TRAIL-PUSH] 추출 → canon_map_name + Follower._is_valid_sunbi_map 필터
  ④ 세그먼트: 맵 변경 / 시간 gap>10s / 델타 맨해튼>6(OCR 노이즈 실측 1.6%) 분리
  ⑤ 스텝 분해: 단위스텝 / 한 축 2~3칸 보간 / 대각(각축<=2) 주축 우선 분해 / 그 외 분리
  ⑥ 샘플: 스텝당 hindsight 목표 2개(k in 3..8, 9..15 균등, seed=0) + STAY ~8%
  ⑦ 분할(세션 단위 누수 금지): 최신 세션들 >=15% 홀드아웃(원시 세그먼트만 저장),
     나머지 85/15 train/val. ⚠️같은 격수 방송을 여러 힐러가 받은 세션(W_h0/1/2 등,
     md5 로 안 잡힘)은 ts0+첫맵 그룹으로 묶어 같은 쪽에 배치 (누수 차단).
  ⑧ 출력: nav_dataset/train.npz, val.npz, holdout_trails.jsonl, meta.json

인코딩은 전부 nav_features 단일 정본 import (자체 구현 금지 — 좌표축 D=y+, U=y-).

  py _nav_dataset.py
"""
from __future__ import annotations

import hashlib
import io
import json
import pathlib
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "dist_dosa"))
from src.fsm.controller import Follower  # noqa: E402  맵명 구조검증 정본
from src.fsm.map_grid import canon_map_name  # noqa: E402  맵명 정규화 정본
from src.fsm.nav_features import (  # noqa: E402  인코딩 단일 정본 (D=y+, U=y-)
    ACTION_IDX, ACTIONS, CH, N_SCALAR, PATCH, action_from_delta, encode_patch,
    encode_scalars)

LOG_DIR = ROOT / "logs_cloud"
MAPS_DIR = ROOT / "maps_cloud"
OUT_DIR = ROOT / "nav_dataset"

# --- 파라미터 (경로 딥러닝 학습.md §3 — 전부 meta.json 에 기록) ---
GAP_S = 10.0        # 세그먼트 분리: push 간 시간 gap
NOISE_MANH = 6      # 세그먼트 분리: 델타 맨해튼 초과 = OCR 노이즈 (실측 1.6%)
K_NEAR = (3, 8)     # hindsight 목표 1 (가까운 목표)
K_FAR = (9, 15)     # hindsight 목표 2 (먼 목표)
STAY_P = 0.17       # 스텝당 STAY 샘플 확률 → 전체 샘플의 ~8% (스텝당 목표샘플 ~2개)
HOLDOUT_FRAC = 0.15  # 최신 세션 홀드아웃 (학습·val 완전 제외)
VAL_FRAC = 0.15      # 홀드아웃 제외분의 train/val 분리
SEED = 0
GROUP_TS_S = 90.0   # 다중 힐러가 같은 격수 방송 받은 세션 병합 창 (ts0 근접+같은 첫맵)

# TRAIL-PUSH 실측 라인 (참고: _backfill_maps.py RE_PUSH + 선두 23자 타임스탬프):
#   2026-07-04 13:08:13.655 DEBUG [TRAIL-PUSH] map='선비족입구' coord=(13, 21) idx=0 ...
RE_PUSH = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}).*?"
    r"\[TRAIL-PUSH\] map='([^']*)' coord=\((\d+),\s*(\d+)\)")
# 세션 배너 실측: '  SESSION START : 2026-07-04 13:08:12' (파일당 최대 1개 실측이나
# 병합 로그 대비 분리 로직 유지)
BANNER = "SESSION START"


def _md5(fp: pathlib.Path) -> str:
    h = hashlib.md5()
    with open(fp, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_ts(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def iter_sessions(fp: pathlib.Path):
    """파일 → (세션id, [(ts_dt, ts_str, map, x, y), ...]) 시퀀스.

    배너 라인마다 새 세션 시작. 맵명은 canon + 구조검증 통과분만.
    """
    sessions = [[]]
    with open(fp, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if BANNER in line:
                sessions.append([])
                continue
            if "[TRAIL-PUSH]" not in line:  # 정규식 전 빠른 컷
                continue
            m = RE_PUSH.match(line)
            if not m:
                continue
            ts = _parse_ts(m.group(1))
            if ts is None:
                continue
            name = canon_map_name(m.group(2))
            if not Follower._is_valid_sunbi_map(name):
                continue
            sessions[-1].append((ts, m.group(1), name, int(m.group(3)),
                                 int(m.group(4))))
    nonempty = [(i, p) for i, p in enumerate(sessions) if p]
    for i, pushes in nonempty:
        sid = fp.name if len(nonempty) == 1 else f"{fp.name}#s{i}"
        yield sid, pushes


def _decompose_pair(x0, y0, x1, y1):
    """연속 좌표쌍 델타 → 중간+끝 셀 리스트 (단위스텝 보장) 또는 None(④ 분리).

    ① 맨해튼1 → 그대로  ② 한 축 2~3칸 → 보간  ③ 대각(각축<=2) → 주축 먼저
    ④ 그 외 → None (호출측이 세그먼트 분리)
    """
    dx, dy = x1 - x0, y1 - y0
    adx, ady = abs(dx), abs(dy)
    if adx + ady == 1:
        return [(x1, y1)]
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    if dy == 0 and 2 <= adx <= 3:  # ② x축 보간
        return [(x0 + sx * i, y0) for i in range(1, adx + 1)]
    if dx == 0 and 2 <= ady <= 3:  # ② y축 보간
        return [(x0, y0 + sy * i) for i in range(1, ady + 1)]
    if dx != 0 and dy != 0 and adx <= 2 and ady <= 2:  # ③ 대각: 주축 먼저
        cells = []
        if adx >= ady:  # 주축 x (동률이면 x 우선)
            cells += [(x0 + sx * i, y0) for i in range(1, adx + 1)]
            cells += [(x1, y0 + sy * j) for j in range(1, ady + 1)]
        else:           # 주축 y
            cells += [(x0, y0 + sy * j) for j in range(1, ady + 1)]
            cells += [(x0 + sx * i, y1) for i in range(1, adx + 1)]
        return cells
    return None


def build_segments(pushes, stats):
    """세션 push 시퀀스 → 단위스텝 세그먼트 [{map, ts0, cells}].

    분리 조건: 맵 변경 / gap>GAP_S / 맨해튼>NOISE_MANH / ④ 분해불가.
    (0,0) 중복 push 는 dedup (분리 아님 — 이동 없는 재전송).
    """
    segs = []
    cur = None  # {"map", "ts0", "cells", "last_ts"}

    def close():
        nonlocal cur
        if cur is not None and len(cur["cells"]) >= 2:
            segs.append(cur)
        cur = None

    for ts, ts_str, name, x, y in pushes:
        if cur is not None:
            if name != cur["map"]:
                stats["brk_map"] += 1
                close()
            elif (ts - cur["last_ts"]).total_seconds() > GAP_S:
                stats["brk_gap"] += 1
                close()
        if cur is None:
            cur = {"map": name, "ts0": ts_str, "cells": [(x, y)],
                   "last_ts": ts}
            continue
        px, py = cur["cells"][-1]
        dx, dy = x - px, y - py
        if dx == 0 and dy == 0:
            stats["dedup_zero"] += 1
            cur["last_ts"] = ts
            continue
        if abs(dx) + abs(dy) > NOISE_MANH:
            stats["brk_noise"] += 1
            close()
            cur = {"map": name, "ts0": ts_str, "cells": [(x, y)],
                   "last_ts": ts}
            continue
        mid = _decompose_pair(px, py, x, y)
        if mid is None:  # ④ 분해 불가 (대각 3칸축 등)
            stats["brk_decomp"] += 1
            close()
            cur = {"map": name, "ts0": ts_str, "cells": [(x, y)],
                   "last_ts": ts}
            continue
        if len(mid) > 1:
            stats["interp_steps"] += len(mid) - 1
        cur["cells"].extend(mid)
        cur["last_ts"] = ts
    close()
    for s in segs:
        del s["last_ts"]
    return segs


def plan_samples(segments, rng, stats):
    """세그먼트들 → 샘플 계획 [(map, s_cell, goal_cell, last_dir, label)].

    스텝 t: 목표 2개(k in K_NEAR/K_FAR 각 균등 1개, 세그 끝 clamp),
    goal==s_t 스킵. 추가 STAY_P 확률로 goal=s_t 라벨 '-' 샘플.
    """
    out = []
    for seg in segments:
        cells = seg["cells"]
        m = seg["map"]
        n = len(cells) - 1  # 스텝 수
        acts = [action_from_delta(cells[i + 1][0] - cells[i][0],
                                  cells[i + 1][1] - cells[i][1])
                for i in range(n)]
        assert all(a in ACTION_IDX and a != "-" for a in acts), \
            f"단위스텝 위반: {m} {cells[:5]}"
        for t in range(n):
            last = acts[t - 1] if t > 0 else "-"
            for lo, hi in (K_NEAR, K_FAR):
                gi = min(t + rng.randint(lo, hi), n)
                goal = cells[gi]
                if goal == cells[t]:
                    stats["goal_eq_skip"] += 1
                    continue
                out.append((m, cells[t], goal, last, acts[t]))
            if rng.random() < STAY_P:
                out.append((m, cells[t], cells[t], last, "-"))
        stats["steps"] += n
    return out


def load_map_slots():
    """maps_cloud/*.json → {canon 맵명: slot dict} (encode_patch 직접 전달용)."""
    slots = {}
    for fp in sorted(MAPS_DIR.glob("*.json")):
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        name = canon_map_name(d.get("map", ""))
        if not name:
            continue
        # 같은 canon 명 충돌 시 관측 셀 많은 쪽 유지
        old = slots.get(name)
        if old is None or len(d.get("cells") or {}) > len(old.get("cells") or {}):
            slots[name] = d
    return slots


def encode_split(samples, slots, patch_cache, missing_maps, tag):
    """샘플 계획 → (X uint8, G float32, Y uint8). 패치는 (map,x,y) 캐시."""
    t0 = time.time()
    X, G, Y = [], [], []
    for i, (m, s, g, last, a) in enumerate(samples):
        key = (m, s[0], s[1])
        p = patch_cache.get(key)
        if p is None:
            slot = slots.get(m)
            if slot is None:
                missing_maps[m] += 1
                slot = {}
            # float 0..1 → x255 반올림 uint8 (스펙 §7)
            p = np.rint(encode_patch(slot, s[0], s[1]) * 255.0).astype(np.uint8)
            patch_cache[key] = p
        X.append(p)
        G.append(encode_scalars(s, g, last))
        Y.append(ACTION_IDX[a])
        if (i + 1) % 20000 == 0:
            print(f"  [{tag}] 인코딩 {i + 1}/{len(samples)} "
                  f"({time.time() - t0:.0f}s)")
    if not X:
        return (np.zeros((0, CH, PATCH, PATCH), np.uint8),
                np.zeros((0, N_SCALAR), np.float32), np.zeros((0,), np.uint8))
    return (np.stack(X).astype(np.uint8),
            np.stack(G).astype(np.float32),
            np.asarray(Y, dtype=np.uint8))


def main():
    t_start = time.time()
    OUT_DIR.mkdir(exist_ok=True)
    rng_goal = random.Random(SEED)
    rng_split = random.Random(SEED)

    # ① md5 중복 제거 -------------------------------------------------------
    all_logs = sorted(LOG_DIR.glob("*.log"))
    by_md5 = {}
    for fp in all_logs:
        by_md5.setdefault(_md5(fp), []).append(fp)
    uniq_files = [v[0] for v in by_md5.values()]  # 그룹당 첫 파일(이름순) 유지
    dup_groups = sum(1 for v in by_md5.values() if len(v) > 1)
    print(f"[1/5] 로그 {len(all_logs)}개 → md5 유니크 {len(uniq_files)}개 "
          f"(중복 {dup_groups}그룹)")

    # ②③④⑤⑥ 세션 파싱 → 세그먼트 → 샘플 계획 -----------------------------
    stats = Counter()
    sessions = []  # {"sid","ts0","first_map","segments","samples"}
    for fi, fp in enumerate(sorted(uniq_files, key=lambda p: p.name)):
        for sid, pushes in iter_sessions(fp):
            stats["push_lines"] += len(pushes)
            segs = build_segments(pushes, stats)
            if not segs:
                continue
            samples = plan_samples(segs, rng_goal, stats)
            sessions.append({
                "sid": sid,
                "ts0": pushes[0][0],
                "first_map": pushes[0][2],
                "segments": segs,
                "samples": samples,
            })
        if (fi + 1) % 40 == 0:
            print(f"  파싱 {fi + 1}/{len(uniq_files)} 파일 "
                  f"({time.time() - t_start:.0f}s)")
    n_seg_total = sum(len(s["segments"]) for s in sessions)
    n_sample_total = sum(len(s["samples"]) for s in sessions)
    print(f"[2/5] 세션 {len(sessions)}개 / 세그먼트 {n_seg_total}개 / "
          f"스텝 {stats['steps']}개 / 샘플 계획 {n_sample_total}개")

    # ⑦-a 방송 중복 그룹핑 (md5 로 안 잡히는 다중 힐러 동일 세션 → 같은 쪽) --
    groups = []  # {"members":[session], "first_map", "ts_last", "n_samples"}
    for s in sorted(sessions, key=lambda s: s["ts0"]):
        g = groups[-1] if groups else None
        if (g is not None and s["first_map"] == g["first_map"]
                and (s["ts0"] - g["ts_last"]).total_seconds() <= GROUP_TS_S):
            g["members"].append(s)
            g["ts_last"] = max(g["ts_last"], s["ts0"])
            g["n_samples"] += len(s["samples"])
        else:
            groups.append({"members": [s], "first_map": s["first_map"],
                           "ts_last": s["ts0"],
                           "n_samples": len(s["samples"])})
    n_multi = sum(1 for g in groups if len(g["members"]) > 1)
    print(f"[3/5] 세션 그룹 {len(groups)}개 (다중힐러 병합 {n_multi}그룹)")

    # ⑦-b 홀드아웃: 최신 그룹부터 샘플 >=15% ---------------------------------
    holdout, rest = [], []
    target_ho = n_sample_total * HOLDOUT_FRAC
    acc = 0
    for g in sorted(groups, key=lambda g: g["ts_last"], reverse=True):
        if acc < target_ho:
            holdout.append(g)
            acc += g["n_samples"]
        else:
            rest.append(g)
    # ⑦-c 나머지 85/15 train/val (그룹=세션 단위, seed 고정 셔플) ------------
    rng_split.shuffle(rest)
    rest_total = sum(g["n_samples"] for g in rest)
    target_val = rest_total * VAL_FRAC
    val_g, train_g = [], []
    v_acc = 0
    for g in rest:
        if v_acc < target_val:
            val_g.append(g)
            v_acc += g["n_samples"]
        else:
            train_g.append(g)

    def _flat(gs, key):
        return [x for g in gs for s in g["members"] for x in s[key]]

    train_samples = _flat(train_g, "samples")
    val_samples = _flat(val_g, "samples")
    ho_sessions = [s for g in holdout for s in g["members"]]
    print(f"      train {len(train_samples)} / val {len(val_samples)} / "
          f"holdout {acc} 샘플상당 (목표 ho>={target_ho:.0f})")

    # ⑧ 인코딩 + 저장 --------------------------------------------------------
    slots = load_map_slots()
    print(f"[4/5] maps_cloud 슬롯 {len(slots)}개 로드. 인코딩 시작")
    patch_cache = {}
    missing_maps = Counter()
    Xt, Gt, Yt = encode_split(train_samples, slots, patch_cache,
                              missing_maps, "train")
    Xv, Gv, Yv = encode_split(val_samples, slots, patch_cache,
                              missing_maps, "val")
    np.savez_compressed(OUT_DIR / "train.npz", X=Xt, G=Gt, Y=Yt)
    np.savez_compressed(OUT_DIR / "val.npz", X=Xv, G=Gv, Y=Yv)

    # 홀드아웃: 인코딩 없이 원시 세그먼트(단위스텝 분해 후 셀열)만 저장
    with open(OUT_DIR / "holdout_trails.jsonl", "w", encoding="utf-8") as f:
        for s in ho_sessions:
            for seg in s["segments"]:
                f.write(json.dumps(
                    {"session": s["sid"], "map": seg["map"],
                     "ts0": seg["ts0"],
                     "steps": [list(c) for c in seg["cells"]]},
                    ensure_ascii=False) + "\n")

    def _dist(Y):
        c = Counter(int(y) for y in Y)
        return {a: c.get(i, 0) for i, a in enumerate(ACTIONS)}

    def _maps(samples):
        return sorted({m for m, *_ in samples})

    meta = {
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params": {
            "seed": SEED, "gap_s": GAP_S, "noise_manhattan": NOISE_MANH,
            "k_near": list(K_NEAR), "k_far": list(K_FAR), "stay_p": STAY_P,
            "holdout_frac": HOLDOUT_FRAC, "val_frac": VAL_FRAC,
            "group_ts_s": GROUP_TS_S, "patch": PATCH, "ch": CH,
            "n_scalar": N_SCALAR,
            "holdout_steps_note": "steps=단위스텝 분해 후 셀열 (연속쌍=단위스텝)",
        },
        "logs": {"total_files": len(all_logs),
                 "unique_files": len(uniq_files),
                 "md5_dup_groups": dup_groups},
        "parse": {k: stats[k] for k in sorted(stats)},
        "sessions": {"total": len(sessions), "groups": len(groups),
                     "multi_healer_groups": n_multi},
        "split": {
            "train": {"samples": len(train_samples),
                      "sessions": sum(len(g["members"]) for g in train_g),
                      "segments": sum(len(s["segments"]) for g in train_g
                                      for s in g["members"]),
                      "class_dist": _dist(Yt),
                      "maps": len(_maps(train_samples))},
            "val": {"samples": len(val_samples),
                    "sessions": sum(len(g["members"]) for g in val_g),
                    "segments": sum(len(s["segments"]) for g in val_g
                                    for s in g["members"]),
                    "class_dist": _dist(Yv),
                    "maps": len(_maps(val_samples))},
            "holdout": {"planned_samples": acc,
                        "sessions": len(ho_sessions),
                        "segments": sum(len(s["segments"])
                                        for s in ho_sessions),
                        "session_ids": sorted(s["sid"] for s in ho_sessions)},
        },
        "maps_cloud_slots": len(slots),
        "maps_missing_in_maps_cloud": dict(missing_maps),
        "unique_patches_cached": len(patch_cache),
    }
    with open(OUT_DIR / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print(f"[5/5] 저장 완료 → {OUT_DIR}  (총 {time.time() - t_start:.0f}s)")
    print(json.dumps(meta["split"], ensure_ascii=False, indent=1,
                     default=str)[:2000])
    print("train class:", _dist(Yt))
    print("val   class:", _dist(Yv))


if __name__ == "__main__":
    # Windows 콘솔 한글 출력 보정
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    main()
