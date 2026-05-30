# 옛날바람 3PC 매크로 설계서 (v2)

## 1. 환경

- OS: Windows 11
- Python: 3.11 (64bit)
- 실행 단위: **도사 PC에서 독립 실행되는 봇 1개**. 격수 PC는 사람이 직접 조작 (매크로 없음)
- 의존성
  - `pywin32` (keybd_event, GetKeyState)
  - `opencv-python` (템플릿 매칭)
  - `mss` (고속 스크린샷)
  - `numpy`
  - `PyQt6` 또는 `tkinter` (GUI 설정창)
  - `pytesseract` (타겟 이름 OCR, 선택)
  - `pyyaml` (설정 저장)

## 2. 배포

```
[격수 PC]   — 사람 조작, 매크로 X
[도사1 PC]  — slave.exe (PyInstaller 단일 EXE)
[도사2 PC]  — slave.exe (동일 EXE + 다른 config.yaml)
```

## 3. GUI 설정 창 (최초 실행 + 핫키 호출)

### 3.1 탭 구성

**[기본]**
- 격수 캐릭터명: `_________` (빨탭 대상 식별)
- 도사 순번: ( ) 도사1  ( ) 도사2  ← 파력무참 오프셋 결정

**[스킬 - 넘패드 매핑]**
각 넘패드 키(Num1~Num9)에 스킬 선택. NumLock 싸이클에 포함할 키는 체크박스.

| 넘패드 | 스킬 | 싸이클 포함 |
|--------|------|------------|
| Num1 | [드롭다운: 봉황의기원 ▼] | ☑ |
| Num2 | [드롭다운: 혼마술 ▼] | ☑ |
| Num3 | [드롭다운: 신령의기원 ▼] | ☐ |
| Num4 | [드롭다운: 백호의희원 ▼] | ☐ (조건부) |
| Num5 | [드롭다운: 백호의희원'첨 ▼] | ☐ (조건부) |
| Num6 | [드롭다운: 공력증강 ▼] | ☐ (조건부) |
| Num7 | [드롭다운: 파력무참 ▼] | ☐ (조건부) |
| Num8 | [드롭다운: 금강불체 ▼] | ☐ (조건부) |
| Num9 | [드롭다운: 부활 ▼] | ☐ (특수) |

- **싸이클 포함 = NumLock OFF로 자동 연사 대상**
- **싸이클 미포함 = 조건부 이벤트 시전 (KEYDOWN→KEYUP 단발)**

**[조건부 시전 규칙]**
- 백호의희원: ☑ 자동 시전 / MP ≥ [____]%일 때 / 쿨 간격 [____]초
- 백호의희원'첨: ☑ 자동 시전 / 격수 HP ≤ [____]% or 쿨마다 / 간격 [____]초
- 공력증강: ☑ 자동 / MP ≤ [____]% 이고 HP ≥ [____]% 일 때
- 파력무참: ☑ 자동 / 180초 쿨 / 도사 순번 오프셋 자동 적용
- 금강불체: ☐ OFF (고스펙) / ☑ ON 이면 쿨마다 시전

**[화면 영역]** — `캘리브레이션` 버튼으로 드래그 선택
- 자기 HP 바
- 자기 MP 바
- 격수 타겟 이름표 (상단)
- 미니맵
- 격수 체력바 (그룹창)

**[핫키]**
- Start: F9 / Stop: F10 / Panic: F12 (기본값, 변경 가능)

**[저장 / 시작]**

### 3.2 config.yaml 저장 형태

```yaml
main_char:
  name: "격수캐릭명"

dosa_order: 1   # 1 or 2

numpad_mapping:
  num1: { skill: bonghwang, in_cycle: true }
  num2: { skill: honma,      in_cycle: true }
  num3: { skill: shinryeong, in_cycle: false }
  num4: { skill: baekho,     in_cycle: false }
  num5: { skill: baekho_cheom, in_cycle: false }
  num6: { skill: gongryeok,  in_cycle: false }
  num7: { skill: paryeok,    in_cycle: false }
  num8: { skill: geumgang,   in_cycle: false }
  num9: { skill: revive,     in_cycle: false }

conditional:
  baekho:
    enabled: true
    mp_threshold_pct: 60
    interval_sec: 3
  baekho_cheom:
    enabled: true
    main_hp_threshold_pct: 70
    interval_sec: 5
  gongryeok:
    enabled: true
    mp_low_pct: 30
    self_hp_safe_pct: 70
  paryeok:
    enabled: true
    cooldown_sec: 180
    offset_sec: 0   # 도사2는 90
  geumgang:
    enabled: false
    cooldown_sec: 20

regions:
  self_hp:     [x, y, w, h]
  self_mp:     [x, y, w, h]
  target_name: [x, y, w, h]
  minimap:     [x, y, w, h]
  group_main_hp: [x, y, w, h]  # 그룹창의 격수 HP 바

hotkeys:
  start: F9
  stop:  F10
  panic: F12
```

## 4. 모듈 구조

```
oldbaram-macro/
├── slave/
│   ├── main.py                # 진입점, 이벤트 루프
│   ├── gui/
│   │   ├── settings_window.py # PyQt6 설정창
│   │   └── calibrator.py      # 영역 드래그 선택
│   ├── state.py               # 상태머신
│   ├── input/
│   │   ├── keyboard.py        # keybd_event wrapper
│   │   ├── numlock.py         # 토글
│   │   └── hold_keys.py       # KEYDOWN 유지
│   ├── vision/
│   │   ├── capture.py         # mss
│   │   ├── find_main.py       # 격수 캐릭터 매칭
│   │   ├── target_ui.py       # 타겟 이름 OCR
│   │   ├── hp_reader.py       # HP/MP 바 픽셀 (자기+격수그룹)
│   │   ├── ghost_detect.py    # 유령 상태 감지 (사망)
│   │   └── map_detect.py      # 맵 전환 (블랙아웃)
│   ├── skills/
│   │   ├── cycle.py           # NumLock 메인힐 싸이클
│   │   ├── conditional.py     # 조건부 스킬 스케줄러
│   │   ├── paryeok.py         # 파력무참 오프셋 타이머
│   │   └── revive.py          # 부활 (자가/격수)
│   ├── follow/
│   │   └── pathing.py         # 격수 위치 → 방향키
│   └── assets/
│       └── ... (템플릿 이미지들)
└── tools/
    └── record_template.py     # 캘리브레이션 도우미
```

## 5. 상태 머신

```
  [ INIT ] → 빨탭, 싸이클 기동
     ▼
  [ FOLLOW ] ──격수 2칸 이내──→ [ COMBAT ]
     ▲                           │
     │                           │ 격수 사라짐
     │                           ▼
     │                     [ MAP_CHANGE ]
     │                           │
     └──── 복귀 ─────────────────┘

  [ DEAD ] ← 자기 HP 0 또는 유령 감지 (어느 상태에서든)
     │
     └── 자가부활 → 회복 대기 → INIT
```

### 5.1 상태별 동작

| 상태 | 메인힐 싸이클 | 조건부 스킬 | 따라가기 | 빨탭 |
|------|-------------|------------|---------|------|
| INIT | OFF → 기동 | 대기 | 정지 | 최초 시도 |
| FOLLOW | **ON** (이동 중에도 힐 계속) | 실행 | 이동 | 2초마다 |
| COMBAT | **ON** | 실행 | 정지 | 2초마다 |
| MAP_CHANGE | **일시 OFF** (다른 맵에 혼마 방해) | 일시정지 | 마지막 격수 위치로 | 전환 후 즉시 |
| DEAD | OFF + 모든 키 UP | 정지 | 정지 | 정지 |

## 6. 핵심 알고리즘

### 6.1 NumLock 메인힐 싸이클

```python
def start_cycle(cycle_keys: list[int]):
    for k in cycle_keys:
        keybd_event(k, 0, KEYDOWN, 0)
    set_numlock(False)   # OFF → 자동 연사 시작

def stop_cycle(cycle_keys: list[int]):
    set_numlock(True)    # ON → 시전 중단
    for k in cycle_keys:
        keybd_event(k, 0, KEYUP, 0)
```

### 6.2 조건부 스킬 스케줄러 (이벤트 루프)

```python
async def conditional_loop():
    while running:
        now = time.time()

        # 백호의희원 (자가 MP 순환)
        if cfg.baekho.enabled and self_mp_pct() >= cfg.baekho.mp_threshold_pct:
            if now - last['baekho'] > cfg.baekho.interval_sec:
                cast_single(key_of('baekho'))
                last['baekho'] = now

        # 백호의희원'첨 (그룹 힐)
        if cfg.baekho_cheom.enabled:
            if main_hp_pct() <= cfg.baekho_cheom.main_hp_threshold_pct \
               or now - last['baekho_cheom'] > cfg.baekho_cheom.interval_sec:
                cast_single(key_of('baekho_cheom'))
                last['baekho_cheom'] = now

        # 공력증강 (응급 MP, HP 안전치 확인)
        if cfg.gongryeok.enabled:
            if self_mp_pct() <= cfg.gongryeok.mp_low_pct \
               and self_hp_pct() >= cfg.gongryeok.self_hp_safe_pct:
                cast_single(key_of('gongryeok'))

        # 파력무참 (오프셋 + 쿨)
        if cfg.paryeok.enabled:
            since_start = now - session_start
            phase = (since_start + cfg.paryeok.offset_sec) % cfg.paryeok.cooldown_sec
            if phase < 0.5 and monsters_nearby():
                cast_single(key_of('paryeok'))

        # 금강불체 (옵션)
        if cfg.geumgang.enabled:
            if now - last['geumgang'] > cfg.geumgang.cooldown_sec:
                cast_single(key_of('geumgang'))
                last['geumgang'] = now

        await asyncio.sleep(0.1)
```

### 6.3 파력무참 오프셋 — 겹침 회피

- 도사1: `offset = 0` → 0s, 180s, 360s… 시점에 시전
- 도사2: `offset = 90` → 90s, 270s, 450s… 시점에 시전
- 각 시전 후 45초 지속 → 도사1 (0~45s, 180~225s, …), 도사2 (90~135s, 270~315s, …)
- 커버리지: 180초 중 90초 = **50%**

### 6.4 빨탭 루프 (수정)

```python
async def tab_lock_loop():
    while running:
        name = ocr_target_name()  # 상단 타겟 이름표 OCR
        if name != cfg.main_char.name:
            for _ in range(5):
                press_once("tab")
                await asyncio.sleep(0.15)
                if ocr_target_name() == cfg.main_char.name:
                    break
        await asyncio.sleep(2.0)
```

### 6.5 따라가기 (2칸 이내)

```python
TILE = 32   # 픽셀 (캘리브레이션)
ADJACENT_RANGE = 2 * TILE   # 파력무참 범위 2칸

def follow_step():
    pos = find_main_in_screenshot()
    if pos is None:
        return "LOST"  # → MAP_CHANGE
    dx, dy = pos.x - SCREEN_CENTER.x, pos.y - SCREEN_CENTER.y
    if abs(dx) <= ADJACENT_RANGE and abs(dy) <= ADJACENT_RANGE:
        return "ADJACENT"
    key = dominant_direction(dx, dy)
    press_once(key)
    return "MOVING"
```

### 6.6 사망/부활

```python
def handle_death():
    stop_cycle(all_cycle_keys)
    release_all_keys()

    if is_ghost():
        # 자가부활 시전
        press_once(key_of('revive_self'))
        wait_for_alive(timeout=30)

    # HP/MP 회복 대기
    wait_until(lambda: self_hp_pct() > 80 and self_mp_pct() > 60,
               timeout=60)

    transition_to(INIT)

def check_main_dead():
    if main_hp_pct() == 0 or main_is_ghost():
        # 부활 스킬은 싸이클 정지 후 단발 시전
        stop_cycle()
        press_once(key_of('revive_main'))
        await asyncio.sleep(2)
        start_cycle()
```

## 7. 단축키 / 안전장치

| 키 | 동작 |
|----|------|
| F9 | Start (INIT 진입) |
| F10 | 일시정지 (모든 키 UP, NumLock ON 복원, 싸이클 OFF) |
| F12 | **패닉** — 프로세스 즉시 종료 + 강제 복원 |

- **`atexit.register(cleanup)`** 로 비정상 종료 시에도 키/NumLock 복원
- 게임 창이 포커스 아니면 싸이클 일시 중지 (오입력 방지)

## 8. 구현 순서 (PoC → 완성)

1. NumLock 토글 + 키홀드 단독 (게임 밖 노트패드에서 검증)
2. `mss` 스크린샷 + 격수 템플릿 매칭 정확도
3. 상단 타겟 이름 OCR 정확도
4. HP/MP 바 픽셀 읽기 (자기 + 그룹창 격수)
5. 빨탭 루프 단독
6. Follow 단독
7. 메인힐 싸이클 + 조건부 스케줄러
8. 상태머신 통합
9. 맵 전환 감지 + 복귀
10. 사망/자가부활
11. GUI (PyQt6) + 캘리브레이터
12. 파력무참 오프셋 실측 검증
13. PyInstaller → 배포

## 9. 미확정 항목

- [ ] 격수 캐릭터명 (GUI에서 사용자가 입력 — 런타임 확정)
- [ ] 각 스킬의 정확한 MP 소모량 수치
- [ ] 자가부활 / 격수부활 스킬의 정확한 이름/소모
- [ ] `monsters_nearby()` 판별 방법 (화면 몹 스프라이트 매칭? 혼마 이펙트 감지?)
- [ ] HP/MP 바 픽셀 좌표 (GUI 캘리브레이터로 해결)
- [ ] 파력무참 오프셋 90초가 실제 최적인지 실측 검증 필요
