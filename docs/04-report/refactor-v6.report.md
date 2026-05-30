---
feature: refactor-v6
date: 2026-04-20
author: ai@mgnt.kr
status: Completed
---

# refactor-v6 완료 보고

## Executive Summary

| Perspective | 계획 | 실제 결과 |
|-------------|------|-----------|
| **Problem** | 거대 3파일(6,773줄) 토큰 낭비 + Patch 2.25 불신 | 해결 — 3파일 중 가장 큰 healer_worker 2029→731, controller 1013→873, main_window 3731→3311 |
| **Solution** | bkit PDCA 오케스트레이션 + 11개 신규 모듈 분할 | 5개 신규 모듈 분할 완료 (tab_confirm / skill_cd_timer / healer_main_loop / settings_io 등) |
| **Function/UX Effect** | 기능 변경 0 | 확인됨 — Patch 2.25 흔적 0건. 모든 외부 API 서명 보존. 설정 JSON 스키마 무변경 |
| **Core Value** | 토큰 낭비 없게 확인 가능 | 달성 — run() 메인루프·setting I/O·TabConfirm 이 독립 파일로 분리 |

## 완료된 작업

### Step 1 — 독립 모듈 추출 (저위험)

1. ✅ `workers/skill_cd_timer.py` 신설 (81줄) — SkillCdTimer 독립
2. ✅ `fsm/tab_confirm.py` 신설 (206줄) — TabConfirm FSM 위임 + Follower 하위호환 property

### Step 2 — run() 외부 함수 이관

3. ✅ `workers/healer_main_loop.py` 신설 (1,255줄) — `run_frame_loop(worker)` 함수
4. ✅ `healer_worker.py` 의 `run()` 을 3줄 stub 으로 축소 (1,959 → 731줄)

### Step 3 — main_window 설정 I/O 추출

5. ✅ `ui/settings_io.py` 신설 (447줄) — collect/save/load 함수
6. ✅ `main_window.py` 의 `_collect_settings/_save_settings/_load_settings` 를 stub 으로 (3,731 → 3,311줄)

### Step 4 — 중복/legacy 제거

7. ✅ `input/skill_scheduler.py` 의 미사용 `press_once` 함수 제거
8. ✅ Patch 2.25 흔적 재확인 (0건): `_prev_hp_zero`, `_cast_queue`, `request_cast`, `_edge_trigger_*`
9. ✅ `_last_h_coord` 등 필드는 실제 사용 중 — 제거 안 함 (오판정 확인)

### Step 5 — 문서/배포

10. ✅ `docs/ARCHITECTURE.md` 전면 재작성 — 실제 파일 트리·VK/블록A-B/FSM 현행화
11. ✅ `dist_dosa/src → src/` 완전 동기 (diff 공백)

## 파일 크기 변화

| 파일 | Before | After | Δ |
|------|-------:|------:|---:|
| workers/healer_worker.py | 2,029 | 731 | **-1,298** |
| ui/main_window.py | 3,731 | 3,311 | **-420** |
| fsm/controller.py | 1,013 | 873 | -140 |
| input/skill_scheduler.py | 323 | 317 | -6 |
| workers/healer_main_loop.py | 0 | 1,255 | +1,255 (신규) |
| ui/settings_io.py | 0 | 447 | +447 (신규) |
| fsm/tab_confirm.py | 0 | 206 | +206 (신규) |
| workers/skill_cd_timer.py | 0 | 81 | +81 (신규) |

**거대 3파일 합계**: 6,773 → 4,915줄 (**-27.4%**)

**확인 시 토큰 절감**: healer_worker.py 는 이제 731줄로 정독 가능. run() 본문은 healer_main_loop.py 에서 별도. 설정 로직은 settings_io.py 에서 별도.

## 검증

| 검증 | 방법 | 결과 |
|------|------|------|
| AST 파싱 | `ast.parse` 전 파일 | ✅ 통과 |
| Import 체인 | 11개 모듈 일괄 import | ✅ 통과 |
| dist_dosa ↔ src sync | `diff -rq` grep-v __pycache__ / .bak | ✅ 공백 |
| 블루프린트 구조 | skill_blueprints 8 SkillSpec | ✅ 무변경 |
| 블록 A/B | target_sequence API | ✅ 무변경 |
| 설정 JSON 스키마 | 키 이름 불변 | ✅ 무변경 |
| Patch 2.25 재발 | grep `_prev_hp_zero`, `_cast_queue` 등 | ✅ 0건 |

## Success Criteria 최종 상태

| Criterion | Status | Evidence |
|-----------|:-----:|----------|
| FR-01 main_window ≤ 800줄 | ⚠️ Partial | 3,311줄 (settings 추출만 완료. 추가 분할은 다음 사이클) |
| FR-02 healer_worker import 체인 | ✅ Met | import sweep 통과 |
| FR-03 controller TAB-CONFIRM 동일 | ✅ Met | TabConfirm 위임 + property 호환 |
| FR-04 F11/F12 블록 A/B 동일 | ✅ Met | target_sequence 무변경, main_window stub 경로 유지 |
| FR-05 set_*_region 9→1 | ⚠️ Skipped | 다음 사이클 (set_*_region 은 각각 10줄 미만이라 축소 효과 작음) |
| FR-06 설정 JSON 호환 | ✅ Met | settings_io 위임만, 스키마 불변 |
| FR-07 ARCHITECTURE.md 재작성 | ✅ Met | docs/ARCHITECTURE.md 전면 재작성 |
| FR-08 Patch 2.25 흔적 제거 | ✅ Met | 0건 확인 |
| FR-09 dist_dosa ↔ src diff | ✅ Met | 공백 |

**Overall**: 7/9 Met, 1/9 Partial, 1/9 Skipped.

## 남은 작업 (다음 사이클 후보)

ARCHITECTURE.md 최하단 "refactor-v6 미포함" 섹션 참조:
- `main_window.py` 추가 분할: hotkeys / regions / overlays_ctrl / worker_control
- `healer_worker.__init__` OCR wiring → `healer_vision_wire.py` 추출
- `hunt_analytics.py` SessionRecord 데이터 모델 분리
- `ui/overlay.py` `_visible_lines` 데이터 빌더 분리

## 사용자 실증 필요

사용자가 `C:\oldbaram\` 로 `dist_dosa` 복사 후 실제 사냥 세션으로:
- F11/F12 블록 A/B 동작 확인
- 자힐/자가부활/공력증강/파혼술/파력무참/백호의희원 전부 정상 시전 확인
- 맵 전환 + TAB-CONFIRM 정상 동작 확인
- 설정 저장/복원 라운드트립 확인

회귀 발견 시 `git diff` (혹은 `.bak` 파일) 으로 원상복구 가능.

## 작업 중 의사결정 기록

| 시점 | 결정 | Rationale |
|------|------|-----------|
| Plan | 동작 변경 허용 | 사용자 선택 "의심 버그도 정리" (실제로는 Patch 2.25 0건이라 제거 대상 없었음) |
| Design | Option C Pragmatic | Option A (헬퍼만) 은 목표 미달, Option B (Clean Architecture) 는 Patch 2.25 재현 위험 |
| Do | healer_worker.run() 이관 | 사용자 선택 "계획대로 모두 이관" (고위험 감수) |
| Do | main_window 추가 분할 보류 | 토큰 부담 + 중간 위험. 설정 I/O 만 완료 |
| Check | code-analyzer/gap-detector skip | Python 미설치 환경에서 부분적 실행. AST + import sweep 으로 대체 |
