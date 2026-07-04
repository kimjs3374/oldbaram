"""힐러 PC 메인 컨트롤러. UDP 수신 state → 현재 FSM 상태 평가 → 입력 송신."""
import json
import os
import pathlib
import re
import time
from collections import deque
from typing import Optional

from ..net.protocol import State
from .map_grid import MapGrid
from .state import FsmState
from .tab_confirm import TabConfirm


class Follower:
    """격수 좌표 이력 기반 이동 방향 추정 + hysteresis."""

    def __init__(self, red_lost_sec: float = 1.0,
                 stuck_sec: float = 3.0,
                 dead_reckon_sec: float = 2.0,
                 red_gain_frames: int = 2,
                 disconnect_sec: float = 5.0,
                 portal_sec: float = 0.2,
                 loading_sec: float = 0.3,
                 new_map_sec: float = 0.3):
        self.red_lost_sec = red_lost_sec  # 빨탭→흰탭 히스테리시스 (sec)
        self.stuck_sec = stuck_sec
        self.dead_reckon_sec = dead_reckon_sec
        self.red_gain_frames = red_gain_frames  # 흰탭→빨탭 (연속 N프레임)
        self.disconnect_sec = disconnect_sec
        self.portal_sec = portal_sec
        self.loading_sec = loading_sec
        self.new_map_sec = new_map_sec

        self._last_red_seen = 0.0
        self._red_confirm_count = 0
        self._red_confirmed = False
        self._last_coord: Optional[tuple] = None
        self._last_coord_change = time.time()
        self._last_map: str = ""
        self._healer_map: str = ""
        self._last_valid_coord_time = 0.0
        self._last_udp_time = 0.0
        self._transition_start = 0.0   # ENTER_PORTAL 시작 시각
        self._transition_phase: Optional[FsmState] = None
        self._state = FsmState.IDLE
        self._coord_hist = deque(maxlen=6)
        # 격수가 이전 맵에서 빠져나간 시점 스냅샷 (맵 이동 추종용).
        self._exit_map: str = ""
        self._exit_coord: Optional[tuple] = None
        self._exit_dir: str = "-"
        # 맵별 격수 마지막 관측(좌표/방향). 힐러가 다른 맵에 남아있을 때
        # "그 맵에서 격수가 마지막으로 본 위치/방향"으로 이동하기 위한 자료.
        self._map_last_coord: dict = {}   # map_name → (x,y)  [호환용]
        self._map_last_dir: dict = {}     # map_name → 'L/R/U/D/-'
        self._map_last_ts: dict = {}      # map_name → timestamp
        # 맵별 격수 경로 트레일(breadcrumb). 격수 좌표 변할 때마다 푸시.
        # 도사는 이 트레일을 순서대로 밟아가며 따라오고, 마지막 좌표에서
        # exit_dir로 밀어 맵 전환을 일으킨다. 연속 동일 좌표는 dedup.
        # maxlen으로 메모리 상한 두지만 한 맵 내 2000 waypoint면 충분.
        self._map_trail: dict = {}        # map_name → deque[(x,y)]
        # 전역 trail — (map, x, y) 시퀀스. 연속된 두 entry의 map이 다른
        # 지점 = 격수 맵 전환 확정 증거 (trail 자체에 태그 내장).
        # 맵 이름 OCR 지연 케이스에 강인 — 1 프레임 (옛맵, 새좌표) 오염
        # 섞여도 다음 프레임 태그가 바뀌면 transition 확정됨.
        # #16 (23:07:02 선비족3-5(7)→선비족3, d=9 턱걸이 JUMP) 같은 경계
        # 케이스에서 ATK-COORD-JUMP에 의존하지 않고 전환 확정.
        self._global_trail: deque = deque(maxlen=4000)
        # 태그 전이 감지 시 N초 동안 exit_dir 강제 홀드 (다른 이동 결정 무시).
        # 격수가 맵 넘긴 순간 힐러에게 "무조건 exit_dir로 이동" 지시.
        self._force_exit_until: float = 0.0
        # 2026-06-11 수정A: 본인 빨탭 후 맵전환(전이시도) 직후 흰탭 재arm 억제 창.
        # 수정B(2026-06-11): 빨탭 확정(note_tab_done_ok) 직후에도 동일 grace.
        # 빨라진 YOLO가 전환 중 찰나 흰탭 1프레임 잡아 또 Tab→방금 만든 빨탭
        # 깨는 현상(사용자 진단) 차단. env OB_POST_TAB_GRACE_MS(기본 1500) 조정.
        self._post_tab_grace_until: float = 0.0
        try:
            self._post_tab_grace_sec: float = max(
                0.0, int(os.environ.get("OB_POST_TAB_GRACE_MS", "1500")) / 1000.0)
        except Exception:
            self._post_tab_grace_sec = 1.5
        self._force_exit_sec: float = 2.5
        # 2026-04-22: 격수가 포탈 살짝 담그고 돌아오는(A→B→A 왕복) 경우 힐러가
        # 무조건 따라 들어가는 문제 방지. CTRL-MAPCHG 감지 후 pend_delay 지연 뒤
        # force_exit 활성. 지연 중 다른 MAPCHG 감지되면 덮어써서 원위치 가능.
        self._force_exit_start: float = 0.0
        self._force_exit_pend_sec: float = 0.0
        # 도사가 해당 맵 trail에서 "진행한 마지막 idx" (단조 증가).
        # 뒷걸음 금지 — 한 번 지나간 wp 뒤로는 절대 안 감.
        self._map_progress: dict = {}     # map_name → int
        # 마지막 next_waypoint 호출의 진단 정보. healer_gui가 TRAIL-DIAG로 로그.
        self._wp_last_diag: Optional[dict] = None
        # 외부(healer_gui)에서 주입하는 로거. None이면 조용히 넘어감.
        self.log = None
        # TRAIL-PUSH 로그 스로틀용: 맵별 마지막 기록 시각.
        self._last_push_log_ts: dict = {}
        self._push_count: dict = {}
        # C안: 역방향 맵 복귀 디바운스.
        # 2초 내 A→B→A 와 같은 왕복은 OCR flicker 가능성 → 3프레임 확인 후 수락.
        self._prev_last_map: str = ""
        self._last_mapchg_ts: float = 0.0
        self._pending_reversion_count: int = 0
        self._reversion_debounce_sec: float = 2.0
        self._reversion_confirm_frames: int = 3
        # F안: 힐러 자신의 맵 OCR도 역방향 디바운스.
        self._prev_healer_map: str = ""
        self._last_healer_mapchg_ts: float = 0.0
        self._healer_reversion_count: int = 0
        # I안: 격수 map_seq edge 감지 → 잠깐 멈추고 재계획.
        # 격수가 맵 전환 순간 map_seq를 +1 → 힐러는 edge 감지 시 pause_sec 동안
        # 키 입력 release + 이동 결정 스킵. 재개 시 next_waypoint가 snap-forward로
        # "힐러 현재 위치 근처 idx"부터 이어감 → 뒤로 돌아가는 낭비 제거.
        self._last_seen_map_seq: int = 0
        self._pause_until: float = 0.0
        self._pause_sec: float = 0.1
        # snap-forward 허용 거리. 현재 cur~last_idx 사이에서 힐러와 맨해튼이
        # 이 값 이하인 가장 먼 idx로 cur 점프. 너무 크면 오염 coord로 튈 수 있음.
        self._snap_forward_threshold: int = 10
        # J안: 격수 coord jump filter — 동일 맵 내 이전 유효 coord 대비 너무 크게
        # 튀면 OCR 오류로 판정, 해당 프레임 atk.coord_valid=False 강제.
        # trail push B1(jump reject)과 동일 임계. 단, atk 객체 자체 coord_valid를
        # 조작해 _decide_move 에까지 효과 파급.
        self._atk_last_valid_coord_by_map: dict = {}  # map → (x,y)
        self._atk_jump_threshold: int = 8
        # K안: 힐러맵 OCR crosscheck — 새 map_name이 들어올 때 힐러 coord가
        # 그 맵 trail 범위 밖이면 OCR 오인으로 판정.
        # 기존: N프레임 후 시간 기반 자동 수락 → OCR flicker로 뚫리는 버그 있었음.
        # 수정(A+B): bbox 밖이면 무한정 거부. 실제 맵 전환 시 힐러가 포탈 통과해
        # 좌표가 새 맵 bbox 안으로 들어오는 순간에만 수락.
        # confirm 값은 로그 출력 주기용(스팸 방지)으로만 참조.
        self._hmap_coord_mismatch_count: int = 0
        self._hmap_coord_mismatch_confirm: int = 30
        self._hmap_bbox_margin: int = 20
        # B안: 오염 거부 임계.
        # 한 맵 내 연속 좌표가 점프 임계보다 크면 거부 (격수 순간이동 불가).
        # 맵 전환 직후 첫 좌표가 이전 맵 exit_coord 근처면 오염 → 거부.
        # 2026-06-11 롤백: v28에서 3으로 낮췄다가 대참사 — 힐러가 UDP로 받는
        # 격수 좌표는 수신주기로 d=3~7 정상(격수 화면은 d=1이지만 송신/수신
        # 주기로 점프). 3으로 막으니 TRAIL-REJECT 1947회(push 245)로 trail 텅 빔
        # → 추종 전멸. 8 복원. (19,4) 노이즈 문제는 jump_reject로 못 잡음 — 별도.
        self._jump_reject_threshold: int = 8
        # 2026-06-11 맵 끝자리 OCR 오독 게이트: 맵바 폰트≠좌표 폰트라 digit_cnn
        # 재사용 불가 → 폰트 무관 방식. 옛바 1칸이동이라 같은맵 격수좌표는
        # d≤4(v30 jump_max). 격수 좌표가 연속인데 맵명만 변하면 끝자리 OCR
        # 오독(실증 02:55: 격수 (22,6)→(26,6) R연속인데 맵 z2↔z3 진동).
        # min_jump=5(>jump_max 4)면 거부, N프레임 연속초과만 진짜 전환 수용.
        self._atk_prev_coord = None
        self._map_change_min_jump: int = 5
        self._map_ocr_reject: int = 0
        self._map_ocr_reject_max: int = 3
        self._fresh_reject_threshold: int = 3
        # 맵별 "방금 리셋된 상태" 플래그 — 첫 push에 이전 맵 exit_coord 체크용.
        self._fresh_map_guard: dict = {}  # map_name → (exit_coord, expires_ts)
        self._reject_count: dict = {}  # 통계용
        # MAP-SYNC: 맵 OCR(RapidOCR, 0.5s throttle)이 좌표 OCR(digit_cnn)보다
        # 느려 맵 전환 순간 "좌표는 새 맵 / 맵이름은 이전 맵"인 500~1000ms 창이 생김.
        # 이 창에서 B2:MAPNEQ가 엉뚱한 방향으로 키 hold → 왔다갔다 버그 유발.
        # 해결: 맵 전환 트리거 감지 시 h_map==a_map 될 때까지 키 입력 보류(최대 1.5초).
        # 트리거 2개: (1) 격수 map_seq edge (2) 힐러 좌표 점프 (같은 h_map 내 d>60).
        self._map_sync_until: float = 0.0
        self._map_sync_duration: float = 0.3
        self._healer_last_coord_by_map: dict = {}  # map_name → (x,y)
        self._healer_coord_jump_threshold: int = 60
        # 포탈 DB (2026-06-10 사용자 제안): (from_map→to_map) 포탈 좌표 영구 누적.
        # 격수 맵전환 확정 시 경계 정제된 exit_coord 기록 → 다음부턴 중앙값으로
        # 포탈 위치/방향 직행 (OCR 노이즈·UDP 누락 무관). 파일=실행루트.
        # 2026-06-11: portals_v2 로 파일명 변경 → v24 EXIT-BOUNDARY 코너보완
        # 이전에 쌓인 잘못된 학습(예 2-3(1)→(2) (19,5)U)을 자동 폐기하고
        # v24 로직으로 새로 학습. 구 portals.json 은 무시(사용자 수동삭제 불필요).
        self._portal_db_path = (pathlib.Path(__file__).resolve().parents[2]
                                / "portals_v2.json")
        self._portal_db: dict = {}  # "from|to" → {coords:[[x,y]..], dir, n}
        self._load_portal_db()
        # 맵 데이터 grid (S0 실시간 수집): 좌표 walk/tab + STUCK blocked 영구
        # 누적 → maps/<맵>.json (맵 데이터화 로드맵.md §6). add_* 는 메모리
        # dict O(1), flush 만 디스크라 30초 throttle 로 핫패스 IO 회피.
        self._grid = MapGrid(pathlib.Path(__file__).resolve().parents[2]
                             / "maps")
        self._grid_flush_interval: float = 30.0
        self._grid_last_flush: float = time.time()
        # 격수 막힘률(§6.5): (방향, 시작x, 시작y, 시작ts). 격수가 키 누른 뒤
        # 그 축 좌표가 변하면 통행 성공, 0.5s 안 변하면 막힘(몹 or 벽) 누적.
        self._atk_try = None
        self._atk_block_sec: float = 0.5
        # EXIT-FALLBACK (2026-06-10): exit_dir 오판/출구좌표 UDP누락 안전망.
        # map_neq 지속 + 힐러 좌표 8초 정체(정상 전환 실측 최대 5.3s + 여유)
        # → exit_dir 교체 (반대→직교 순환). healer-120 (7) 14초 정체 사고 대응.
        self._exit_fallback_sec: float = 5.0  # 2026-06-15: 8→5 ((7) 19s 방치 단축)
        self._exit_fallback_n: int = 0
        self._healer_coord_progress_ts: float = time.time()
        self._healer_coord_progress_base: Optional[tuple] = None
        # TAB-CONFIRM: 흰탭 감지 → Home→Tab → red&!white 확정. 상세는 tab_confirm.py.
        # Follower 는 _tab 인스턴스에 위임. 하위 호환용 property 로 외부 노출.
        self._tab = TabConfirm(
            hard_timeout=10.0,
            required=2,
            key_gap=0.06,
            fg_retry_max=2,
            pre_stability_duration=0.0,
            post_confirm_duration=0.0,
            retry_max=2,
        )

    # ---- 경계 기반 출구점 (2026-06-10) ----
    @staticmethod
    def _boundary_exit(pts) -> tuple:
        """trail 점열에서 '관측 bbox 경계에 닿은 마지막 점'을 출구로 선택.

        포탈은 맵 가장자리(상하좌우 어느 쪽이든, 맵마다 다름)에 있고 절대
        경계값은 하드코딩 불가 → 그 맵에서 관측된 min/max 상대 판정.
        마지막 1점 OCR 노이즈가 경계에 안 닿으면 자동 배제됨
        (healer-120 (7) 사고: tail (6,1)→(4,7) 노이즈가 exit_dir 'D' 오판).
        반환 (coord, dir, idx) — 못 찾으면 (None, None, None).
        idx는 경계점의 trail 내 위치 — 그 뒤 점들은 오염(맵명 OCR 지연 창에
        들어온 새 맵 좌표) 절단용 (healer-37 (7) 사고 2026-06-10).
        """
        pl = list(pts) if pts else []
        if len(pl) < 2:
            return None, None, None
        xs = [p[0] for p in pl]
        ys = [p[1] for p in pl]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        # 폭이 너무 좁은 축은 판정 제외 (복도형 맵 — 전부 경계가 돼버림).
        use_x = (xmax - xmin) >= 3
        use_y = (ymax - ymin) >= 3

        def _bcands(px, py):
            c = []
            if use_x and px <= xmin + 1:
                c.append("L")
            if use_x and px >= xmax - 1:
                c.append("R")
            if use_y and py <= ymin + 1:
                c.append("U")
            if use_y and py >= ymax - 1:
                c.append("D")
            return c

        # 뒤에서부터 최대 10점. 경계 닿은 점 + 진행방향 일치 채택.
        # OCR 노이즈 점프는 bbox 극값을 만들어도 진행 방향과 어긋나 기각됨
        # (healer-120 (7) 사고: 오염 다점 (6,1)→(3,6)→(4,6) 진행불일치 기각,
        #  (7,1) U 채택). 이 부분은 그대로 — 다점 오염에 강건.
        # 2026-06-11 보완: 마지막 점이 코너(2축 경계)이고 직전 점이 이미 한
        # 경계축에 도달했으면 그 축 우선 (포탈 앞 미세조정 강건).
        #  healer-37 2-3(1)→(2): 격수 (18,9)→(19,9) R로 xmax 도달 후 (19,4)
        #  로 y만 변동 → 코너 (19,4) 의 진행 U 대신, 직전 (19,9) 가 공유하는
        #  R 경계 채택. 기존엔 진행 U 일치로 (19,5)U 오학습했음.
        lo = max(0, len(pl) - 10)
        for i in range(len(pl) - 1, lo - 1, -1):
            px, py = pl[i]
            cands = _bcands(px, py)
            if not cands:
                continue
            if i >= 1:
                qx, qy = pl[i - 1]
                # 코너 보완: 직전 점과 공유하는 경계축이 정확히 1개면 그 축.
                if len(cands) >= 2:
                    shared = [c for c in cands if c in _bcands(qx, qy)]
                    if len(shared) == 1:
                        return (px, py), shared[0], i
                    # 2026-06-15: 코너 모호(공유축 0/2+) 시 세로(U/D) 우선.
                    # 포탈은 보통 진행 끝 수직(상/하단)이라 가로 진행방향이
                    # 오판되기 쉬움 ((7)→허브 (3,0) 'L' 오학습). 가로 출구가
                    # 명확하면(공유축 1개) 위에서 이미 보존. 틀리면 EXIT-FALLBACK.
                    # 2026-06-15 재수정: 단 직전 진행이 순수 가로(dy==0,dx≠0)면
                    # 진짜 가로 진출이라 R/L 존중(아래 pref). (19,10) R진출을
                    # U로 덮던 버그 — 격수 (18,10)→(19,10) R인데 R·U 공유코너라
                    # U/D 우선이 U로 오판. 순수 가로는 노이즈 아님(연속 단조).
                    _dxc, _dyc = px - qx, py - qy
                    if not (_dyc == 0 and _dxc != 0):
                        for _vd in ("U", "D"):
                            if _vd in cands:
                                return (px, py), _vd, i
                dx, dy = px - qx, py - qy
                if dx or dy:
                    pref = (("R" if dx > 0 else "L")
                            if abs(dx) >= abs(dy)
                            else ("D" if dy > 0 else "U"))
                    if pref in cands:
                        return (px, py), pref, i
                    continue  # 진행-경계 불일치 = 노이즈 의심 → 더 과거로.
                continue  # 제자리 dedup 직후 — 판정 불가, 더 과거로.
            return (px, py), cands[0], i
        return None, None, None
        return None, None, None

    # ---- 선비족 z(층) 전환 출구 방향 = 고정 규칙 (2026-06-15 사용자) ----
    # 굴/지역 무관 전부 동일: 1→2 R, 2→3 U, 3→4 U, 4→5 U, 5→6 L,
    # 6→7 L, 7→로비 U. 추정(global/boundary/portal-db)을 최종 override.
    _SUNBI_Z_EXIT = {1: "R", 2: "U", 3: "U", 4: "U", 5: "L", 6: "L", 7: "U"}
    _SUNBI_Z_RE = re.compile(r"(?:제2)?선비족\d+-\d+\((\d+)\)\s*$")
    # 2026-06-20: 맵 학습(trail/grid) 저장 게이트용 구조 검증. 입구/허브/굴 정상
    # 구조만 통과 → OCR 오독('거적데기 백호의희원 3초' 등 27/233 파일 오염)이
    # maps/*.json 으로 저장되는 것 차단.
    # 2026-06-28: 본성입구/선녀의방/비밀통로/일본신궁 사냥터 라인 추가. 기존이
    # 선비족 전용이라 새 사냥터는 trail push 가 통째 거부돼(782) 발자국이 안
    # 쌓임 → 같은맵 추종 불가(힐러가 격수 못 따라감)였던 근본 해소. base 표기는
    # 사용자 확정: 본성입구{1-7}/선녀의방{1-17}(1지역은 '제N' 생략)/비밀통로/
    # 일본신궁. 함수명(_is_valid_sunbi_map)은 호출부 호환 위해 유지.
    _SUNBI_VALID_RE = re.compile(
        r"^(?:제\d+)?(?:"
        r"선비족(?:입구|\d+방?|\d+(?:-\d+)?(?:\(\d+\))?)"
        r"|본성입구\d+|선녀의방\d+|무사의방\d+|닌자의방|비밀통로|일본신궁\d*"
        r")$")

    @classmethod
    def _is_valid_sunbi_map(cls, m: str) -> bool:
        return bool(m) and bool(cls._SUNBI_VALID_RE.match(m))

    # ---- 본성입구/선녀의방/무사의방/비밀통로 = 맵명별 고정 출구방향 ----
    # (2026-06-28 사용자 실측). 선비족 z층 규칙과 달리 '떠나는 맵' 이름만으로
    # 방향 확정(다음맵 무관). 제{x} 접두는 지역만 다르고 같은 번호면 같은 방향
    # 으로 간주(맵 구조 동일). 무사의방10=출구없음(끝 맵)→테이블 제외(None→
    # 기존 추정 fallback). 닌자의방 방향은 미제공(None).
    _BONSUNG_EXIT = {1: "U", 2: "U", 3: "D", 4: "U", 5: "U", 6: "D", 7: "U"}
    _SUNNYEO_EXIT = {1: "D", 2: "U", 3: "D", 4: "U", 5: "R", 6: "R", 7: "U",
                     8: "U", 9: "D", 10: "U", 11: "U", 12: "U", 13: "U",
                     14: "R", 15: "U", 16: "L", 17: "U"}
    _MUSA_EXIT = {1: "R", 2: "R", 3: "U", 4: "U", 5: "D", 6: "R", 7: "R",
                  8: "U", 9: "U"}  # 10=출구없음
    _BIMIL_EXIT = "R"
    _LINE_EXIT_RE = re.compile(
        r"^(?:제\d+)?(본성입구|선녀의방|무사의방|비밀통로|닌자의방)(\d*)$")

    @classmethod
    def _line_exit_dir(cls, m):
        """본성입구 라인 사냥터의 맵명별 고정 출구방향. 해당없으면 None.

        떠나는 맵(prev/_exit_map) 이름만으로 결정. 선비족(_sunbi_exit_dir)보다
        우선 적용하되 배타적(맵 base 가 다름). 무사의방10/닌자의방 등 미정은
        None → 기존 추정(EXIT-BOUNDARY/PORTAL-DB) 으로 fallback.
        """
        if not m:
            return None
        mt = cls._LINE_EXIT_RE.match(m)
        if not mt:
            return None
        base, num = mt.group(1), mt.group(2)
        if base == "비밀통로":
            return cls._BIMIL_EXIT
        if base == "닌자의방":
            return None
        if not num:
            return None
        tbl = {"본성입구": cls._BONSUNG_EXIT, "선녀의방": cls._SUNNYEO_EXIT,
               "무사의방": cls._MUSA_EXIT}.get(base)
        return tbl.get(int(num)) if tbl else None

    @classmethod
    def _sunbi_exit_dir(cls, prev_map, new_map):
        """선비족 굴 z(층) 정방향 전환의 고정 출구 방향. 해당없으면 None.

        prev=선비족x-y(z) 이고 new=z+1(정방향)이면 표 적용. z==7에서
        굴 밖(허브/로비/입구)으로 나가면 U. 역방향(데리러 복귀)·동일·
        비선비족은 None → 기존 추정 로직 유지(망치지 않음).
        """
        if not prev_map:
            return None
        m = cls._SUNBI_Z_RE.search(prev_map)
        if not m:
            return None
        z = int(m.group(1))
        nm = cls._SUNBI_Z_RE.search(new_map or "")
        if nm is not None:
            new_z = int(nm.group(1))
            if new_z == z + 1 and z in cls._SUNBI_Z_EXIT:
                return cls._SUNBI_Z_EXIT[z]
            return None
        # 새 맵이 굴 구조가 아님(허브/로비/입구) — z==7 탈출만 확정.
        if z == 7:
            return "U"
        return None

    # ---- 포탈 DB (2026-06-10) ----
    def _load_portal_db(self) -> None:
        try:
            if self._portal_db_path.exists():
                self._portal_db = json.loads(
                    self._portal_db_path.read_text(encoding="utf-8"))
        except Exception:
            self._portal_db = {}

    def _save_portal_db(self) -> None:
        try:
            self._portal_db_path.write_text(
                json.dumps(self._portal_db, ensure_ascii=False, indent=1),
                encoding="utf-8")
        except Exception:
            pass

    def _portal_record(self, from_map: str, to_map: str,
                       coord, direction: str) -> None:
        """이번 전환에서 관측한 출구좌표/방향 누적 (최근 9회 유지)."""
        if not from_map or not to_map or coord is None:
            return
        key = f"{from_map}|{to_map}"
        e = self._portal_db.setdefault(
            key, {"coords": [], "dir": "-", "n": 0})
        e["coords"].append([int(coord[0]), int(coord[1])])
        e["coords"] = e["coords"][-9:]
        e["n"] = int(e.get("n", 0)) + 1
        if direction in ("L", "R", "U", "D"):
            e["dir"] = direction
        self._save_portal_db()

    def portal_lookup(self, from_map: str, to_map: str) -> tuple:
        """과거 관측 포탈 (coord, dir). 좌표는 축별 중앙값(노이즈 흡수)."""
        e = self._portal_db.get(f"{from_map}|{to_map}")
        if not e or not e.get("coords"):
            return None, "-"
        xs = sorted(c[0] for c in e["coords"])
        ys = sorted(c[1] for c in e["coords"])
        mid_x = xs[len(xs) // 2]
        mid_y = ys[len(ys) // 2]
        return (mid_x, mid_y), str(e.get("dir", "-"))

    def _note_atk_attempt(self, s, now: float) -> None:
        """격수 방향키 + 좌표 변화로 통행/막힘 누적 (§6.5 막힘률).

        격수(인간)가 d 방향 키를 누른 뒤:
          - 그 축 좌표가 변함 → 통행 성공(add_attempt passed=True).
          - 0.5s 안 변함 → 막힘(passed=False). 같은 자리 재무장 →
            계속 막히면 0.5s마다 누적(벽=막힘률 1.0, 몹=가끔이라 낮음).
        축 변화로만 판정(부호 무관) → U/D 좌표축 부호 불확실 회피.
        """
        key = getattr(s, "atk_key", "-")
        # 진단(2026-06-13): 격수 atk_key 수신 흔적 (5s throttle). healer 로그에
        # [ATK-KEY-RX] 가 보이면 격수 송신+힐러 수신 OK → 누적 로직 점검.
        # 안 보이면 격수 atk_key='-' (격수 미업데이트 or detect 게이트 막힘).
        if key in ("L", "R", "U", "D") and self.log is not None \
                and now - getattr(self, "_atk_key_log_ts", 0.0) > 5.0:
            self._atk_key_log_ts = now
            self.log.debug(f"[ATK-KEY-RX] {key} atk=({s.x},{s.y}) "
                           f"map={s.map_name!r} valid={s.coord_valid}")
        if (not s.map_name or key not in ("L", "R", "U", "D")
                or not s.coord_valid):
            self._atk_try = None
            return
        cx, cy = s.x, s.y
        tr = self._atk_try
        if tr is None or tr[0] != key:
            self._atk_try = (key, cx, cy, now)
            return
        _, sx, sy, ts = tr
        moved = (cx != sx) if key in ("L", "R") else (cy != sy)
        if moved:
            self._grid.add_attempt(s.map_name, sx, sy, key, True)
            self._atk_try = (key, cx, cy, now)        # 새 위치서 재시작
        elif now - ts >= self._atk_block_sec:
            self._grid.add_attempt(s.map_name, sx, sy, key, False)
            self._atk_try = (key, sx, sy, now)        # 같은 자리 재무장

    def note_blocked(self, map_name: str, x: int, y: int, d: str) -> None:
        """STUCK 확정 벽(좌표+방향)을 맵 grid 에 영구 누적.

        healer_worker._decide_move 의 STUCK-RESET(3.5s 초과 = 진짜 벽) 지점에서
        호출. blacklist 는 휘발 TTL, grid 는 영구 누적(장애물 1차 증거).
        """
        self._grid.add_blocked(map_name, x, y, d)

    def note_healer_map(self, map_name: str, healer_coord: Optional[tuple] = None):
        """힐러 자신의 맵 이름 주입 (OCR 결과). 맵 전환 3단계 판정용.

        F안: 힐러 자신의 맵 OCR도 역방향 flicker 디바운스 적용.
        2초 내 A→B→A 역방향 변경은 3프레임 확인 후 수락.

        K안: healer_coord가 들어오고 새 map_name의 trail 범위에서 margin 밖이면
        OCR 오인 판정 → N프레임 추가 확인 후 수락. trail 비어있으면 스킵.
        """
        now = time.time()
        # MAP-SYNC 트리거: 같은 h_map 내 힐러 좌표가 물리적으로 불가능한 점프
        # (옛바 이동 모델: 순간이동/사다리 없음) → 실제로는 맵이 바뀐 것.
        # 맵 OCR이 아직 못 따라잡은 상태라 정책: h_map==a_map 될 때까지 이동 보류.
        # 맵이 이미 바뀐 프레임에서도 이전 맵 기준 좌표가 기록돼 있으면 트리거 가능.
        if healer_coord is not None and map_name:
            # EXIT-FALLBACK 진행 추적: 힐러 좌표가 base 대비 맨해튼 2 이상
            # 움직이면 진행으로 간주 (1칸 제자리 진동은 무시) → 타이머 리셋.
            base = self._healer_coord_progress_base
            _moved = (base is None or (abs(healer_coord[0] - base[0])
                                       + abs(healer_coord[1] - base[1])) >= 2)
            # 2026-06-15 fix: 벽 왕복((1,8)↔(1,11) 진동)을 '진행'으로 오인해
            # EXIT-FALLBACK 8s 타이머를 무한 리셋하던 버그(전 세션 0회 발동, (7)
            # 탈출 19s 방치). 최근 방문한 좌표로의 이동은 진행이 아님(왕복).
            if not hasattr(self, "_progress_recent"):
                self._progress_recent = deque(maxlen=8)
            _revisit = healer_coord in self._progress_recent
            self._progress_recent.append(healer_coord)
            if _moved and not _revisit:
                self._healer_coord_progress_base = healer_coord
                self._healer_coord_progress_ts = now
            prev_hc = self._healer_last_coord_by_map.get(map_name)
            if prev_hc is not None:
                dj = (abs(healer_coord[0] - prev_hc[0])
                      + abs(healer_coord[1] - prev_hc[1]))
                if dj > self._healer_coord_jump_threshold:
                    self._map_sync_until = now + self._map_sync_duration
                    if self.log is not None:
                        self.log.debug(
                            f"[HEALER-COORD-JUMP] map={map_name!r} "
                            f"prev={prev_hc} new={healer_coord} d={dj} "
                            f"thr={self._healer_coord_jump_threshold} "
                            f"→ MAP-SYNC hold {self._map_sync_duration*1000:.0f}ms"
                        )
            # 맵 grid 수집: 힐러 walk (좌표 변할 때만, OCR 점프는 제외).
            # 2026-07-05: 맵명 구조검증 게이트 추가 — 격수 훅(update L830)과
            # 동일. 게이트 없이는 OCR 오독('브브로' 등) 파일이 maps/ 에 축적
            # (maps_cloud 감사에서 오염 파일 다수 실측). 수집만 거르고 맵
            # 추적/trail 로직은 불변.
            if self._is_valid_sunbi_map(map_name):
                if prev_hc is None:
                    self._grid.add_walk(map_name, healer_coord[0],
                                        healer_coord[1])
                elif healer_coord != prev_hc:
                    _dj = (abs(healer_coord[0] - prev_hc[0])
                           + abs(healer_coord[1] - prev_hc[1]))
                    if _dj <= self._healer_coord_jump_threshold:
                        self._grid.add_walk(map_name, healer_coord[0],
                                            healer_coord[1])
            self._healer_last_coord_by_map[map_name] = healer_coord
        if not map_name or map_name == self._healer_map:
            # 같은 맵이면 reversion 카운터 리셋.
            self._healer_reversion_count = 0
            self._hmap_coord_mismatch_count = 0
            return
        # K안: 맵 변경 시도 — coord 범위 crosscheck.
        # 새 map_name의 trail bbox에서 healer_coord가 margin 이상 벗어나면 거부.
        if healer_coord is not None:
            new_trail = self._map_trail.get(map_name)
            if new_trail and len(new_trail) >= 2:
                xs = [p[0] for p in new_trail]
                ys = [p[1] for p in new_trail]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                hx, hy = healer_coord
                m = self._hmap_bbox_margin
                out_of_bbox = (hx < min_x - m or hx > max_x + m
                               or hy < min_y - m or hy > max_y + m)
                if out_of_bbox:
                    self._hmap_coord_mismatch_count += 1
                    # A+B: bbox 밖이면 무한정 거부. 시간 기반 자동 수락 제거.
                    # 로그는 초기 5프레임 + confirm 주기마다만 (스팸 방지).
                    if self.log is not None and (
                        self._hmap_coord_mismatch_count <= 5
                        or self._hmap_coord_mismatch_count
                           % self._hmap_coord_mismatch_confirm == 0
                    ):
                        self.log.debug(
                            f"[HMAP-COORD-MISMATCH] reject "
                            f"{self._healer_map!r}→{map_name!r} "
                            f"h={healer_coord} "
                            f"bbox=[{min_x-m},{max_x+m}]x[{min_y-m},{max_y+m}] "
                            f"frame={self._hmap_coord_mismatch_count}"
                        )
                    return
                else:
                    # bbox 안으로 들어옴 — 수락. 이전 거부 기록 있었으면 로그.
                    if (self._hmap_coord_mismatch_count > 0
                            and self.log is not None):
                        self.log.debug(
                            f"[HMAP-COORD-MISMATCH-CONFIRM] accepted "
                            f"{self._healer_map!r}→{map_name!r} h={healer_coord} "
                            f"entered bbox after "
                            f"{self._hmap_coord_mismatch_count} reject frames"
                        )
                    self._hmap_coord_mismatch_count = 0
        # 역방향 판정: map_name이 직전 healer_map과 동일?
        is_reverse = (
            map_name == getattr(self, "_prev_healer_map", "")
            and (now - getattr(self, "_last_healer_mapchg_ts", 0.0))
                < self._reversion_debounce_sec
        )
        if is_reverse:
            self._healer_reversion_count = getattr(
                self, "_healer_reversion_count", 0) + 1
            if self._healer_reversion_count < self._reversion_confirm_frames:
                if self.log is not None:
                    self.log.debug(
                        f"[HMAP-DEBOUNCE] hold reversion "
                        f"{self._healer_map!r}→{map_name!r} "
                        f"frame={self._healer_reversion_count}/"
                        f"{self._reversion_confirm_frames}"
                    )
                return  # 아직 확정 아님 — healer_map 유지.
            if self.log is not None:
                self.log.debug(
                    f"[HMAP-DEBOUNCE-CONFIRM] accepted "
                    f"{self._healer_map!r}→{map_name!r} "
                    f"after {self._healer_reversion_count} frames"
                )
            self._healer_reversion_count = 0
        else:
            self._healer_reversion_count = 0
        # 수락 — 변경 기록.
        self._prev_healer_map = self._healer_map
        self._last_healer_mapchg_ts = now
        self._healer_map = map_name

    def _map_ocr_gate(self, s) -> None:
        """맵 끝자리 OCR 오독 거부 — 격수 좌표 연속이면 맵전환 아님.

        부수효과: 오독 판정 시 s.map_name 을 직전 맵으로 되돌림(거부).
        맵바 폰트≠좌표 폰트로 digit_cnn 재사용 불가 → 폰트 무관 좌표 게이트.
        옛바 1칸이동: 같은맵 격수좌표 d≤4(v30 jump_max). 맵명만 변하고 좌표
        연속(<min_jump)이면 끝자리 오독. N프레임 연속초과면 진짜 전환 수용.
        """
        if not (s.map_name and self._last_map
                and s.map_name != self._last_map
                and getattr(s, "coord_valid", False)
                and self._atk_prev_coord is not None):
            self._map_ocr_reject = 0
            return
        pcx, pcy = self._atk_prev_coord
        d_atk = abs(s.x - pcx) + abs(s.y - pcy)
        if d_atk >= self._map_change_min_jump:
            self._map_ocr_reject = 0  # 좌표 급변 동반 = 진짜 맵전환.
            return
        self._map_ocr_reject += 1
        if self._map_ocr_reject <= self._map_ocr_reject_max:
            if self.log is not None:
                self.log.debug(
                    f"[MAP-OCR-HOLD] {self._last_map!r}→{s.map_name!r} "
                    f"격수좌표연속 d={d_atk}(<{self._map_change_min_jump}) "
                    f"끝자리 오독 거부 "
                    f"{self._map_ocr_reject}/{self._map_ocr_reject_max}"
                )
            s.map_name = self._last_map  # 오독 거부 → 직전 맵 유지.
        else:
            self._map_ocr_reject = 0  # 연속 초과 = 실제 전환 → 통과.

    def update(self, s: Optional[State]) -> FsmState:
        now = time.time()
        # 맵 grid 주기 flush (dirty 맵만 디스크 저장). 핫패스 IO 회피용 throttle.
        if now - self._grid_last_flush >= self._grid_flush_interval:
            self._grid_last_flush = now
            try:
                self._grid.flush()
            except Exception:
                pass
        if s is None:
            s = State()  # 빈 상태로라도 계속 평가 (disconnected 판정)
        # 격수 막힘률 누적 (§6.5). 실패해도 본 로직 막지 않게 방어.
        try:
            self._note_atk_attempt(s, now)
        except Exception:
            pass

        if getattr(s, "seq", 0) > 0 or getattr(s, "coord_valid", False):
            self._last_udp_time = now

        # 2026-06-11 v34 롤백: v33 _map_ocr_gate 비활성. min_jump=5 전제(맵전환=
        # 격수좌표 급변)가 틀림 — 맵전환도 격수좌표 d<5 빈번(포탈 양쪽 좌표 유사)
        # → 진짜 맵전환 83회 오독차단(CTRL-MAPCHG 12→3) 추종 전멸. 좌표연속성으론
        # 맵전환/오독 구분 불가. 끝자리 오독은 다른 방법(측정 후) 필요.
        # self._map_ocr_gate(s)  # 비활성 — 추종 전멸 유발

        # C안: 역방향 맵 복귀 디바운스 — **pre-pipeline 위치**.
        # 2026-04-15 수정: 과거엔 MAP-SEQ-EDGE/MAP-SYNC 설정 **뒤**에 있어
        # OCR flicker(mapA→mapB→mapA 2s 내)가 이미 pause/sync를 폭주시킨 뒤
        # 뒤늦게 debounce만 수락/거부 판정 → 가짜 MAP-PAUSE/SYNC 낭비.
        # 이제 reversion 확정 전엔 pause/sync 트리거 자체를 스킵.
        # 최근 2초 내 mapA→mapB 변경이 있었고, 이번 프레임이 mapB→mapA로
        # 되돌리려는 신호라면 3프레임 확인 후 수락.
        accept_frame = True
        if (s.map_name and s.map_name != self._last_map and self._last_map
                and s.map_name == self._prev_last_map
                and (now - self._last_mapchg_ts) < self._reversion_debounce_sec):
            self._pending_reversion_count += 1
            if self._pending_reversion_count < self._reversion_confirm_frames:
                accept_frame = False
                if self.log is not None:
                    self.log.debug(
                        f"[MAP-DEBOUNCE] hold reversion "
                        f"{self._last_map!r}→{s.map_name!r} "
                        f"frame={self._pending_reversion_count}/"
                        f"{self._reversion_confirm_frames} "
                        f"elapsed={(now - self._last_mapchg_ts):.2f}s"
                    )
            else:
                if self.log is not None:
                    self.log.debug(
                        f"[MAP-DEBOUNCE-CONFIRM] reversion accepted "
                        f"{self._last_map!r}→{s.map_name!r} "
                        f"after {self._pending_reversion_count} frames"
                    )
                self._pending_reversion_count = 0
        else:
            self._pending_reversion_count = 0

        if not accept_frame:
            # 디바운스 중 — MAP-SEQ-EDGE/좌표/맵 갱신 전부 스킵.
            # 상태평가는 계속(DISCONNECTED 등).
            return self._eval_state_only(now)

        # I안: 격수 map_seq edge 감지 — 맵전환 이벤트.
        # _pause_until 설정 → 힐러는 해당 구간 키 입력 release + 이동 결정 스킵.
        s_map_seq = getattr(s, "map_seq", 0)
        if s_map_seq > self._last_seen_map_seq:
            prev_seq = self._last_seen_map_seq
            self._last_seen_map_seq = s_map_seq
            self._pause_until = now + self._pause_sec
            # MAP-SYNC: 격수 맵 전환 = 힐러도 곧 전환할 가능성 높음.
            # h_map == a_map 될 때까지(최대 1.5초) 이동 결정 보류.
            self._map_sync_until = now + self._map_sync_duration
            # 2026-04-22: ATK-WARP(같은 맵 이름 내 워프)에서도 map_seq++ 통지됨.
            # 맵 이름이 안 바뀌는 케이스는 line 562 자연 리셋이 안 걸림 →
            # 여기서 해당 맵 jump 가드 anchor 명시적 pop. 맵 변경 중복 pop은 무해.
            if s.map_name:
                self._atk_last_valid_coord_by_map.pop(s.map_name, None)
            # TAB-CONFIRM arm은 더 이상 MAP-SEQ-EDGE에서 하지 않음.
            # 흰탭 3프레임 확정 시 healer_gui에서 arm (nc=2 YOLO 신뢰 기반).
            if self.log is not None:
                self.log.debug(
                    f"[MAP-SEQ-EDGE] prev={prev_seq}→{s_map_seq} "
                    f"pause_until={self._pause_until:.3f} "
                    f"({self._pause_sec*1000:.0f}ms) "
                    f"map_sync_until={self._map_sync_until:.3f} "
                    f"({self._map_sync_duration*1000:.0f}ms) "
                    f"atk_map={s.map_name!r} healer_map={self._healer_map!r}"
                )
            # 재설계 (2026-04-16): MAP-SEQ-EDGE 중 TAB-CONFIRM 활성이면 **취소**.
            # 새 맵 진입 시 이전 route의 상황 전제(타겟 잃음)가 무효화됨. MAP-PAUSE
            # + 새 맵 안정 후 새로운 arm 조건(FOLLOW & map_eq & 흰탭 3프레임)이
            # 다시 평가됨. Tab/ESC 재송신 방지.
            if self._tab.active:
                _was_tab_sent = self._tab.tab_sent
                old_sub = self._tab.reset()
                if self.log is not None:
                    self.log.debug(
                        f"[TAB-CONFIRM-CANCEL] MAP-SEQ-EDGE → "
                        f"route sub={old_sub!r} 폐기 tab_sent={_was_tab_sent}"
                    )
                # 2026-06-11 수정A: 이미 본인 빨탭(Tab) 송신 후 맵 전환됨
                # = G1 전이 시도 완료. 새 맵에서 즉시 재arm 하면 또 본인 빨탭
                # → 본인 힐 루프(사용자 #5-1). grace 동안 흰탭 arm 억제 →
                # 전이됐으면 빨탭 정상, 진짜 흰탭이면 grace 후 재시도.
                if _was_tab_sent:
                    self._post_tab_grace_until = now + self._post_tab_grace_sec
                    if self.log is not None:
                        self.log.info(
                            f"[POST-TAB-GRACE] 본인빨탭 후 맵전환=전이시도 → "
                            f"{self._post_tab_grace_sec:.1f}s arm 억제"
                        )

        # MAP-SYNC 해제 조건: h_map == a_map 확정되면 즉시 sync 해제.
        # (맵 OCR 따라잡음 — 좌표-맵 일관성 회복).
        if (self._map_sync_until > 0.0 and s.map_name
                and self._healer_map and s.map_name == self._healer_map):
            if self.log is not None:
                remain = max(0.0, self._map_sync_until - now)
                self.log.debug(
                    f"[MAP-SYNC-DONE] h_map==a_map={s.map_name!r} "
                    f"released early remain={remain*1000:.0f}ms"
                )
            self._map_sync_until = 0.0

        # EXIT-FALLBACK (2026-06-10): exit_dir 오판/출구좌표 UDP누락 안전망.
        # 조건: 맵 불일치(전환 못 끝냄) + 힐러 좌표 8초 정체(맨해튼<2,
        #       정상 전환 실측 최대 5.3s + 여유) → exit_dir 교체.
        # 순서: 반대방향 먼저 (오판이면 반대가 정답일 확률 — (7) 사고 D→U),
        #       그다음 직교 2방향 순환. 좌표 진행/맵 일치 시 자동 리셋.
        _mapneq_now = bool(self._healer_map and self._last_map
                           and self._healer_map != self._last_map)
        # 2026-06-19: 출구 직전(exit_coord 2칸 이내)이면 방향 교체가 출구서
        # 멀어지게 함(출구는 1칸 직진뿐) → EXIT-FALLBACK skip, exit_dir 유지.
        # (7)→로비 출구(6,0) 직전 (6,1) 몹/타힐러 막힘에 직교로 헛돌던 것(120
        # 로그 12:49~50) 차단. 장애물 풀리면 그 방향 통과(STUCK-WAIT healer 가
        # 대기 담당). progress_base(최근 힐러좌표)로 근접 판정.
        _near_exit_fb = False
        _hcb = self._healer_coord_progress_base
        if self._exit_coord is not None and _hcb is not None:
            if (abs(_hcb[0] - self._exit_coord[0])
                    + abs(_hcb[1] - self._exit_coord[1])) <= 2:
                _near_exit_fb = True
        if not _mapneq_now:
            self._exit_fallback_n = 0
        elif (self._exit_dir in ("L", "R", "U", "D")
              and now - self._healer_coord_progress_ts
              >= self._exit_fallback_sec
              and not _near_exit_fb):
            _rev = {"L": "R", "R": "L", "U": "D", "D": "U"}
            _ortho = {"L": ["U", "D"], "R": ["U", "D"],
                      "U": ["L", "R"], "D": ["L", "R"]}
            # 선비족 z전환 확정방향(7→로비 U 등)이면 반대(D)로 뒤집으면 규칙
            # 위반 → 직교만 순환(출구 x정렬 유도). 2026-06-16: SUNBI override
            # (_exit_map 기반 lock)는 맵전환 순간 맵명 오독 시 _exit_map 오염→
            # 0회 발동(173446 SUNBI-EXIT 0, U→D 처박). 그래서 lock 의존 대신
            # EXIT-FALLBACK 시점 현재 힐러맵으로 _sunbi_exit_dir 직접 재확인.
            _sdir = (self._line_exit_dir(self._healer_map)
                     or self._sunbi_exit_dir(self._healer_map, self._last_map))
            if _sdir is not None and self._exit_dir == _sdir:
                cands = _ortho[self._exit_dir]
            else:
                cands = [_rev[self._exit_dir]] + _ortho[self._exit_dir]
            new_dir = cands[self._exit_fallback_n % len(cands)]
            self._exit_fallback_n += 1
            self._healer_coord_progress_ts = now  # 다음 8초 재카운트
            if self.log is not None:
                self.log.info(
                    f"[EXIT-FALLBACK] {self._exit_fallback_sec:.0f}s 정체 → "
                    f"exit_dir {self._exit_dir!r}→{new_dir!r} "
                    f"(try#{self._exit_fallback_n} "
                    f"h_map={self._healer_map!r} a_map={self._last_map!r})"
                )
            self._exit_dir = new_dir

        # 좌표/맵별 last_seen 기록을 **transition 체크보다 먼저** 수행 (피드백 1:
        # "맵 변경 감지 순간이 아니라 계속 격수이동경로는 기록해야됨").
        # 그렇지 않으면 transition_phase 동안 early return으로 기록 누락.
        # J안: 격수 coord 점프 필터 — 동일 맵 내 이전 유효 coord 대비 jump_threshold
        # 초과시 OCR 오류 판정, s.coord_valid=False 강제. _decide_move 에까지 반영.
        if s.coord_valid and s.map_name:
            last_valid = self._atk_last_valid_coord_by_map.get(s.map_name)
            if last_valid is not None:
                dj = (abs(s.x - last_valid[0]) + abs(s.y - last_valid[1]))
                # 2026-06-10: > → >= (경계 포함). healer-37 (7) 사고에서 오염
                # 점프 (6,1)→(3,6)이 d=8로 임계 8과 같아 d>8 미충족 → 통과.
                # 실측 정상 push 간 델타 ≤5 (30Hz 수신, 1Hz 로그 기준)라 안전.
                if dj >= self._atk_jump_threshold:
                    if self.log is not None:
                        self.log.debug(
                            f"[ATK-COORD-JUMP] map={s.map_name!r} "
                            f"prev={last_valid} new=({s.x},{s.y}) d={dj} "
                            f"thr={self._atk_jump_threshold} "
                            f"→ coord_valid=False 강제"
                        )
                    s.coord_valid = False
        if s.coord_valid:
            coord = (s.x, s.y)
            self._last_valid_coord_time = now
            if self._last_coord is None or coord != self._last_coord:
                self._last_coord_change = now
                self._last_coord = coord
                self._coord_hist.append((now, coord))
            if s.map_name and self._is_valid_sunbi_map(s.map_name):
                # B안 필터 — 비정상 push 거부.
                # 2026-06-20: 맵명 구조검증 게이트 — OCR 오독이 trail/grid 오염 차단.
                push_ok = True
                reject_reason = ""
                trail = self._map_trail.get(s.map_name)
                # B2: 맵 전환 직후 첫 push — 이전 맵 exit_coord 근처면 오염.
                guard = self._fresh_map_guard.get(s.map_name)
                if guard is not None:
                    ex_coord, expires = guard
                    if now >= expires:
                        # 만료 — guard 해제.
                        del self._fresh_map_guard[s.map_name]
                    elif ex_coord is not None:
                        d_exit = (abs(coord[0] - ex_coord[0])
                                  + abs(coord[1] - ex_coord[1]))
                        if d_exit <= self._fresh_reject_threshold:
                            push_ok = False
                            reject_reason = (
                                f"fresh_near_exit exit={ex_coord} d={d_exit}"
                            )
                # B1: 기존 trail 끝점과 과도한 점프 거부.
                if push_ok and trail and len(trail) > 0:
                    px, py = trail[-1]
                    d_jump = abs(coord[0] - px) + abs(coord[1] - py)
                    # 2026-06-10: > → >= (수정 ③과 동일 — 경계값 d=8 통과 버그).
                    if d_jump >= self._jump_reject_threshold:
                        push_ok = False
                        reject_reason = (
                            f"jump last={trail[-1]} d={d_jump} "
                            f"threshold={self._jump_reject_threshold}"
                        )
                if not push_ok:
                    rc = self._reject_count.get(s.map_name, 0) + 1
                    self._reject_count[s.map_name] = rc
                    # 2026-06-15: 맵 전환 시 격수 새맵 좌표를 옛맵명으로 1초간
                    # 수신 → 같은 좌표 점프가 수십~수백회 반복 reject(로그 5190회
                    # 폭주). 같은 coord 연속 reject 는 첫 1회만 로그(throttle).
                    if self.log is not None \
                            and coord != getattr(self, "_last_reject_coord", None):
                        self._last_reject_coord = coord
                        self.log.debug(
                            f"[TRAIL-REJECT] map={s.map_name!r} coord={coord} "
                            f"reason={reject_reason} reject_total={rc}"
                        )
                else:
                    self._last_reject_coord = None  # 성공 push → throttle 리셋
                    # J안: 성공 push 시 last_valid 갱신 (다음 프레임 점프 판정 기준).
                    self._atk_last_valid_coord_by_map[s.map_name] = coord
                    self._map_last_coord[s.map_name] = coord
                    self._map_last_dir[s.map_name] = self._hist_direction()
                    self._map_last_ts[s.map_name] = now
                    # trail 누적: 해당 맵 deque 없으면 생성, 마지막 좌표와 다를 때만.
                    if trail is None:
                        trail = deque(maxlen=2000)
                        self._map_trail[s.map_name] = trail
                    if not trail or trail[-1] != coord:
                        trail.append(coord)
                        # 맵 grid 수집: 격수 walk(+ red_tab 이면 사냥 스팟).
                        # trail dedup 안이라 좌표 변할 때만 1회 (정지 중 중복 X).
                        self._grid.add_walk(s.map_name, coord[0], coord[1],
                                            tab=bool(getattr(s, "red_tab", False)))
                        # 전역 trail에도 (map, x, y) 태그 포함 push.
                        # 태그 전이 감지용. 같은 (map, coord) 중복 회피.
                        tagged = (s.map_name, coord[0], coord[1])
                        if not self._global_trail \
                                or self._global_trail[-1] != tagged:
                            self._global_trail.append(tagged)
                        # 첫 push 성공 → fresh guard 해제.
                        if s.map_name in self._fresh_map_guard:
                            del self._fresh_map_guard[s.map_name]
                        if self.log is not None:
                            cnt = self._push_count.get(s.map_name, 0) + 1
                            self._push_count[s.map_name] = cnt
                            last_ts = self._last_push_log_ts.get(
                                s.map_name, 0.0)
                            if cnt <= 10 or (now - last_ts) >= 1.0:
                                self._last_push_log_ts[s.map_name] = now
                                self.log.debug(
                                    f"[TRAIL-PUSH] map={s.map_name!r} "
                                    f"coord={coord} idx={len(trail)-1} "
                                    f"len={len(trail)} total_pushes={cnt}"
                                )

        # 격수 맵 변경 감지 → ENTER_PORTAL 단계 시작
        if s.map_name and s.map_name != self._last_map and self._last_map:
            # 이전 맵 이탈 직전 스냅샷 (맵 이동 추종용).
            # _last_coord/_coord_hist는 이미 새 맵의 첫 좌표로 오염됨 →
            # 이전 맵 트레일의 마지막 2점으로 exit_coord/exit_dir 재계산.
            self._exit_map = self._last_map
            old_trail = self._map_trail.get(self._last_map)
            # E안: "통과맵"(trail 비어있음) 전환 시 이전 exit_dir 계승.
            # 격수가 A→B→C 빠르게 지나가고 B에서 좌표 못 건져 B_trail=[]일 때,
            # B→C 전환시 exit_dir을 '-'로 두지 않고 A→B 때 썼던 dir 그대로 사용.
            prior_exit_dir = self._exit_dir
            prior_exit_coord = self._exit_coord
            computed = False
            if old_trail and len(old_trail) >= 1:
                self._exit_coord = old_trail[-1]
                if len(old_trail) >= 2:
                    px, py = old_trail[-2]
                    lx, ly = old_trail[-1]
                    dx, dy = lx - px, ly - py
                    if abs(dx) < 1 and abs(dy) < 1:
                        self._exit_dir = "-"
                    elif abs(dx) >= abs(dy):
                        self._exit_dir = "R" if dx > 0 else "L"
                    else:
                        self._exit_dir = "D" if dy > 0 else "U"
                    computed = True
                else:
                    self._exit_dir = "-"
            else:
                self._exit_coord = self._last_coord
                self._exit_dir = self._hist_direction()
            # E안 계승: 새로 계산된 exit_dir이 '-'인데 이전 exit_dir이 유효하면 계승.
            # A→B→A 복귀 패턴(목적지 == 직전 맵)이면 방향을 반전해서 상속.
            # ex: 대방성입구→대방성(L) 직후 대방성→대방성입구(trail=1)
            #     → 대방성입구로 "되돌아가는" 포털은 반대편(R)에 있을 확률 높음.
            inherited_dir = False
            if self._exit_dir == "-" and prior_exit_dir in ("L", "R", "U", "D"):
                reverse_map = {"L": "R", "R": "L", "U": "D", "D": "U"}
                is_return = (s.map_name == self._prev_last_map
                             and self._prev_last_map != "")
                inherited_value = (reverse_map[prior_exit_dir]
                                   if is_return else prior_exit_dir)
                self._exit_dir = inherited_value
                # coord도 이전 값 유지 (새 coord 추정 불가한 경우).
                if self._exit_coord is None:
                    self._exit_coord = prior_exit_coord
                inherited_dir = True
                if self.log is not None:
                    self.log.debug(
                        f"[EXIT-INHERIT] {self._last_map!r}→{s.map_name!r} "
                        f"inherited exit_dir={inherited_value!r} "
                        f"(prior={prior_exit_dir!r} return={is_return} "
                        f"prev_last={self._prev_last_map!r} "
                        f"old_trail_empty={len(old_trail) if old_trail else 0})"
                    )
            # 전역 trail 태그 전이 기반 exit_dir 재계산 — 오염 entry 배제.
            # global_trail 뒤에서부터 스캔하여 이전 맵(self._last_map) 유효 궤적
            # 마지막 2점 찾음. old_trail 기반 계산이 (옛맵, 새좌표) 오염 entry에
            # 속은 경우를 보정.
            g_clean_exit_dir = None
            g_clean_exit_coord = None
            gt = list(self._global_trail)
            # 맵 태그가 self._last_map인 entry만 역순 수집 (최대 5개).
            same_map_tail = []
            for e in reversed(gt):
                if e[0] == self._last_map:
                    same_map_tail.append((e[1], e[2]))
                    if len(same_map_tail) >= 5:
                        break
                elif same_map_tail:
                    # 이전 맵 구간 지나 더 과거 맵까지 가면 중단.
                    break
            # same_map_tail은 [최신, ..., 과거] — 마지막 2점으로 방향 계산.
            if len(same_map_tail) >= 1:
                g_clean_exit_coord = same_map_tail[0]
                if len(same_map_tail) >= 2:
                    lx, ly = same_map_tail[0]
                    px, py = same_map_tail[1]
                    dx, dy = lx - px, ly - py
                    if abs(dx) >= 1 or abs(dy) >= 1:
                        if abs(dx) >= abs(dy):
                            g_clean_exit_dir = "R" if dx > 0 else "L"
                        else:
                            g_clean_exit_dir = "D" if dy > 0 else "U"
            # old_trail 기반 결과와 다르면 global 결과로 덮어씀 (로그로 추적).
            if g_clean_exit_dir and g_clean_exit_dir != self._exit_dir:
                if self.log is not None:
                    self.log.debug(
                        f"[EXIT-DIR-GLOBAL] override "
                        f"old_trail_dir={self._exit_dir!r} → "
                        f"global_dir={g_clean_exit_dir!r} "
                        f"coord={g_clean_exit_coord} "
                        f"same_map_tail={same_map_tail[:3]}"
                    )
                self._exit_dir = g_clean_exit_dir
                if g_clean_exit_coord is not None:
                    self._exit_coord = g_clean_exit_coord
            # 경계 기반 출구점 (2026-06-10): trail 관측 bbox 경계에 닿은
            # 마지막 점을 출구로. 마지막 1~2점 OCR 노이즈((4,7)류)에 강건 —
            # 2점 델타/global 계산을 최우선 override.
            b_coord, b_dir, b_idx = self._boundary_exit(old_trail)
            if b_dir is not None:
                if self.log is not None and (b_dir != self._exit_dir
                                             or b_coord != self._exit_coord):
                    self.log.info(
                        f"[EXIT-BOUNDARY] override dir "
                        f"{self._exit_dir!r}→{b_dir!r} "
                        f"coord {self._exit_coord}→{b_coord}"
                    )
                self._exit_dir = b_dir
                self._exit_coord = b_coord
                # EXIT-TRIM (2026-06-10): 경계점 이후 점들은 맵명 OCR 지연 창
                # (~1.5s)에 들어온 새 맵 좌표가 옛 맵명으로 push된 오염.
                # 안 자르면 B1 exit_dash가 가짜 trail 끝점을 추종
                # (healer-37 (7) 사고: (7,1) 뒤 (6,1),(3,6),(4,6) 오염 →
                # 힐러가 (4,6) 추종, 포탈 x=7 못 가고 28s 정체).
                # 경계점==출구이므로 그 뒤를 잘라 trail 끝점==출구좌표 보장.
                # old_trail은 _map_trail의 deque 그 자체 → in-place 절단으로
                # 다음 랩 재방문 시에도 깨끗한 trail 유지.
                n_trim = len(old_trail) - 1 - b_idx
                if n_trim > 0:
                    for _ in range(n_trim):
                        old_trail.pop()
                    if self.log is not None:
                        self.log.info(
                            f"[EXIT-TRIM] map={self._exit_map!r} "
                            f"경계점 {b_coord} 이후 {n_trim}점 절단"
                        )
            # 포탈 DB: ① 이번 관측 기록(경계 정제값) ② 과거 누적(중앙값) 적용.
            # 과거 데이터가 있으면 단발 노이즈/UDP 누락과 무관하게 정확한
            # 포탈 좌표/방향으로 직행 (데이터 쌓일수록 정확).
            self._portal_record(self._exit_map, s.map_name,
                                self._exit_coord, self._exit_dir)
            p_coord, p_dir = self.portal_lookup(self._exit_map, s.map_name)
            if p_coord is not None:
                if self.log is not None and (p_coord != self._exit_coord
                                             or (p_dir != self._exit_dir
                                                 and p_dir != "-")):
                    self.log.info(
                        f"[PORTAL-DB] {self._exit_map!r}→{s.map_name!r} "
                        f"coord={p_coord} dir={p_dir!r} "
                        f"(이번 관측 coord={self._exit_coord} "
                        f"dir={self._exit_dir!r})"
                    )
                self._exit_coord = p_coord
                if p_dir in ("L", "R", "U", "D"):
                    self._exit_dir = p_dir
            # 선비족 z(층) 전환 고정 출구 방향 — 모든 추정의 최종 override
            # (사용자 2026-06-15: 굴/지역 무관 무조건 이 구조). 추정 오판
            # (EXIT-BOUNDARY R→U 등)을 원천 차단. coord는 기존값 유지.
            # 본성입구 라인(맵명 고정방향) 우선 → 없으면 선비족 z전환 규칙.
            sdir = self._line_exit_dir(self._exit_map)
            if sdir is None:
                sdir = self._sunbi_exit_dir(self._exit_map, s.map_name)
            if sdir is not None:
                if self.log is not None and sdir != self._exit_dir:
                    self.log.info(
                        f"[SUNBI-EXIT] {self._exit_map!r}→{s.map_name!r} "
                        f"dir {self._exit_dir!r}→{sdir!r} (고정규칙)")
                self._exit_dir = sdir
                # 2026-06-16: z전환 확정 방향 → EXIT-FALLBACK 이 반대로 뒤집지
                # 못하게 잠금. (7)→로비 U 확정인데 5s 정체 시 반대 D 로 뒤집어
                # 아래로 처박던 사고(173446 [EXIT-FALLBACK] U→D). 직교(출구 x
                # 정렬)만 허용하고 반대(규칙 위반)는 금지한다.
                self._exit_dir_sunbi_locked = True
            else:
                self._exit_dir_sunbi_locked = False
            # EXIT-FALLBACK 리셋 — 새 전환 시작.
            self._exit_fallback_n = 0
            self._healer_coord_progress_ts = now
            self._healer_coord_progress_base = None
            # 태그 전이 감지 = 격수 맵 전환 확정 → exit_dir 강제 홀드 창 개시.
            # 2026-04-22: pend_delay 지연 적용 — 격수 포탈 왕복 방지.
            self._force_exit_start = now + self._force_exit_pend_sec
            self._force_exit_until = self._force_exit_start + self._force_exit_sec
            self._transition_start = now
            self._transition_phase = FsmState.ENTER_PORTAL
            # C안 보조: 역방향 탐지용 prev_last_map 갱신 + mapchg 시각 기록.
            self._prev_last_map = self._last_map
            self._last_mapchg_ts = now
            self._last_map = s.map_name
            self._coord_hist.clear()
            # B안 보조: 새 맵 trail 리셋 + fresh guard 설정 (2초 유효).
            # 이 구간 내 첫 push가 exit_coord와 ≤3이면 오염 판정으로 거부.
            self._fresh_map_guard[s.map_name] = (self._exit_coord, now + 2.0)
            if self.log is not None:
                self.log.debug(
                    f"[CTRL-MAPCHG] {self._exit_map!r}→{s.map_name!r} "
                    f"exit_coord={self._exit_coord} "
                    f"exit_dir={self._exit_dir!r} "
                    f"old_trail_len={len(old_trail) if old_trail else 0} "
                    f"old_trail_tail={list(old_trail or [])[-10:]} "
                    f"fresh_guard_until={now+2.0:.3f}"
                )
            self._map_trail[s.map_name] = deque(maxlen=2000)
            self._map_progress[s.map_name] = 0
            self._push_count[s.map_name] = 0
            # J안: 새 맵 진입 — 이전 체류 시 마지막 coord 기록 리셋. 복귀 시 첫
            # coord가 portal 반대쪽이라 점프로 오판되는 걸 방지.
            self._atk_last_valid_coord_by_map.pop(s.map_name, None)
            # 주의: 이전 버전에서 여기서 현재 좌표를 강제 push했는데 제거.
            # 그 coord는 오염일 수 있으므로 fresh guard를 통과한 다음 push부터 허용.
        elif s.map_name and not self._last_map:
            self._last_map = s.map_name
            self._last_mapchg_ts = now

        # 맵 전환 3단계 진행
        if self._transition_phase is not None:
            elapsed = now - self._transition_start
            phase = self._transition_phase
            # ENTER_PORTAL → LOADING 전환: 힐러 맵이 바뀌거나 portal_sec 경과
            if phase == FsmState.ENTER_PORTAL:
                if self._healer_map == self._last_map or elapsed >= self.portal_sec:
                    self._transition_phase = FsmState.LOADING
                    self._transition_start = now
                    self._state = FsmState.LOADING
                    return self._state
                self._state = FsmState.ENTER_PORTAL
                return self._state
            # LOADING → NEW_MAP: 힐러 맵이 격수 맵과 일치하면 종료 카운트
            if phase == FsmState.LOADING:
                if (self._healer_map == self._last_map or
                        elapsed >= self.loading_sec):
                    self._transition_phase = FsmState.NEW_MAP
                    self._transition_start = now
                    self._state = FsmState.NEW_MAP
                    return self._state
                self._state = FsmState.LOADING
                return self._state
            if phase == FsmState.NEW_MAP:
                if elapsed >= self.new_map_sec:
                    self._transition_phase = None
                else:
                    self._state = FsmState.NEW_MAP
                    return self._state

        # 빨탭 히스테리시스 (흰탭→빨탭 = red_gain_frames 연속 관측 필요)
        if s.red_tab:
            self._last_red_seen = now
            self._red_confirm_count += 1
            if self._red_confirm_count >= self.red_gain_frames:
                self._red_confirmed = True
        else:
            self._red_confirm_count = 0
            # 빨탭→흰탭은 red_lost_sec 경과 후 확정 해제
            if (now - self._last_red_seen) >= self.red_lost_sec:
                self._red_confirmed = False

        # 우선순위: DISCONNECTED → COMBAT/FOLLOW/STUCK → DEAD_RECKON
        if self._last_udp_time > 0 and (now - self._last_udp_time) > self.disconnect_sec:
            self._state = FsmState.DISCONNECTED
            return self._state
        coord_stale = (now - self._last_valid_coord_time) > self.dead_reckon_sec
        if self._red_confirmed:
            self._state = FsmState.COMBAT
        elif coord_stale:
            self._state = FsmState.DEAD_RECKON
        elif (now - self._last_coord_change) > self.stuck_sec:
            self._state = FsmState.STUCK
        else:
            self._state = FsmState.FOLLOW
        return self._state

    def _eval_state_only(self, now: float) -> FsmState:
        """맵/좌표 갱신 없이 현재 저장 상태로 FSM만 재평가.

        디바운스 중(역방향 flicker 의심) 호출. 기존 _last_map/_map_trail/
        _coord_hist 등은 건드리지 않고 DISCONNECTED/COMBAT/FOLLOW 등만 갱신.
        """
        # 빨탭 히스테리시스는 update 본류에서만 갱신하므로 여기선 기존 값 재사용.
        if (self._last_udp_time > 0
                and (now - self._last_udp_time) > self.disconnect_sec):
            self._state = FsmState.DISCONNECTED
            return self._state
        coord_stale = (now - self._last_valid_coord_time) > self.dead_reckon_sec
        if self._red_confirmed:
            self._state = FsmState.COMBAT
        elif coord_stale:
            self._state = FsmState.DEAD_RECKON
        elif (now - self._last_coord_change) > self.stuck_sec:
            self._state = FsmState.STUCK
        else:
            self._state = FsmState.FOLLOW
        return self._state

    def direction(self) -> str:
        """최근 좌표 변화로 격수 이동 방향 추정. 'L/R/U/D/-'."""
        return self._hist_direction()

    def _hist_direction(self) -> str:
        """현재 히스토리 기반 방향."""
        if len(self._coord_hist) < 2:
            return "-"
        _, (x0, y0) = self._coord_hist[0]
        _, (x1, y1) = self._coord_hist[-1]
        dx, dy = x1 - x0, y1 - y0
        if abs(dx) < 1 and abs(dy) < 1:
            return "-"
        if abs(dx) >= abs(dy):
            return "R" if dx > 0 else "L"
        return "D" if dy > 0 else "U"

    def exit_dir(self) -> str:
        """격수가 이전 맵에서 이탈할 때의 진행 방향."""
        return self._exit_dir

    def exit_coord(self) -> Optional[tuple]:
        return self._exit_coord

    def exit_map(self) -> str:
        return self._exit_map

    def force_exit_active(self, now: Optional[float] = None) -> bool:
        """전역 trail 태그 전이 감지 후 exit_dir 강제 홀드 창 중인지.
        pend_delay 기간(start 전)에는 False — 격수 포탈 왕복 시 pending 덮어쓰기.
        """
        if now is None:
            now = time.time()
        return self._force_exit_start <= now < self._force_exit_until

    def force_exit_remaining(self, now: Optional[float] = None) -> float:
        if now is None:
            now = time.time()
        return max(0.0, self._force_exit_until - now)

    def post_tab_grace_active(self, now: Optional[float] = None) -> bool:
        """본인 빨탭 후 맵전환 직후 grace 창(흰탭 재arm 억제) 중인지 (수정A)."""
        if now is None:
            now = time.time()
        return now < self._post_tab_grace_until

    def cancel_force_exit(self) -> None:
        """force_exit 홀드 즉시 해제 (2026-06-10).

        도사가 격수와 같은 맵에 도달(map_neq=False)하면 exit_dir 강제 밀기의
        목적(포탈 통과)이 달성됨 → 해제. 안 하면 force_exit 잔여 시간 동안
        도사가 격수를 지나쳐 다음 포탈로 또 넘어가버림(혼자 포탈/뒤로 복귀 사고).
        """
        self._force_exit_until = 0.0
        self._force_exit_start = 0.0

    def last_seen_in(self, map_name: str):
        """해당 맵에서 격수가 마지막으로 관측된 (coord, dir).

        관측 기록 없으면 None. 힐러가 다른 맵에 있을 때 이 정보로 이동 결정.
        """
        if not map_name:
            return None
        c = self._map_last_coord.get(map_name)
        d = self._map_last_dir.get(map_name, "-")
        if c is None:
            return None
        return (c, d)

    def trail_for(self, map_name: str):
        """해당 맵에서 격수가 지나간 경로 전체(순서 있는 좌표 리스트).

        빈 트레일이면 빈 리스트 반환. 도사가 이 경로를 순서대로 밟아간다.
        """
        if not map_name:
            return []
        t = self._map_trail.get(map_name)
        if not t:
            return []
        return list(t)

    def next_waypoint(self, map_name: str, healer_coord, tol: int = 2,
                      exit_dash: bool = False, stop_before: int = 0):
        """도사가 trail을 **순서대로** 따라가도록 다음 타겟 반환.

        stop_before>0 (슬롯 추종): 끝(격수)에서 그만큼 앞 칸을 상한으로 삼아
        격수 뒤 stop_before 칸에 정지(exit_dir 안 밈). 격수 이동하면 trail 이
        늘어 상한도 밀착 따라감. 0=기존(끝까지 + 끝 도달 시 exit_dir 밀기).

        단조 진행 정책: `_map_progress[map_name]` idx는 증가만 함.
        도사가 trail[idx] 도달(tol 내)하면 idx += 1. 절대 뒤로 안 감.
        이미 지나온 wp는 무시. 끝점 도달하면 None 반환 → exit_dir로 밀기.

        Args:
            map_name: 힐러 현재 맵.
            healer_coord: (x, y) 튜플. None 허용.
            tol: "도달"로 간주할 맨해튼 거리.
            exit_dash: True면 "exit 최단" 모드. 맵 전환 중(map_neq=True)에
                격수 전투 zigzag trail을 스킵하고 exit에 가장 가까운 직선
                도달 가능 waypoint로 직행. 일반 사냥 중엔 False (trail 순서
                대로 밟기 — feedback_route_trail.md 정책 유지).

        Returns:
            (next_coord, last_dir)  — 다음 타겟과 그 지점에서의 격수 방향.
            None                    — 트레일 없거나 힐러가 이미 끝점 도달.
        """
        trail = self._map_trail.get(map_name)
        if not trail:
            self._wp_last_diag = {"map": map_name, "reason": "no_trail"}
            return None
        cur = self._map_progress.get(map_name, 0)
        # 슬롯 추종: 끝(격수)에서 stop_before 칸 앞이 내 상한. 아래 snap/진행/
        # 도달 로직이 전부 이 last_idx 기준으로 돌아 격수 뒤 그 칸에 멈춘다.
        # stop_before=0 → 진짜 끝 → 기존 동작 완전 동일(회귀 0).
        last_idx = max(0, (len(trail) - 1) - int(stop_before))
        if cur > last_idx:
            cur = last_idx
        if healer_coord is None:
            self._wp_last_diag = {
                "map": map_name, "len": len(trail), "cur": cur,
                "wp": trail[cur], "h": None, "d": None,
                "tail": list(trail)[-5:], "reason": "h_none",
            }
            return (trail[cur], self._map_last_dir.get(map_name, "-"))
        hx, hy = healer_coord
        initial_cur = cur
        # I안 snap-forward: cur~last_idx 범위에서 힐러와 맨해튼이 snap_threshold
        # 이하인 "가장 가까운" idx를 찾아 cur을 그쪽으로 점프. 단조성 유지 (forward only).
        # 목적: OLD 맵에서 coord-follow(B3)로만 있다가 맵전환 순간 cur=0 상태로
        # 남은 경우, 힐러가 trail 시작점과 멀리 있으면 힐러 현재 위치에 가장 가까운
        # wp부터 추종 시작.
        # 2026-04-22: "가장 먼 idx" → "가장 가까운 idx" 정책 변경. 기존은 힐러가
        # 어떤 idx든 thr 이내면 trail_end로 점프 → B1:TRAIL 0회, 중간 wp 스킵,
        # feedback_route_trail.md "경로 그대로 밟기, 지름길 금지" 정책 위반
        # (힐러1.txt 17:29:07 cur=0→6/6 즉시 end_reached 재현).
        snap_thr = self._snap_forward_threshold
        best_snap_idx = cur
        best_snap_d = None
        if exit_dash:
            # 2026-04-23 exit_dash: exit(last_idx) 쪽에서 역순 스캔. 힐러가
            # 직선 도달 가능한 가장 exit-가까운 wp를 선택해 격수 zigzag 스킵.
            # 맵 전환 중에만 사용 — 포탈 통과 최단 경로.
            for i in range(last_idx, cur - 1, -1):
                wx, wy = trail[i]
                d = abs(wx - hx) + abs(wy - hy)
                if d <= snap_thr:
                    best_snap_idx = i
                    best_snap_d = d
                    break
            if best_snap_idx > cur and self.log is not None:
                self.log.debug(
                    f"[SNAP-EXIT] map={map_name!r} "
                    f"cur={cur}→{best_snap_idx}/{last_idx} "
                    f"h={healer_coord} d={best_snap_d} thr={snap_thr} "
                    f"(exit_dash zigzag skip)"
                )
            if best_snap_idx > cur:
                cur = best_snap_idx
        else:
            # 일반 모드: 가장 가까운 idx (기존 정책)
            for i in range(cur, last_idx + 1):
                wx, wy = trail[i]
                d = abs(wx - hx) + abs(wy - hy)
                if d <= snap_thr:
                    if (best_snap_d is None
                            or d < best_snap_d
                            or (d == best_snap_d and i > best_snap_idx)):
                        best_snap_idx = i
                        best_snap_d = d
            if best_snap_idx > cur:
                if self.log is not None:
                    self.log.debug(
                        f"[SNAP-FWD] map={map_name!r} "
                        f"cur={cur}→{best_snap_idx}/{last_idx} "
                        f"h={healer_coord} d={best_snap_d} thr={snap_thr}"
                    )
                cur = best_snap_idx
        # 기존 tol 기반 전진: trail[cur]에 도달했으면 다음으로.
        while cur < last_idx:
            wx, wy = trail[cur]
            if abs(wx - hx) + abs(wy - hy) <= tol:
                cur += 1
            else:
                break
        if cur != initial_cur and self.log is not None:
            self.log.debug(
                f"[PROGRESS] map={map_name!r} "
                f"cur={initial_cur}→{cur}/{last_idx} "
                f"h={healer_coord} reached_wps=[{initial_cur}:{cur}]"
            )
        self._map_progress[map_name] = cur
        wx, wy = trail[cur]
        d_to_wp = abs(wx - hx) + abs(wy - hy)
        base_diag = {
            "map": map_name, "len": len(trail), "cur": cur,
            "last_idx": last_idx, "wp": (wx, wy),
            "h": (hx, hy), "d": d_to_wp,
            "tail": list(trail)[-5:],
        }
        # 2026-04-22: cur이 last_idx 도달하면 d_to_wp 무관 end_reached (sticky).
        # 기존 'd_to_wp <= tol' 조건 때문에 h가 trail_end에서 tol 초과로 벗어나면
        # advancing으로 되돌아가 trail_end 방향 권장 → exit_dir 반대축일 때 U↔D/L↔R
        # 무한 진동 (힐러1.txt 17:08:06~ trail_end=(7,5) ed=U 재현).
        # cur 단조 증가 + snap-forward(thr=10) 지름길 방지 조합으로 "trail 순서대로
        # 밟기" 정책 유지됨. 끝 도달 후 exit_dir 밀기는 feedback_route_trail.md 정책.
        if cur >= last_idx:
            # 슬롯 추종(stop_before>0): 내 자리(격수 뒤 stop_before 칸) 도달 →
            # 그 자리 정지 target 반환(exit_dir 안 밈; 같은맵 줄서기). 격수 이동
            # 하면 last_idx 가 늘어 다시 advancing.
            if stop_before > 0:
                base_diag["reason"] = "slot_reached"
                self._wp_last_diag = base_diag
                return (trail[last_idx], self._map_last_dir.get(map_name, "-"))
            # exit_dash 최종 목표 (2026-06-10): trail 끝점 대신 **보정된
            # 출구좌표**(_exit_coord — EXIT-BOUNDARY/PORTAL-DB 반영값).
            # trail 끝점은 오염 가능(EXIT-TRIM이 못 자른 케이스: 경계 미발견),
            # _exit_coord는 보정 파이프라인 통과값이라 항상 우선.
            # (healer-37 (7) 사고: exit=(7,1) 보정됐는데 추종은 raw 끝점
            # (4,6) → 포탈 못 감. 보정값-추종 불일치 해소.)
            if (exit_dash and self._exit_coord is not None
                    and self._exit_map == map_name):
                ex, ey = self._exit_coord
                d_to_exit = abs(ex - hx) + abs(ey - hy)
                if d_to_exit > tol:
                    base_diag["wp"] = (ex, ey)
                    base_diag["d"] = d_to_exit
                    base_diag["reason"] = "exit_dash_portal"
                    self._wp_last_diag = base_diag
                    return ((ex, ey), self._exit_dir or "-")
                base_diag["reason"] = "end_reached"
                self._wp_last_diag = base_diag
                return None
            # exit_dash 중이고 아직 last_idx에 도달 못 했으면 wp 계속 반환.
            # (None 반환 시 caller가 exit_dir 밀기 fallback → 포탈 좌표 아닌
            # 방향키만 눌려 엉뚱한 곳으로 갈 수 있음)
            if exit_dash and d_to_wp > tol:
                base_diag["reason"] = "exit_dash_target"
                self._wp_last_diag = base_diag
                return (trail[cur], self._map_last_dir.get(map_name, "-"))
            base_diag["reason"] = "end_reached"
            self._wp_last_diag = base_diag
            return None
        base_diag["reason"] = "advancing"
        self._wp_last_diag = base_diag
        return (trail[cur], self._map_last_dir.get(map_name, "-"))

    def wp_diag(self):
        return self._wp_last_diag

    def trail_tail(self, map_name: str, n: int = 10):
        t = self._map_trail.get(map_name)
        if not t:
            return []
        return list(t)[-n:]

    def progress_of(self, map_name: str):
        return self._map_progress.get(map_name, 0)

    def is_paused(self, now: Optional[float] = None) -> bool:
        """I안 — 격수 맵전환 직후 pause 창 여부. 힐러 키 주입 차단용.

        MAP-SYNC 확장: 맵 OCR이 좌표 OCR보다 느린 구간(h_map≠a_map)에서도
        pause 유지. 트리거: 격수 map_seq edge 또는 힐러 좌표 점프.
        해제: h_map==a_map 확정 또는 타임아웃(1.5초).

        TAB-CONFIRM 확장: 맵 전환 후 빨탭 확정 전엔 계속 pause.
        Post-confirm stabilize (#4): done_ok 직후 N ms 이동 차단.
        """
        if now is None:
            now = time.time()
        return (now < self._pause_until
                or now < self._map_sync_until
                or self._tab.is_pausing(now))

    # ----- TAB-CONFIRM 위임 (실제 로직은 fsm/tab_confirm.py) ----------

    def tab_confirm_tick(
        self,
        now: float,
        red_raw: bool,
        white_raw: bool,
        h_coord: Optional[tuple] = None,
    ) -> str:
        """매 프레임 호출. TabConfirm.tick 위임."""
        return self._tab.tick(now, red_raw, white_raw, h_coord)

    def arm_tab_confirm(self, now: float, map_neq: bool = False) -> None:
        """흰탭 arm 조건 확정 시 호출. TabConfirm.arm 위임."""
        self._tab.arm(now, map_neq)

    def note_tab_fg_mismatch(self, now: float) -> bool:
        """(#1) fg_match=False 재큐잉. TabConfirm.note_fg_mismatch 위임."""
        return self._tab.note_fg_mismatch(now)

    def note_tab_done_ok(self, now: float) -> None:
        """(#4) done_ok 직후 post-confirm pause. TabConfirm.note_done_ok 위임.

        수정B(2026-06-11): 빨탭 확정 직후 post_tab_grace 설정 → 빨라진 YOLO가
        전환 중 찰나 흰탭 1프레임 잡아 재arm(또 Tab)→방금 만든 빨탭 깨는 현상
        차단(사용자 진단: "Tab 눌러 빨탭 됐는데 흰탭 인식해 Tab 또 누름").
        grace 동안 흰탭 arm 억제, 진짜 지속 흰탭이면 grace 후 정상 재arm.
        """
        self._tab.note_done_ok(now)
        self._post_tab_grace_until = now + self._post_tab_grace_sec
        if self.log is not None:
            self.log.info(
                f"[POST-TAB-GRACE] 빨탭 확정 → {self._post_tab_grace_sec:.1f}s "
                f"흰탭 재arm 억제 (찰나 플리커 재Tab 방지)"
            )

    # ----- 하위 호환 property (외부에서 _tab_confirm_* 직접 접근 허용) -----
    @property
    def _tab_confirm_active(self) -> bool:
        return self._tab.active

    @property
    def _tab_confirm_substate(self) -> str:
        return self._tab.substate

    @property
    def _tab_confirm_started(self) -> float:
        return self._tab.started

    @property
    def _tab_confirm_required(self) -> int:
        return self._tab.required

    @property
    def _tab_confirm_map_neq_at_arm(self) -> bool:
        return self._tab.map_neq_at_arm

    @property
    def _tab_retry_count(self) -> int:
        return self._tab.retry_count

    @property
    def _tab_retry_max(self) -> int:
        return self._tab.retry_max

    @property
    def _tab_fg_retry_count(self) -> int:
        return self._tab.fg_retry_count

    @property
    def _tab_fg_retry_max(self) -> int:
        return self._tab.fg_retry_max

    @property
    def _tab_post_confirm_duration(self) -> float:
        return self._tab.post_confirm_duration

    def pause_remaining(self, now: Optional[float] = None) -> float:
        if now is None:
            now = time.time()
        return max(0.0, max(self._pause_until, self._map_sync_until) - now)
