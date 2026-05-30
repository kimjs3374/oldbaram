# Plan: healer-mainloop-refactor

> **Scope 확장 알림**: 사용자 Checkpoint 답변에 따라 본 Plan은 단순 healer 메인 루프 리팩토링이 아닌 **전체 시스템(healer + attacker + UI) 사람-유사 AI 매크로 재설계**로 확장됨. 파일명은 사용자 지시 유지.

| 항목 | 값 |
|---|---|
| Feature | `healer-mainloop-refactor` (전면 재설계) |
| Phase | Plan |
| 작성일 | 2026-04-25 |
| 상태 | 작성 완료 — 사용자 승인 대기 |

---

## Executive Summary

| 관점 | 요약 |
|---|---|
| **Problem (문제)** | 메인 루프가 보조 작업(emit/log/cooldown submit/UI dict build/dispatch)을 떠안아 fps 124→30 회귀, CPU/GPU 동시 점유로 PC 온도 80°C+. 단순 키 결정 루프가 1-2ms여야 하는데 13ms+. 백그라운드 스레드 11개가 GIL/GPU 경합. |
| **Solution (해결)** | 사람-유사 4영역 분리 아키텍처 — **눈(감시)/뇌(트리거)/손(실행)/근육(메인루프)** + 기억(학습). 기존 `src/` 보존, 신규 `src_v2/` 빅뱅 재작성. Event Bus 기반 느슨한 결합 + 플러그인 레지스트리로 사용자 확장. |
| **Function/UX (기능/사용자)** | 모든 기존 기능 100% 보존 (자힐/맵이동/넘락/스킬/SEQ-RCLICK/TAB-LOCK/오버레이). 새 기능은 플러그인 등록만으로 추가. fps 100+ 회복 → 게임 반응성 개선. 사용자가 트리거 룰 커스터마이즈 가능. |
| **Effect/Core Value (효과/핵심가치)** | 메인 1-2ms 본체로 슬림화 → fps 100+. CPU/GPU 점유율 절반 이하 → PC 발열 정상화. 새 기능 추가 시 기존 코드 건드릴 일 없음 → 회귀 위험 0. AI 자가학습 모듈 추가 ready. |

---

## Context Anchor

| 키 | 값 |
|---|---|
| **WHY** | 3주간 누적된 메인 루프 비대화로 fps 1/4 → PC 발열 80°C+. 메크로 본질("키 결정")이 보조 작업에 묻힘. 사람-유사 설계로 본질 회복 + 미래 확장 ready. |
| **WHO** | 격수(인간 조작) + 힐러 2 PC 자동 봇. 사용자 = 매크로 운영자(개발자 겸). |
| **RISK** | 빅뱅 재작성 = 기능 회귀 위험 큼. 완화책: 기존 `src/` 동결 보존, `src_v2/` 신규 작성, 기능별 단위 테스트(시나리오 기록), 단계적 배포(healer1 먼저 → healer2 → attacker). |
| **SUCCESS** | (1) fps 100+ 회복 (PERF 측정), (2) 모든 기존 기능 동등 동작 (시나리오 패스), (3) 새 스킬/룰 플러그인 1건 등록 5분 내 가능. |
| **SCOPE** | 전체 시스템 재설계. healer/attacker 워커 + UI receiver + 백그라운드 OCR/YOLO/UDP/스케줄러 모두. 사용자 cfg/region 설정은 마이그레이션. |

---

## 1. 비전: 사람-유사 AI 매크로 (Human-Mimic Architecture)

옛바 매크로의 본질을 사람의 의식 모델로 재설계:

```
┌─────────────────────────────────────────────────────────────┐
│                    HUMAN-MIMIC MACHINE                      │
│                                                             │
│   ┌─────┐      ┌──────┐      ┌──────┐      ┌──────────┐    │
│   │ 눈  │ ───▶ │  뇌  │ ───▶ │  손  │      │  기억    │    │
│   │감시 │      │트리거│      │실행  │ ◀──▶ │ 학습/AI │    │
│   └─────┘      └──────┘      └──────┘      └──────────┘    │
│      ▲             │             │              ▲           │
│      │             ▼             ▼              │           │
│      └──── 근육(메인루프: 키 hold/release) ────┘           │
│                                                             │
│           이벤트 버스 (Event Bus, lock-free)                │
└─────────────────────────────────────────────────────────────┘
```

| 역할 | 비유 | 책임 | 빈도 |
|---|---|---|---|
| **눈 (감시)** | 시각 | 화면 캡처, OCR, YOLO, UDP 수신 | 백그라운드 (10-30Hz 각자) |
| **뇌 (트리거)** | 의식 | 감시 결과 → 룰 평가 → 시전 결정 | 이벤트 기반 (감시 변화 시) |
| **손 (실행)** | 운동 | 키/마우스 입력, 스킬 시퀀스 | 트리거 명령 시 |
| **기억/학습** | 메모리 | 행동 결과 기록, AI 패턴 학습 | 비동기 (시간당 등) |
| **근육 (메인)** | 무의식 | 키 hold/release, 좌표 비교 (1-2ms) | 100+Hz |

핵심 원칙:
1. **메인 루프는 근육만**: 키 hold/release + 좌표 비교 1-2ms 이하. 다른 거 없음.
2. **모든 보조 작업은 백그라운드 + Event Bus**: 메인은 latest snapshot read만.
3. **트리거는 룰 엔진 + 플러그인**: 룰 추가/수정만으로 새 스킬 대응.
4. **실행은 큐 + 우선순위 디스패처**: 시전 충돌 자동 해소.
5. **AI 학습 모듈 ready**: 행동 결과 시계열 저장, 향후 RL/패턴 학습 모듈 plug-in.

---

## 2. 요구사항 (Requirements)

### 2.1 기능 요구사항 (Functional)

| ID | 요구사항 | 출처 |
|---|---|---|
| FR-1 | 메인 루프 본체 = 키 hold/release + 좌표 비교만 (목표 1-2ms) | 사용자 명시 |
| FR-2 | 따라가기 / 맵이동 / 넘락 토글 → 메인 루프 직속 | 사용자 명시 |
| FR-3 | OCR/YOLO/UDP/HP/MP/쿨다운 → 모두 "눈" 영역 백그라운드 | 사용자 명시 |
| FR-4 | 트리거(자힐/공증/부활/파혼술/SEQ-RCLICK 등) → "뇌" 룰 엔진 | 사용자 명시 |
| FR-5 | 스킬 시전(키 입력/시퀀스) → "손" 실행 큐 | 사용자 명시 |
| FR-6 | 모든 기존 기능 100% 보존 (회귀 0) | 사용자 명시 |
| FR-7 | 새 스킬/룰 추가 = 플러그인 등록만으로 가능 (기존 코드 무수정) | 사용자 명시 |
| FR-8 | AI 자가학습 모듈 plug-in ready (행동 결과 기록 + 모듈 hook) | 사용자 명시 |
| FR-9 | 사용자 커스텀 가능 (룰/임계값/우선순위) | 사용자 명시 |
| FR-10 | 기존 `src/` 보존 + `src_v2/` 신규 작성 (롤백 안전) | 사용자 명시 |
| FR-11 | 기존 cfg/region/스킬 설정 마이그레이션 | 명시되진 않았으나 운영 필수 |

### 2.2 비기능 요구사항 (Non-Functional)

| ID | 요구사항 | 측정 |
|---|---|---|
| NFR-1 | fps 100+ 회복 | PERF 로그 |
| NFR-2 | CPU/GPU 점유율 절반 이하 | 작업관리자 |
| NFR-3 | PC 온도 80°C 이하 | HW 모니터 |
| NFR-4 | 메인 루프 본체 1-2ms (PERF total) | PERF 로그 |
| NFR-5 | 보조 스레드 GIL/GPU 경합 최소화 | py-spy |

### 2.3 제약 (Constraints)

- **넥슨 안티치트**: 메모리 접근 불가 → 비전(OCR+YOLO)만 유효 (변경 불가)
- **Tailscale UDP**: 격수↔힐러 통신 (변경 불가)
- **EasyOCR(GPU) + PaddleOCR(공유)** 유지 (모델 변경 시 정확도 위험)
- **사용자 PC 환경**: Win11, Python 3.12, RTX 4070 SUPER

---

## 3. 현재 상태 분석 (As-Is)

### 3.1 메인 루프 비대화 누적 (2026-04-19 → 04-25)

| 시점 | fps | total ms | full ms | 비고 |
|---|---|---|---|---|
| 4/19 (bak) | ? | ? | ? | refactor 시작 전 |
| 4/23 12:44 | **124** | 0.0 | 0.8 | 정상 baseline |
| 4/25 (수정 전) | 22 | 11.0 | 70.5 | 회귀 |
| 4/25 (4건 fix 후) | 30 | 12.7 | 13.2 | full 회복, total 잔존 |

**원인 (전수 검토 결과)**:
- 메인 루프에 dict-22-key emit + log f-string + cooldown 4종 submit + 진단 로그 누적
- 백그라운드 스레드 11개+: cooldown_ocr × 3 (cd/buff/chat) + xp_ocr + hpmp + easyocr + yolo + AsyncGrabber + scheduler + udp_recv + heartbeat
- worker thread 매 cycle frame.copy() (cooldown 3 인스턴스 × ~6MB) → 메모리 대역폭 점유
- GIL 경합으로 yolo predict 43→60ms 부풀음

### 3.2 기존 구조 (src/)

```
src/
├── workers/healer_worker.py  (3123 lines, 메인 루프 비대화)
├── workers/attacker_worker.py
├── fsm/controller.py          (943 lines, fol.update 매 프레임)
├── vision/{ocr, yolo, hpmp, cooldown_ocr, xp_ocr, map_ocr}.py
├── input/{keys, skill_scheduler, target_sequence, numlock_cycle}.py
├── net/{udp_receiver, udp_sender, protocol}.py
├── ui/{main_window, overlays, ...}
└── app/{healer_gui*, attacker, hunt_analytics}.py
```

### 3.3 임시 패치 적용 상태 (현재 6건)

> 신규 v2 합류 시까지 운영용. 마이그레이션 후 src/ 동결.

1. ATK-COORD-JUMP 필터 제거 (controller.py)
2. crop_frame.copy() 제거 (healer_worker.py:1353)
3. SEQ-RCLICK 스레드 dispatch
4. TAB-LOCK pending 스레드 dispatch
5. cooldown_ocr worker frame.copy() 제거
6. tail sleep 5ms 유지 (4→5 롤백)

---

## 4. 제안 아키텍처 (To-Be)

### 4.1 디렉터리 구조

```
oldbaram/
├── src/                    # 동결, 보존 (롤백용)
└── src_v2/                 # 신규 빅뱅 작성
    ├── core/
    │   ├── event_bus.py        # lock-free pub/sub
    │   ├── snapshot.py         # 감시 결과 atomic snapshot
    │   └── plugin_registry.py  # 플러그인 등록/조회
    ├── eyes/                   # 감시 (백그라운드)
    │   ├── capture.py          # 화면 캡처 (mss async)
    │   ├── yolo_watcher.py     # YOLO 백그라운드
    │   ├── ocr_watcher.py      # 좌표/맵 OCR
    │   ├── cooldown_watcher.py # 쿨다운/버프/채팅 OCR
    │   ├── hpmp_watcher.py     # HP/MP OCR
    │   ├── xp_watcher.py       # 경험치 OCR
    │   └── udp_watcher.py      # UDP 수신
    ├── brain/                  # 트리거 (룰 엔진)
    │   ├── rule_engine.py      # 룰 평가 + 우선순위
    │   ├── rules/              # 플러그인 룰 (사용자 커스텀)
    │   │   ├── self_heal.py
    │   │   ├── attacker_revive.py
    │   │   ├── parhon.py
    │   │   ├── seq_rclick.py
    │   │   └── tab_lock.py
    │   └── decision.py         # 결정 → 실행 큐 push
    ├── hands/                  # 실행 (스킬/입력)
    │   ├── input_dispatcher.py # 키/마우스 디스패처
    │   ├── skill_executor.py   # 시퀀스 실행
    │   ├── numlock_cycle.py    # 넘락 토글 사이클
    │   └── sequences/          # 시퀀스 레지스트리 (플러그인)
    ├── muscle/                 # 메인 루프 (근육)
    │   └── main_loop.py        # 1-2ms 본체 (키 결정만)
    ├── memory/                 # 학습/기록
    │   ├── action_log.py       # 행동 시계열 기록
    │   ├── ai_hook.py          # AI 모듈 plug 포인트
    │   └── pattern_learner.py  # (Future) RL/패턴
    ├── ui/                     # UI (비동기 dispatcher 분리)
    │   ├── publisher.py        # frame_ready 분리 publish 스레드
    │   └── windows/            # 기존 main_window 마이그레이션
    ├── config/                 # 설정 (cfg 마이그레이션)
    └── migrations/             # src → src_v2 마이그레이션 스크립트
```

### 4.2 핵심 컴포넌트

#### A. Event Bus (lock-free)

```python
# 표시용 의사 코드
class EventBus:
    def publish(self, topic: str, payload: Any): ...
    def subscribe(self, topic: str, handler: Callable): ...
    # 구현: ringbuffer + 원자 인덱스, lock-free read
```

토픽 예: `eye.coord`, `eye.hp`, `eye.cooldown`, `udp.attacker_state`, `brain.cast_request`, `hand.cast_done`, `memory.action`.

#### B. Snapshot (atomic ref read)

```python
class Snapshot:
    """모든 감시 결과의 마지막 값. 메인은 이거만 read."""
    coord: tuple
    hp: int
    mp: int
    cooldowns: dict
    attacker_state: AttackerState
    # ... atomic write by watchers, lock-free read by muscle
```

메인 루프는 lock 없이 snapshot 필드만 읽음 (CPython GC + GIL 보장 atomic ref read).

#### C. Plugin Registry

```python
@register_rule(name="self_heal", priority=10, topics=["eye.hp"])
def self_heal_rule(snapshot, ctx):
    if snapshot.hp < ctx.thr_hp:
        return CastRequest("self_heal")
    return None
```

사용자가 신규 룰 파일 1개 만들고 등록만 하면 끝. 기존 코드 변경 0.

#### D. 메인 루프 (muscle/main_loop.py)

```python
def main_loop(snap, hands):
    while running:
        t0 = perf_counter()
        # 1. snapshot read (lock-free)
        h = snap.healer_coord
        a = snap.attacker_coord
        # 2. 따라가기 결정
        want = decide_direction(h, a, snap.healer_map, snap.attacker_map)
        # 3. 키 hold/release
        hands.set_direction(want)
        # 4. 넘락 토글 체크
        if snap.numlock_cycle_due:
            hands.toggle_numlock()
        # 1-2ms 목표
        sleep(max(0, 0.005 - (perf_counter() - t0)))
```

**메인은 트리거 평가 안 함**. 트리거는 brain 스레드가 snapshot 변화 이벤트 받아서 자체 평가.

### 4.3 데이터 흐름

```
[화면/UDP] ──▶ Eyes (백그라운드 N개)
                 │
                 ▼
              [Snapshot] (atomic, lock-free)
                 │           │
                 ▼           ▼
             [Brain]      [Muscle]
            (룰엔진)    (메인루프)
                 │           │
                 └── EventBus┘
                     │       │
                     ▼       ▼
                  [Hands] (실행큐)
                     │
                     ▼
                  [Memory] (기록/학습)
```

### 4.4 확장성

새 스킬 추가 예시:
1. `src_v2/brain/rules/new_skill.py` 작성
2. `@register_rule(...)` 데코레이터
3. 끝.

새 OCR 영역 추가:
1. `src_v2/eyes/new_watcher.py` 작성
2. `@register_watcher(...)` 데코레이터
3. snapshot에 새 필드 추가
4. 끝.

---

## 5. 단계 계획 (Phasing)

> 사용자: "전체 빅뱅 재작성이지만 데이터 보존". → src_v2/ 빅뱅 작성, 단계적 배포(테스트 안전).

### Phase 1 — 골격 (core)
- `src_v2/core/{event_bus, snapshot, plugin_registry}.py`
- 단위 테스트로 Bus/Snapshot 검증

### Phase 2 — 눈 (eyes) 마이그레이션
- 기존 vision/* 모듈 → eyes/* 래핑
- Snapshot 채우는 동작만, 기존 src 워커는 미동
- 검증: snapshot 값이 기존 워커 결과와 일치

### Phase 3 — 손 (hands)
- 기존 input/* + scheduler → hands/*
- 시퀀스 레지스트리 도입

### Phase 4 — 뇌 (brain)
- 기존 시전 트리거(자힐/공증/부활/파혼/SEQ-RCLICK/TAB-LOCK) → 플러그인 룰로
- 룰 엔진 + 우선순위 큐

### Phase 5 — 근육 (muscle)
- 신규 메인 루프 작성
- 기존 src/healer_worker run() 대체 (worker entry만 교체)

### Phase 6 — UI 분리
- frame_ready emit → publisher 스레드로
- main_window 등 receiver 그대로 (publisher만 교체)

### Phase 7 — 메모리/학습 plug 포인트
- action_log 기록 시작
- ai_hook 인터페이스 정의 (구체 구현은 후속 PDCA cycle)

### Phase 8 — 검증/배포
- healer1 → healer2 → attacker 순차 배포
- 시나리오 테스트: 자힐/맵이동/공증/부활/파혼/TAB-CONFIRM/SEQ-RCLICK
- PERF 비교: fps 100+ 도달 검증

### Phase 9 — 마이그레이션 도구 + src/ 동결
- 사용자 cfg/region/스킬 설정 자동 마이그레이션 스크립트
- src/ 코드 동결 (롤백 보존)

---

## 6. 성공 기준 (Success Criteria)

| ID | 기준 | 측정 방법 |
|---|---|---|
| SC-1 | fps ≥ 100 (10초 평균) | PERF 로그 |
| SC-2 | total ≤ 2ms (메인 본체) | PERF 로그 |
| SC-3 | full ≤ 5ms | PERF 로그 |
| SC-4 | 자힐/맵이동/공증/부활/파혼/SEQ-RCLICK/TAB-CONFIRM 전부 정상 동작 | 시나리오 패스 |
| SC-5 | 새 룰 1건 추가 5분 내 등록 가능 | 실측 |
| SC-6 | CPU 점유율 ≤ 30%, GPU ≤ 50% | 작업관리자 |
| SC-7 | PC 온도 ≤ 75°C | HW 모니터 |
| SC-8 | 기존 cfg 마이그레이션 무손실 | 비교 검증 |

---

## 7. 위험 및 완화 (Risks & Mitigation)

| ID | 위험 | 영향 | 완화 |
|---|---|---|---|
| R-1 | 빅뱅 재작성 회귀 | High | src/ 보존, 단계적 배포, 시나리오 테스트 |
| R-2 | Event Bus 자체 오버헤드 | Med | lock-free ringbuffer, 마이크로벤치 검증 |
| R-3 | 마이그레이션 cfg 손실 | High | 자동 마이그레이션 스크립트 + 백업 |
| R-4 | Phase 6 UI 비동기 분리 시 race | Med | 단위 테스트 + UI 갱신 누락 모니터 |
| R-5 | AI 학습 모듈 plug 인터페이스 미흡 | Low | Phase 7만 hook 정의, 실 구현은 후속 PDCA |
| R-6 | 사용자 운영 중단 시간 | Med | healer1 먼저 배포, 검증 후 healer2/attacker |

---

## 8. 비고 (Notes)

- **현재 적용된 임시 6건 패치**는 src/에 남기되, src_v2/ 합류 시 의미 없어짐.
- **AI 자가학습**은 본 Plan에서는 hook 인터페이스 + action_log만 정의. 실제 학습 알고리즘(RL/모방학습 등)은 별도 PDCA cycle.
- 사용자 명시 우선 원칙 유지: 룰/임계값/시퀀스 변경은 항상 사용자 cfg에서.
