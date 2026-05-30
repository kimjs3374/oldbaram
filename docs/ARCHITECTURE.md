# 옛날바람 자동 힐러 — 아키텍처 문서

최근 갱신: 2026-04-20 (refactor-v6: 거대 3파일 분할 + 블루프린트 정비)

## 전체 구성

```
D:\oldbaram\                   (개발 환경)
├─ src/                        ← 개발 소스 (dist_dosa 와 sync)
├─ dist_dosa/                  ← 배포 정본 (사용자가 C:\oldbaram 으로 복사)
├─ models/                     ← YOLO/OCR 가중치
├─ dataset/                    ← YOLO 학습 데이터
├─ logs/                       ← 런타임 로그 (회전 20MB × 3)
├─ docs/                       ← 본 문서 + PDCA 산출물
├─ memory_reader/              ← (폐기) 넥슨 안티치트로 사용 불가
├─ tools/                      ← YOLO 학습·프로브·분할 스크립트
├─ CLAUDE.md                   ← 작업 규율 (최우선)
└─ 맵이동 로직.md               ← FSM·trail·TAB-CONFIRM 상세

C:\oldbaram\                   (실행 환경 - 다른 기계)
  └─ dist_dosa 복사본
```

## 실행 역할

| 역할 | 기계 수 | 조작 | 입력 | 네트워크 |
|------|--------|------|------|----------|
| 격수(attacker) | 1 | 사람 | 맵/좌표/체마 OCR + 격수 버프(혼마술) OCR | UDP 송신 30Hz |
| 힐러(healer 도사) | 2 | 자동 봇 | YOLO(red/white tab) + OCR(맵/좌표/쿨/버프/HP/MP/XP) + FSM | UDP 수신 + 쿨다운/buff 보고 1Hz |

## `dist_dosa/src/` 모듈 트리 (refactor-v6)

```
src/
├─ app/                          엔트리 포인트
│   ├─ healer_gui.py             QApplication + main() 만 (얇은 진입)
│   ├─ attacker.py               격수 캡처 + UDP + 쿨다운 수신 루프
│   ├─ healer.py                 (legacy) CLI 전용 옛 진입점
│   ├─ hunt_analytics.py         lap/xp/session 추적 + JSONL 저장
│   ├─ monitor.py                UDP 수신 디버그 뷰
│   └─ debug_ocr.py / debug_yolo.py / poc_keytest.py
│
├─ capture/
│   └─ screen.py                 mss 기반 윈도우 캡처
│
├─ vision/                       영상 인식
│   ├─ yolo.py                   YoloRunner — nc=2 CLS_RED/CLS_WHITE 직판정
│   ├─ ocr.py                    Ocr — PaddleOCRv5 (맵) + EasyOCR (좌표)
│   ├─ map_ocr.py                MapOcrWorker — PaddleOCR 백그라운드 스레드
│   ├─ cooldown_ocr.py           CooldownOcr — 쿨다운/닉네임 비블로킹 OCR
│   ├─ hpmp.py                   HpMp / HpMpReader — HP/MP OCR + event push
│   ├─ xp_ocr.py                 XpOcr — 레벨/XP OCR + xp_per_hour 집계
│   └─ calibration.py            격수 좌표↔픽셀 캘리브레이터
│
├─ fsm/                          유한상태머신 [refactor-v6 분할]
│   ├─ controller.py             Follower — trail/exit_dir/MAP-SYNC (873줄)
│   ├─ tab_confirm.py            ★NEW TabConfirm — Home→Tab self-target FSM (206줄)
│   └─ state.py                  FsmState enum
│
├─ input/                        키 입력 + 스킬 [블루프린트 중심]
│   ├─ skill_blueprints.py       ★ SkillSpec 선언 + default_skills() (329줄)
│   │                             — 조건부 8종: 자가부활/격수부활/자힐/공력증강/
│   │                               파력무참/백호의희원/백호의희원첨/파혼술/금강불체
│   ├─ skill_scheduler.py        SkillScheduler 엔진 (burst + verify + retry)
│   ├─ target_sequence.py        block_a_self_target / block_b_return_to_attacker /
│   │                             run_block_ab_combined (F11 통합)
│   ├─ numlock_cycle.py          NumLockCycler (힐러 3슬롯 토글)
│   ├─ global_hotkeys.py         RegisterHotKey (F11/F12 + Ctrl/Shift 폴백)
│   └─ keys.py                   KeyController — SendInput/PostMessage/HID
│
├─ net/                          네트워크
│   ├─ protocol.py               State / CooldownReport / ControlCmd 직렬화
│   ├─ udp_sender.py             UdpSender — unicast/broadcast
│   └─ udp_receiver.py           StateReceiver / CooldownReceiver
│
├─ workers/                      Qt 백그라운드 스레드 [refactor-v6 분할]
│   ├─ healer_worker.py          ★ HealerWorker — __init__ + set_*_region + run() stub (731줄)
│   ├─ healer_main_loop.py       ★NEW run_frame_loop — 메인 프레임 루프 본체 (1255줄)
│   ├─ skill_cd_timer.py         ★NEW SkillCdTimer — OCR anchor + monotonic 감산 (81줄)
│   ├─ attacker_worker.py        AttackerWorker — Attacker 래퍼 + 쿨다운 수신
│   ├─ heartbeat.py              HealerHeartbeat / AttackerHeartbeat — 1Hz keep-alive
│   └─ control_listener.py       ControlListener — 원격 start/pause/stop 수신
│
├─ ui/                           PyQt5 위젯 [refactor-v6 분할]
│   ├─ main_window.py            ★ MainWindow — UI/핫키/영역/워커/오버레이 (3311줄)
│   ├─ settings_io.py            ★NEW collect/save/load — ~/.oldbaram_gui.json (447줄)
│   ├─ dialogs.py                SkillDialog / ParamDialog / NetworkDialog
│   ├─ overlay.py                GameOverlay + SkillAlertOverlay
│   ├─ hunter_helper_panel.py    HunterHelperOverlay — 격수 PC 스킬 목록
│   ├─ hunt_report_dialog.py     사냥 리포트 다이얼로그
│   ├─ region_picker.py          드래그 영역 선택기
│   ├─ region_overlay.py         초록 박스 시각화
│   ├─ status_strip.py           격수맵/힐러맵/좌표/상태 5필드
│   └─ styles.py                 QSS 팔레트
│
├─ utils/                        공용 저수준 헬퍼
│   ├─ logger_setup.py           _setup_logger, LOG_DIR
│   ├─ win_helpers.py            _user32, _is_fg_hwnd, frame_to_qpix
│   ├─ state_names.py            FSM 상태 한글 매핑
│   └─ window_geom.py            게임창 좌표 변환/추적
│
├─ tools/                        디버그 유틸
└─ config.py                     config.yaml 로더
```

## 파일 크기 (refactor-v6)

| 파일 | 줄수 | 변동 | 역할 |
|------|-----:|------|------|
| ui/main_window.py | 3,311 | -420 | MainWindow (settings_io 추출) |
| ui/settings_io.py | 447 | +447 | 신규 (JSON I/O) |
| workers/healer_worker.py | 731 | -1,298 | HealerWorker (run() 이관) |
| workers/healer_main_loop.py | 1,255 | +1,255 | 신규 (run_frame_loop) |
| workers/skill_cd_timer.py | 81 | +81 | 신규 (SkillCdTimer 독립) |
| fsm/controller.py | 873 | -140 | Follower (TAB-CONFIRM 이관) |
| fsm/tab_confirm.py | 206 | +206 | 신규 (TabConfirm FSM) |
| input/skill_blueprints.py | 329 | 0 | 그대로 (이미 정리됨) |
| input/skill_scheduler.py | 317 | -6 | press_once 제거 |
| **합계** | **~17,258** | ~+126 | 분할로 평균 파일 크기 감소 |

## 데이터 플로우

```
[격수 PC]
  screen.py 캡처
    → ocr.py 맵+좌표 / cooldown_ocr.py 혼마술 버프
    → attacker.py 집계
    → UdpSender.broadcast
  ↓  30Hz State 패킷 (map_name, x, y, map_seq, map_change_pending,
                    hp_pct, mp_pct, debuff_honmasul_sec)
  ↓
[힐러 PC × 2]
  UdpReceiver.StateReceiver
    → fsm.controller.Follower (trail / exit_dir / MAP-SYNC)
    → fsm.tab_confirm.TabConfirm (Home→Tab self-target)
  screen.py 캡처
    → yolo.py red/white tab
    → ocr.py 맵+좌표
    → cooldown_ocr.py 쿨/닉/버프 (비블로킹 스레드)
    → hpmp.py HP/MP 비율 (event push)
    → xp_ocr.py XP + 레벨
  → input.skill_scheduler.SkillScheduler (predicate polling, poll 0.1s)
      ↑ ctx = {cooldowns, buffs, attacker_buffs, self_hp_pct, self_mp_pct,
              attacker_hp_pct, attacker_mp_pct}
      predicate → input.skill_blueprints (자가부활/격수부활/자힐/공력증강/
                                          파력무참/백호/파혼술)
      blocks_movement 시 → target_sequence.run_block_ab_combined
                           = TAB→HOME→TAB → 토글OFF → 부활/자힐 burst
                           → ESC→TAB→TAB → 토글ON
  → input.keys.KeyController (SendInput)
  ↓
  CooldownReport 1Hz → 격수 PC GameOverlay / HunterHelperOverlay
```

## 스킬 시스템 (블루프린트 중심)

### VK 배치 (2026-04-20 최종)

| 스킬 | NumPad | 분류 |
|------|:------:|------|
| 메인힐 (봉황/신령 택1) | 1 | NumLock 싸이클 |
| 혼마술 | 2 | NumLock 싸이클 |
| 공력증강 | 3 | 조건부 (MP < 임계치) |
| 백호의희원 | 4 | 조건부 (쿨 OCR) |
| 백호의희원첨 | 5 | 조건부 (쿨 OCR) |
| 부활 | 6 | 조건부 (자가 HP=0 or 격수 HP=0) |
| 파혼술 | 7 | 조건부 (혼마술 감지) |
| 파력무참 | 8 | 조건부 (180s 고정) |
| 금강불체 | 0 | 조건부 (옵션) |

### 조건부 우선순위 (SkillScheduler priority 오름차순)

| 우선순위 | 스킬 | predicate | 시퀀스 |
|:-------:|------|-----------|--------|
| 0 | 자가부활 | self_hp_pct == 0 | pre_block_ab (A+B 통합 훅) |
| 1 | 격수부활 | attacker_hp_pct == 0 AND self_hp>0 | 단순 burst 1.5s |
| 2 | 자힐 | self_hp_pct < thr(UI) | pre_block_ab (A+B 통합 훅) |
| 3 | 공력증강 | self_mp_pct < thr(UI) | 단순 burst 1.0s |
| 4 | 파혼술 | attacker_buffs["혼마술"] > 0 | 단순 burst 0.5s |
| 10 | 파력무참 | True (180s 쿨 고정) | buff 검증 burst |
| 11 | 백호의희원(첨) | cooldowns 관측 없음 | until_ready 무한 재시도 |
| 12 | 금강불체 | 옵션 (기본 off) | 단순 burst |

### 블록 A/B (자힐/자가부활 시퀀스)

- **블록 A**: TAB → HOME → TAB (self-target) → NumLock 토글 OFF → 부활 burst 0.5s → 자힐 burst 1.0s.
- **블록 B**: ESC → TAB → TAB → NumLock 토글 재ON.
- **F11** = A+B 통합 (스킬 설정 체크박스 `chk_f11_ab_combined` OFF 면 A 단독).
- **F12** = B 단독 (복구용).
- **A+B 진입 직전**: `keys.release_all()` 로 모든 방향키 release (이동 간섭 차단).
- **blocks_movement=True 스킬**: SkillScheduler 가 `keys.set_movement_lock(True)` 콜백 호출 → A+B 시퀀스 중 방향키 press 완전 차단.

### 크로스-PC 트리거 (파혼술)

혼마술 감지는 격수 PC, 파혼술 시전은 힐러 PC — 4단계 파이프라인:

1. **격수**: `attacker.Attacker.buff_ocr` = `CooldownOcr`. `latest_debuff_honmasul_sec()` 반환.
2. **UDP State**: `net/protocol.py:State.debuff_honmasul_sec` 필드 송신.
3. **힐러**: `healer_main_loop._ctx()` 가 `ctx["attacker_buffs"]["혼마술"]` 로 주입.
4. **SkillSpec**: `skill_blueprints._attacker_debuff_present(c, "혼마술")` predicate.

## FSM / 맵 이동

### Follower (fsm/controller.py)

- trail: 맵별 격수 좌표 breadcrumb. 도사가 이를 순서대로 밟으며 따라감.
- `_global_trail`: (map, x, y) 시퀀스. 태그 전이로 맵 전환 확정.
- `_force_exit_until`: 태그 전이 감지 후 N초 exit_dir 강제 홀드.
- `_map_progress`: 맵별 진행 idx. 뒷걸음 금지.
- 역방향 디바운스 (2초 내 A→B→A 는 3프레임 확인).
- `_map_sync_until`: 맵 OCR(Paddle CPU 느림) 과 좌표 OCR 레이스 방지 (1.5s pause).
- Coord jump filter: 동일 맵 내 Δ ≥ 8칸이면 OCR 오류로 거부.
- hmap crosscheck: 힐러 좌표가 해당 맵 trail bbox 밖이면 OCR 오인 거부.

### TabConfirm (fsm/tab_confirm.py)

흰탭 3프레임 감지 → `arm()` → 상태기계:
1. **home** pending: pre-stability 대기 (h_coord 불변 확인) → Home 송신 → tab 큐잉.
2. **tab** pending: key_gap 대기 → Tab 송신 → A_wait_red.
3. **A_wait_red**: `red_raw AND !white_raw` 2프레임 연속 → done_ok.
4. hard timeout (10s) → retry_count<max 면 retry_arm, 아니면 done_timeout.
5. fg_match=False 시 `note_fg_mismatch()` → home 재큐잉.

Follower 는 하위 호환 property (`_tab_confirm_active`, `_tab_confirm_substate`, 등) 로 외부 API 호환성 유지.

## 제어 채널 (격수 → 힐러)

```
MainWindow 버튼 → AttackerWorker.send_control → UdpSender
  ↓ ControlCmd (target_idx, cmd)
ControlListener → HealerWorker.apply_remote_control → armed/follow 토글
```

## 설정 영속성

- `~/.oldbaram_gui.json` — `ui/settings_io.py` 가 I/O 담당.
- MainWindow 메서드 `_collect_settings/_save_settings/_load_settings` 는 얇은 delegate.
- 영역 저장 4곳 표준 루트 (영역 한 곳만 빠뜨리면 "저장은 됐는데 안 먹음" 버그):
  1. `ui/settings_io.collect` — 수집
  2. `ui/settings_io.load` — 복원
  3. `main_window._tick_msw_tracker` — msw.exe 이동 시 shift
  4. `main_window._start_healer/_start_attacker` — 워커 시작 시 주입

## Check 결과 (refactor-v6 완료 확인)

| 검증 | 결과 |
|------|------|
| Python AST 파싱 | ✅ 전 파일 통과 |
| Import 체인 | ✅ HealerWorker, MainWindow, TabConfirm, SkillCdTimer, SkillScheduler, default_skills 모두 load |
| Patch 2.25 흔적 | ✅ 0건 (`_prev_hp_zero`, `_cast_queue`, `request_cast`, `_edge_trigger_*` 모두 부재) |
| legacy re-export | ✅ `press_once` 제거. 실사용 없음 확인 후 삭제 |
| 블루프린트 구조 | ✅ `skill_blueprints.py` 8 SkillSpec 유지. wiring hooks 는 `healer_main_loop._hook_*` closure 에 위치 |

## 관련 문서

- `D:/oldbaram/CLAUDE.md` — 작업 규율·반성문 체크리스트·금지 재제안
- `D:/oldbaram/맵이동 로직.md` — 맵 전환 FSM·trail·TAB-CONFIRM Route A 상세
- `D:/oldbaram/반성문.md` — 과거 실수 누적
- `D:/oldbaram/docs/01-plan/features/refactor-v6.plan.md` — 본 리팩토링 Plan
- `D:/oldbaram/docs/02-design/features/refactor-v6.design.md` — 본 리팩토링 Design
- `C:\Users\ENG\.claude\projects\D--oldbaram\memory\MEMORY.md` — 자동 메모리 인덱스

## refactor-v6 미포함 (향후 사이클)

- `main_window.py` 3,311줄 — 추가 분할 후보: `ui/hotkeys.py`, `ui/regions.py`, `ui/overlays_ctrl.py`, `ui/worker_control.py`.
- `healer_worker.py` 의 `__init__` 내 OCR reader 초기화 → `healer_vision_wire.py` 추출.
- `set_*_region` 9개 → 공통 헬퍼 통합.
- `hunt_analytics.py` 623줄 — 현 구조로 유지 가능하나 SessionRecord 데이터 모델 별도 파일화 검토.
- `ui/overlay.py` 797줄 — `_visible_lines`/`_analytics_lines` 데이터 빌더 분리.
