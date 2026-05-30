"""UDP 수신 ON/OFF 별 OCR predict 시간 비교 테스트.

목적: "격수 PC 켜면 힐러 fps 드롭" 의 진짜 원인 확인.
방법:
  1) PaddleOCR 단독 루프 돌리면서 predict 시간 측정 (OCR 만)
  2) UDP 수신 스레드 추가 (격수 송신 시뮬) + OCR 동시
  3) 두 케이스 predict 시간 비교

실행 방식: 같은 프로세스 내 스레드 분리. 실제 힐러 구조 모방.
"""
from __future__ import annotations
import socket
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent / "dist_dosa"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from paddleocr import TextRecognition  # type: ignore

# -------------------- 공통 --------------------

MODEL_DIR = ROOT / "models" / "korean_PP-OCRv5_mobile_rec"


def make_ocr():
    kw = {"model_name": "korean_PP-OCRv5_mobile_rec"}
    if MODEL_DIR.is_dir():
        kw["model_dir"] = str(MODEL_DIR)
    return TextRecognition(**kw)


def make_sample_img():
    # 한글 샘플 이미지 생성 (HP/MP 비슷한 크기).
    img = np.full((60, 200, 3), 255, dtype=np.uint8)
    cv2.putText(img, "603541/603541", (5, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    return img


# -------------------- UDP 시뮬 --------------------

class UdpSenderSim(threading.Thread):
    """격수 역할: 초당 60회 UDP 패킷 송신."""
    def __init__(self, port: int, hz: int = 60):
        super().__init__(daemon=True, name="udp-sender-sim")
        self.port = port
        self.hz = hz
        self._stop_evt = threading.Event()
        self._count = 0

    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setblocking(False)
        payload = (
            b'{"ver":5,"type":"state","seq":0,"ts_ms":0,'
            b'"map_name":"mapA","coord_valid":true,"x":15,"y":23,'
            b'"red_tab":true,"red_cx":100,"red_cy":200,"last_dir":"R",'
            b'"hp":603541,"mp":956466,"map_seq":1,'
            b'"hp_pct":95,"mp_pct":99,"debuff_honmasul_sec":-1,'
            b'"buff_mujang_sec":-1,"buff_boho_sec":-1,'
            b'"map_change_pending":false}'
        )
        interval = 1.0 / self.hz
        next_t = time.perf_counter()
        while not self._stop_evt.is_set():
            try:
                s.sendto(payload, ("127.0.0.1", self.port))
            except Exception:
                pass
            self._count += 1
            next_t += interval
            wait = next_t - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
        s.close()

    def stop(self):
        self._stop_evt.set()


class UdpReceiverSim(threading.Thread):
    """힐러 UdpReceiver 와 동일 구조: recvfrom + parse_packet(간소)."""
    def __init__(self, port: int, use_drain: bool = True):
        super().__init__(daemon=True, name="udp-recv-sim")
        self._stop_evt = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", port))
        self._sock.settimeout(0.5)
        self.use_drain = use_drain
        self._count = 0

    def run(self):
        import json
        while not self._stop_evt.is_set():
            try:
                data, _ = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            self._count += 1
            try:
                json.loads(data.decode("utf-8"))
            except Exception:
                pass
            if self.use_drain:
                self._sock.setblocking(False)
                try:
                    while True:
                        d, _ = self._sock.recvfrom(4096)
                        self._count += 1
                        try:
                            json.loads(d.decode("utf-8"))
                        except Exception:
                            pass
                except (BlockingIOError, OSError):
                    pass
                finally:
                    self._sock.setblocking(True)

    def stop(self):
        self._stop_evt.set()


# -------------------- 측정 --------------------

def bench_ocr(ocr, img, n=100):
    # warmup
    for _ in range(3):
        list(ocr.predict(img))
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        list(ocr.predict(img))
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    return {
        "n": n,
        "avg": sum(times) / n,
        "p50": times[n // 2],
        "p95": times[int(n * 0.95)],
        "min": times[0],
        "max": times[-1],
    }


def main():
    print("=" * 60)
    print("OCR predict vs UDP 수신 영향 테스트")
    print("=" * 60)

    ocr = make_ocr()
    img = make_sample_img()

    # Case A: UDP 없음 (순수 OCR)
    print("\n[A] UDP 없음 — 순수 OCR predict 100회")
    a = bench_ocr(ocr, img, n=100)
    print(f"   avg={a['avg']:.2f}ms p50={a['p50']:.2f}ms "
          f"p95={a['p95']:.2f}ms max={a['max']:.2f}ms")

    # Case B: UDP 수신 스레드 + 송신 시뮬 60Hz (힐러 현재 구조 = drain 사용)
    print("\n[B] UDP 60Hz 수신 중 — drain ON (현재 힐러 구조)")
    PORT = 55999
    recv = UdpReceiverSim(PORT, use_drain=True)
    send = UdpSenderSim(PORT, hz=60)
    recv.start()
    time.sleep(0.1)
    send.start()
    time.sleep(0.5)  # UDP 안정화
    b = bench_ocr(ocr, img, n=100)
    send.stop(); recv.stop()
    send.join(timeout=1.0); recv.join(timeout=1.0)
    print(f"   avg={b['avg']:.2f}ms p50={b['p50']:.2f}ms "
          f"p95={b['p95']:.2f}ms max={b['max']:.2f}ms "
          f"(sent={send._count} recv={recv._count})")

    # Case C: UDP 수신 스레드만 (송신 없음 = 격수 OFF 시뮬)
    print("\n[C] UDP 수신 스레드만 (송신자 없음 = 격수 OFF 시뮬)")
    recv2 = UdpReceiverSim(PORT, use_drain=True)
    recv2.start()
    time.sleep(0.3)
    c = bench_ocr(ocr, img, n=100)
    recv2.stop(); recv2.join(timeout=1.0)
    print(f"   avg={c['avg']:.2f}ms p50={c['p50']:.2f}ms "
          f"p95={c['p95']:.2f}ms max={c['max']:.2f}ms")

    # 비교
    print("\n" + "=" * 60)
    print("결론:")
    print(f"  A(UDP 없음) avg    = {a['avg']:.2f}ms")
    print(f"  B(UDP 60Hz)  avg   = {b['avg']:.2f}ms  (차이 {b['avg']-a['avg']:+.2f}ms)")
    print(f"  C(recv만 송신X)    = {c['avg']:.2f}ms  (차이 {c['avg']-a['avg']:+.2f}ms)")
    if b["avg"] > a["avg"] * 1.5:
        print("  => UDP 수신이 OCR 속도를 크게 늘림 (상관관계 확정)")
    elif b["avg"] > a["avg"] * 1.1:
        print("  => UDP 수신이 OCR 속도 소폭 증가")
    else:
        print("  => UDP 수신은 OCR 속도에 영향 거의 없음 (원인 다른 곳)")


if __name__ == "__main__":
    main()
