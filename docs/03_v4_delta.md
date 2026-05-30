# 설계 v4 — v3 대비 Delta

> v3 문서(`01_mechanism_v3.md`, `02_design_v3.md`) 기반.
> v4는 두 차례 외부 자문(재미나이) 답변을 반영한 **변경점만** 기록한다.
> 최종 확정 시 v5로 통합 예정.

## 0. 사용 리스크 고지 (신규)

이 매크로는 다음 기술을 사용한다:
- 게임 프로세스 메모리 리딩 (`ReadProcessMemory`)
- 키보드 입력 자동화 (`SendInput`/`PostMessage`/HW HID)
- 클라이언트 간 상태 동기화 (UDP)

**이는 대부분의 온라인 게임 이용약관 위반 소지가 있으며, 계정 제재/영구 정지의 사유가 될 수 있다.**
개인 학습/연구 목적 이외의 사용은 권장하지 않는다.
anti-cheat 우회나 탐지 회피 구현은 이 설계에 포함하지 않는다.

## 1. 상태 머신 재구조 (v3 flat → v4 우선순위 레이어)

### 1.1 우선순위 레이어

```
LEVEL 0 (최우선, 다른 모든 상태 인터럽트)
  ├ DEAD           : 자기 HP 0 or 유령 플래그
  └ DISCONNECTED   : 격수 UDP stale > 5초

LEVEL 1 (맵 전환 라이프사이클)
  ├ ENTER_PORTAL   : 격수 맵 변경 감지된 순간
  ├ LOADING        : 자기 좌표 무효(0/쓰레기값) 구간
  └ NEW_MAP        : 자기 좌표 유효 복귀, 격수 맵과 일치 확인 중

LEVEL 2 (따라가기 / 전투)
  ├ COMBAT         : 격수 2칸 이내
  ├ FOLLOW         : 격수 2칸 밖, SAME_MAP
  └ STUCK          : 좌표 3초 이상 미변동 + 목표 거리 멀음

LEVEL 3 (평시)
  └ INIT           : 초기화 / 첫 좌표 수신 대기
```

### 1.2 상태 머신 입력 (Tick마다 평가)

```python
def classify_state(self_state, main_state, udp_latency, detection, history):
    # LEVEL 0
    if self_state.hp == 0 or self_state.is_ghost:
        return "DEAD"
    if udp_latency > 5.0:
        return "DISCONNECTED"

    # LEVEL 1
    if main_state.map != history.last_main_map:
        return "ENTER_PORTAL"
    if not self_state.coord_valid:
        return "LOADING"
    if self_state.map != main_state.map:
        return "NEW_MAP"

    # LEVEL 2
    if history.stuck_duration > 3.0 and distance(self_state, main_state) > STUCK_THRESH:
        return "STUCK"
    if distance(self_state, main_state) <= COMBAT_RANGE:
        return "COMBAT"
    return "FOLLOW"
```

## 2. 맵 전환 3단계 상세

### 2.1 상태 전이

```
[SAME_MAP]
   │ main.map 변경 감지
   ▼
[ENTER_PORTAL]  (격수 포털 진입 직후)
   │ 격수 마지막 이동 방향 hold
   │ 자기 맵도 변경 감지 or 자기 좌표 무효 시
   ▼
[LOADING]  (자기 로딩 화면)
   │ 모든 키 release (로딩 중 입력은 의미 없음)
   │ self.coord_valid=True 복귀
   ▼
[NEW_MAP]  (자기 로딩 완료, 맵 확인 중)
   │ self.map == main.map
   ▼
[SAME_MAP]
   │ 또는 self.map != main.map (포털 방향 잘못 탐)
   ▼
[ENTER_PORTAL] 재진입 (반대 방향으로 복귀 시도)
```

### 2.2 좌표 유효성 판단

```python
def is_coord_valid(state):
    # 로딩 중 쓰레기값 가드
    if state.x == 0.0 and state.y == 0.0:
        return False
    if abs(state.x) > 1e6 or abs(state.y) > 1e6:
        return False
    return True
```

### 2.3 LOADING 타임아웃
- 기본 8초. 초과 시 경고 로그 + INIT 복귀.

## 3. Stuck 탐지 & 복구 (신규)

### 3.1 탐지
```python
EPSILON = 0.5   # 좌표 단위
STUCK_WINDOW = 3.0   # sec

if len(position_history.last_seconds(STUCK_WINDOW)) > 0:
    max_move = max_displacement(position_history.last_seconds(STUCK_WINDOW))
    if max_move < EPSILON and distance_to_target > STUCK_THRESH:
        state = "STUCK"
```

### 3.2 복구 로직
옛바는 직선 이동만 가능 → 복구 단순화:

```
STUCK 진입
  → 현재 이동 방향의 반대 방향 1초 hold
  → 원래 방향으로 재시도
  → 3회 반복 후에도 STUCK이면 → 경고 + 원래 방향 계속 (사람 개입 유도)
```

### 3.3 파라미터 (실측 튜닝)
- `EPSILON`: 메모리 좌표 단위 확인 후 결정
- `STUCK_WINDOW`: 2~5초 (너무 짧으면 정상 정지도 STUCK 오탐)
- `STUCK_THRESH`: COMBAT_RANGE(2칸)보다 커야 함

## 4. 히스테리시스 명시 (detection flicker 방어)

### 4.1 빨탭 ↔ 흰탭 전이

```python
# 빨탭 → 흰탭 (신중하게)
if not detected_this_frame:
    miss_count += 1
    if miss_count >= FRAMES_PER_SEC * 1.0:   # 1초 연속 미검출
        tab_state = "WHITE"

# 흰탭 → 빨탭 (빠르게 복귀)
if detected_this_frame:
    hit_count += 1
    if hit_count >= 2:   # 2프레임 연속 검출
        tab_state = "RED"
        miss_count = 0
```

### 4.2 상태 전이 지연 원칙
- **"위험한 전이"(싸이클 OFF, 따라가기 방향 전환)는 느리게**
- **"안전한 전이"(복귀, 재개)는 빠르게**

## 5. UDP 패킷 스키마 v4

```python
# common/schema.py
@dataclass
class PlayerPacket:
    ver: int = 4                # Protocol Version (v3 없었음)
    seq: int                    # Sequence Number (v3 없었음)
    role: Role
    map: str
    x: float
    y: float
    coord_valid: bool           # 좌표 유효성 (로딩 중 False)
    map_valid: bool             # 맵 이름 유효성
    hp_pct: float
    mp_pct: float
    is_ghost: bool
    last_dir: Optional[str]     # 최근 이동 방향 (맵 전환 시 힌트)
    ts: float
```

### 5.1 수신측 처리

```python
def on_packet(pkt):
    if pkt.ver != EXPECTED_VERSION:
        logger.error("프로토콜 불일치. 업데이트 필요.")
        state.halt()
        return
    last = latest[pkt.role]
    if last and pkt.seq <= last.seq:
        return  # 구패킷 drop
    if last and pkt.ts < last.ts:
        return  # 타임스탬프 역전 drop
    latest[pkt.role] = pkt
```

### 5.2 송신 주기 변경
- v3: 50Hz
- **v4: 30Hz** (파이썬 sleep 정밀도 이슈 회피. 실시간 체감 동일)

## 6. 네트워크 전송 방식

- v3: UDP 브로드캐스트
- **v4: UDP Unicast** (3PC 고정 IP, config에 명시)

```yaml
# config.yaml 추가
network:
  own_ip: 192.168.0.11
  own_role: dosa1
  peers:
    - { role: main,  ip: 192.168.0.10 }
    - { role: dosa2, ip: 192.168.0.12 }
  port: 51900
  send_hz: 30
```

## 7. 입력 채널 선택 플로우 (신규)

```
1. SendInput 시도 → 게임 창 포커스 상태에서 먹히는가?
   YES → 포커스 순환 방식 채택 (3창 번갈아 포커스)
   NO → 다음
2. PostMessage 시도 → 백그라운드로 먹히는가?
   YES → PostMessage 채택 (가장 이상적)
   NO → 다음
3. Arduino Leonardo HID 에뮬레이션
   → USB 허브로 3대 PC 연결, 라우팅 스위치
   → 하드웨어 레벨이라 소프트 탐지 불가
```

**이 검증은 실 게임에서 직접 해야 한다. 책상 위 메모장 테스트와 결과 다를 수 있다.**

## 8. 키 입력 Jitter (탐지 회피, 필수)

```python
import random

def human_press(vk, duration_ms=None):
    down_t = duration_ms or random.randint(30, 70)
    jitter_t = random.randint(-50, 50)

    time.sleep(max(0, (BASE_INTERVAL_MS + jitter_t) / 1000))
    send_keydown(vk)
    time.sleep(down_t / 1000)
    send_keyup(vk)
```

## 9. Watchdog 프로세스 (신규)

```
[메인 봇 프로세스]
  ├ 주기적으로 watchdog에 heartbeat
  └ 비정상 종료 시 heartbeat 중단

[Watchdog 프로세스] (별도 EXE)
  ├ heartbeat 3초 미수신 → 강제 키 release 루틴
  │  - 모든 방향키, 넘패드 키 KEYUP
  │  - NumLock 상태 복원 (원래 ON이면 ON으로)
  └ 자기 자신 종료
```

v3의 `atexit`만으로는 불충분. **강제 종료(Ctrl+Alt+Del, 전원 off 등) 대응은 외부 watchdog만 가능.**

## 10. DPI awareness (신규, 필수)

```python
# slave/main.py 최상단
import ctypes
ctypes.windll.user32.SetProcessDPIAware()
```

- Windows 125%/150% 스케일링 환경에서 mss 캡처 좌표 어긋남 방지.
- **도사 PC 사용자에게 "DPI 100% 권장" 안내도 추가.**

## 11. 추론 파이프라인 multiprocessing (신규)

```
[Process 1: Capture]      mss → SharedMemory
     ↓
[Process 2: Inference]    SharedMemory → YOLO → bbox Queue
     ↓
[Process 3: Logic]        bbox Queue + UDP Queue → 상태 머신 → Key Queue
     ↓
[Process 4: Input]        Key Queue → SendInput / PostMessage
```

- 각 프로세스는 독립 GIL
- 실패 격리 (추론 프로세스 크래시해도 Input 프로세스는 살아서 키 release 가능)

## 12. 구현 순서 v4 (검증 우선순위 재배열)

**Phase 0: 실기 검증 (설계 유효성 확정용, 1~2시간)**
- 0.1 Cheat Engine으로 옛바 맵 이름 string scan → 주소 찾기 가능 여부
- 0.2 `pymem`으로 ReadProcessMemory 시도 → anti-cheat 통과 여부
- 0.3 메모장에서 SendInput/PostMessage 동작 확인
- 0.4 옛바에서 SendInput 포커스 상태/백그라운드 동작 확인
- 0.5 mss 캡처 + YOLO 추론 E2E 지연 측정

**Phase 1: 기반 모듈 (Phase 0 결과에 따라 분기)**
- 메모리 리더 (성공 시) 또는 비전 기반 follow (실패 시)
- UDP Unicast 송수신
- 추론 멀티프로세스 구조
- 입력 wrapper (SendInput/HW HID)
- Watchdog

**Phase 2: 로직**
- 우선순위 상태 머신
- Stuck 탐지/복구
- 맵 전환 3단계
- 빨탭/흰탭 히스테리시스

**Phase 3: 스킬 운영**
- NumLock 싸이클
- 조건부 스케줄러
- 파력무참 오프셋

**Phase 4: 배포**
- GUI (PyQt6)
- PyInstaller (ONNX Runtime 옵션 검토)
- 설정 마이그레이션 (protocol version)

## 13. v3 → v4 요약 표

| 항목 | v3 | v4 |
|------|-----|-----|
| 상태 머신 | flat 전이 | 우선순위 레이어 (Level 0~3) |
| 맵 전환 | DIFF_MAP 1개 | ENTER_PORTAL / LOADING / NEW_MAP 3개 |
| Stuck | 없음 | 3초 탐지 + 반대방향 복구 |
| 히스테리시스 | 언급만 | 명시 (빨탭 1초, 흰탭 2프레임) |
| UDP 스키마 | 최소 필드 | ver/seq/coord_valid/last_dir 추가 |
| UDP 주기 | 50Hz | 30Hz |
| UDP 전송 | 브로드캐스트 | Unicast |
| 입력 | PostMessage 전제 | 실측 분기 (SendInput/PostMessage/HW) |
| Jitter | 없음 | ±50ms + Down 30~70ms 랜덤 |
| Watchdog | atexit | 별도 프로세스 |
| DPI | 언급 없음 | SetProcessDPIAware 필수 |
| 추론 | threading/asyncio | multiprocessing 4-stage |
| 리스크 고지 | 없음 | 섹션 0 |
