"""격수 미리보기용 화면 프레임 스트리밍 (TCP, JPEG).

힐러 N대 → 격수 1대로 게임 화면 썸네일을 실시간 전송한다.
- 기존 UDP 채널(State 54545 / CooldownReport 45455)과 **완전 분리**된 TCP.
  JPEG 프레임은 수십 KB라 UDP 단일 패킷에 안 들어가 분할/유실로 깨진다 → TCP.
- 프레이밍: 고정 헤더(길이 프리픽스) + 닉 + JPEG payload.
    헤더 = !4sBBHI = magic("OBPV"), ver, healer_idx, nick_len, jpeg_len
    이어서 nick(utf-8, nick_len), jpeg(jpeg_len).
- 송신측(FrameSender): 백그라운드 스레드가 fps 주기로 최신 프레임만 인코딩/전송.
  연결 끊기면 자동 재연결. 메인 루프는 submit() 으로 참조만 넘김(블로킹 0).
- 수신측(FrameReceiver): TCP 서버. 힐러 연결마다 스레드 1개, 프레임 디코드 →
  on_frame(idx, nick, frame_bgr) 콜백. (Qt signal 변환은 UI 쪽 책임.)
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from typing import Callable, Optional, Tuple

import cv2
import numpy as np

_MAGIC = b"OBPV"
_VER = 1
_HDR = struct.Struct("!4sBBHI")  # magic, ver, idx, nick_len, jpeg_len
_MAX_JPEG = 4 * 1024 * 1024      # 4MB 방어 상한 (정상 프레임은 수십 KB)


def _recvall(sock: socket.socket, n: int) -> Optional[bytes]:
    """정확히 n바이트 수신. 연결 종료/오류 시 None."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except Exception:
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


class FrameSender:
    """힐러측: 격수 1곳으로 JPEG 프레임을 주기 전송.

    - set_target(ip): 격수 IP 지정/갱신 (하트비트로 자동 확보한 src 사용).
    - submit(frame_bgr): 최신 프레임 참조만 저장 (오래된 건 버림 = drop-old).
    - 내부 스레드가 1/fps 주기로 resize→JPEG 인코딩→sendall.
    """

    def __init__(self, idx: int, port: int, fps: float = 4.0,
                 width: int = 480, quality: int = 50,
                 nickname: str = "", log=None):
        self._idx = int(idx)
        self._port = int(port)
        self._interval = 1.0 / max(1.0, float(fps))
        self._width = int(width)
        self._quality = int(quality)
        self._nick = str(nickname or "")
        self._log = log

        self._lock = threading.Lock()
        self._latest: Optional[np.ndarray] = None
        self._target_ip: Optional[str] = None
        self._sock: Optional[socket.socket] = None
        self._connected_ip: Optional[str] = None

        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="frame-sender", daemon=True
        )
        self._thread.start()

    def set_target(self, ip: str) -> None:
        ip = str(ip or "")
        with self._lock:
            if ip != self._target_ip:
                self._target_ip = ip

    def set_nickname(self, nickname: str) -> None:
        with self._lock:
            self._nick = str(nickname or "")

    def submit(self, frame_bgr: np.ndarray) -> None:
        # 참조만 저장. grab() 은 매 iter 새 ndarray 라 race 없음.
        with self._lock:
            self._latest = frame_bgr

    def stop(self) -> None:
        self._stop.set()
        try:
            self._thread.join(timeout=1.0)
        except Exception:
            pass
        self._close()

    # ---- 내부 ----
    def _close(self) -> None:
        with self._lock:
            s = self._sock
            self._sock = None
            self._connected_ip = None
        if s is not None:
            try:
                s.close()
            except Exception:
                pass

    def _ensure_conn(self, ip: str) -> Optional[socket.socket]:
        with self._lock:
            if self._sock is not None and self._connected_ip == ip:
                return self._sock
        # 대상 변경 or 미연결 → 재연결.
        self._close()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((ip, self._port))
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            return None
        with self._lock:
            self._sock = s
            self._connected_ip = ip
        if self._log:
            try:
                self._log.info(f"[PREVIEW-SEND] connected → {ip}:{self._port}")
            except Exception:
                pass
        return s

    def _encode(self, frame: np.ndarray) -> Optional[bytes]:
        try:
            h, w = frame.shape[:2]
            if w > self._width:
                nh = max(1, int(round(h * self._width / float(w))))
                frame = cv2.resize(frame, (self._width, nh),
                                   interpolation=cv2.INTER_AREA)
            ok, enc = cv2.imencode(
                ".jpg", frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), self._quality],
            )
            if not ok:
                return None
            return enc.tobytes()
        except Exception:
            return None

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.perf_counter()
            with self._lock:
                ip = self._target_ip
                frame = self._latest
                nick = self._nick
            if ip and frame is not None:
                s = self._ensure_conn(ip)
                if s is not None:
                    jpeg = self._encode(frame)
                    if jpeg is not None:
                        nb = nick.encode("utf-8")[:65535]
                        try:
                            hdr = _HDR.pack(
                                _MAGIC, _VER, self._idx & 0xFF,
                                len(nb), len(jpeg),
                            )
                            s.sendall(hdr + nb + jpeg)
                        except Exception:
                            self._close()  # 다음 iter 재연결
            elapsed = time.perf_counter() - t0
            remain = self._interval - elapsed
            self._stop.wait(remain if remain > 0 else 0.001)


class FrameReceiver:
    """격수측: TCP 서버. 힐러 연결마다 스레드, 프레임 디코드 → 콜백.

    on_frame(idx: int, nick: str, frame_bgr: np.ndarray) 는 **수신 스레드**에서
    호출된다. UI 갱신은 콜백 안에서 Qt signal 로 넘길 것 (직접 위젯 조작 금지).
    """

    def __init__(self, bind_host: str, port: int,
                 on_frame: Callable[[int, str, np.ndarray], None], log=None):
        self._bind = (str(bind_host or "0.0.0.0"), int(port))
        self._on_frame = on_frame
        self._log = log
        self._srv: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._accept_loop, name="frame-recv", daemon=True
        )

    def start(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(self._bind)
        srv.listen(8)
        srv.settimeout(0.5)
        self._srv = srv
        self._thread.start()
        if self._log:
            try:
                self._log.info(f"[PREVIEW-RECV] listening {self._bind}")
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._srv is not None:
                self._srv.close()
        except Exception:
            pass
        try:
            self._thread.join(timeout=1.0)
        except Exception:
            pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._srv.accept()
            except socket.timeout:
                continue
            except Exception:
                if self._stop.is_set():
                    break
                time.sleep(0.2)
                continue
            threading.Thread(
                target=self._conn_loop, args=(conn, addr),
                name="frame-conn", daemon=True,
            ).start()

    def _conn_loop(self, conn: socket.socket, addr) -> None:
        conn.settimeout(10.0)
        try:
            while not self._stop.is_set():
                head = _recvall(conn, _HDR.size)
                if head is None:
                    break
                magic, ver, idx, nick_len, jpeg_len = _HDR.unpack(head)
                if magic != _MAGIC or ver != _VER or jpeg_len > _MAX_JPEG:
                    break
                payload = _recvall(conn, nick_len + jpeg_len)
                if payload is None:
                    break
                nick = payload[:nick_len].decode("utf-8", "replace")
                jpeg = payload[nick_len:]
                try:
                    arr = cv2.imdecode(
                        np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR
                    )
                except Exception:
                    arr = None
                if arr is not None:
                    try:
                        self._on_frame(int(idx), nick, arr)
                    except Exception:
                        pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
