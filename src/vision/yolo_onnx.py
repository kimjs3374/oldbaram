"""torch-free YOLOv8 추론기 (onnxruntime CPU 전용).

ultralytics(=torch 1.1GB) 의존 제거용. exe 배포 경량화(2026-06-16).
출력 인터페이스는 yolo.YoloRunner.detect() 와 동일한 List[Detection].

배경:
  - config.vision.device = -1 (ONNX CPU 강제) 이므로 ultralytics predict 는
    어차피 onnx 백엔드로만 동작. 그 경로를 ultralytics 없이 직접 재현한다.
  - best.onnx: 입력 [b,3,h,w](dynamic), 출력 [1,4+nc,anchors], NMS 미포함.
    전처리=letterbox(ultralytics 호환), 후처리=conf threshold + 클래스별 NMS.
  - letterbox/NMS 파라미터는 ultralytics predict 기본값과 일치시켜
    박스 좌표가 픽셀 단위로 동일하도록 맞춤 (verify 스크립트로 대조 검증).
"""
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
import onnxruntime as ort


# nc=2 클래스 매핑 (yolo.py 와 동일)
CLS_RED = 0
CLS_WHITE = 1


@dataclass
class Detection:
    x1: int; y1: int; x2: int; y2: int
    conf: float
    cls: int
    tab_color: str = "RED"   # "RED" | "WHITE"

    @property
    def cx(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2

    @property
    def w(self) -> int:
        return self.x2 - self.x1

    @property
    def h(self) -> int:
        return self.y2 - self.y1


def _letterbox(im: np.ndarray, new_shape: int = 416,
               color: Tuple[int, int, int] = (114, 114, 114),
               stride: int = 32, auto: bool = True
               ) -> Tuple[np.ndarray, float, Tuple[float, float]]:
    """ultralytics LetterBox(auto=True, scaleup=True) 호환.

    ultralytics predict 파이프라인은 단일 이미지에서 rect(auto=True) letterbox 를
    사용 → 정사각 416 이 아니라 짧은 변을 stride 배수로만 패딩(416x256 등).
    정사각 패딩은 입력이 달라져 conf 가 미세하게 어긋나므로 auto=True 가 정답.
    반환: (padded_img, ratio, (left, top)) — 한쪽 패딩 픽셀.
    """
    h, w = im.shape[:2]
    r = min(new_shape / h, new_shape / w)
    # scaleup=True: 작은 이미지 확대 허용 (ultralytics predict 기본).
    new_unpad = (int(round(w * r)), int(round(h * r)))  # (w, h)
    dw = (new_shape - new_unpad[0])
    dh = (new_shape - new_unpad[1])
    if auto:  # rect: 짧은 변을 stride 배수로만 패딩
        dw = dw % stride
        dh = dh % stride
    dw /= 2.0
    dh /= 2.0
    if (w, h) != new_unpad:
        im = cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    im = cv2.copyMakeBorder(im, top, bottom, left, right,
                            cv2.BORDER_CONSTANT, value=color)
    return im, r, (left, top)


class OnnxYolo:
    """onnxruntime CPU 단독 YOLOv8 추론기. torch/ultralytics 의존 0."""

    def __init__(self, weights: str, imgsz: int = 416, conf: float = 0.25,
                 iou: float = 0.5, log_fn=None, **_ignored):
        w = Path(weights)
        if w.suffix.lower() != ".onnx":
            onnx_sib = w.with_suffix(".onnx")
            if onnx_sib.exists():
                w = onnx_sib
            else:
                raise FileNotFoundError(
                    f"onnx weights not found (torch-free 빌드는 .onnx 필수): {w}")
        if not w.exists():
            raise FileNotFoundError(f"weights not found: {w}")
        self.imgsz = int(imgsz)
        self.conf = float(conf)
        self.iou = float(iou)
        self._backend = "onnx"
        # intra_op=1: YOLO/RapidOCR/digit_cnn 3엔진 스레드 경합 방지 (digit_cnn.py 주석 참조).
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(
            str(w), sess_options=so, providers=["CPUExecutionProvider"])
        self._inp_name = self.sess.get_inputs()[0].name
        if log_fn is not None:
            try:
                log_fn(f"[YOLO-INIT] backend=onnx(direct) weights={w.name} imgsz={imgsz}")
            except Exception:
                pass
        # warmup
        try:
            self.detect(np.zeros((720, 1280, 3), dtype=np.uint8))
        except Exception:
            pass

    @property
    def backend(self) -> str:
        return self._backend

    def detect(self, frame: np.ndarray) -> List[Detection]:
        img, r, (padx, pady) = _letterbox(frame, self.imgsz)
        blob = img[:, :, ::-1].transpose(2, 0, 1)          # BGR→RGB, HWC→CHW
        blob = np.ascontiguousarray(blob, dtype=np.float32) / 255.0
        blob = blob[None]                                   # add batch
        out = self.sess.run(None, {self._inp_name: blob})[0]  # (1, 4+nc, N)
        pred = out[0].T                                     # (N, 4+nc)
        if pred.shape[0] == 0:
            return []
        boxes_xywh = pred[:, :4]                            # cx,cy,w,h (letterbox 좌표)
        scores = pred[:, 4:]                                # (N, nc)
        # multi_label=True (ultralytics 기본, nc>1): 한 박스가 여러 클래스 임계를
        # 넘으면 (box,cls) 쌍을 각각 생성. argmax 단일선택은 ultralytics 와 어긋남.
        ri, ci = np.where(scores >= self.conf)              # row(anchor), col(class)
        if ri.size == 0:
            return []
        boxes_xywh = boxes_xywh[ri]
        conf = scores[ri, ci]
        cls = ci
        # cxcywh → xyxy (letterbox 좌표계)
        xy = boxes_xywh.copy()
        xy[:, 0] = boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2
        xy[:, 1] = boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2
        xy[:, 2] = boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2
        xy[:, 3] = boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2
        # 클래스별 NMS (agnostic=False, ultralytics 기본). 클래스 오프셋으로 분리.
        max_wh = 7680
        offset = cls.astype(np.float32) * max_wh
        boxes_for_nms = xy.copy()
        boxes_for_nms[:, [0, 2]] += offset[:, None]
        boxes_for_nms[:, [1, 3]] += offset[:, None]
        # cv2.dnn.NMSBoxes 는 (x,y,w,h) top-left 형식.
        nms_in = np.empty_like(boxes_for_nms)
        nms_in[:, 0] = boxes_for_nms[:, 0]
        nms_in[:, 1] = boxes_for_nms[:, 1]
        nms_in[:, 2] = boxes_for_nms[:, 2] - boxes_for_nms[:, 0]
        nms_in[:, 3] = boxes_for_nms[:, 3] - boxes_for_nms[:, 1]
        idxs = cv2.dnn.NMSBoxes(nms_in.tolist(), conf.tolist(),
                                self.conf, self.iou)
        if len(idxs) == 0:
            return []
        idxs = np.array(idxs).reshape(-1)
        dets: List[Detection] = []
        H, W = frame.shape[:2]
        for i in idxs:
            x1, y1, x2, y2 = xy[i]
            # letterbox 역변환: 패딩 제거 후 비율 복원
            x1 = (x1 - padx) / r; x2 = (x2 - padx) / r
            y1 = (y1 - pady) / r; y2 = (y2 - pady) / r
            x1 = int(max(0, min(W - 1, round(x1))))
            y1 = int(max(0, min(H - 1, round(y1))))
            x2 = int(max(0, min(W - 1, round(x2))))
            y2 = int(max(0, min(H - 1, round(y2))))
            k = int(cls[i])
            tab_color = "WHITE" if k == CLS_WHITE else "RED"
            dets.append(Detection(x1, y1, x2, y2, float(conf[i]), k, tab_color))
        return dets
