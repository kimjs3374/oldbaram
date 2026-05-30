---
feature: refactor-v6
date: 2026-04-20
author: ai@mgnt.kr
project: oldbaram
version: v6-refactor
---

# refactor-v6 Planning Document

> **Summary**: 거대 3파일(main_window 3731줄 · healer_worker 2029줄 · controller 1013줄) 분할 + 블루프린트 중심 간소화 + ARCHITECTURE.md 재작성. 사용자가 Plan/Design/Do/Check/Report 확인 시 토큰 낭비 없도록 모듈 단일책임화.
>
> **Project**: oldbaram (옛날바람 3-PC 자동 힐링 매크로)
> **Version**: v6-refactor
> **Author**: ai@mgnt.kr
> **Date**: 2026-04-20
> **Status**: Draft

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | 코드 17,132줄 중 거거대 3파일(6,773줄, 39.5%) 독점. 확인/수정 시 매번 수천 줄 Read → 토큰 낭비 + 사용자 신뢰 하락. Patch 2.25 오버엔지니어링 흔적·legacy re-export·미사용 필드 잔존. |
| **Solution** | bkit PDCA 오케스트레이션으로 단일책임 모듈 분할(11개 신규 파일) + 중복 헬퍼 추출(`_set_region`×9, 설정 쌍 4개) + SkillBlueprint 래퍼(`_hook_block_*`) 를 skill_wire 모듈로 이관. 동작 불변. |
| **Function/UX Effect** | 기능 변경 0. 개발자(AI) 측면: 파일당 평균 Read 크기 ~400줄로 감소 → 향후 세션 토큰 사용 40~60% 절감 기대. 사용자 측면: 버그 수정 사이클 단축. |
| **Core Value** | "확인할 때 토큰 낭비 없게" — 사용자 직접 요구. 블루프린트 중심 구조로 신규 스킬 추가 비용 최소화. |

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 거대 파일로 인한 토큰 낭비 + 직전 Patch 2.25 오버엔지니어링 사고로 인한 불신. 구조 단순화로 신뢰 회복. |
| **WHO** | 개발자(AI Claude) + 사용자(ai@mgnt.kr). AI 는 파일 읽기 토큰 절약, 사용자는 버그 수정 속도 향상. |
| **RISK** | (1) 분할 중 회귀 버그 발생 (2) 사용자 C:\oldbaram 복사 사이클 증가 (3) Patch 2.25 재현 위험 — 오버엔지니어링 재발. |
| **SUCCESS** | 모든 기존 동작 보존 (gap-detector ≥ 90%) + ARCHITECTURE.md 현행화 + 거대 파일 3개 모두 800줄 이하로 축소. |
| **SCOPE** | Plan → Design(3옵션) → Do(분할 실행) → Check(gap-detector + code-analyzer) → Report. 단계별 dist_dosa 수정 후 src sync. |

---

## 1. Overview

### 1.1 Purpose

전체 리팩토링으로 (a) 토큰 사용량 감소 (b) 블루프린트 중심 구조 확립 (c) 의심 버그/죽은 코드 정리 (d) ARCHITECTURE.md 현행화 달성.

### 1.2 Background

- 2026-04-20 직전 세션: Patch 2.25 (HpMp edge trigger + scheduler cast_queue 우회) 전면 롤백. 사용자 "검증 없는 가설 툭 던지지 말 것" 경고.
- 세션 스냅샷 (`memory/project_session_snapshot.md`) 확인: 롤백 완료, `_prev_hp_zero` 등 모든 2.25 필드 제거 확인.
- 사용자 현재 요구: "플러그인 다 활용, bkit pdca 데려와, 좆같이 하지 마". → 직접 편집 아닌 bkit 오케스트레이션 위임.

### 1.3 Related Documents

- **메모리 규율**: `D:\oldbaram\CLAUDE.md` (반성문 체크리스트 20항)
- **세션 스냅샷**: `C:\Users\ENG\.claude\projects\D--oldbaram\memory\project_session_snapshot.md`
- **블루프린트 규칙**: `memory/feedback_skill_blueprints_separation.md`
- **배포 정본 규칙**: `memory/project_dist_canonical.md`
- **VK 배치 (2026-04-20)**: `memory/feedback_vk_layout_2026_04_20.md`
- **블록 A/B 최종**: `memory/feedback_block_ab_final_2026_04_20.md`
- **기존 아키텍처**: `docs/ARCHITECTURE.md` (재작성 대상, 2026-04-19 v5.17)
- **맵 이동 로직**: `docs/../맵이동 로직.md`

---

## 2. Scope

### 2.1 In Scope

- [ ] `ui/main_window.py` (3,731줄) → 6분할: `settings_io.py`, `hotkeys.py`, `regions.py`, `overlays_ctrl.py`, `worker_control.py`, 잔여 `main_window.py`
- [ ] `workers/healer_worker.py` (2,029줄) → 4분할: `healer_vision_wire.py`, `healer_skill_wire.py`, `healer_fsm_wire.py`, `healer_main_loop.py` + `skill_cd_timer.py` 독립화
- [ ] `fsm/controller.py` (1,013줄) → `tab_confirm.py` 추출 (L873~1009 상태기계)
- [ ] 중복 헬퍼 추출: `set_*_region` 9개 → `_set_region()` 1개 / `_collect_*`·`_load_*` 영역 4쌍 → 영역 dict 헬퍼
- [ ] 블루프린트 wiring 이관: `_hook_block_a/b/ab`, `_hook_self_resurrect_post` → `healer_skill_wire.py`
- [ ] 죽은 코드 제거: `_last_h_coord` 등 미사용 필드, legacy re-export 검토
- [ ] `docs/ARCHITECTURE.md` 전면 재작성: 현재 파일 트리·VK 배치·블록 A/B·F11/F12·우선순위·동적 임계치·hunt_analytics 반영
- [ ] dist_dosa → src sync (diff 공백 보장)

### 2.2 Out of Scope

- **`skill_blueprints.py` / `skill_scheduler.py` / `target_sequence.py`**: 이미 깔끔 (사용자 확정 스펙). 미세 오탈자/주석 외 변경 금지.
- **YOLO 학습 / 모델 교체**: 별도 사이클.
- **메모리 리더 재도입**: 메모리 `feedback_forbidden_approaches.md` 금지 재제안 목록.
- **새 기능 추가**: 순수 구조 리팩토링 + 의심 버그 정리만. 신규 동작 금지.
- **격수 PC (`attacker.py`, `attacker_worker.py`)**: 이번 범위 제외 (영향 최소화).

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | `main_window.py` 각 분할 모듈 단일책임 · 800줄 이하 | High | Pending |
| FR-02 | `healer_worker.py` 분할 후 import 체인 정상 · run() 메인루프 동작 보존 | High | Pending |
| FR-03 | `fsm/controller.py` TAB-CONFIRM 분리 후 trail/exit_dir 동작 동일 | High | Pending |
| FR-04 | `_hook_block_*` 래퍼 이관 후 F11/F12 블록 A/B 시퀀스 동일 재현 | High | Pending |
| FR-05 | 중복 `set_*_region` 9 → 1 헬퍼 · 외부 API 서명 100% 보존 | High | Pending |
| FR-06 | 설정 JSON 스키마(`~/.oldbaram_gui.json`) 호환 · 기존 사용자 설정 무손실 로드 | High | Pending |
| FR-07 | `docs/ARCHITECTURE.md` 전면 재작성 · 실제 파일 트리 100% 일치 | High | Pending |
| FR-08 | Patch 2.25 흔적·legacy re-export·미사용 필드 제거 | Medium | Pending |
| FR-09 | dist_dosa/src ↔ src/ diff 공백 (sync 검증) | High | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| 동작 보존 | 모든 기존 시나리오 동일 결과 | bkit:gap-detector ≥ 90% 매치 |
| 코드 크기 | 거대 파일 평균 50% 이상 축소 | `wc -l` 전/후 비교 |
| Import 무결성 | 모든 엔트리 import 통과 | `python -m py_compile` |
| 사용자 토큰 | 파일당 Read 평균 ≤ 500줄 | 분할 후 파일 크기 확인 |
| 정적 품질 | 중대 이슈 0 | bkit:code-analyzer |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] 11개 신규 모듈 파일 생성 완료 (dist_dosa 기준)
- [ ] `main_window.py` ≤ 800줄, `healer_worker.py` ≤ 600줄, `controller.py` ≤ 700줄
- [ ] F11/F12/자힐/자가부활/공력증강/파혼술 동작 사용자 실증
- [ ] `docs/ARCHITECTURE.md` 재작성 완료
- [ ] `diff -rq dist_dosa/src src | grep -v __pycache__` 공백
- [ ] `bkit:gap-detector` 매치율 ≥ 90%
- [ ] `bkit:code-analyzer` 중대 이슈 0

### 4.2 Quality Criteria

- [ ] 기존 OCR·YOLO·FSM·UDP·키입력 체인 100% 보존
- [ ] 사용자 메모리 규율 전 항목 준수
- [ ] 어느 분할 파일도 import cycle 없음

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| 분할 중 import cycle 발생 | High | Medium | Design 단계에서 의존성 그래프 먼저 그림. 신규 wire 모듈은 단방향 import만. |
| Patch 2.25 같은 기능 변경 섞임 | High | Medium | **기능 변경 절대 금지** 룰. Check 단계 gap-detector 로 behavioral diff 확인. 메모리 `feedback_dont_rollback_verified_fix.md` 준수. |
| 사용자 C:\oldbaram 복사 사이클 증가 | Medium | High | 한 번에 전체 완료 후 1회 sync 지시. 중간 단계 복사 요청 금지. |
| 설정 JSON 스키마 깨짐 | High | Low | `_collect_settings`/`_load_settings` 시그니처 보존. 영역 키 이름 불변. |
| ARCHITECTURE.md 와 코드 재괴리 | Medium | Medium | 재작성을 **마지막 Do 단계 직후** 에 배치. 파일 트리를 실제 `find` 결과로 채움. |
| bkit gap-detector 오탐 | Medium | Medium | 경계 시나리오(F11, 자힐, 맵 이동)는 수동 재확인. |

---

## 6. Impact Analysis

### 6.1 Changed Resources

| Resource | Type | Change Description |
|----------|------|--------------------|
| `dist_dosa/src/ui/main_window.py` | Python module | 3,731 → ~800줄. 6개 하위 모듈로 분할. MainWindow 클래스는 남기되 메서드 다수 이관. |
| `dist_dosa/src/workers/healer_worker.py` | Python module | 2,029 → ~600줄. 4개 wire 모듈로 분할. SkillCdTimer 독립 파일. |
| `dist_dosa/src/fsm/controller.py` | Python module | 1,013 → ~700줄. TAB-CONFIRM FSM 별도 클래스로. |
| `dist_dosa/src/input/skill_blueprints.py` | Python module | 무변경 (이미 정리된 선언 모듈). |
| `dist_dosa/src/input/skill_scheduler.py` | Python module | legacy re-export 블록 검토만 (실사용 없으면 제거). |
| `dist_dosa/src/input/target_sequence.py` | Python module | 무변경 |
| `docs/ARCHITECTURE.md` | Docs | 전면 재작성 |
| `dist_dosa/src/app/healer_gui.py` | Entry point | import 경로 확인만 (필요 시 수정) |

### 6.2 Current Consumers

| Resource | Operation | Code Path | Impact |
|----------|-----------|-----------|--------|
| `main_window.MainWindow` | import | `app/healer_gui.py` | 경로 보존 |
| `workers.healer_worker.HealerWorker` | import | `main_window.py`, `app/healer.py` | 경로 보존 |
| `workers.healer_worker.SkillCdTimer` | import (내부) | `healer_worker.py` 자체 | 신규 파일 경로로 이동 |
| `fsm.controller.FollowController` | import | `healer_worker.py` | 경로 보존 |
| `fsm.controller` TAB-CONFIRM 메서드들 | 내부 호출 | `controller.update()` | 신규 클래스 델리게이션 |
| 설정 JSON | 파일 | `~/.oldbaram_gui.json` | 스키마 무변경 |
| skill_scheduler re-export | import | `workers/healer_worker.py` | 실 사용처 확인 후 제거 결정 |

### 6.3 Verification

- [ ] 모든 기존 import 경로 보존 확인 (grep 로 검증)
- [ ] 설정 JSON 스키마 무변경
- [ ] F11/F12 핫키 콜백 경로 동일
- [ ] 원격 제어 API `apply_remote_control` 시그니처 불변

---

## 7. Architecture Considerations

### 7.1 Project Level Selection

| Level | Characteristics | Recommended For | Selected |
|-------|-----------------|-----------------|:--------:|
| Starter | 단순 구조 | 정적 사이트 | ☐ |
| Dynamic | 기능 모듈 + BaaS | 웹/데스크탑 앱 | ☑ |
| Enterprise | 엄격한 레이어 분리 | 대규모 시스템 | ☐ |

**선택 근거**: PyQt5 + multiprocess 수준. 로컬 데스크탑 매크로로 Dynamic 규모.

### 7.2 Key Architectural Decisions

| Decision | Options | Selected | Rationale |
|----------|---------|----------|-----------|
| 분할 단위 | 기능별 / 계층별 / 하이브리드 | **기능별** | 현재 모듈 구조 유지하며 파일만 쪼개기 |
| wire 모듈 패턴 | Mixin / 컴포지션 / 순수 함수 | **순수 함수** | HealerWorker 에 `_setup_*()` 로 호출. 상태는 워커 보유 |
| TAB-CONFIRM 추출 | 상태 enum + 함수 / 별도 클래스 | **별도 클래스** | 8개 상태 + 타임아웃 카운터 → 클래스로 캡슐화 |
| 설정 I/O | 클래스 래퍼 / 헬퍼 함수 | **헬퍼 함수** | 기존 MainWindow 메서드 시그니처 유지, 내부 중복만 제거 |
| import 구조 | 절대 / 상대 | **상대 `from ..`** | 기존 코드 스타일 유지 |

### 7.3 Clean Architecture Approach

```
Selected Level: Dynamic

Target Structure (dist_dosa/src/ 기준):
  app/          엔트리 (healer_gui 등) — 변경 최소
  capture/      mss 기반 캡처 — 변경 없음
  vision/       YOLO/OCR — 변경 없음
  fsm/
    controller.py   FollowController (축소)
    tab_confirm.py  ← NEW: TabConfirm FSM
    state.py        (기존)
  input/
    skill_blueprints.py   ← 깔끔, 그대로
    skill_scheduler.py    ← 그대로 (legacy re-export 점검)
    target_sequence.py    ← 그대로
    numlock_cycle.py      ← 그대로
    keys.py               ← 그대로
    global_hotkeys.py     ← 그대로
  net/          UDP — 변경 없음
  workers/
    healer_worker.py       ← 축소 (메인 오케스트레이터)
    healer_vision_wire.py  ← NEW
    healer_skill_wire.py   ← NEW
    healer_fsm_wire.py     ← NEW
    healer_main_loop.py    ← NEW
    skill_cd_timer.py      ← NEW (독립)
    attacker_worker.py     ← 범위 외
    heartbeat.py           ← 그대로
    control_listener.py    ← 그대로
  ui/
    main_window.py    ← 축소 (엔트리 + 다이얼로그)
    settings_io.py    ← NEW
    hotkeys.py        ← NEW
    regions.py        ← NEW
    overlays_ctrl.py  ← NEW
    worker_control.py ← NEW
    (기존 overlay.py, dialogs.py, region_picker.py 등 유지)
  utils/        유틸 — 변경 없음
```

---

## 8. Convention Prerequisites

### 8.1 Existing Project Conventions

- [x] `CLAUDE.md` 작업 규율 20항 존재
- [x] 메모리 규율 (`~/.claude/projects/D--oldbaram/memory/`) 존재
- [ ] `docs/01-plan/conventions.md` (이번 PDCA 에서 생성 안 함)
- [ ] ESLint/Prettier (Python 프로젝트 — 해당 없음)

### 8.2 Conventions to Define/Verify

| Category | Current State | To Define | Priority |
|----------|---------------|-----------|:--------:|
| 파일명 | snake_case (기존) | 유지 | - |
| 폴더 구조 | 기능별 서브폴더 | 유지 | - |
| Import 순서 | stdlib → 3rd → local | 유지 | - |
| docstring | 한글 (기존) | 유지 | Medium |
| 주석 | 한글 (기존) | 유지 | - |

### 8.3 Environment Variables

해당 없음. 로컬 데스크탑 앱 (Tailscale IP 등은 `cfg` 에서 관리).

### 8.4 Pipeline Integration

9-phase pipeline 미적용. PDCA 단독.

---

## 9. Next Steps

1. [x] Plan 문서 작성
2. [ ] Design 문서 작성 (`/pdca design refactor-v6`) — 3 옵션 제시 + 사용자 선택
3. [ ] Do 단계 (실제 분할)
4. [ ] Check 단계 (gap-detector + code-analyzer)
5. [ ] Report 단계
6. [ ] ARCHITECTURE.md 재작성 (Do 직후)
7. [ ] 사용자 `C:\oldbaram` 복사 및 실증

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-20 | 초안 — bkit PDCA 오케스트레이션 진입 | ai@mgnt.kr |
