"""UDP receiver.

v5 확장:
- 기존 State 수신 (힐러측): `latest()` 유지 (호환).
- ControlCmd 콜백 (힐러측): `set_control_handler(fn)`.
- 송신자 IP 자동 획득 (힐러측): `last_src_addr()` — 격수 IP+port.
- CooldownReport 콜백 (격수측): 격수는 별도 수신 포트(ATTACKER_RECV_PORT)에
  `CooldownReceiver`를 bind해 사용.
"""
import socket
import threading
import time
from typing import Optional, Callable, Tuple

from .protocol import State, ControlCmd, CooldownReport, parse_packet

# 잠긴 격수 소스가 이 시간 동안 무수신이면 새 소스로 인계(격수 PC 교체/재시작).
_SRC_TAKEOVER_SEC = 5.0
# 다른 소스 거부 로그 간격(초) — 폭주 방지.
_SRC_REJECT_LOG_SEC = 10.0


class UdpReceiver:
    """힐러측 수신기. State latest + ControlCmd 콜백 + 격수 IP 자동 파악."""

    def __init__(self, bind_host: str, port: int):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # SO_REUSEADDR 금지: Windows UDP는 TIME_WAIT 없어 불필요하고,
        # 켜두면 ControlListener가 같은 포트에 공존 bind된 경우 패킷
        # 라우팅이 비결정적이 되어 State가 엉뚱한 소켓으로 감. 여기서
        # bind가 실패하면 listener가 아직 살아있다는 명확한 신호.
        try:
            self._sock.bind((bind_host, port))
        except OSError as e:
            import sys as _sys
            print(
                f"[UDP-BIND] FAIL bind_host={bind_host} port={port} err={e}",
                file=_sys.stderr, flush=True,
            )
            raise
        import sys as _sys
        print(
            f"[UDP-BIND] ok bind_host={bind_host} port={port}",
            file=_sys.stderr, flush=True,
        )
        self._sock.settimeout(0.5)
        self._latest: Optional[State] = None
        self._last_seq = -1
        self._last_src: Optional[Tuple[str, int]] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._ctrl_handler: Optional[Callable[[ControlCmd], None]] = None
        self._state_first_logged = False
        # 🔴 2026-07-08: 격수 소스 IP 고정. 격수 프로세스가 2개 살아 있으면
        # (구 프로세스 미종료 등) 힐러가 두 소스의 State 를 번갈아 받아 맵/좌표가
        # 진동한다. 실사고: attacker-0(국경지대 방치) + attacker-36(사냥 진행)
        # → 힐러 맵 '국경지대'↔'선비족입구' 50ms 주기 플리커, MAP-DEBOUNCE
        # -CONFIRM 496회. seq 역행 가드(아래)는 seq 차 1000 이상이면 통과시켜
        # 서로 다른 프로세스의 seq 를 막지 못한다.
        # UdpSender 는 소켓 1개를 프로세스 수명 내내 재사용 → (ip, port) 쌍이
        # 격수 프로세스 식별자. IP 만 잠그면 같은 PC 중복 실행을 못 막는다.
        self._locked_src: Optional[Tuple[str, int]] = None
        self._last_pkt_ts = 0.0        # 잠긴 소스로부터의 마지막 수신 시각
        self._rej_log_ts = 0.0
        self._rej_count = 0

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def set_control_handler(self, fn: Callable[[ControlCmd], None]) -> None:
        """ControlCmd 수신 시 호출될 콜백. 수신 스레드에서 호출됨."""
        self._ctrl_handler = fn

    def last_src_addr(self) -> Optional[Tuple[str, int]]:
        """마지막으로 State를 받은 (src_ip, src_port). 격수 IP 자동 획득용."""
        with self._lock:
            return self._last_src

    def _accept_src(self, addr, s: State) -> bool:
        """격수 State 소스 (ip, port) 고정 게이트. 잠긴 소스 외 패킷은 버린다.

        잠긴 소스가 _SRC_TAKEOVER_SEC 동안 무수신이면 새 소스로 인계 —
        격수 재시작(포트 변경)·PC 교체·IP 변경은 정상 흡수한다. 반대로 두
        프로세스가 **동시에** 살아 있으면 먼저 잡힌 쪽만 계속 수용한다.
        """
        src = (addr[0], addr[1]) if addr else ("", 0)
        now = time.time()
        import sys as _sys
        with self._lock:
            if self._locked_src is None:
                self._locked_src = src
                self._last_pkt_ts = now
                print(f"[UDP-SRC] 격수 소스 고정 {src[0]}:{src[1]} seq={s.seq} "
                      f"map='{s.map_name}'", file=_sys.stderr, flush=True)
                return True
            if src == self._locked_src:
                self._last_pkt_ts = now
                return True
            # 다른 소스 — 잠긴 소스가 끊겼으면 인계, 아니면 거부.
            idle = now - self._last_pkt_ts
            if idle >= _SRC_TAKEOVER_SEC:
                old = self._locked_src
                self._locked_src = src
                self._last_pkt_ts = now
                self._rej_count = 0
                print(f"[UDP-SRC] 격수 소스 인계 {old[0]}:{old[1]} → "
                      f"{src[0]}:{src[1]} (직전 소스 {idle:.1f}s 무수신)",
                      file=_sys.stderr, flush=True)
                return True
            self._rej_count += 1
            if now - self._rej_log_ts >= _SRC_REJECT_LOG_SEC:
                self._rej_log_ts = now
                print(f"[UDP-SRC] 경고: 격수 프로세스가 2개 이상 송신 중 — "
                      f"고정={self._locked_src[0]}:{self._locked_src[1]} "
                      f"거부={src[0]}:{src[1]} "
                      f"(누적 {self._rej_count}패킷, map='{s.map_name}'). "
                      f"구 격수 프로세스를 종료하세요.",
                      file=_sys.stderr, flush=True)
            return False

    def _loop(self):
        # 2026-04-22: 원래 구조로 원복. 이전 drain 패턴이 State 중간 drop 유발
        # 가능성. 단순 한 패킷씩 처리 → 매 State 즉시 latest 갱신.
        while self._running:
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            msg = parse_packet(data)
            if msg is None:
                continue
            if isinstance(msg, State):
                s = msg
                # 🔴 격수 소스 IP 고정 — 격수 프로세스 2개 동시 송신 차단.
                if not self._accept_src(addr, s):
                    continue
                with self._lock:
                    if s.seq < self._last_seq and (self._last_seq - s.seq) < 1000:
                        continue
                    self._latest = s
                    self._last_seq = s.seq
                    self._last_src = addr
                if not self._state_first_logged:
                    self._state_first_logged = True
                    try:
                        import sys as _sys
                        print(
                            f"[UDP-RECV] first State from={addr} "
                            f"seq={s.seq} map='{s.map_name}'",
                            file=_sys.stderr, flush=True,
                        )
                    except Exception:
                        pass
            elif isinstance(msg, ControlCmd):
                if self._ctrl_handler is not None:
                    try:
                        self._ctrl_handler(msg)
                    except Exception:
                        pass

    def latest(self) -> Optional[State]:
        with self._lock:
            return self._latest

    def stop(self):
        self._running = False
        try:
            self._sock.close()
        except OSError:
            pass


class CooldownReceiver:
    """격수측 수신기. 힐러들이 보낸 CooldownReport를 인덱스별로 보관."""

    def __init__(self, bind_host: str, port: int):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((bind_host, port))
        self._sock.settimeout(0.5)
        self._reports: dict[int, CooldownReport] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # 핸들러 시그니처: fn(report) 또는 fn(report, src_addr). 인자 수 자동 감지.
        self._on_report: Optional[Callable] = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def set_report_handler(self, fn: Callable[[CooldownReport], None]) -> None:
        self._on_report = fn

    def latest_for(self, src_idx: int) -> Optional[CooldownReport]:
        with self._lock:
            return self._reports.get(src_idx)

    def all_reports(self) -> dict:
        with self._lock:
            return dict(self._reports)

    def _loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            msg = parse_packet(data)
            if not isinstance(msg, CooldownReport):
                continue
            with self._lock:
                self._reports[msg.src_idx] = msg
            if self._on_report is not None:
                try:
                    # 먼저 (report, src_addr) 시도 → TypeError면 (report) fallback.
                    try:
                        self._on_report(msg, addr)
                    except TypeError:
                        self._on_report(msg)
                except Exception:
                    pass

    def stop(self):
        self._running = False
        try:
            self._sock.close()
        except OSError:
            pass
