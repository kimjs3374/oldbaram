"""NavNet 학습 — 격수 발자국 goal-conditioned behavior cloning (경로 딥러닝 학습.md §4).

입력: nav_dataset/train.npz, val.npz
    X uint8  (N, 6, 15, 15)  패치 (nav_features.encode_patch 값 ×255 양자화)
    G float32(N, 8)          스칼라 (nav_features.encode_scalars)
    Y uint8  (N,)            정답 액션 idx (nav_features.ACTIONS 순서)
출력: dist_dosa/src/fsm/nav_policy.onnx + src/fsm/nav_policy.onnx (digit_cnn 양트리 관례)

인코딩은 nav_features.py 단일 정본을 import 만 한다 (자체 재구현 금지 —
학습/추론 인코딩 갈라지면 모델이 조용히 엉뚱한 답을 냄).
좌표축 실측 정본: D=y+, U=y- (메모리 project_coord_axis_ud).

자체검증/오버라이드:
    --data DIR      데이터 폴더 (기본 nav_dataset)
    --out-dir DIR   onnx 출력 (기본: nav_dataset/staging — 게이트 통과 전
                    실모델 미오염. 승격/배포는 _nav_auto.bat). env NAV_OUT_DIR 동일.
    --epochs/--batch/--log  자체검증용 축소 실행.
"""
import argparse
import collections
import os
import pathlib
import random
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "dist_dosa"))

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

# 피처 인코딩 단일 정본 (_train_digit_cnn.py 의 coord_template 공유 관례와 동일)
from src.fsm.nav_features import ACTIONS, CH, N_SCALAR, NAV_ONNX, PATCH

# --- seed 고정 (digit_cnn 관례: random/np/torch 전부) ---
SEED = 0
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
torch.cuda.manual_seed_all(SEED)

CLASS_NAMES = tuple("STAY" if a == "-" else a for a in ACTIONS)  # U/D/L/R/STAY


class _Tee:
    """stdout 을 logs_train_nav.txt 에도 동시 기록 (logs_train_nano.txt 관례).

    콘솔이 cp949 면 일부 문자(—)에서 UnicodeEncodeError → 콘솔만 대체문자,
    파일은 항상 UTF-8 원문 (학습 로그 유실 방지).
    """

    def __init__(self, path):
        self._f = open(path, "a", encoding="utf-8")
        self._out = sys.stdout
        try:  # 가능하면 콘솔 인코딩 오류를 대체문자로 (Python 3.7+)
            self._out.reconfigure(errors="replace")
        except Exception:
            pass

    def write(self, s):
        try:
            self._out.write(s)
        except UnicodeEncodeError:
            enc = getattr(self._out, "encoding", None) or "utf-8"
            self._out.write(s.encode(enc, errors="replace").decode(enc))
        self._f.write(s)

    def flush(self):
        self._out.flush()
        self._f.flush()


class NavNet(nn.Module):
    """패치(6ch 15×15) conv + 스칼라(8) fc → concat → 5-way (U/D/L/R/STAY)."""

    def __init__(self):
        super().__init__()
        s2 = (PATCH + 1) // 2  # stride2 conv 후 한 변 (15→8)
        self.conv = nn.Sequential(
            nn.Conv2d(CH, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(),
            nn.Conv2d(32, 64, 3, padding=1, stride=2), nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.Conv2d(64, 64, 3, padding=1), nn.ReLU(),
            nn.Flatten(),
            nn.Linear(64 * s2 * s2, 192), nn.ReLU())
        self.gfc = nn.Sequential(nn.Linear(N_SCALAR, 32), nn.ReLU())
        self.head = nn.Sequential(
            nn.Linear(192 + 32, 96), nn.ReLU(),
            nn.Linear(96, len(ACTIONS)))

    def forward(self, x, g):
        return self.head(torch.cat([self.conv(x), self.gfc(g)], dim=1))


def _load_npz(fp: pathlib.Path):
    """npz 로드 + 형태 검증. X 는 uint8 그대로 두고 배치에서 /255 (메모리 절약)."""
    d = np.load(fp)
    X, G, Y = d["X"], d["G"], d["Y"]
    if X.ndim != 4 or X.shape[1:] != (CH, PATCH, PATCH):
        raise SystemExit(f"[FAIL] {fp.name} X shape {X.shape} != (N,{CH},{PATCH},{PATCH})")
    if G.shape != (len(X), N_SCALAR):
        raise SystemExit(f"[FAIL] {fp.name} G shape {G.shape} != (N,{N_SCALAR})")
    if len(Y) != len(X):
        raise SystemExit(f"[FAIL] {fp.name} Y 길이 {len(Y)} != {len(X)}")
    return X, G.astype(np.float32), Y.astype(np.int64)


def _to_x(x: torch.Tensor, dev) -> torch.Tensor:
    """uint8 패치 → float32 0..1 (dataset 이 이미 float 이면 그대로)."""
    x = x.to(dev)
    return x.float().div_(255.0) if x.dtype == torch.uint8 else x.float()


def _evaluate(net, loader, dev):
    """val top-1 + 클래스별 (맞음, 전체) 집계."""
    net.eval()
    cor = tot = 0
    per = {i: [0, 0] for i in range(len(ACTIONS))}
    with torch.no_grad():
        for x, g, y in loader:
            p = net(_to_x(x, dev), g.to(dev)).argmax(1).cpu()
            cor += int((p == y).sum())
            tot += len(y)
            for pi, yi in zip(p.tolist(), y.tolist()):
                per[yi][1] += 1
                per[yi][0] += int(pi == yi)
    return cor / max(1, tot), per


def main():
    ap = argparse.ArgumentParser(description="NavNet 학습 + ONNX export")
    ap.add_argument("--data", default=str(ROOT / "nav_dataset"))
    # 2026-07-05 자동학습: 기본 출력 = staging (게이트 통과 전 실모델 오염 금지.
    # 이전엔 학습이 검증 전에 양트리 실모델을 덮어쓰는 결함이 있었음).
    # 게이트 PASS 후 승격/배포는 _nav_auto.bat 이 수행.
    ap.add_argument("--out-dir",
                    default=os.environ.get("NAV_OUT_DIR")
                    or str(ROOT / "nav_dataset" / "staging"),
                    help="onnx 출력 오버라이드 (자체검증용 — 미지정 시 양트리 src/fsm)")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--patience", type=int, default=5)
    # 클래스 weight 방식: inv=빈도역수(기존), sqrt=완화(√역수, top-1 유리),
    # none=무가중. A(사람 일치율) 게이트 튜닝용 — 실측 후 선택.
    ap.add_argument("--weights", choices=("inv", "sqrt", "none"),
                    default="inv")
    ap.add_argument("--log", default=str(ROOT / "logs_train_nav.txt"))
    args = ap.parse_args()

    sys.stdout = _Tee(args.log)
    print(f"\n===== _nav_train {time.strftime('%Y-%m-%d %H:%M:%S')} =====", flush=True)

    data_dir = pathlib.Path(args.data)
    tr_fp, va_fp = data_dir / "train.npz", data_dir / "val.npz"
    if not tr_fp.is_file() or not va_fp.is_file():
        print(f"[FAIL] 데이터셋 없음: {tr_fp} / {va_fp} — 먼저 _nav_dataset.py 실행", flush=True)
        sys.exit(1)

    Xtr, Gtr, Ytr = _load_npz(tr_fp)
    Xva, Gva, Yva = _load_npz(va_fp)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"train {len(Xtr)}  val {len(Xva)}  dev={dev}  batch={args.batch}", flush=True)

    # 클래스 빈도 역수 weight (digit_cnn 관례) — 평균 1 로 정규화 (loss 스케일 안정)
    cnt = np.bincount(Ytr, minlength=len(ACTIONS)).astype(np.float64)
    print("클래스 분포(train):",
          " ".join(f"{n}={int(c)}" for n, c in zip(CLASS_NAMES, cnt)), flush=True)
    if args.weights == "none":
        w = np.ones_like(cnt)
    elif args.weights == "sqrt":
        w = 1.0 / np.sqrt(np.maximum(1.0, cnt))
    else:
        w = 1.0 / np.maximum(1.0, cnt)
    w = w * (len(w) / w.sum())
    wts = torch.tensor(w, dtype=torch.float32)
    print(f"클래스 weight({args.weights}):",
          " ".join(f"{n}={v:.2f}" for n, v in zip(CLASS_NAMES, w)), flush=True)

    net = NavNet().to(dev)
    n_param = sum(p.numel() for p in net.parameters())
    print(f"파라미터 {n_param:,} (~{n_param / 1e6:.2f}M)", flush=True)

    lossf = nn.CrossEntropyLoss(weight=wts.to(dev), label_smoothing=0.05)
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-4)
    gen = torch.Generator().manual_seed(SEED)  # shuffle 재현성
    tl = DataLoader(
        TensorDataset(torch.from_numpy(Xtr), torch.from_numpy(Gtr),
                      torch.from_numpy(Ytr)),
        batch_size=args.batch, shuffle=True, num_workers=0, generator=gen)
    vl = DataLoader(
        TensorDataset(torch.from_numpy(Xva), torch.from_numpy(Gva),
                      torch.from_numpy(Yva)),
        batch_size=1024, num_workers=0)

    best_acc, best_state, best_ep, bad = -1.0, None, -1, 0
    for ep in range(args.epochs):
        t0 = time.time()
        net.train()
        loss_sum = nb = 0
        for x, g, y in tl:
            opt.zero_grad()
            loss = lossf(net(_to_x(x, dev), g.to(dev)), y.to(dev))
            loss.backward()
            opt.step()
            loss_sum += float(loss)
            nb += 1
        acc, _per = _evaluate(net, vl, dev)
        mark = ""
        if acc > best_acc:
            best_acc, best_ep, bad = acc, ep, 0
            # state dict 는 cpu 복사본으로 보관 (cuda 학습 → cpu export)
            best_state = {k: v.detach().cpu().clone()
                          for k, v in net.state_dict().items()}
            mark = " *best"
        else:
            bad += 1
        print(f"ep{ep:02d} loss={loss_sum / max(1, nb):.4f} "
              f"val_acc={acc:.4f} ({time.time() - t0:.1f}s){mark}", flush=True)
        if bad >= args.patience:
            print(f"early stop (개선 없음 {args.patience} epoch)", flush=True)
            break

    if best_state is None:
        print("[FAIL] 학습 실패 (best 없음)", flush=True)
        sys.exit(1)

    # --- val 최고 모델 저장 + 클래스별 정확도 표 (cpu 기준) ---
    torch.save(best_state, data_dir / "nav_policy_best.pt")
    net_cpu = NavNet()
    net_cpu.load_state_dict(best_state)
    net_cpu.eval()
    acc, per = _evaluate(net_cpu, vl, "cpu")
    print(f"\nbest ep{best_ep} val_acc={acc:.4f}", flush=True)
    print("[클래스별 val 정확도]", flush=True)
    for i, name in enumerate(CLASS_NAMES):
        c, t = per[i]
        r = c / t if t else float("nan")
        print(f"  {name:<5}: {c:>6}/{t:<6} = {r:.4f}" if t
              else f"  {name:<5}: (val 샘플 없음)", flush=True)

    # --- ONNX export (cpu, eval, opset12, 배치축만 dynamic — digit_cnn 관례) ---
    if args.out_dir:
        out_dir = pathlib.Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        primary = out_dir / NAV_ONNX
        copies = []  # 자체검증: 진짜 산출 경로(dist_dosa/src/fsm) 오염 금지
    else:
        primary = ROOT / "dist_dosa" / "src" / "fsm" / NAV_ONNX
        copies = [ROOT / "src" / "fsm" / NAV_ONNX]
    dummy_x = torch.zeros(1, CH, PATCH, PATCH)
    dummy_g = torch.zeros(1, N_SCALAR)
    torch.onnx.export(
        net_cpu, (dummy_x, dummy_g), str(primary),
        input_names=["x", "g"], output_names=["y"],
        dynamic_axes={"x": {0: "b"}, "g": {0: "b"}, "y": {0: "b"}},
        opset_version=12)
    for dst in copies:
        shutil.copy(primary, dst)
    print(f"ONNX 저장: {primary}"
          + (f" (+ {copies[0]})" if copies else " (오버라이드 — 양트리 복사 생략)"),
          flush=True)

    # --- torch ↔ onnxruntime 패리티 (배포 exe 고정 1.20.1) ---
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1  # YOLO/OCR 경합 회귀 방지 실측 관례 (문서 §9)
    so.inter_op_num_threads = 1
    sess = ort.InferenceSession(str(primary), sess_options=so,
                                providers=["CPUExecutionProvider"])
    n = min(64, len(Xva))
    xb = Xva[:n].astype(np.float32) / (255.0 if Xva.dtype == np.uint8 else 1.0)
    gb = Gva[:n]
    with torch.no_grad():
        t_out = net_cpu(torch.from_numpy(xb), torch.from_numpy(gb)).numpy()
    o_out = sess.run(["y"], {"x": xb, "g": gb})[0]
    diff = float(np.abs(t_out - o_out).max())
    agree = float((t_out.argmax(1) == o_out.argmax(1)).mean())
    print(f"패리티 (ort {ort.__version__}, {n}샘플): max|Δ|={diff:.2e} "
          f"argmax 일치={agree:.4f}", flush=True)
    if diff >= 1e-3:
        print("[FAIL] torch↔ort 패리티 불일치 (>=1e-3) — export 검증 실패", flush=True)
        sys.exit(1)
    print("PARITY PASS — TRAIN_DONE", flush=True)


if __name__ == "__main__":
    main()
