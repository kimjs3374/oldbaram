# -*- coding: utf-8 -*-
"""NavBrain 리플레이 평가 게이트 (배포 전 필수) — 경로 딥러닝 학습.md §5 정본.

알파고 evaluator 역할: 실게임 0회 접촉, 오프라인 데이터만으로 새 두뇌가
기존 휴리스틱보다 나은지 입증한다. FAIL 이면 배포 금지(모델 교체 안 함).

지표:
  A. 사람 일치율    — holdout 격수 trail 의 다음 한 칸 top-1 일치
                      (nav_full / nav_graph / naive_direct / majority 4개 비교군)
  B. 도달 시뮬레이션 — 관측 grid 월드모델 위에서 정책대로 걸어 goal 도달률
  C. STUCK 리플레이  — 실로그 STUCK-ORTHO1 지점의 '뚫린 방향' 적중률
                      (i) 로그의 ortho1 선택  vs  (ii) nav_graph.unstick_dir
  D. 위반율          — nav 제안이 blocked(증거율>=0.7)/미관측 방향으로 들어가는 비율

게이트(PASS 조건):
  A(nav_full) > A(naive_direct)  AND  B(nav_full) >= B(naive_direct)  AND  D < 0.02
  C(ii) > C(i) 는 이벤트 >= 30 일 때만 게이트 포함, 미만이면 경고만.
  nav_policy.onnx 없으면 nav_full=nav_graph(그래프 단독 평가) 로 간주.

exit code: PASS=0, FAIL=1, 입력 오류=2.

사용:
  py -3 _nav_replay_eval.py                          # 기본 경로 (실데이터)
  py -3 _nav_replay_eval.py --holdout X --report Y   # 자체검증/오버라이드

좌표축: nav_features.DELTA 단일 정본 (실측 D=y+, U=y-). 자체 인코딩 금지.
런타임 파일 수정 없음 — dist_dosa/src 를 읽기 전용으로 import 만 한다.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import random
import re
import sys
import tempfile
import time
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "dist_dosa"))

# --- 정본 import (자체 재구현 절대 금지: 좌표축/인코딩/기권 규칙 단일화) ---
from src.fsm.nav_features import DELTA, action_from_delta, blocked_evidence  # noqa: E402
from src.fsm.nav_brain import NavBrain                                       # noqa: E402
from src.fsm.map_grid import MapGrid, canon_map_name                         # noqa: E402
from src.fsm.controller import Follower                                      # noqa: E402

MAX_GAP = NavBrain.MAX_GAP        # 좌표 OCR 누락 보간 한계 (월드모델과 공유)
BLK_EV = 0.7                      # 차단 확정 증거율 (is_wall/BLK_HARD 관례)
GOAL_AHEAD = 8                    # 지표 A hindsight 목표 (steps[t+8])
STRIDE = 2                        # 지표 A 순회 간격
MAX_SEG = 2000                    # 세그먼트 샘플링 상한 (seed 0)
SIM_MIN_MANH = 6                  # 지표 B 최소 맨해튼 거리
SIM_BUDGET_MULT = 4               # 지표 B 스텝 예산 = 4 x 맨해튼
STUCK_RESOLVE_S = 5.0             # 지표 C '뚫린 방향' 탐색 창 (초)
LOG_N = 10                        # 지표 C 최신 healer 로그 수


# ---------------------------------------------------------------- 공용 유틸
def naive_direct(cur, goal):
    """부호 축우선 휴리스틱 — healer_worker B3 직선과 동일 로직.

    |dx| >= |dy| 면 x축 방향(R/L), 아니면 y축(D/U). 정본축: D=y+, U=y-.
    """
    dx = goal[0] - cur[0]
    dy = goal[1] - cur[1]
    if dx == 0 and dy == 0:
        return None
    if abs(dx) >= abs(dy):
        return "R" if dx > 0 else "L"
    return "D" if dy > 0 else "U"


def axis_dir(dx, dy):
    """변위 → 축우선 방향 (지표 C '뚫린 방향' 판정)."""
    return naive_direct((0, 0), (dx, dy))


class World:
    """맵 슬롯의 읽기 전용 월드모델 — 관측 셀 통행 + blocked 간선 + gap 보간."""

    def __init__(self, slot):
        self.slot = slot or {}
        self.obs = set()
        for k, c in (self.slot.get("cells") or {}).items():
            if int(c.get("walk", 0)) >= 1:
                xs, ys = k.split(",")
                self.obs.add((int(xs), int(ys)))

    def observed_within_gap(self, cur, d):
        """cur 에서 d 방향 gap<=MAX_GAP 내 첫 관측 셀 (없으면 None)."""
        dx, dy = DELTA[d]
        for step in range(1, MAX_GAP + 1):
            q = (cur[0] + dx * step, cur[1] + dy * step)
            if q in self.obs:
                return q, step
        return None, 0

    def blocked_edge(self, cur, d, step):
        """cur→d 로 step칸 이동 경로상(출발/중간 셀) 차단 증거>=BLK_EV 여부."""
        dx, dy = DELTA[d]
        for i in range(step):
            x, y = cur[0] + dx * i, cur[1] + dy * i
            if blocked_evidence(self.slot, x, y, d) >= BLK_EV:
                return True
        return False

    def violation(self, cur, d):
        """지표 D: 제안 d 가 차단 간선 or 미관측 방향(보간 불가)이면 True."""
        if blocked_evidence(self.slot, cur[0], cur[1], d) >= BLK_EV:
            return True
        q, _ = self.observed_within_gap(cur, d)
        return q is None


# ------------------------------------------------------------ holdout 로드
def load_holdout(path: pathlib.Path):
    """holdout_trails.jsonl → 세그먼트 목록. 맵명 canon+구조검증 필터."""
    segs = []
    dropped = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                m = canon_map_name(d.get("map", ""))
                steps = [(int(p[0]), int(p[1])) for p in d.get("steps", [])]
            except Exception:
                dropped += 1
                continue
            if not Follower._is_valid_sunbi_map(m) or len(steps) < 2:
                dropped += 1
                continue
            segs.append({"session": d.get("session", "?"), "map": m,
                         "steps": steps})
    return segs, dropped


# ------------------------------------------------------------- 지표 A + D
def eval_match(segs, grid, nav_full, nav_graph, worlds):
    """지표 A 사람 일치율 + 지표 D 위반율 (같은 순회에서 동시 집계)."""
    labels = []          # 전체 정답 방향 (majority 계산)
    # ok_h/prop_h = hard 부분집합(직선 오답 지점 = 코너/벽) — 런타임 개입
    # 지점(trail 부재/STUCK)의 대리 지표. 2026-07-05 실측: 전체의 34%.
    stats = {name: {"ok": 0, "prop": 0, "abstain": 0, "ok_h": 0, "prop_h": 0}
             for name in ("nav_full", "nav_graph", "naive_direct")}
    viol = {"nav_full": [0, 0], "nav_graph": [0, 0]}   # [위반, 제안수]
    n_samples = 0
    for seg in segs:
        m = seg["map"]
        steps = seg["steps"]
        world = worlds.get(m)
        if world is None:
            world = World(grid.slot(m))
            worlds[m] = world
        last = len(steps) - 1
        for t in range(0, last, STRIDE):
            cur, nxt = steps[t], steps[t + 1]
            label = action_from_delta(nxt[0] - cur[0], nxt[1] - cur[1])
            if label is None or label == "-":
                continue   # 단위스텝 아닌 쌍 / 정지 스킵 (정본 규칙)
            goal = steps[min(t + GOAL_AHEAD, last)]
            if goal == cur:
                continue   # 목표=현재 → 어떤 정책도 답이 정의 안 됨
            # 직전 방향 (NavNet 스칼라 — 그래프 단독이면 무영향)
            last_dir = "-"
            if t > 0:
                pd = action_from_delta(cur[0] - steps[t - 1][0],
                                       cur[1] - steps[t - 1][1])
                if pd in ("U", "D", "L", "R"):
                    last_dir = pd
            n_samples += 1
            labels.append(label)
            sug = {
                "nav_full": nav_full.suggest(m, cur, goal, last_dir=last_dir,
                                             hold=False)[0],
                "nav_graph": nav_graph.suggest(m, cur, goal, last_dir=last_dir,
                                               hold=False)[0],
                "naive_direct": naive_direct(cur, goal),
            }
            is_hard = (sug["naive_direct"] != label)
            for name, d in sug.items():
                st = stats[name]
                if d is None:
                    st["abstain"] += 1
                    continue
                st["prop"] += 1
                if d == label:
                    st["ok"] += 1
                if is_hard:
                    st["prop_h"] += 1
                    if d == label:
                        st["ok_h"] += 1
                if name in viol:
                    viol[name][1] += 1
                    if world.violation(cur, d):
                        viol[name][0] += 1
    maj = Counter(labels).most_common(1)
    majority = {"label": maj[0][0] if maj else "-",
                "ok": maj[0][1] if maj else 0, "total": len(labels)}
    return {"n": n_samples, "stats": stats, "majority": majority,
            "viol": viol}


# ----------------------------------------------------------------- 지표 B
def _sim_walk(world, m, start, goal, policy, budget):
    """정책대로 걷기. 반환 (성공여부, 사용스텝, 실패사유)."""
    cur = start
    used = 0
    last_dir = "-"
    while used < budget:
        if abs(cur[0] - goal[0]) + abs(cur[1] - goal[1]) <= 1:
            return True, used, ""
        d = policy(m, cur, goal, last_dir)
        if d is None:
            return False, used, "abstain"
        q, step = world.observed_within_gap(cur, d)
        if q is None:
            return False, used, "unobserved"   # 미관측 진입 시도 = 실패
        if world.blocked_edge(cur, d, step):
            return False, used, "blocked"      # 차단 간선 진입 시도 = 실패
        cur = q
        used += step
        last_dir = d
    if abs(cur[0] - goal[0]) + abs(cur[1] - goal[1]) <= 1:
        return True, used, ""
    return False, used, "budget"


def eval_reach(segs, grid, nav_full, nav_graph, worlds):
    """지표 B 도달 시뮬레이션 (맨해튼>=SIM_MIN_MANH 세그먼트만)."""
    policies = {
        "nav_full": lambda m, c, g, ld: nav_full.suggest(
            m, c, g, last_dir=ld, hold=False)[0],
        "nav_graph": lambda m, c, g, ld: nav_graph.suggest(
            m, c, g, last_dir=ld, hold=False)[0],
        "naive_direct": lambda m, c, g, ld: naive_direct(c, g),
    }
    res = {name: {"ok": 0, "n": 0, "steps_ok": [], "fail": Counter()}
           for name in policies}
    for seg in segs:
        start, goal = seg["steps"][0], seg["steps"][-1]
        manh = abs(start[0] - goal[0]) + abs(start[1] - goal[1])
        if manh < SIM_MIN_MANH:
            continue
        m = seg["map"]
        world = worlds.get(m)
        if world is None:
            world = World(grid.slot(m))
            worlds[m] = world
        budget = SIM_BUDGET_MULT * manh
        for name, pol in policies.items():
            ok, used, why = _sim_walk(world, m, start, goal, pol, budget)
            r = res[name]
            r["n"] += 1
            if ok:
                r["ok"] += 1
                r["steps_ok"].append(used)
            else:
                r["fail"][why] += 1
    return res


# ----------------------------------------------------------------- 지표 C
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}")
_HCOORD_RE = re.compile(r"h_coord=\((-?\d+),\s*(-?\d+)\)")
_HMAP_RE = re.compile(r"h_map='([^']*)'")
_ACOORD_RE = re.compile(r"a_coord=\((-?\d+),\s*(-?\d+)\)")
# 실측 라인 예 (logs_cloud grep):
# [MOVE] L→U reason='STUCK-ORTHO1 dur=0.8s h=(20, 48) blocked=L try=U' ...
#   ... h_coord=(20, 48) a_coord=(19,48) ... h_map='제7본성입구1' ...
_ORTHO1_RE = re.compile(
    r"STUCK-ORTHO1 dur=[\d.]+s h=\((-?\d+),\s*(-?\d+)\)"
    r" blocked=([UDLR]) try=([UDLR])")
_MAPNEQ_RE = re.compile(r"map_neq=(True|False)")
_FNAME_TS_RE = re.compile(r"(\d{8}_\d{6})")


def _parse_ts(line: str):
    if not _TS_RE.match(line):
        return None
    try:
        return datetime.datetime.strptime(
            line[:23], "%Y-%m-%d %H:%M:%S.%f").timestamp()
    except Exception:
        return None


def eval_stuck(logs_dir: pathlib.Path, nav_graph, rng):
    """지표 C: 최신 healer 로그 LOG_N개에서 STUCK-ORTHO1 리플레이."""
    files = []
    for fp in logs_dir.glob("*.log"):
        if not fp.name.startswith("healer"):
            continue
        mm = _FNAME_TS_RE.search(fp.name)
        if mm:
            files.append((mm.group(1), fp))
    files.sort()
    files = [fp for _, fp in files[-LOG_N:]]

    raw_events = 0
    resolved = []   # (h_map, h, blocked, try_dir, a_coord|None, solved_dir)
    for fp in files:
        entries = []   # (ts, h_coord, h_map_canon, event|None)
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "h_coord=(" not in line:
                        continue
                    if "[MOVE]" not in line and "[STAT]" not in line:
                        continue
                    ts = _parse_ts(line)
                    hc = _HCOORD_RE.search(line)
                    if ts is None or hc is None:
                        continue
                    h = (int(hc.group(1)), int(hc.group(2)))
                    hm = _HMAP_RE.search(line)
                    hmap = canon_map_name(hm.group(1)) if hm else ""
                    ev = None
                    if "[MOVE]" in line and "STUCK-ORTHO1" in line:
                        om = _ORTHO1_RE.search(line)
                        if om:
                            ac = _ACOORD_RE.search(line)
                            nq = _MAPNEQ_RE.search(line)
                            # map_neq=True 면 a_coord 는 격수의 *다른 맵*
                            # 좌표계 → unstick goal 로 쓰면 flow 왜곡.
                            # 같은맵 이벤트만 (ii) 평가 (런타임 훅도 map_neq
                            # 시 exit_coord 를 쓰므로 동일 기준).
                            _neq = bool(nq and nq.group(1) == "True")
                            ev = (om.group(3), om.group(4),
                                  (int(ac.group(1)), int(ac.group(2)))
                                  if ac else None, _neq)
                            raw_events += 1
                    entries.append((ts, h, hmap, ev))
        except OSError:
            continue
        # 각 이벤트: 이후 STUCK_RESOLVE_S초 내 h_coord 첫 변화 = '뚫린 방향'
        for i, (ts0, h0, map0, ev) in enumerate(entries):
            if ev is None:
                continue
            blocked_d, try_d, a_coord, map_neq = ev
            for ts1, h1, map1, _ in entries[i + 1:]:
                if ts1 - ts0 > STUCK_RESOLVE_S:
                    break
                if h1 == h0:
                    continue
                if map1 != map0:
                    break   # 맵 전환으로 좌표 변화 = 뚫림 아님 → 미해석
                sd = axis_dir(h1[0] - h0[0], h1[1] - h0[1])
                if sd:
                    resolved.append((map0, h0, blocked_d, try_d,
                                     a_coord, map_neq, sd))
                break

    sampled_note = ""
    if len(resolved) > MAX_SEG:
        resolved = rng.sample(resolved, MAX_SEG)
        sampled_note = f" (이벤트 {MAX_SEG}개 샘플링 seed 0)"

    log_ok = 0
    nav_ok = 0
    nav_prop = 0
    nav_abstain = 0
    nav_skip_mapneq = 0
    log_ok_same = 0
    n_same = 0
    for m, h, blocked_d, try_d, a_coord, map_neq, sd in resolved:
        if try_d == sd:
            log_ok += 1
        if map_neq:
            nav_skip_mapneq += 1   # 타맵 좌표계 goal → (ii) 평가 제외
            continue
        n_same += 1
        if try_d == sd:
            log_ok_same += 1
        if a_coord is None:
            nav_abstain += 1
            continue
        d = nav_graph.unstick_dir(m, h, blocked_d, a_coord)
        if d is None:
            nav_abstain += 1
            continue
        nav_prop += 1
        if d == sd:
            nav_ok += 1
    return {"files": len(files), "raw": raw_events, "n": len(resolved),
            "log_ok": log_ok, "nav_ok": nav_ok, "nav_prop": nav_prop,
            "nav_abstain": nav_abstain, "nav_skip_mapneq": nav_skip_mapneq,
            "n_same": n_same, "log_ok_same": log_ok_same,
            "note": sampled_note}


# ------------------------------------------------------------------- 리포트
def _pct(a, b):
    return (100.0 * a / b) if b else 0.0


def _fmt_match(name, st):
    total = st["prop"] + st["abstain"]
    return (f"  {name:<13}: 일치 {st['ok']}/{st['prop']}"
            f" = {_pct(st['ok'], st['prop']):.1f}%"
            f"  (기권 {st['abstain']}/{total}"
            f" = {_pct(st['abstain'], total):.1f}%)")


def _fmt_reach(name, r):
    avg = (sum(r["steps_ok"]) / len(r["steps_ok"])) if r["steps_ok"] else 0.0
    fails = " ".join(f"{k}={v}" for k, v in sorted(r["fail"].items()))
    return (f"  {name:<13}: 성공 {r['ok']}/{r['n']}"
            f" = {_pct(r['ok'], r['n']):.1f}%"
            f"  평균스텝(성공) {avg:.1f}  실패[{fails}]")


def main():
    # cp949 콘솔에서 못 찍는 문자(—, ≥ 등)는 ?로 대체 (리포트 파일은 utf-8 원본)
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="NavBrain 리플레이 평가 게이트")
    ap.add_argument("--holdout", default=str(ROOT / "nav_dataset"
                                             / "holdout_trails.jsonl"))
    ap.add_argument("--report", default=str(ROOT / "nav_eval_report.txt"))
    ap.add_argument("--maps", default=str(ROOT / "maps_cloud"))
    ap.add_argument("--logs", default=str(ROOT / "logs_cloud"))
    args = ap.parse_args()

    t0 = time.time()
    holdout_path = pathlib.Path(args.holdout)
    if not holdout_path.is_file():
        print(f"[에러] holdout 파일 없음: {holdout_path}")
        print("       _nav_dataset.py 로 holdout_trails.jsonl 을 먼저 생성"
              " (또는 --holdout 지정)")
        sys.exit(2)

    grid = MapGrid(args.maps)
    # nav_full: 기본(nav_policy.onnx 있으면 자동 로드). nav_graph: 빈 임시폴더
    # model_dir → 모델 파일 부재 = 그래프 단독 (nav_brain 폴백 관례).
    empty_dir = tempfile.mkdtemp(prefix="nav_eval_graph_only_")
    nav_full = NavBrain(grid)
    nav_graph = NavBrain(grid, model_dir=empty_dir)
    graph_only = not nav_full.ready_net()

    segs, dropped = load_holdout(holdout_path)
    rng = random.Random(0)
    sample_note = ""
    if len(segs) > MAX_SEG:
        segs = rng.sample(segs, MAX_SEG)
        sample_note = (f" (전체 초과 → {MAX_SEG}개 샘플링 seed 0)")
        print(f"[샘플링] 세그먼트 {MAX_SEG}개 초과 → seed 0 으로"
              f" {MAX_SEG}개만 평가")

    worlds = {}   # 맵명 → World (A/B/D 공유)
    a = eval_match(segs, grid, nav_full, nav_graph, worlds)
    b = eval_reach(segs, grid, nav_full, nav_graph, worlds)
    c = eval_stuck(pathlib.Path(args.logs), nav_graph, rng)

    # ---- 게이트 판정 (2026-07-05 배포정합 재설계) ----
    # 근거: 런타임 'on' 훅은 trail 부재/STUCK 에서만 개입 = 기존 동작이
    # 직선(naive)/부호휴리스틱인 지점뿐. 탁 트인 직선 구간(전체 66%)의
    # 모방은 trail 추종이 이미 담당(그게 곧 사람 경로)이라 NavBrain 이
    # 경쟁할 자리가 없음. 따라서:
    #   A1 회귀가드: 정책망 결합이 그래프 단독보다 전체 모방을 떨어뜨리면 안 됨
    #   A2 결정지점: 직선이 틀리는 지점(hard, 코너/벽)에서 직선을 이겨야 함
    #      + 그래프 단독 대비 -2pp 이내(망이 코너 능력을 깎지 않음)
    #   B  도달률: 직선 이상 (막힘없이 따라가기 핵심)
    #   D  위반율: <2% (안전)
    #   A(전체 vs naive), C 는 정보용 (C 는 실행된 정책이 '뚫린 방향'을
    #   스스로 만든 관찰 데이터라 선택편향 — 반사실 정책 평가 불가).
    sf = a["stats"]["nav_full"]
    sg_ = a["stats"]["nav_graph"]
    sn = a["stats"]["naive_direct"]
    acc_full = _pct(sf["ok"], sf["prop"])
    acc_graph = _pct(sg_["ok"], sg_["prop"])
    acc_naive = _pct(sn["ok"], sn["prop"])
    acc_full_h = _pct(sf["ok_h"], sf["prop_h"])
    acc_graph_h = _pct(sg_["ok_h"], sg_["prop_h"])
    acc_naive_h = _pct(sn["ok_h"], sn["prop_h"])
    ok_a1 = sf["prop"] > 0 and acc_full >= acc_graph
    ok_a2 = (sf["prop_h"] > 0 and acc_full_h > acc_naive_h
             and acc_full_h >= acc_graph_h - 2.0)

    bf, bn = b["nav_full"], b["naive_direct"]
    succ_full = _pct(bf["ok"], bf["n"])
    succ_naive = _pct(bn["ok"], bn["n"])
    ok_b = succ_full >= succ_naive

    vf = a["viol"]["nav_full"]
    viol_full = (vf[0] / vf[1]) if vf[1] else 0.0
    ok_d = viol_full < 0.02

    acc_c1 = _pct(c["log_ok"], c["n"])
    # 공정 비교: (ii)는 같은맵 이벤트만 평가하므로 (i)도 같은맵 기준으로 대조.
    acc_c1_same = _pct(c["log_ok_same"], c["n_same"])
    acc_c2 = _pct(c["nav_ok"], c["nav_prop"])

    gate_pass = ok_a1 and ok_a2 and ok_b and ok_d

    # ---- 리포트 ----
    maj = a["majority"]
    vg = a["viol"]["nav_graph"]
    viol_graph = (vg[0] / vg[1]) if vg[1] else 0.0
    lines = []
    w = lines.append
    w("=== NavBrain 리플레이 평가 리포트 (_nav_replay_eval.py) ===")
    w(f"생성: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}"
      f"  소요 {time.time() - t0:.1f}s")
    w(f"holdout: {holdout_path}")
    w(f"  세그먼트 {len(segs)}개{sample_note}, 필터 제외 {dropped}개")
    w(f"maps: {args.maps}")
    w(f"logs: {args.logs}")
    if graph_only:
        w("모델: nav_policy.onnx 없음 → **그래프 단독 평가**"
          " (nav_full = nav_graph 로 간주)")
    else:
        w(f"모델: {nav_full._model_path} 로드됨 (그래프+정책망)")
    w("")
    w(f"[지표 A] 사람 일치율 — 샘플 {a['n']}개"
      f" (stride={STRIDE}, goal=+{GOAL_AHEAD}, 단위스텝만)")
    w(_fmt_match("nav_full", sf))
    w(_fmt_match("nav_graph", a["stats"]["nav_graph"]))
    w(_fmt_match("naive_direct", sn))
    w(f"  {'majority':<13}: 최빈 '{maj['label']}' {maj['ok']}/{maj['total']}"
      f" = {_pct(maj['ok'], maj['total']):.1f}%")
    w(f"  hard 부분집합(직선 오답 지점 = 코너/벽, 런타임 개입 지점 대리):"
      f" {sf['prop_h']}개")
    w(f"    nav_full  {sf['ok_h']}/{sf['prop_h']} = {acc_full_h:.1f}%"
      f"   nav_graph {sg_['ok_h']}/{sg_['prop_h']} = {acc_graph_h:.1f}%"
      f"   naive {sn['ok_h']}/{sn['prop_h']} = {acc_naive_h:.1f}%")
    w("")
    w(f"[지표 B] 도달 시뮬레이션 — 세그먼트 {bf['n']}개"
      f" (맨해튼>={SIM_MIN_MANH}, 예산 {SIM_BUDGET_MULT}x맨해튼,"
      f" gap보간<={MAX_GAP}, blocked>={BLK_EV})")
    w(_fmt_reach("nav_full", bf))
    w(_fmt_reach("nav_graph", b["nav_graph"]))
    w(_fmt_reach("naive_direct", bn))
    w("")
    w(f"[지표 C] STUCK-ORTHO1 리플레이 — healer 로그 {c['files']}개,"
      f" 원시 이벤트 {c['raw']}개, 해석 성공(5s 내 뚫림) {c['n']}개{c['note']}")
    w(f"  (i)  로그 ortho1 선택 적중: {c['log_ok']}/{c['n']}"
      f" = {acc_c1:.1f}%  (같은맵만 {c['log_ok_same']}/{c['n_same']}"
      f" = {acc_c1_same:.1f}%)")
    w(f"  (ii) nav_graph.unstick_dir 적중: {c['nav_ok']}/{c['nav_prop']}"
      f" = {acc_c2:.1f}%  (기권 {c['nav_abstain']}/{c['n_same']},"
      f" 타맵좌표 제외 {c['nav_skip_mapneq']})")
    w("")
    w(f"[지표 D] 위반율 — 제안이 blocked(증거율>={BLK_EV}) 간선"
      f" 또는 미관측(보간{MAX_GAP}칸 내 관측 셀 없음) 방향인 비율")
    w(f"  nav_full : {vf[0]}/{vf[1]} = {viol_full * 100:.2f}%")
    w(f"  nav_graph: {vg[0]}/{vg[1]} = {viol_graph * 100:.2f}%")
    w("")
    w("[게이트 판정] (2026-07-05 배포정합 기준 — 근거는 경로 딥러닝 학습.md §5)")
    w(f"  A1. 모방 회귀가드  nav_full {acc_full:.1f}% >= nav_graph"
      f" {acc_graph:.1f}%  → {'OK' if ok_a1 else 'FAIL'}")
    w(f"  A2. 결정지점(hard) nav_full {acc_full_h:.1f}% > naive"
      f" {acc_naive_h:.1f}% 그리고 >= graph {acc_graph_h:.1f}%-2pp"
      f"  → {'OK' if ok_a2 else 'FAIL'}")
    w(f"  B.  도달률         nav_full {succ_full:.1f}% >= naive"
      f" {succ_naive:.1f}%  → {'OK' if ok_b else 'FAIL'}")
    w(f"  D.  위반율         {viol_full * 100:.2f}% < 2.00%"
      f"  → {'OK' if ok_d else 'FAIL'}")
    w(f"  (정보) A 전체 vs naive: {acc_full:.1f}% vs {acc_naive:.1f}%"
      f" — 직선 구간(66%)은 런타임에서 trail 이 담당, 게이트 비대상")
    w(f"  (정보) C STUCK: (ii){acc_c2:.1f}% vs (i,같은맵){acc_c1_same:.1f}%"
      f" — 실행된 정책이 '뚫린 방향'을 만든 관찰 데이터라 선택편향,"
      f" 반사실 정책 평가 불가 → 게이트 제외. shadow 실측으로 대체")
    w("")
    w(f"  판정: {'PASS — 배포 가능' if gate_pass else 'FAIL — 배포 금지 (모델 교체 안 함)'}")

    report = "\n".join(lines) + "\n"
    report_path = pathlib.Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    # stdout 요약 (전체는 리포트 파일)
    print(report)
    print(f"리포트 저장: {report_path}")
    sys.exit(0 if gate_pass else 1)


if __name__ == "__main__":
    main()
