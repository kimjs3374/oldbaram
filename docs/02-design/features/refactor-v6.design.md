---
feature: refactor-v6
date: 2026-04-20
author: ai@mgnt.kr
project: oldbaram
version: v6-refactor
---

# refactor-v6 Design Document

> **Summary**: 11개 신규 모듈 분할 지도 + 의존성 그래프 + 단계별 구현 순서. 동작 보존 원칙 엄수.
>
> **Project**: oldbaram
> **Version**: v6-refactor
> **Author**: ai@mgnt.kr
> **Date**: 2026-04-20
> **Status**: Draft
> **Planning Doc**: [refactor-v6.plan.md](../../01-plan/features/refactor-v6.plan.md)

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 거대 파일로 인한 토큰 낭비 + 직전 Patch 2.25 오버엔지니어링으로 인한 불신. |
| **WHO** | 개발자(AI Claude) + 사용자(ai@mgnt.kr). |
| **RISK** | 분할 중 회귀 버그, import cycle, 기능 변경 섞임, 설정 스키마 파손. |
| **SUCCESS** | gap-detector ≥ 90%, 거대 파일 3개 모두 800줄 이하, ARCHITECTURE.md 현행화. |
| **SCOPE** | Plan → Design → Do(11개 신규파일) → Check → Report. |

---

## 1. Overview

### 1.1 Design Goals

1. **단일책임 원칙**: 각 분할 모듈 하나의 관심사만 담당.
2. **동작 완전 보존**: 리팩토링 = 구조만 변경, 동작 0 변경.
3. **블루프린트 중심**: 스킬 wiring 을 `skill_blueprints.py` 중심 구조로 수렴.
4. **토큰 효율**: 파일당 평균 400줄 이하. 확인 시 부분 Read 가능.

### 1.2 Design Principles

- **순수 함수 우선**: wire 모듈은 상태 없는 `_setup_*(worker, ...)` 함수.
- **상대 import**: `from ..input.skill_blueprints import ...` 스타일 유지.
- **단방향 의존**: wire → 서브컴포넌트 → utils. 역방향 금지.
- **기능 변경 금지**: 리팩토링과 버그 수정 동시 금지. 버그는 Check 단계에서만 식별 · Report 이후 별도 사이클.

---

## 2. Architecture Options

### 2.0 Architecture Comparison

| Criteria | Option A: Minimal | Option B: Clean | **Option C: Pragmatic (SELECTED)** |
|----------|:-:|:-:|:-:|
| **Approach** | 헬퍼 추출만, 파일 분할 X | 계층별 완전 재구조 (presentation/application/domain) | 거대 3파일 기능별 분할 + 헬퍼 |
| **New Files** | 0 | 20+ | 11 |
| **Modified Files** | 3 | 30+ | 6 |
| **Complexity** | Low | Very High | Medium |
| **Maintainability** | Low (거대 파일 유지) | Very High | High |
| **Effort** | Small | Huge | Medium |
| **Risk** | Low | Very High (회귀) | Low-Medium |
| **Recommendation** | 토큰 목표 미달성 | 과잉 · Patch 2.25 재현 위험 | **사용자 요구 직접 해결** |

**Selected**: **Option C — Pragmatic Balance**
**Rationale**: 사용자 명시 요구("한 번에 전체", "토큰 낭비 없게") 직접 해결. Option A 는 거대 파일이 남아 목표 미달. Option B 는 Clean Architecture 강제 계층이 Python 데스크탑 매크로에 과잉 추상화 → Patch 2.25 트라우마 재현 위험.

### 2.1 Component Diagram (분할 후)

```
┌──────────────────────────────────────────────────────────────┐
│                       app/healer_gui.py                      │
│                        (엔트리 포인트)                         │
└───────────────────────────┬──────────────────────────────────┘
                            │ creates
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                    ui/main_window.py                         │
│            (축소: UI 빌드 + 다이얼로그 + 역할전환)              │
├──────────────────────────────────────────────────────────────┤
│ uses (ui/ 하위):                                              │
│  ├ settings_io.py   _collect/_load/_save, 영역 dict 헬퍼      │
│  ├ hotkeys.py       F11/F12 등록·콜백·블록 테스트            │
│  ├ regions.py       6개 영역 picker/selector/clear            │
│  ├ overlays_ctrl.py GameOverlay/AlertOverlay 토글·위치·투명도 │
│  └ worker_control.py start/stop/_on_stopped/_on_frame         │
└───────────────────────────┬──────────────────────────────────┘
                            │ creates
                            ▼
┌──────────────────────────────────────────────────────────────┐
│              workers/healer_worker.py                        │
│         (축소: HealerWorker __init__ + run() 상위 오케)       │
├──────────────────────────────────────────────────────────────┤
│ uses (workers/ 하위):                                         │
│  ├ skill_cd_timer.py    SkillCdTimer 독립                    │
│  ├ healer_vision_wire.py setup_ocr_readers(w, cfg)           │
│  ├ healer_skill_wire.py  setup_scheduler(w, keys, ...)       │
│  ├ healer_fsm_wire.py    setup_fsm(w, cfg)                   │
│  └ healer_main_loop.py  run_frame_loop(w) — 메인 반복         │
├──────────────────────────────────────────────────────────────┤
│ uses (input/):   skill_blueprints, skill_scheduler,          │
│                  target_sequence, numlock_cycle, keys        │
│ uses (vision/):  yolo, ocr, cooldown_ocr, hpmp, xp_ocr       │
│ uses (fsm/):     FollowController (+ tab_confirm 내부)       │
│ uses (net/):     UDP sender/receiver, protocol               │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow (불변 — 기존과 동일)

```
격수 PC:  screen → ocr → attacker → UDP broadcast (30Hz)
                                       │
                                       ▼
힐러 PC:  UDP recv → controller (trail/exit_dir/TAB-CONFIRM via tab_confirm.py)
          screen → yolo + ocr + cooldown_ocr + hpmp
                 → ctx → skill_scheduler → skill_blueprints predicates
                                        → keys (SendInput) + target_sequence (blocks)
          → CooldownReport 1Hz UDP 격수
```

### 2.3 Dependencies (분할 내부)

| Component | Depends On | Why |
|-----------|-----------|-----|
| `ui/main_window.py` | `settings_io`, `hotkeys`, `regions`, `overlays_ctrl`, `worker_control`, `dialogs` | 엔트리/다이얼로그/역할전환만 담당, 나머지 위임 |
| `ui/settings_io.py` | nothing (pure I/O) | JSON 저장/복원만, 외부 의존 최소 |
| `ui/hotkeys.py` | `global_hotkeys`, `target_sequence` (블록 테스트) | F11/F12 등록 + 콜백 |
| `ui/regions.py` | `region_picker`, `region_overlay` | 6개 영역 지정 UI 흐름 |
| `ui/overlays_ctrl.py` | `overlay.py` (GameOverlay/SkillAlertOverlay) | 토글·위치·투명도 |
| `ui/worker_control.py` | `workers.healer_worker`, `workers.attacker_worker` | start/stop/frame 처리 |
| `workers/healer_worker.py` | 위 5 wire + `skill_cd_timer` + 기존 서브컴포넌트 | 메인 오케스트레이터 |
| `workers/healer_vision_wire.py` | `vision/*`, `capture/screen` | OCR readers 셋업 |
| `workers/healer_skill_wire.py` | `input/skill_blueprints`, `input/skill_scheduler`, `input/target_sequence` | 스케줄러 훅 셋업 |
| `workers/healer_fsm_wire.py` | `fsm/controller` | Follower 셋업 |
| `workers/healer_main_loop.py` | `capture`, `vision`, `input`, `net` | run() 프레임 루프 |
| `workers/skill_cd_timer.py` | time 모듈만 | OCR 앵커 + monotonic 감산 |
| `fsm/controller.py` | `tab_confirm.py` | TAB-CONFIRM 상태기계 위임 |
| `fsm/tab_confirm.py` | 독립 클래스 | 상태 · 타임아웃 · retry 관리 |

**Import cycle 검증**: `ui/worker_control → workers/healer_worker → (wire modules) → (vision/input/fsm/net)` 단방향. `ui` 에서 `workers` 로, `workers` 에서 하위 서브폴더로만.

---

## 3. Data Model

N/A — Python 데스크탑 매크로. DB 없음. 설정 JSON 스키마 무변경.

### 3.1 설정 JSON 스키마 (보존 대상)

`~/.oldbaram_gui.json` 기존 키 100% 보존. 신규 키 추가 금지. `ui/settings_io.py` 가 헬퍼 함수로 중복 제거하되 키 이름·중첩 구조 불변.

---

## 4. API Specification

N/A — 로컬 앱. UDP 프로토콜은 `net/protocol.py` 불변.

### 4.1 내부 Python API 변경 (서명 보존)

| 함수 | Before | After | Change |
|------|--------|-------|--------|
| `HealerWorker.__init__` | 인라인 셋업 | `setup_ocr_readers()` + `setup_scheduler()` + `setup_fsm()` 호출 | 내부만, 외부 호출 불변 |
| `HealerWorker.set_*_region` (9개) | 각각 try/except | 공통 `_set_region(name, setter, x,y,w,h)` 호출 | 외부 시그니처 불변 |
| `MainWindow._collect_settings` | 인라인 dict 조립 | `settings_io.collect(self)` 위임 | 외부 호출 불변 |
| `MainWindow._setup_global_hotkeys` | 인라인 | `hotkeys.setup(self)` 위임 | 외부 호출 불변 |
| `SkillCdTimer` | healer_worker.py 내부 | `skill_cd_timer.py` 독립 | 참조하는 코드 import 경로만 변경 |

---

## 5. UI/UX Design

N/A — UI 빌드 로직은 `_build_ui` 그대로 유지. 분할은 내부 구현만.

---

## 6. Error Handling

기존 패턴 유지: `try/except` + `self.log.info/error`. 추가 변경 없음.

---

## 7. Security Considerations

N/A — 로컬 매크로. 안티치트 관련 기존 정책 유지(비전만, 메모리 리더 금지).

---

## 8. Test Plan

### 8.1 Test Scope

| Type | Target | Tool |
|------|--------|------|
| Import 검증 | 모든 엔트리 파일 | `python -m py_compile` |
| 구조 검증 | dist_dosa/src ↔ src/ | `diff -rq` |
| 회귀 검증 | 기능 매핑 | `bkit:gap-detector` |
| 정적 품질 | 중대 이슈 | `bkit:code-analyzer` |
| 실증 | 사용자 사냥 세션 | C:\oldbaram 복사 후 10분 세션 |

### 8.2 Test Cases (Key)

- [ ] Happy path: 힐러 모드 시작 → armed ON → 격수 따라가며 자동 힐링 → stop.
- [ ] 자힐 시퀀스: HP 낮춤 → 블록A(TAB→HOME→TAB→토글OFF→부활/자힐 burst) → 블록B(ESC→TAB→TAB→재lock) 동일 재현.
- [ ] F11 테스트 버튼: 체크박스 ON 이면 A+B, OFF 이면 A 단독. 사용자가 직접 F11 테스트.
- [ ] 맵 이동: 격수 이동 → 힐러 trail 추종 → exit_dir → 다음 맵 도착 후 TAB-CONFIRM.
- [ ] 격수 모드: attacker.py 경로 정상 (이번 범위 외지만 회귀 확인).
- [ ] 설정 저장/복원: 모든 영역(cd/nick/buff/hp/mp/game/xp/coord/map) 라운드트립.

---

## 9. Clean Architecture

### 9.1 Layer Structure

Python 프로젝트 — 기능별 폴더 구조 유지.

| Layer | Responsibility | Location |
|-------|---------------|----------|
| **Entry** | 앱 진입 | `app/healer_gui.py`, `app/attacker.py` |
| **UI** | PyQt5 위젯 | `ui/` |
| **Worker** | Qt QThread 백그라운드 | `workers/` |
| **Domain Logic** | FSM, 스킬, 키입력 | `fsm/`, `input/` |
| **Vision** | OCR, YOLO, 캡처 | `vision/`, `capture/` |
| **Protocol** | UDP 통신 | `net/` |
| **Utility** | 공용 헬퍼 | `utils/` |
| **Config** | 설정 로더 | `config.py` |

### 9.2 Dependency Rules

```
Entry ──→ UI ──→ Worker ──→ Domain/Vision/Protocol ──→ Utility
               └─→ (직접 Vision 호출 금지 — worker 경유)
```

### 9.3 This Feature's Layer Assignment

| Component | Layer | Location |
|-----------|-------|----------|
| `ui/settings_io.py` | UI (I/O helper) | `dist_dosa/src/ui/settings_io.py` |
| `ui/hotkeys.py` | UI (hotkey) | `dist_dosa/src/ui/hotkeys.py` |
| `ui/regions.py` | UI (region picker wrapper) | `dist_dosa/src/ui/regions.py` |
| `ui/overlays_ctrl.py` | UI (overlay controller) | `dist_dosa/src/ui/overlays_ctrl.py` |
| `ui/worker_control.py` | UI (worker lifecycle) | `dist_dosa/src/ui/worker_control.py` |
| `workers/skill_cd_timer.py` | Worker (helper) | `dist_dosa/src/workers/skill_cd_timer.py` |
| `workers/healer_vision_wire.py` | Worker (setup) | `dist_dosa/src/workers/healer_vision_wire.py` |
| `workers/healer_skill_wire.py` | Worker (setup) | `dist_dosa/src/workers/healer_skill_wire.py` |
| `workers/healer_fsm_wire.py` | Worker (setup) | `dist_dosa/src/workers/healer_fsm_wire.py` |
| `workers/healer_main_loop.py` | Worker (main loop) | `dist_dosa/src/workers/healer_main_loop.py` |
| `fsm/tab_confirm.py` | Domain (FSM) | `dist_dosa/src/fsm/tab_confirm.py` |

---

## 10. Coding Convention

### 10.1 Naming (Python)

| Target | Rule | Example |
|--------|------|---------|
| 모듈 | snake_case.py | `healer_skill_wire.py` |
| 클래스 | PascalCase | `TabConfirm`, `SkillCdTimer` |
| 함수/메서드 | snake_case | `setup_scheduler()`, `_ctx()` |
| 상수 | UPPER_SNAKE_CASE | `DEFAULT_SLOTS`, `VK_NUMPAD1` |
| 사적 멤버 | `_` 접두 | `_tick_event`, `_hook_block_a` |

### 10.2 Import Order

```python
# 1. stdlib
from __future__ import annotations
import time
import threading
from typing import Callable, Optional

# 2. 3rd party
from PyQt5 import QtCore, QtWidgets

# 3. 상대 import (프로젝트 내)
from ..input.skill_blueprints import SkillSpec, default_skills
from ..input import target_sequence
```

### 10.3 docstring / 주석

- **한글 유지** (기존 스타일).
- docstring 은 모듈/클래스/public 함수 첫 줄만 필수. 그 외는 선택.
- 주석 금지 사항 반복: 의도 불분명 커밋 주석, 죽은 코드 주석 블록. "# 2026-04-20 Patch 2.xx" 식의 갱신 이력은 유지 (기존 스타일).

---

## 11. Implementation Guide

### 11.1 File Structure (분할 후)

```
dist_dosa/src/
├── app/                   (변경 없음)
│   ├── healer_gui.py      entry
│   ├── attacker.py
│   └── ...
├── capture/screen.py     (변경 없음)
├── vision/               (변경 없음)
│   ├── yolo.py
│   ├── ocr.py
│   ├── cooldown_ocr.py
│   ├── hpmp.py
│   ├── xp_ocr.py
│   ├── map_ocr.py
│   └── calibration.py
├── fsm/
│   ├── controller.py     ← 축소 (~700줄)
│   ├── tab_confirm.py    ← NEW (~250줄)
│   └── state.py
├── input/                (변경 없음, 이미 깔끔)
│   ├── skill_blueprints.py
│   ├── skill_scheduler.py
│   ├── target_sequence.py
│   ├── numlock_cycle.py
│   ├── global_hotkeys.py
│   └── keys.py
├── net/                  (변경 없음)
├── workers/
│   ├── healer_worker.py       ← 축소 (~500줄)
│   ├── healer_vision_wire.py  ← NEW (~300줄)
│   ├── healer_skill_wire.py   ← NEW (~250줄)
│   ├── healer_fsm_wire.py     ← NEW (~100줄)
│   ├── healer_main_loop.py    ← NEW (~700줄)
│   ├── skill_cd_timer.py      ← NEW (~100줄)
│   ├── attacker_worker.py
│   ├── heartbeat.py
│   └── control_listener.py
├── ui/
│   ├── main_window.py        ← 축소 (~700줄)
│   ├── settings_io.py        ← NEW (~400줄)
│   ├── hotkeys.py            ← NEW (~200줄)
│   ├── regions.py            ← NEW (~300줄)
│   ├── overlays_ctrl.py      ← NEW (~250줄)
│   ├── worker_control.py     ← NEW (~400줄)
│   ├── dialogs.py
│   ├── overlay.py
│   ├── region_picker.py
│   ├── region_overlay.py
│   ├── hunter_helper_panel.py
│   ├── hunt_report_dialog.py
│   ├── status_strip.py
│   └── styles.py
├── utils/                (변경 없음)
└── config.py             (변경 없음)
```

### 11.2 Implementation Order (회귀 위험 순)

**원칙**: 의존성 하단부터 → 상단으로. 각 단계 완료 시 `python -m py_compile` 통과 확인.

#### Step 1 — 독립 모듈 추출 (위험 최소)

1. [ ] `workers/skill_cd_timer.py` 신설 → `healer_worker.py` L20~89 복사, import 변경.
2. [ ] `healer_worker.py` 에서 SkillCdTimer 클래스 제거하고 `from .skill_cd_timer import SkillCdTimer` 추가.
3. [ ] `fsm/tab_confirm.py` 신설 → `controller.py` L873~1009 의 tab_confirm_tick 및 관련 상태 필드 추출.
4. [ ] `controller.py` 의 Follower 에 `self._tab_confirm = TabConfirm(self)` 위임. 메서드 내부 호출 리다이렉트.

**검증**: `python -c "from dist_dosa.src.workers.healer_worker import HealerWorker; from dist_dosa.src.fsm.controller import Follower"` 통과.

#### Step 2 — healer_worker wire 모듈 (중간 위험)

5. [ ] `workers/healer_vision_wire.py` 신설 → OCR readers 초기화 코드 (`__init__` L99~307 중 OCR 부분) 추출. 함수 `setup_ocr_readers(worker, cfg) -> None` 형태.
6. [ ] `workers/healer_skill_wire.py` 신설 → `_ctx`, `_hook_block_a/b/ab`, `_hook_self_resurrect_post`, default_skills 조립 (L700~827) 추출. 함수 `setup_scheduler(worker, cfg) -> SkillScheduler`.
7. [ ] `workers/healer_fsm_wire.py` 신설 → Follower 초기화 (L664~668) 추출. 함수 `setup_fsm(worker, cfg) -> FollowController`.
8. [ ] `workers/healer_main_loop.py` 신설 → `run()` 본문 (L548~1780) 추출. 함수 `run_frame_loop(worker) -> None`. `stop`/`armed` 는 worker 인스턴스 참조.
9. [ ] 9개 `set_*_region` / `clear_*_region` → 공통 헬퍼 `_apply_region(worker, name, setter, x, y, w, h)` 로 대체.
10. [ ] `healer_worker.py` 축소: `__init__` 은 `setup_*()` 호출 3줄 · `run()` 은 `run_frame_loop(self)` 호출 1줄.

**검증**: `python -m py_compile dist_dosa/src/workers/healer_worker.py` 통과 + `grep -n "def set_" dist_dosa/src/workers/healer_worker.py` 결과 헬퍼 위임.

#### Step 3 — main_window 분할 (중간 위험)

11. [ ] `ui/settings_io.py` 신설 → `_collect_settings`, `_load_settings`, `_save_settings` (L3199~3731) 추출. 함수 `collect(mw) -> dict`, `load(mw, data) -> None`, `save(mw) -> None`. 영역 저장 쌍 4개는 내부 dict 헬퍼로 통합.
12. [ ] `ui/hotkeys.py` 신설 → `_setup_global_hotkeys`, `_on_hotkey_fired`, `_on_test_block_a/b` (L1656~1821) 추출.
13. [ ] `ui/regions.py` 신설 → 6개 영역 picker/selector/clear (L1330~1827) 추출.
14. [ ] `ui/overlays_ctrl.py` 신설 → GameOverlay/SkillAlertOverlay 토글·투명도·위치 (L1127~1321) 추출.
15. [ ] `ui/worker_control.py` 신설 → `start_worker`, `_start_healer`, `_start_attacker`, `stop_worker`, `_on_stopped`, `_on_frame`, `_activate_msw_window`, `_snap_to_msw_right` (L2532~3197) 추출.
16. [ ] `ui/main_window.py` 축소: 엔트리/다이얼로그/역할전환/원격제어만 남기고 나머지는 thin wrapper.

**검증**: `python -m py_compile dist_dosa/src/ui/main_window.py` 통과 + GUI 실행 후 설정 로드/저장 라운드트립 확인.

#### Step 4 — 중복·legacy 제거 (낮은 위험)

17. [ ] `skill_scheduler.py` 의 legacy re-export (`from .skill_blueprints import SkillSpec, default_skills, _cd_empty, _buff_present, _attacker_debuff_present  # noqa: F401`) 사용처 grep. 실사용자 없으면 제거, 있으면 블루프린트로 직접 import 하도록 변경.
18. [ ] `_last_h_coord` 등 미사용 필드 제거.
19. [ ] Patch 2.25 흔적 final sweep (이미 롤백 확인됐으나 재확인).

#### Step 5 — src sync + ARCHITECTURE.md

20. [ ] `cp -r dist_dosa/src/* src/` (혹은 rsync) → diff 공백 확인.
21. [ ] `docs/ARCHITECTURE.md` 전면 재작성.

### 11.3 Session Guide

#### Module Map

| Module | Scope Key | Description | Estimated Turns |
|--------|-----------|-------------|:---------------:|
| skill_cd_timer + tab_confirm 추출 | `step-1` | 독립 모듈 2개 | 6 |
| healer_worker 4분할 | `step-2` | wire 모듈 4개 + 헬퍼 | 15 |
| main_window 5분할 | `step-3` | UI 모듈 5개 | 20 |
| 중복·legacy 제거 | `step-4` | 정리 | 4 |
| sync + ARCHITECTURE | `step-5` | 배포 준비 | 5 |

#### Recommended Session Plan (한 번에 전체 — 사용자 결정)

| Session | Phase | Scope | Turns |
|---------|-------|-------|:-----:|
| 현재 세션 | Plan + Design + Do + Check + Report | 전체 | 60~80 |

사용자가 "한 번에 전체" 선택 → 이 세션에 모든 단계 진행. Turns 제한 발생 시 세션 분할.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-20 | 초안 — Option C 선택 기반 11 모듈 분할 지도 | ai@mgnt.kr |
