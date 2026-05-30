# Design: healer-mainloop-refactor

> **선택된 아키텍처**: Option B — Clean (Plan 원안 100%)
>
> 사람-유사 4영역(눈/뇌/손/근육) + 기억/학습 plug + Event Bus + Plugin Registry.
> 기존 `src/` 동결 보존, 신규 `src_v2/` 빅뱅 작성.

| 항목 | 값 |
|---|---|
| Feature | `healer-mainloop-refactor` |
| Phase | Design |
| 작성일 | 2026-04-25 |
| 기반 | Plan v1 (2026-04-25) |

---

## Context Anchor

| 키 | 값 |
|---|---|
| **WHY** | 메인 루프 비대화로 fps 124→30, 발열 80°C+. 매크로 본질("키 결정") 회복 + 미래 확장 ready. |
| **WHO** | 격수(인간) + 힐러2 PC 자동 봇. 사용자 = 운영자 겸 개발자. |
| **RISK** | 빅뱅 재작성 회귀 위험. 완화: src/ 동결, src_v2/ 신규, 단계 배포(healer1→healer2→attacker), 시나리오 테스트. |
| **SUCCESS** | fps 100+, 모든 기능 동등 동작, 새 룰 5분 등록. |
| **SCOPE** | healer + attacker + UI 전체. 백그라운드 OCR/YOLO/UDP/스케줄러 모두. cfg 마이그레이션. |

---

## 1. Overview

### 1.1 설계 원칙

1. **Single Responsibility**: 각 영역은 하나의 책임만 짐.
   - 눈 = 화면 인식, 뇌 = 의사결정, 손 = 입력 실행, 근육 = 메인 루프 키 hold/release, 기억 = 기록/학습.
2. **Loose Coupling**: 영역 간 직접 호출 금지. Event Bus + Snapshot으로만 통신.
3. **Lock-Free Read**: 메인 근육 루프는 lock 0회. atomic ref read만.
4. **Plugin First**: 새 기능 = 새 플러그인 파일 1개. 기존 코드 0 수정.
5. **Backward Compat**: 기존 cfg/region/스킬 설정 100% 마이그레이션.

### 1.2 새 패키지 트리

```
src_v2/
├── core/
│   ├── __init__.py
│   ├── event_bus.py        # Lock-free pub/sub
│   ├── snapshot.py         # Atomic snapshot (감시 결과 통합)
│   ├── plugin_registry.py  # 플러그인 등록/조회
│   └── types.py            # 공통 dataclass (CastRequest, EyeReport 등)
├── eyes/                   # 감시 (백그라운드 스레드 N개)
│   ├── __init__.py
│   ├── base_watcher.py     # 워처 베이스 클래스
│   ├── capture.py          # AsyncGrabber 래핑
│   ├── yolo_watcher.py
│   ├── ocr_watcher.py      # 좌표/맵
│   ├── cooldown_watcher.py # 쿨/버프/채팅 (3 인스턴스)
│   ├── hpmp_watcher.py
│   ├── xp_watcher.py
│   └── udp_watcher.py
├── brain/                  # 트리거 (룰 엔진)
│   ├── __init__.py
│   ├── rule_engine.py      # 룰 평가 + 우선순위 큐
│   ├── decision.py
│   └── rules/              # 플러그인 룰 (사용자 커스텀 가능)
│       ├── self_heal.py
│       ├── attacker_revive.py
│       ├── self_revive.py
│       ├── parhon.py
│       ├── baekho.py
│       ├── parlyuk.py
│       ├── gyoungryeok.py
│       ├── seq_rclick.py
│       └── tab_lock.py
├── hands/                  # 실행 (스킬/입력)
│   ├── __init__.py
│   ├── input_dispatcher.py # 키/마우스 디스패처 (별도 스레드)
│   ├── skill_executor.py   # 시퀀스 실행
│   ├── numlock_cycle.py
│   └── sequences/
│       ├── self_heal_seq.py
│       ├── attacker_revive_seq.py
│       └── ...
├── muscle/                 # 메인 루프 (근육)
│   └── main_loop.py        # 1-2ms 본체 (키 결정만)
├── memory/                 # 기억/학습
│   ├── __init__.py
│   ├── action_log.py       # 행동 시계열 기록
│   ├── ai_hook.py          # AI 모듈 plug 인터페이스
│   └── pattern_learner.py  # (Future) RL/패턴 (Phase 7는 빈 구현)
├── ui/                     # UI 비동기 분리
│   ├── publisher.py        # frame_ready 분리 publish 스레드
│   └── windows/            # main_window 마이그레이션
├── config/                 # cfg 마이그레이션
│   ├── loader.py
│   └── migration_v1_to_v2.py
├── workers/                # 워커 entry (기존 healer_worker 대체)
│   ├── healer_worker_v2.py
│   └── attacker_worker_v2.py
└── tests/                  # 시나리오 테스트 (회귀 검증용)
    ├── test_event_bus.py
    ├── test_snapshot.py
    ├── test_rules/
    └── test_scenarios/
```

---

## 2. 핵심 컴포넌트 상세

### 2.1 Event Bus (`core/event_bus.py`)

**책임**: 영역 간 메시지 전달. publish/subscribe.

**인터페이스**:

```python
from typing import Callable, Any
from dataclasses import dataclass
import threading
import collections

@dataclass(frozen=True)
class Event:
    topic: str
    payload: Any
    ts: float  # time.monotonic()

class EventBus:
    def __init__(self):
        self._subs: dict[str, list[Callable]] = collections.defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, topic: str, handler: Callable[[Event], None]) -> None:
        with self._lock:
            self._subs[topic].append(handler)

    def publish(self, topic: str, payload: Any) -> None:
        # 핸들러 스냅샷 (publish 중 subscribe 변경 영향 없게)
        with self._lock:
            handlers = list(self._subs.get(topic, ()))
        evt = Event(topic, payload, time.monotonic())
        for h in handlers:
            try:
                h(evt)
            except Exception as e:
                # 핸들러 예외는 다른 핸들러에 영향 없게 격리
                _log_handler_err(topic, h, e)
```

**토픽 명명 규칙**: `{영역}.{이벤트}` 형식.

| 토픽 | 페이로드 | 발행자 | 주요 구독자 |
|---|---|---|---|
| `eye.coord` | `(x, y)` | ocr_watcher | brain |
| `eye.map_changed` | `str` | ocr_watcher | brain, memory |
| `eye.hp` | `int` | hpmp_watcher | brain |
| `eye.mp` | `int` | hpmp_watcher | brain |
| `eye.cooldown` | `dict` | cooldown_watcher | brain |
| `eye.attacker_state` | `AttackerState` | udp_watcher | brain |
| `eye.red_tab` | `Detection` | yolo_watcher | brain |
| `eye.white_tab` | `Detection` | yolo_watcher | brain |
| `brain.cast_request` | `CastRequest` | rule_engine | hands |
| `hand.cast_done` | `CastResult` | skill_executor | brain, memory |
| `hand.cast_failed` | `CastError` | skill_executor | brain, memory |
| `memory.action` | `ActionRecord` | (모든 영역) | memory |

**성능 요구**: publish 1 microsecond 이하 (핸들러 자체 시간 제외). subscribe lock은 publish 중 동시 호출 시에만 의미. 메인 근육 루프는 publish/subscribe 모두 호출 안 함.

### 2.2 Snapshot (`core/snapshot.py`)

**책임**: 모든 감시 결과의 최신값. 메인 근육이 lock-free read.

**인터페이스**:

```python
@dataclass
class Snapshot:
    # 좌표
    healer_coord: Optional[Tuple[int, int]] = None
    healer_map: str = ""
    attacker_coord: Optional[Tuple[int, int]] = None
    attacker_map: str = ""
    # HP/MP
    hp: int = -1
    mp: int = -1
    hp_cur: int = -1
    mp_cur: int = -1
    # 쿨다운/버프
    cd_parlyuk: int = -1
    cd_baekho: int = -1
    buff_parlyuk_active: bool = False
    # 격수 상태
    attacker_hp: int = -1
    attacker_honma_sec: int = -1
    attacker_mujang_sec: int = -1
    attacker_boho_sec: int = -1
    attacker_map_seq: int = 0
    # 빨탭/흰탭
    red_tab_present: bool = False
    red_tab_pos: Optional[Tuple[int, int]] = None
    white_tab_present: bool = False
    # 메타
    last_eye_update_ts: float = 0.0

class SnapshotStore:
    """Watchers update fields atomically. Muscle reads without lock."""
    def __init__(self):
        self._snap = Snapshot()

    def update(self, **fields) -> None:
        # 단순 setattr — Python ref 할당은 GIL 보장 atomic.
        for k, v in fields.items():
            setattr(self._snap, k, v)
        self._snap.last_eye_update_ts = time.monotonic()

    def read(self) -> Snapshot:
        # ref 반환 (consumer는 read-only로 다뤄야 함).
        return self._snap

    def read_field(self, name: str):
        return getattr(self._snap, name, None)
```

**Lock-free 보증**: CPython의 GIL이 단일 attribute write/read의 원자성을 보장. 복합 연산(예: 좌표 (x,y) 동시 업데이트)은 watcher가 새 tuple을 setattr 하므로 read 측에서 부분 업데이트 못 봄.

### 2.3 Plugin Registry (`core/plugin_registry.py`)

**책임**: 룰/시퀀스/워처 플러그인 등록 및 조회.

**인터페이스**:

```python
@dataclass
class RuleSpec:
    name: str
    priority: int  # 낮을수록 먼저 평가
    topics: list[str]  # 어느 이벤트에 반응할지
    handler: Callable[[Snapshot, RuleContext], Optional[CastRequest]]
    enabled: bool = True

class PluginRegistry:
    _rules: list[RuleSpec] = []
    _sequences: dict[str, Callable] = {}
    _watchers: dict[str, type] = {}

    @classmethod
    def register_rule(cls, spec: RuleSpec) -> None: ...

    @classmethod
    def register_sequence(cls, name: str, fn: Callable) -> None: ...

    @classmethod
    def register_watcher(cls, name: str, watcher_cls: type) -> None: ...

# 데코레이터 헬퍼
def rule(name: str, priority: int = 100, topics: list[str] = None):
    def deco(fn):
        PluginRegistry.register_rule(RuleSpec(
            name=name, priority=priority,
            topics=topics or [], handler=fn
        ))
        return fn
    return deco
```

**플러그인 등록 예** (사용자 추가 룰):

```python
# src_v2/brain/rules/my_custom_rule.py
from core.plugin_registry import rule
from core.types import CastRequest

@rule(name="my_custom", priority=50, topics=["eye.hp"])
def my_custom_rule(snap, ctx):
    if snap.hp < 50 and ctx.cooldowns["my_skill"] == 0:
        return CastRequest("my_skill", priority=50)
    return None
```

**자동 로드**: `brain/rules/`, `hands/sequences/`, `eyes/` 디렉터리의 `*.py` 파일을 startup 시 import → 데코레이터 자동 등록.

### 2.4 Eyes (감시) — 베이스 워처

**책임**: 화면/UDP에서 정보 추출 → SnapshotStore 업데이트 + EventBus publish.

**베이스 클래스**:

```python
class BaseWatcher(threading.Thread):
    def __init__(self, name: str, store: SnapshotStore, bus: EventBus,
                 poll_sec: float = 0.5):
        super().__init__(daemon=True, name=f"eye_{name}")
        self.name = name
        self.store = store
        self.bus = bus
        self.poll_sec = poll_sec
        self._stop = threading.Event()

    def run(self):
        while not self._stop.wait(self.poll_sec):
            try:
                self._tick()
            except Exception:
                _log_err(self.name)

    def _tick(self):
        """서브클래스 구현. snapshot.update + bus.publish."""
        raise NotImplementedError

    def stop(self):
        self._stop.set()
```

각 워처는 기존 src/vision의 모듈을 래핑. 예:

```python
class OcrWatcher(BaseWatcher):
    def __init__(self, store, bus, paddle_rec):
        super().__init__("ocr", store, bus, poll_sec=0.05)
        self._ocr = HealerOcr(paddle_rec)  # 기존 OCR 인스턴스

    def _tick(self):
        frame = self.store.read_field("last_frame")
        if frame is None: return
        r = self._ocr.read(frame)
        if r.coord and r.coord != self.store.read_field("healer_coord"):
            self.store.update(healer_coord=r.coord)
            self.bus.publish("eye.coord", r.coord)
        if r.map_name and r.map_name != self.store.read_field("healer_map"):
            self.store.update(healer_map=r.map_name)
            self.bus.publish("eye.map_changed", r.map_name)
```

### 2.5 Brain (룰 엔진)

**책임**: 이벤트 → 룰 평가 → CastRequest 발행.

**구조**:

```python
class RuleEngine:
    def __init__(self, store: SnapshotStore, bus: EventBus,
                 hands_queue: PriorityQueue):
        self.store = store
        self.bus = bus
        self.hq = hands_queue
        # 등록된 모든 룰 토픽별로 인덱싱
        self._by_topic: dict[str, list[RuleSpec]] = {}
        for spec in PluginRegistry._rules:
            for t in spec.topics:
                self._by_topic.setdefault(t, []).append(spec)
            self._by_topic[t].sort(key=lambda s: s.priority)
        # 모든 토픽 구독
        for t in self._by_topic:
            bus.subscribe(t, self._on_event)

    def _on_event(self, evt: Event):
        snap = self.store.read()
        ctx = RuleContext(...)  # cfg/이전 상태/쿨 등
        for spec in self._by_topic.get(evt.topic, []):
            if not spec.enabled: continue
            req = spec.handler(snap, ctx)
            if req is not None:
                self.hq.put((req.priority, req))
                break  # 우선순위 첫 룰만 통과
```

**룰 우선순위 예**:

| 룰 | priority | 설명 |
|---|---|---|
| self_revive | 1 | 자가부활 (HP=0) |
| attacker_revive | 2 | 격수 부활 |
| self_heal | 10 | 자힐 (HP < 임계) |
| gyoungryeok | 20 | 경력증강 (MP < 임계) |
| parlyuk | 30 | 파력무참 (쿨 가능) |
| baekho | 30 | 백호의희원 |
| parhon | 40 | 파혼술 (격수 혼마술) |
| seq_rclick | 50 | SEQ-RCLICK (자힐 중) |
| tab_lock | 50 | TAB-LOCK pending |

### 2.6 Hands (실행)

**책임**: brain의 CastRequest 큐를 받아 실제 키/마우스 입력 실행.

**구조**:

```python
class InputDispatcher(threading.Thread):
    def __init__(self, hands_queue: PriorityQueue, bus: EventBus):
        super().__init__(daemon=True, name="hands_dispatch")
        self.q = hands_queue
        self.bus = bus

    def run(self):
        while True:
            priority, req = self.q.get()
            if req is _STOP: break
            try:
                seq_fn = PluginRegistry._sequences.get(req.name)
                if seq_fn is None:
                    self.bus.publish("hand.cast_failed",
                                     CastError(req, "no_sequence"))
                    continue
                seq_fn(req.ctx)  # 시퀀스 실행 (block 길어도 메인 무관)
                self.bus.publish("hand.cast_done", CastResult(req, "ok"))
            except Exception as e:
                self.bus.publish("hand.cast_failed", CastError(req, str(e)))
```

**시퀀스 등록 예**:

```python
# src_v2/hands/sequences/self_heal_seq.py
from core.plugin_registry import sequence

@sequence("self_heal")
def self_heal(ctx):
    # 기존 SEQ-A/B 로직 그대로 옮김
    press_vk(VK_TAB); time.sleep(0.04)
    press_home(); time.sleep(0.04)
    # ...
```

### 2.7 Muscle (메인 루프)

**책임**: 키 hold/release 본체. **1-2ms 목표**.

**구현**:

```python
def main_loop(store: SnapshotStore, hands: HandsAPI, cfg):
    keys = KeyController(hands)
    cycler = NumlockCycler()
    last_dir = "-"
    while not stop_evt.is_set():
        t0 = perf_counter()
        # 1. snapshot read (lock-free)
        snap = store.read()
        # 2. 따라가기 결정 (순수 함수, 부수효과 0)
        want = decide_direction(snap, cfg)
        # 3. 키 hold/release
        if want != last_dir:
            keys.set_direction(want)
            last_dir = want
        # 4. 넘락 토글 (cycle 시점만 호출)
        cycler.tick(perf_counter())
        # 5. 메인 루프 fps 달성용 sleep
        elapsed = perf_counter() - t0
        if elapsed < 0.005:  # 200fps cap
            sleep(0.005 - elapsed)
```

**제외 항목** (여기서 절대 하지 않음):
- ❌ OCR 호출
- ❌ YOLO 호출
- ❌ EventBus publish/subscribe
- ❌ Log f-string 포맷
- ❌ frame_ready emit
- ❌ Snapshot 업데이트
- ❌ Lock 획득

**`decide_direction` 순수 함수**:

```python
def decide_direction(snap: Snapshot, cfg) -> str:
    """B3:to_target / B5:a_invalid / B1:TRAIL 등 기존 로직 이식."""
    h = snap.healer_coord
    if h is None: return "-"
    a = snap.attacker_coord
    h_map = snap.healer_map
    a_map = snap.attacker_map
    map_neq = bool(h_map) and bool(a_map) and h_map != a_map
    if map_neq:
        # B1: TRAIL 또는 exit_dir
        return _decide_map_neq(snap, cfg)
    if a is None or not snap.attacker_coord_valid:
        # B5: 격수 무효 → 직전 격수 방향
        return snap.attacker_last_dir or "-"
    # B3: to_target
    return _decide_to_target(h, a, cfg)
```

이 함수는 단위 테스트 작성 쉬움 (snapshot 입력 → 방향 출력).

### 2.8 Memory (기억/학습)

**책임**: 행동 결과 기록 + AI plug 인터페이스.

**Phase 7에서는 골격만**:

```python
class ActionLog:
    def record(self, action: str, snapshot_at_decision: dict,
               result: str, latency_ms: float): ...

class AiHook:
    """AI 모듈은 이 인터페이스 구현하여 plug-in."""
    def on_action(self, record: ActionRecord) -> None: ...
    def suggest(self, snapshot: Snapshot) -> Optional[CastRequest]: ...
```

실제 학습 알고리즘은 후속 PDCA cycle.

### 2.9 UI Publisher (분리)

**책임**: 메인 근육 루프에서 UI 갱신을 분리.

```python
class UiPublisher(threading.Thread):
    def __init__(self, store: SnapshotStore, frame_ready_emit, hz=15):
        super().__init__(daemon=True)
        self.store = store
        self.emit = frame_ready_emit
        self.interval = 1.0 / hz

    def run(self):
        while not self._stop.wait(self.interval):
            snap = self.store.read()
            payload = self._build_payload(snap)  # 메인 외부에서 dict build
            self.emit(payload)
```

**핵심**: 메인 근육은 `frame_ready.emit` 절대 호출 안 함. UI Publisher 스레드가 15Hz로 SnapshotStore 읽어 emit.

---

## 3. 데이터 흐름 (Sequence)

### 3.1 일반 사냥 사이클

```
화면 ──▶ Capture(50Hz) ──▶ Snapshot.last_frame
              │
              ├──▶ Yolo(20Hz) ──▶ Snapshot.red_tab + Bus.publish('eye.red_tab')
              ├──▶ Ocr(20Hz)  ──▶ Snapshot.healer_coord + Bus.publish('eye.coord')
              ├──▶ Hpmp(2Hz)  ──▶ Snapshot.hp + Bus.publish('eye.hp')
              ├──▶ CD(1Hz)    ──▶ Snapshot.cd_parlyuk + Bus.publish('eye.cooldown')
              └──▶ UDP        ──▶ Snapshot.attacker_* + Bus.publish('eye.attacker_state')

Bus.publish('eye.hp') ─▶ Brain._on_event ─▶ 룰 평가 ─▶ Hands.queue.put(CastRequest)

Hands.dispatch_loop ─▶ sequence 실행 ─▶ Bus.publish('hand.cast_done')

Muscle(200Hz) ─▶ Snapshot.read ─▶ decide_direction ─▶ keys.hold/release
```

### 3.2 자힐 트리거

```
hpmp_watcher: Snapshot.hp = 30
              Bus.publish('eye.hp', 30)
                  │
                  ▼
brain rule_engine: self_heal_rule(snap, ctx)
                   → CastRequest('self_heal', priority=10)
                  │
                  ▼
hands_queue: put((10, req))
                  │
                  ▼
input_dispatcher: get → sequences['self_heal']() 실행
                  ↓ TAB→HOME→TAB→토글OFF→자힐burst→ESC→TAB→TAB→토글ON
                  Bus.publish('hand.cast_done')
                  │
                  ▼
memory.action_log: record(action='self_heal', latency_ms=...)
```

**핵심**: 메인 근육 루프 무관. 100% 백그라운드.

### 3.3 SEQ-RCLICK (자힐 중 격수 우클릭)

```
self_heal_seq 시작 시점에 sequence가 직접:
  - snap.red_tab_pos 캡처 → ctx 보관
  - sub-thread spawn:
      while ctx.heal_in_progress:
          mouse_click_at(captured_pos, 'right')
          sleep(0.5)
self_heal_seq 종료 시 ctx.heal_in_progress=False → sub-thread 자연 종료
```

메인 근육 0 영향.

---

## 4. 인터페이스 정의

### 4.1 공통 dataclass (`core/types.py`)

```python
@dataclass(frozen=True)
class CastRequest:
    name: str
    priority: int = 100
    ctx: dict = field(default_factory=dict)
    requested_at: float = field(default_factory=time.monotonic)

@dataclass(frozen=True)
class CastResult:
    request: CastRequest
    status: str  # "ok" | "skipped" | "failed"
    detail: str = ""

@dataclass(frozen=True)
class CastError:
    request: CastRequest
    reason: str

@dataclass
class RuleContext:
    cfg: dict
    cooldowns: dict
    last_cast: dict  # name -> ts
    in_progress: set[str]  # 진행 중 시퀀스 이름
```

### 4.2 Public API

| 영역 | API | 설명 |
|---|---|---|
| **EventBus** | `subscribe(topic, fn)`, `publish(topic, payload)` | |
| **SnapshotStore** | `read()`, `read_field(name)`, `update(**fields)` | |
| **PluginRegistry** | `@rule(...)`, `@sequence(...)`, `@watcher(...)` | 데코레이터 |
| **Hands** | `request_cast(req)` | brain용 |
| **Muscle** | `start()`, `stop()` | worker용 |

---

## 5. 마이그레이션 전략

### 5.1 cfg 자동 변환

```python
# src_v2/config/migration_v1_to_v2.py
def migrate(v1_cfg: dict) -> dict:
    """기존 cfg 구조 → v2 구조 변환."""
    v2 = {
        "muscle": {
            "main_loop_target_hz": 200,
            "tail_sleep_ms": 5,
        },
        "eyes": {
            "yolo": {"every_n": v1_cfg.get("yolo_every_n", 4), ...},
            "ocr": {"every_n": v1_cfg.get("ocr_every_n", 1), ...},
            "cooldown": {"poll_sec": v1_cfg["cooldown"].get("poll_sec", 1.0)},
            ...
        },
        "rules": {
            "self_heal": {
                "enabled": True,
                "thr_hp": v1_cfg.get("self_heal_hp_thr", 50),
            },
            ...
        },
        "hands": {...},
        "regions": {  # 화면 영역 그대로 복사
            "game": v1_cfg.get("game_region"),
            "cooldown": ...,
            "buff": ...,
            "hp": ..., "mp": ...,
        },
    }
    return v2
```

### 5.2 단계적 배포

| 단계 | 대상 | 검증 |
|---|---|---|
| 1 | 로컬 dev (D:\oldbaram) | 단위 테스트 |
| 2 | healer1 PC | 자힐/맵이동/공증/부활 시나리오 |
| 3 | healer2 PC | healer1과 동일 |
| 4 | attacker PC | 격수 측 동작 |

각 단계에서 fps PERF 비교 + 문제 발견 시 src/ 롤백.

---

## 6. 위험 완화

### 6.1 Race Condition

- **SnapshotStore**: 단일 attribute write/read는 GIL 보장. 복합 업데이트는 새 객체로 setattr (예: `coord=(x,y)` 한 번에 set).
- **EventBus**: subscribe/publish 내부 lock으로 핸들러 리스트 race 방지. 핸들러 호출은 lock 밖.
- **Hands queue**: PriorityQueue 자체 thread-safe.

### 6.2 시퀀스 충돌

같은 이름 시퀀스 동시 실행 방지 — `RuleContext.in_progress` set으로 차단:

```python
def self_heal_rule(snap, ctx):
    if "self_heal" in ctx.in_progress:
        return None  # 이미 진행 중
    if snap.hp < ctx.cfg["thr_hp"]:
        return CastRequest("self_heal", priority=10)
```

### 6.3 디버깅 가시성

- 모든 주요 이벤트 → `memory.action_log`에 기록 (옵션 disable 가능)
- `dev_debug_overlay.py`: SnapshotStore 모든 필드를 화면에 띄움 (개발 전용)

---

## 7. 성능 목표 검증 방법

| 항목 | 측정 위치 | 목표 |
|---|---|---|
| Muscle 본체 ms | `main_loop` 내부 perf_counter | ≤ 2ms |
| Snapshot read 횟수/sec | counter | ≥ 200 |
| Bus publish ms | publish 내부 perf_counter | ≤ 10μs |
| Hands queue 지연 | put→get latency | ≤ 5ms |
| fps (전체) | 메인 본체 iter 주기 | ≥ 100 |

벤치마크는 `src_v2/tests/bench/` 아래에 작성.

---

## 8. 9 Phase 구현 순서 (Plan §5 매핑)

### Phase 1 — Core Skeleton
- 파일: `core/{event_bus, snapshot, plugin_registry, types}.py`
- 단위 테스트: pub/sub, snapshot atomic, registry decorator
- 산출: `src_v2/core/`

### Phase 2 — Eyes 마이그레이션
- 파일: `eyes/{base_watcher, capture, yolo_watcher, ocr_watcher, cooldown_watcher, hpmp_watcher, xp_watcher, udp_watcher}.py`
- 기존 src/vision 모듈을 BaseWatcher로 래핑. 로직 그대로.
- 검증: SnapshotStore 값이 기존 워커 결과와 1:1 일치 (병렬 실행하여 비교)

### Phase 3 — Hands
- 파일: `hands/{input_dispatcher, skill_executor, numlock_cycle}.py` + `sequences/*.py`
- 기존 src/input/* + skill_scheduler 로직 이식.
- 모든 시퀀스는 `@sequence` 데코레이터로 등록.

### Phase 4 — Brain
- 파일: `brain/{rule_engine, decision}.py` + `rules/*.py`
- 기존 시전 트리거(자힐/공증/부활/파혼/SEQ-RCLICK/TAB-LOCK) 룰로 분리.
- 단위 테스트: 각 룰별 snap 입력 → CastRequest 검증.

### Phase 5 — Muscle
- 파일: `muscle/main_loop.py` + `decide_direction` 순수 함수
- 단위 테스트: 다양한 snap 입력 → 정해진 방향 검증.

### Phase 6 — UI 분리
- 파일: `ui/publisher.py` + `ui/windows/*` 마이그레이션
- frame_ready emit을 UiPublisher 스레드로.

### Phase 7 — Memory/AI hook
- 파일: `memory/{action_log, ai_hook}.py`
- 인터페이스만 + action_log 기록 시작. 학습은 후속 PDCA.

### Phase 8 — Worker entry + 검증
- 파일: `workers/healer_worker_v2.py`, `workers/attacker_worker_v2.py`
- src/healer_worker run() → src_v2/workers/healer_worker_v2.run() 교체
- 시나리오 테스트 패스 + PERF 측정 (fps≥100).

### Phase 9 — cfg 마이그레이션 + src/ 동결
- `config/migration_v1_to_v2.py` 자동 변환
- src/ 동결, README에 v2 안내.

---

## 9. Out of Scope (이번 PDCA에서 안 함)

- AI 학습 알고리즘 실 구현 (후속 PDCA cycle)
- Web 대시보드 (현 GUI 그대로)
- 추가 게임 지원
- 모바일 컨트롤

---

## 10. 의존성

| 의존 | 버전 | 용도 |
|---|---|---|
| Python | 3.12 | 런타임 |
| numpy | 기존 | 프레임 |
| opencv-python | 기존 | 이미지 |
| ultralytics | 기존 | YOLO |
| easyocr | 기존 | OCR |
| paddleocr | 기존 | OCR |
| PyQt5/6 | 기존 | UI |
| mss | 기존 | 캡처 |

새 의존 0개 — 기존 그대로 사용.

---

## 11. Implementation Guide

### 11.1 구현 순서 핵심 원칙

1. **각 Phase 끝에 단위 테스트 통과 + PERF 측정**.
2. **Phase 8 도달 전엔 사용자 PC 배포 안 함** (src_v2 stand-alone 검증 후).
3. **회귀 발견 시 즉시 src/ 롤백**.

### 11.2 파일 매핑 (기존 → 신규)

| 기존 파일 | 신규 위치 | Phase |
|---|---|---|
| `src/capture/screen.py` | `src_v2/eyes/capture.py` (래핑) | 2 |
| `src/vision/yolo.py` | `src_v2/eyes/yolo_watcher.py` (래핑) | 2 |
| `src/vision/ocr.py` | `src_v2/eyes/ocr_watcher.py` (래핑) | 2 |
| `src/vision/cooldown_ocr.py` | `src_v2/eyes/cooldown_watcher.py` (래핑) | 2 |
| `src/vision/hpmp.py` | `src_v2/eyes/hpmp_watcher.py` (래핑) | 2 |
| `src/vision/xp_ocr.py` | `src_v2/eyes/xp_watcher.py` (래핑) | 2 |
| `src/net/udp_receiver.py` | `src_v2/eyes/udp_watcher.py` (래핑) | 2 |
| `src/input/keys.py` | `src_v2/hands/input_dispatcher.py` (이식) | 3 |
| `src/input/numlock_cycle.py` | `src_v2/hands/numlock_cycle.py` (이식) | 3 |
| `src/input/skill_scheduler.py` | `src_v2/hands/skill_executor.py` (재설계) | 3 |
| `src/input/target_sequence.py` | `src_v2/hands/sequences/*.py` (분할) | 3 |
| `src/fsm/controller.py` | `src_v2/brain/decision.py` + `src_v2/brain/rules/*.py` (분할) | 4 |
| `src/workers/healer_worker.py` | `src_v2/muscle/main_loop.py` + `src_v2/workers/healer_worker_v2.py` | 5,8 |
| `src/ui/main_window.py` | `src_v2/ui/windows/main_window.py` + `src_v2/ui/publisher.py` | 6 |

### 11.3 Session Guide (Module Map for `/pdca do --scope`)

| Module Key | 범위 | Phase | 예상 파일 수 | 예상 라인 |
|---|---|---|---|---|
| `core` | Phase 1 (event_bus + snapshot + registry + types) | 1 | 4 | ~400 |
| `eyes` | Phase 2 (모든 watcher) | 2 | 8 | ~1500 |
| `hands` | Phase 3 (dispatcher + sequences) | 3 | 10+ | ~1800 |
| `brain` | Phase 4 (rule engine + 룰들) | 4 | 12+ | ~1500 |
| `muscle` | Phase 5 (메인 루프 + decide) | 5 | 2 | ~500 |
| `ui` | Phase 6 (publisher + 마이그) | 6 | 5 | ~800 |
| `memory` | Phase 7 (action log + ai hook) | 7 | 3 | ~300 |
| `workers` | Phase 8 (worker entry + 시나리오 테스트) | 8 | 4 | ~600 |
| `migration` | Phase 9 (cfg 마이그 + 동결) | 9 | 2 | ~300 |

**Recommended Session Plan**:
- 세션 1: `--scope core` (~400줄, 단위 테스트 포함)
- 세션 2: `--scope eyes` (~1500줄, 8개 watcher)
- 세션 3: `--scope hands` (~1800줄)
- 세션 4: `--scope brain` (~1500줄, 룰 분할 + 엔진)
- 세션 5: `--scope muscle` (~500줄, 단위 테스트)
- 세션 6: `--scope ui,memory` (~1100줄)
- 세션 7: `--scope workers` (~600줄, 시나리오 테스트 + PERF 측정)
- 세션 8: `--scope migration` (~300줄, src/ 동결)

총 8 세션 예상.

---

## 12. 검증 (Acceptance Criteria)

| ID | 기준 | 검증 |
|---|---|---|
| AC-1 | 모든 Phase 단위 테스트 통과 | `pytest src_v2/tests/` |
| AC-2 | Snapshot 값 = 기존 워커 결과 (Phase 2) | 병렬 비교 로그 |
| AC-3 | 모든 시퀀스 등록 + 시전 가능 (Phase 3) | dispatch 로그 |
| AC-4 | 모든 룰 → CastRequest 정확 발행 (Phase 4) | rule 단위 테스트 |
| AC-5 | Muscle 본체 ≤ 2ms (Phase 5) | bench |
| AC-6 | UI 갱신 정상 + 메인 무영향 (Phase 6) | 화면 + PERF |
| AC-7 | action_log 정상 기록 (Phase 7) | 로그 파일 |
| AC-8 | 시나리오 (자힐/맵이동/공증/부활/파혼/SEQ-RCLICK/TAB-CONFIRM) 모두 통과 (Phase 8) | 통합 테스트 |
| AC-9 | fps ≥ 100 (Phase 8) | PERF 로그 |
| AC-10 | cfg 마이그 무손실 (Phase 9) | diff 비교 |
