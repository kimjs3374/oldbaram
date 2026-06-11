# -*- coding: utf-8 -*-
r"""맵바 CRNN(CTC) 학습 — 분할 불필요, 맵바 전체 -> "선비족X-Y(Z)".

라벨: PaddleOCR 1차 -> 정제(base 선비족 + 패턴). npz 캐시(반복 빠르게).
모델: CNN + BiLSTM + CTC. batch 학습. ONNX export.
  cd dist_dosa && py ../_train_map_crnn.py
"""
import sys
import re
import pathlib
import random
import numpy as np
import cv2

sys.path.insert(0, ".")
import torch
import torch.nn as nn

SRC = pathlib.Path("D:/oldbaram/mapcrops_dl")
CACHE = pathlib.Path("D:/oldbaram/_mapcrnn_cache.npz")
LABELS_TSV = pathlib.Path("D:/oldbaram/mapcrops_labels_verified.tsv")
DEV = "cuda" if torch.cuda.is_available() else "cpu"
IMG_H, IMG_W = 32, 160  # T = W/4 = 40 >> 라벨 max(~9)


def clean_label(raw: str):
    raw = raw or ""
    nums = re.findall(r"\d", raw)
    if "-" in raw and len(nums) >= 3:
        x, y, z = nums[0], nums[1], nums[2]
        if x in "12345" and y in "12345" and z in "1234567":
            return f"선비족{x}-{y}({z})"
    if len(nums) == 1 and nums[0] in "12345" and "-" not in raw:
        return f"선비족{nums[0]}"
    if "입" in raw or "구" in raw:
        return "선비족입구"
    return None


def build_dataset():
    # 검수된 라벨(tsv: filename<TAB>label)이 있으면 우선 사용.
    verified = {}
    if LABELS_TSV.exists():
        for ln in LABELS_TSV.read_text(encoding="utf-8").splitlines():
            if "\t" in ln:
                fn, lab = ln.split("\t", 1)
                if lab.strip():
                    verified[fn.strip()] = lab.strip()
        print(f"  검수 라벨 {len(verified)}개 로드(tsv)")
    from paddleocr import TextRecognition
    rec = TextRecognition()
    imgs, labs = [], []
    for p in sorted(SRC.rglob("*.png")):
        img = cv2.imread(str(p))
        if img is None:
            continue
        if p.name in verified:
            lab = verified[p.name]
        else:
            out = rec.predict(img)
            txt = ""
            try:
                for o in out:
                    t = (o.get("rec_text", "") if isinstance(o, dict)
                         else getattr(o, "rec_text", ""))
                    if t:
                        txt = t
                        break
            except Exception:
                pass
            lab = clean_label(txt.strip())
        if lab:
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            g = cv2.resize(g, (IMG_W, IMG_H))
            imgs.append(g)
            labs.append(lab)
    return np.stack(imgs), labs


def load_data():
    # 캐시 우선(반복 학습 빠르게). verified tsv 수정 시 캐시 삭제 후 재빌드.
    if CACHE.exists():
        d = np.load(CACHE, allow_pickle=True)
        return d["imgs"], list(d["labs"])
    imgs, labs = build_dataset()
    np.savez(CACHE, imgs=imgs, labs=np.array(labs, dtype=object))
    return imgs, labs


def augment(g):
    a = g.astype(np.float32)
    if random.random() < 0.5:
        k = random.choice([3, 5])
        a = cv2.GaussianBlur(a, (k, k), 0)
    if random.random() < 0.5:
        a = a + np.random.normal(0, random.uniform(2, 14), a.shape)
    if random.random() < 0.5:
        a = a * random.uniform(0.8, 1.2) + random.uniform(-18, 18)
    if random.random() < 0.4:  # 가로 미세 시프트
        sh = random.randint(-3, 3)
        a = np.roll(a, sh, axis=1)
    return np.clip(a, 0, 255)


class CRNN(nn.Module):
    def __init__(self, n_cls):
        super().__init__()
        self.cnn = nn.Sequential(
            nn.Conv2d(1, 64, 3, 1, 1), nn.BatchNorm2d(64), nn.ReLU(),
            nn.MaxPool2d(2),                                    # 16x80
            nn.Conv2d(64, 128, 3, 1, 1), nn.BatchNorm2d(128), nn.ReLU(),
            nn.MaxPool2d(2),                                    # 8x40
            nn.Conv2d(128, 256, 3, 1, 1), nn.BatchNorm2d(256), nn.ReLU(),
            nn.MaxPool2d((2, 1)),                               # 4x40
            nn.Conv2d(256, 256, 3, 1, 1), nn.BatchNorm2d(256), nn.ReLU(),
            nn.MaxPool2d((2, 1)),                               # 2x40
        )
        self.rnn = nn.LSTM(256 * 2, 256, bidirectional=True,
                           batch_first=True, num_layers=2, dropout=0.1)
        self.fc = nn.Linear(512, n_cls)

    def forward(self, x):
        f = self.cnn(x)
        b, c, h, w = f.shape
        f = f.permute(0, 3, 1, 2).reshape(b, w, c * h)
        r, _ = self.rnn(f)
        return self.fc(r)


def decode(logits, charset):
    ids = logits.argmax(2)  # (B,T)
    res = []
    for row in ids:
        out, prev = [], -1
        for i in row.tolist():
            if i != prev and i != 0:
                out.append(charset[i])
            prev = i
        res.append("".join(out))
    return res


def main():
    print("데이터 로드...")
    imgs, labs = load_data()
    print(f"  학습가능 {len(labs)}장 / 전체 225")
    chars = sorted(set("".join(labs)))
    charset = ["<blank>"] + chars
    c2i = {c: i for i, c in enumerate(charset)}
    uniq = sorted(set(labs))
    print(f"  charset({len(charset)}): {charset}")
    print(f"  고유 맵 {len(uniq)}종")

    model = CRNN(len(charset)).to(DEV)
    opt = torch.optim.Adam(model.parameters(), 1e-3, weight_decay=1e-5)
    sch = torch.optim.lr_scheduler.CosineAnnealingLR(opt, 400)
    ctc = nn.CTCLoss(blank=0, zero_infinity=True)

    N = len(labs)
    # train/val 분리 (일반화 측정) — 같은 맵의 다른 프레임이 val 에 들어가
    # 실제 인식률을 잰다. seed 고정 재현.
    random.seed(42)
    perm = list(range(N))
    random.shuffle(perm)
    nval = max(25, N // 6)
    val_set = set(perm[:nval])
    tr_idx0 = [i for i in range(N) if i not in val_set]
    va_idx = [i for i in range(N) if i in val_set]
    random.seed()
    print(f"  train {len(tr_idx0)} / val {len(va_idx)}")
    idx = list(tr_idx0)
    BS, EPOCHS = 32, 400
    best = 0.0
    for ep in range(EPOCHS):
        model.train()
        random.shuffle(idx)
        tot = 0.0
        for i in range(0, len(idx), BS):
            bi = idx[i:i + BS]
            xb = np.stack([augment(imgs[j]) for j in bi])[:, None] / 255.0
            x = torch.from_numpy(xb).float().to(DEV)
            tgt = torch.cat([torch.tensor([c2i[c] for c in labs[j]])
                             for j in bi]).to(DEV)
            tl = torch.tensor([len(labs[j]) for j in bi])
            logits = model(x)
            T = logits.shape[1]
            lp = logits.log_softmax(2).permute(1, 0, 2)
            il = torch.full((len(bi),), T, dtype=torch.long)
            loss = ctc(lp, tgt, il, tl)
            opt.zero_grad(); loss.backward(); opt.step()
            tot += loss.item() * len(bi)
        sch.step()
        if ep % 20 == 0 or ep == EPOCHS - 1:
            # 평가 (augment 없이) — train/val 따로.
            model.eval()
            with torch.no_grad():
                xb = (imgs[:, None] / 255.0)
                x = torch.from_numpy(xb).float().to(DEV)
                preds = []
                for i in range(0, N, 64):
                    preds += decode(model(x[i:i + 64]).cpu(), charset)
            tr_acc = (sum(preds[j] == labs[j] for j in tr_idx0)
                      / max(1, len(tr_idx0)))
            va_acc = (sum(preds[j] == labs[j] for j in va_idx)
                      / max(1, len(va_idx)))
            best = max(best, va_acc)
            print(f"  ep{ep:3d} loss={tot / len(idx):.4f} "
                  f"train={100 * tr_acc:.1f}% val={100 * va_acc:.1f}%")
            if va_acc >= 0.90 and tr_acc >= 0.99:
                print("  목표 도달 (val>=90%)")
                break

    # 최종 평가 + 틀린 것 출력
    model.eval()
    with torch.no_grad():
        x = torch.from_numpy(imgs[:, None] / 255.0).float().to(DEV)
        preds = []
        for i in range(0, N, 64):
            preds += decode(model(x[i:i + 64]).cpu(), charset)
    wrong = [(labs[j], preds[j]) for j in range(N) if preds[j] != labs[j]]
    acc = (N - len(wrong)) / N
    print(f"\n최종 학습데이터 정확도: {N - len(wrong)}/{N} = {100 * acc:.1f}%")
    if wrong:
        print(f"틀린 {len(wrong)}개(앞20): {wrong[:20]}")

    model.cpu().eval()
    torch.onnx.export(
        model, torch.zeros(1, 1, IMG_H, IMG_W),
        "D:/oldbaram/map_crnn.onnx",
        input_names=["img"], output_names=["logits"],
        dynamic_axes={"img": {0: "B"}}, opset_version=12)
    pathlib.Path("D:/oldbaram/map_crnn_charset.txt").write_text(
        "\n".join(charset), encoding="utf-8")
    print(f"ONNX 저장 완료 (charset {len(charset)})")


if __name__ == "__main__":
    main()
