# 옛날바람 3PC 매크로 설계서 (v3)

> v2 → v3 주요 변경:
> - 따라가기: 화면 템플릿 매칭 → **메모리 리더 좌표 + UDP 브로드캐스트**
> - 빨탭 검출: 템플릿 매칭 → **YOLOv8n 지도학습** (mAP50 0.995 확보)
> - 맵 전환: 화면 블랙아웃 감지 → **맵 이름 변경 이벤트**
> - 흰탭: 별도 학습 없이 **빨탭 미검출 룰** 사용
> - 네트워크: WAN 원격 → **LAN + UDP 브로드캐스트**

## 1. 환경

- OS: Windows 11
- Python: 3.11 (64bit) 또는 3.12
- GPU: RTX 4060 Ti 8GB (도사 PC 로컬 YOLO 추론용)
- 실행 단위: 각 PC에서 독립 실행. 격수 PC는 **좌표 송신만**, 도사 PC는 풀 봇
- 의존성
  - `pywin32` (PostMessage/SendInput/GetKeyState)
  - `opencv-python` (전처리, 디버그 시각화)
  - `mss` (고속 스크린샷)
  - `numpy`
  - `ultralytics` (YOLOv8 추론)
  - `torch + CUDA` (GPU 추론)
  - `pymem` 또는 직접 `ReadProcessMemory` (게임 메모리 리딩)
  - `PyQt6` (GUI 설정창)
  - `pyyaml`

## 2. 배포

```
[격수 PC]   main_sender.exe
            - MemReader (맵명+좌표+HP+MP)
            - UDP Sender (50Hz 브로드캐스트)
            - GUI 없음 (tray 아이콘만)

[도사1 PC]  slave.exe
[도사2 PC]  slave.exe
            - MemReader (자기 좌표/HP/MP)
            - UDP Sender + Receiver
            - YOLO 실시간 추론 (mss + best.pt)
            - 상태 머신
            - 키 송신 (pywin32 PostMessage)
            - GUI (PyQt6)
```

## 3. 모듈 구조

```
oldbaram-macro/
├── common/
│   ├── mem_reader/
│   │   ├── signatures.py       # 맵이름/좌표 메모리 주소 정의
│   │   └── reader.py           # pymem 기반 읽기
│   ├── net/
│   │   ├── udp_sender.py       # 50Hz 송신
│   │   └── udp_receiver.py     # 최신값만 보관
│   └── schema.py               # { role, map, x, y, hp_pct, ts }
│
├── main_sender/
│   └── main.py                 # 격수 PC 송신 전용
│
├── slave/
│   ├── main.py                 # 도사 진입점
│   ├── gui/
│   │   ├── settings_window.py
│   │   └── calibrator.py       # HP/MP 바 영역만 캘리브레이션
│   ├── state/
│   │   ├── machine.py          # INIT/FOLLOW/COMBAT/MAP_CHANGE/DEAD
│   │   └── follow.py           # SAME_MAP/DIFF_MAP/ARRIVED 하위 상태
│   ├── vision/
│   │   ├── capture.py          # mss
│   │   ├── yolo_runner.py      # best.pt 추론 루프 (15~30fps)
│   │   ├── hp_reader.py        # 자기 HP/MP 바 픽셀 (메모리 리더 백업용)
│   │   └── ghost_detect.py
│   ├── input/
│   │   ├── keyboard.py         # PostMessage wrapper
│   │   ├── numlock.py
│   │   └── hold_keys.py
│   ├── skills/
│   │   ├── cycle.py            # NumLock 메인힐 싸이클
│   │   ├── conditional.py      # 조건부 스케줄러
│   │   ├── paryeok.py          # 오프셋 타이머
│   │   └── revive.py
│   └── follow/
│       └── pathing.py          # 좌표 기반 방향 결정
│
├── dataset/                    # 학습 데이터/스크립트 (현존)
│   ├── raw/                    # 1482장 원본
│   ├── full_yolo/              # 병합 학습셋
│   ├── runs/seed/weights/best.pt
│   ├── runs/full/weights/best.pt  # (학습 진행 중)
│   ├── coco_to_yolo.py
│   ├── merge_dataset.py
│   ├── train_full.py
│   ├── pseudo_label.py
│   └── preview_pseudo.py
│
└── tools/
    ├── record_template.py
    └── live_debug.py           # 추론 bbox 오버레이
```

## 4. 데이터 프로토콜 (UDP)

```python
# common/schema.py
from dataclasses import dataclass
from typing import Literal

Role = Literal["main", "dosa1", "dosa2"]

@dataclass
class PlayerState:
    role: Role
    map: str            # 예: "bonghwang_dungeon_1f"
    x: float
    y: float
    hp_pct: float       # 0.0 ~ 1.0
    mp_pct: float       # 0.0 ~ 1.0
    ts: float           # time.time()
```

- 송신: JSON 직렬화 UDP 브로드캐스트 (port 51900), 50Hz
- 수신: `{role: latest_PlayerState}` 딕셔너리로 최신값만 덮어쓰기
- 지연 > 1초인 데이터는 stale로 표시

## 5. 상태 머신 (v3)

```
  [INIT] ──(첫 좌표 수신)──→ [FOLLOW]
     ▲
     │
  [FOLLOW] ─────────────────────────┐
     │  SAME_MAP: 격수와 같은 맵      │
     │  └ 2칸 밖 → 이동              │
     │  └ 2칸 이내 → [COMBAT]        │
     │                               │
  [COMBAT] ─────────────────────────┤
     │  싸이클 ON + 조건부 스킬 활성   │
     │  격수 멀어짐 → [FOLLOW]       │
     │                               │
  [MAP_CHANGE] ─────────────────────┤
     │  DIFF_MAP: 격수가 다른 맵      │
     │  격수 마지막 방향 hold         │
     │  도사 맵 바뀜 → 5.1 INIT 동작 │
     │                               │
  [DEAD] ◄─────────────────────────┘
     │  HP 0 감지 (메모리) or 유령
     │  자가부활 → 회복 대기 → [INIT]
```

### 5.1 상태별 동작

| 상태 | 싸이클 | 조건부 | 이동 | 빨탭 유지 |
|------|--------|--------|------|----------|
| INIT | OFF → ON | 대기 | 정지 | Tab 시도 |
| FOLLOW (SAME_MAP, 2칸 밖) | ON | 실행 | 좌표 방향으로 | 2초마다 |
| COMBAT (SAME_MAP, 2칸 이내) | ON | 실행 | 정지 | 2초마다 |
| MAP_CHANGE | **ON 유지** (자힐 효과) | 일시정지 | 격수 마지막 방향 | 맵 도착 후 즉시 |
| DEAD | OFF + all keys UP | 정지 | 정지 | 정지 |

> v2 대비 변경: MAP_CHANGE에서 싸이클 OFF 하지 않음. 빨탭 풀린 흰탭 상태라 자힐이 들어가서 이동 중 생존율 ↑.

## 6. 핵심 알고리즘

### 6.1 좌표 기반 따라가기

```python
# slave/follow/pathing.py
THRESH = 2.0   # 2칸 이내 정지 (게임 좌표 단위, 실측 튜닝)

def decide_direction(main: PlayerState, me: PlayerState) -> Optional[str]:
    if main.map != me.map:
        return None   # DIFF_MAP 핸들러로 위임
    dx = main.x - me.x
    dy = main.y - me.y
    if abs(dx) <= THRESH and abs(dy) <= THRESH:
        return "STOP"
    if abs(dx) >= abs(dy):
        return "RIGHT" if dx > 0 else "LEFT"
    return "DOWN" if dy > 0 else "UP"
```

### 6.2 맵 전환 처리

```python
# slave/state/follow.py
class FollowController:
    def __init__(self):
        self.main_dir_history: deque = deque(maxlen=10)
        self.diff_map_started: float | None = None

    def tick(self, main: PlayerState, me: PlayerState):
        if main.map == me.map:
            self._record_main_direction(main)
            direction = decide_direction(main, me)
            apply_direction(direction)
            self.diff_map_started = None
        else:
            if self.diff_map_started is None:
                self.diff_map_started = time.time()
            last_dir = self._last_main_direction()
            apply_direction(last_dir)
            if time.time() - self.diff_map_started > 8.0:
                logger.warning("포털 진입 실패 (8초 경과)")
                apply_direction(None)
```

### 6.3 YOLO 실시간 추론

```python
# slave/vision/yolo_runner.py
from ultralytics import YOLO
import mss

model = YOLO(WEIGHTS).to("cuda")
sct = mss.mss()
mon = {"top": 0, "left": 0, "width": 1280, "height": 720}

def infer_once():
    frame = np.array(sct.grab(mon))[..., :3]
    r = model.predict(frame, imgsz=640, conf=0.25, iou=0.5, half=True, verbose=False)[0]
    if len(r.boxes) == 0:
        return None
    i = int(r.boxes.conf.argmax())
    xyxy = r.boxes.xyxy[i].tolist()
    conf = float(r.boxes.conf[i])
    return BoxResult(xyxy=xyxy, conf=conf, ts=time.time())
```

- 목표 FPS 15~30 (GPU FP16으로 여유)
- 결과는 상태머신이 읽어서 "빨탭 유무" → 싸이클 타겟팅 유지/흰탭 분기

### 6.4 NumLock 싸이클 (v2와 동일)
### 6.5 조건부 스케줄러 (v2와 동일)
### 6.6 파력무참 오프셋 (v2와 동일: 도사1 offset 0, 도사2 offset 90)

### 6.7 흰탭 처리

```python
# 빨탭 미검출 1초 이상 지속 시
if time.time() - last_red_detected_ts > 1.0:
    state.is_white_tab = True
    # 싸이클은 유지 (자힐 효과)
else:
    state.is_white_tab = False
```

## 7. GUI (v2 대비 축소)

- 격수 캐릭터명: 메모리 리더가 직접 읽으므로 **입력 불필요**
- 캘리브레이션: HP/MP 바만 (백업용). 메모리 리더가 제대로 읽히면 생략 가능
- 나머지는 v2 동일 (넘패드 매핑/조건부 규칙/핫키)

## 8. 단축키 / 안전장치 (v2 동일)

| 키 | 동작 |
|----|------|
| F9 | Start |
| F10 | 일시정지 |
| F12 | 패닉 (프로세스 종료 + 복원) |

- `atexit` 로 모든 키 UP + NumLock 복원
- 게임 창 포커스 아닐 때 싸이클 일시 정지

## 9. 구현 순서 (v3, 현재 진행도 반영)

| # | 작업 | 상태 |
|---|------|------|
| 1 | 데이터 수집 + YOLO 학습 | 완료 |
| 2 | 통합 1482장 재학습 | **진행 중 (epoch 97)** |
| 3 | 메모리 리더 PoC (맵명+좌표 읽기) | 미착수 |
| 4 | UDP 송수신 모듈 | 미착수 |
| 5 | 실시간 추론 루프 (mss + best.pt) | 미착수 |
| 6 | 키 송신 wrapper (pywin32) | 미착수 |
| 7 | NumLock 싸이클 + 조건부 스케줄러 | 미착수 |
| 8 | 상태 머신 통합 | 미착수 |
| 9 | 따라가기 SAME_MAP/DIFF_MAP 튜닝 | 미착수 |
| 10 | 사망/부활 | 미착수 |
| 11 | GUI (PyQt6) | 미착수 |
| 12 | 파력무참 오프셋 실측 | 미착수 |
| 13 | PyInstaller 배포 | 미착수 |

## 10. 미확정 / 확인 필요 항목

- [ ] 게임 메모리 주소 (맵명/좌표/HP/MP) — 사용자 메모리 리더 이미 보유 추정
- [ ] 좌표 단위 → 픽셀/타일 환산
- [ ] THRESH (정지 거리) 실측값
- [ ] MAP_CHANGE 타임아웃 (기본 8초가 맞는가)
- [ ] 격수 맵 이동 직전 "마지막 방향" 계산 윈도우 크기 (기본 10 샘플)
- [ ] YOLO 재학습 후 실제 mAP (진행 중)
- [ ] 자가부활/격수부활 스킬명
- [ ] 파력무참 오프셋 90초가 최적인지 실측
