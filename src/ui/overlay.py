"""격수 PC 인게임 오버레이.

구성:
  GameOverlay        : 힐러별 쿨 진행 상황 + 시간당 경험치 표시 (왼쪽).
                       남은 쿨이 있는 스킬만 줄로 추가.
  SkillAlertOverlay  : 스킬 시전 3초전 1회 알림 전용. 3초간 표시 후 자동 소멸.
                       힐러별 별도 창 X. 한 창 안에서 줄 단위로 쌓임.
                       맵 영역 아래 + 게임 영역 수평 중심 배치.

공통: 해상도(게임 영역 크기)에 비례해 폰트·패딩·라인 높이 스케일.
모두 프레임리스 + 반투명 + 항상위 + 마우스 입력 통과.
"""
from __future__ import annotations

import ctypes
import time
from typing import Optional, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets


_BASELINE_H = 720.0  # 스케일 기준 해상도 높이.

# 오버레이 종류별 액센트 색 (헤더 밴드 / 좌측 바 / 테두리).
# 색으로 오버레이를 즉시 구분 — 가독성·식별성 향상 (2026-06-12).
ACCENT_CD = (95, 155, 240)      # 힐러 쿨 상태 — 블루
ACCENT_HUNT = (110, 205, 150)   # 사냥 분석 — 그린
ACCENT_NAV = (255, 195, 95)     # 선비족 네비 — 앰버
ACCENT_HELPER = (185, 150, 250)  # 사냥 도우미 — 퍼플
ACCENT_ALERT = (255, 210, 90)   # 스킬 알림 — 옐로


def _fmt_cd(sec: int) -> str:
    if sec is None or sec < 0:
        return "-"
    if sec <= 0:
        return "ready"
    if sec >= 60:
        m = sec // 60
        s = sec % 60
        return f"{m}m{s:02d}s"
    return f"{sec}s"


def _fmt_cd_kr(sec: int) -> str:
    """쿨 표기 (2026-06-12 사용자 요청): 준비=한글 '준비됨', 쿨중=잔여초.

    예) '준비됨' / '30s' / '2m14s'. 음수(미측정/미해당)는 호출측에서 숨김.
    """
    if sec is None or sec <= 0:
        return "준비됨"
    if sec >= 60:
        m = sec // 60
        s = sec % 60
        return f"{m}m{s:02d}s"
    return f"{sec}s"


def _fmt_xph(n: int) -> str:
    if not n or n <= 0:
        return "-"
    return f"{n / 100_000_000.0:.1f}억/h"


def _fmt_xph_compact(n: int) -> str:
    if not n or n <= 0:
        return "-"
    return f"{n / 100_000_000.0:.2f}억"


def _fmt_xp(n: int) -> str:
    if n is None or n <= 0:
        return "0"
    if n >= 100_000_000:
        return f"{n / 100_000_000.0:.2f}억"
    if n >= 10_000:
        return f"{n / 10_000.0:.1f}만"
    return str(int(n))


def _fmt_dur(sec: int) -> str:
    try:
        s = max(0, int(sec))
    except Exception:
        s = 0
    if s < 60:
        return f"{s}s"
    m, r = divmod(s, 60)
    if m < 60:
        return f"{m}m{r:02d}s"
    h, m2 = divmod(m, 60)
    return f"{h}h{m2:02d}m"


class _ScaledOverlay(QtWidgets.QWidget):
    """해상도 스케일 + 수동 드래그 위치 편집 공통 부모.

    편집 모드 OFF(기본) — 입력 통과(WindowTransparentForInput), 드래그 불가.
    편집 모드 ON              — 입력 받음, 좌클릭 드래그로 위치 이동,
                               release 시 `position_changed(x, y)` 방출 +
                               노란 테두리로 편집 상태 시각화.

    스케일 소스: `set_anchor_regions(game, map)` 호출 시 game.h/720.
    game_rect 없으면 scale=1.0. 수동 위치(`set_manual_pos`)가 있으면
    자동 앵커보다 우선.
    """

    position_changed = QtCore.pyqtSignal(int, int)

    def __init__(self):
        super().__init__()
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self._scale: float = 1.0
        # 사용자 수동 크기 배율 (해상도 scale 과 곱해짐). 0.5~2.5.
        # 네비게이션 오버레이 크기조절(2026-06-12) — 모든 오버레이 공통 지원.
        self._user_scale: float = 1.0
        self._game_rect: Optional[Tuple[int, int, int, int]] = None
        self._map_rect: Optional[Tuple[int, int, int, int]] = None
        self._target_hwnd: Optional[int] = None
        self._manual_pos: Optional[Tuple[int, int]] = None
        self._edit_mode: bool = False
        self._drag_offset: Optional[QtCore.QPoint] = None
        # paint-기반 투명도 (setWindowOpacity 대신).
        # Qt + WA_TranslucentBackground + setWindowOpacity 조합은 Windows
        # 계층창(Layered Window) 모드 충돌로 창 전체가 사라지는 사례가
        # 보고돼 있어 안전하게 QPainter alpha 스케일링으로 구현.
        self._opacity_mul: float = 1.0
        # 초기 플래그 = 입력 통과.
        self._apply_flags()

    def set_opacity(self, v: float) -> None:
        """0.0 ~ 1.0 범위 투명도 멀티플라이어 적용. 실제로는 paintEvent 내부
        QColor alpha 값에 이 값을 곱해 렌더링. setWindowOpacity 를 쓰지 않아
        WA_TranslucentBackground 와 충돌해서 창이 사라지는 현상이 없다.
        0.0 = 완전 투명(보이지 않음).
        """
        try:
            f = float(v)
        except Exception:
            f = 1.0
        if f < 0.0:
            f = 0.0
        elif f > 1.0:
            f = 1.0
        # 안전장치: setWindowOpacity 는 반드시 1.0 고정 (과거 저장값 잔재 제거).
        try:
            self.setWindowOpacity(1.0)
        except Exception:
            pass
        self._opacity_mul = f
        self.update()

    def _a(self, alpha: int) -> int:
        """기본 alpha 값에 opacity_mul 을 곱해 0~255 clamp."""
        try:
            return max(0, min(255, int(alpha * self._opacity_mul)))
        except Exception:
            return int(alpha)

    def _apply_flags(self) -> None:
        # 새 정책: StaysOnTop 을 항상 켠다. 단 "다른 앱 위에 안 뜬다" 와
        # "msw 최소화 시 같이 숨는다" 는 main_window 측 visibility 폴링으로
        # show/hide 를 제어해 구현한다. z-order 싸움(owner 대 foreground)에서
        # overlay 가 본창 뒤로 깔리던 문제는 이 조합으로만 안정적으로 해결됨.
        flags = (QtCore.Qt.FramelessWindowHint
                 | QtCore.Qt.Tool
                 | QtCore.Qt.WindowStaysOnTopHint)
        if not self._edit_mode:
            flags |= QtCore.Qt.WindowTransparentForInput
        vis = self.isVisible()
        pos = self.pos()
        self.setWindowFlags(flags)
        if vis:
            self.show()
            self.move(pos)
            self._enforce_owner_link()

    def set_edit_mode(self, on: bool) -> None:
        if bool(on) == self._edit_mode:
            return
        self._edit_mode = bool(on)
        self._apply_flags()
        # 서브클래스 레이아웃/앵커 재계산 (placeholder 크기 등).
        self._on_scale_changed()
        self.update()

    def set_manual_pos(self, x: Optional[int], y: Optional[int]) -> None:
        """수동 위치 저장. None/None 이면 해제. bound 있으면 clamp."""
        if x is None or y is None:
            self._manual_pos = None
        else:
            cx, cy = self._clamp_to_bound(int(x), int(y))
            self._manual_pos = (cx, cy)
        self._on_scale_changed()

    def set_anchor_regions(self,
                           game_rect: Optional[Tuple[int, int, int, int]],
                           map_rect: Optional[Tuple[int, int, int, int]]
                           ) -> None:
        self._game_rect = tuple(game_rect) if game_rect else None
        self._map_rect = tuple(map_rect) if map_rect else None
        if self._game_rect:
            self._scale = max(0.5, min(3.0,
                                       float(self._game_rect[3]) / _BASELINE_H))
        else:
            self._scale = 1.0
        self._on_scale_changed()

    def attach_to_hwnd(self, hwnd) -> None:
        """msw 창 HWND에 바인딩. 이 창 client rect 밖으로 드래그 못 나감.

        또한 GWLP_HWNDPARENT로 owner 관계를 맺어 msw 최소화/비포커스 시
        오버레이도 함께 숨거나 뒤로 보내짐 (다른 stay-on-top 창 위에 튀어나가지 않음).
        """
        try:
            self._target_hwnd = int(hwnd) if hwnd else None
        except Exception:
            self._target_hwnd = None
        # flags에 StaysOnTop 제거 의존성이 있으므로 먼저 재적용.
        self._apply_flags()
        self._enforce_owner_link()
        # 창이 이동/리사이즈되면 오버레이도 안쪽으로 재clamp (500ms 주기).
        if self._target_hwnd:
            if not hasattr(self, "_bound_timer") or self._bound_timer is None:
                self._bound_timer = QtCore.QTimer(self)
                self._bound_timer.setInterval(500)
                self._bound_timer.timeout.connect(self._enforce_bounds)
                self._bound_timer.start()
        self._on_scale_changed()

    def showEvent(self, ev) -> None:  # type: ignore[override]
        super().showEvent(ev)
        # show 시점마다 Qt가 HWND 재생성할 수 있어 owner 재설정.
        try:
            self._enforce_owner_link()
        except Exception:
            pass

    def _enforce_owner_link(self) -> None:
        """내 HWND의 GWLP_HWNDPARENT를 _target_hwnd로 설정.

        Patch 2.24 (2026-04-20): owner link 비활성화 — 실증 완료.
        도사 모드에서 msw HWND 와 Python 오버레이 창을 GWLP_HWNDPARENT 로
        묶으면 msw 내에서 Shift+X 조합 입력이 씹히는 증상 발생. 격수 모드에선
        동일 경로라도 증상 없음 (추정: 오버레이 show 타이밍/HWND 재생성 순서
        차이로 실제 링크가 유효하게 걸리는 건 도사 기동 플로우만). 링크 스킵
        후 Shift+X 정상 복귀 확인. z-order 는 StaysOnTop + visibility 폴링이
        커버.
        """
        return
        if not self._target_hwnd:
            return
        try:
            from ..capture.screen import user32 as _u32
            my_hwnd = int(self.winId())
            if not my_hwnd:
                return
            GWLP_HWNDPARENT = -8
            # 64-bit 안전 — Ptr 버전 우선.
            if hasattr(_u32, "SetWindowLongPtrW"):
                _u32.SetWindowLongPtrW.restype = ctypes.c_void_p
                _u32.SetWindowLongPtrW.argtypes = [
                    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p
                ]
                _u32.SetWindowLongPtrW(
                    ctypes.c_void_p(my_hwnd),
                    GWLP_HWNDPARENT,
                    ctypes.c_void_p(int(self._target_hwnd)),
                )
            else:
                _u32.SetWindowLongW(
                    my_hwnd, GWLP_HWNDPARENT, int(self._target_hwnd)
                )
        except Exception:
            pass

    def _get_bound_rect(self) -> Optional[Tuple[int, int, int, int]]:
        """바인딩된 창의 client rect를 스크린 절대좌표로 반환."""
        hwnd = self._target_hwnd
        if not hwnd:
            return None
        try:
            from ..capture.screen import user32 as _u32
            from ctypes import wintypes, byref
            if not _u32.IsWindow(int(hwnd)):
                return None
            pt = wintypes.POINT(0, 0)
            _u32.ClientToScreen(int(hwnd), byref(pt))
            rc = wintypes.RECT()
            _u32.GetClientRect(int(hwnd), byref(rc))
            w = int(rc.right - rc.left)
            h = int(rc.bottom - rc.top)
            if w <= 0 or h <= 0:
                return None
            return (int(pt.x), int(pt.y), w, h)
        except Exception:
            return None

    def _clamp_to_bound(self, x: int, y: int) -> Tuple[int, int]:
        """(x,y)를 바인딩 창 client rect 안으로 clamp. 바인딩 없으면 그대로."""
        b = self._get_bound_rect()
        if not b:
            return (int(x), int(y))
        bx, by, bw, bh = b
        # 오버레이가 창 보다 크면 좌상단 고정.
        max_x = bx + max(0, bw - self.width())
        max_y = by + max(0, bh - self.height())
        cx = max(bx, min(int(x), max_x))
        cy = max(by, min(int(y), max_y))
        return (cx, cy)

    def _enforce_bounds(self) -> None:
        """주기 호출: 창이 움직여 오버레이가 밖에 나가 있으면 안쪽으로 끌어들임."""
        if not self._target_hwnd:
            return
        p = self.pos()
        cx, cy = self._clamp_to_bound(p.x(), p.y())
        if (cx, cy) != (p.x(), p.y()):
            self.move(cx, cy)
            # 수동 위치 저장값도 동기화 (다음 복원 시 안전).
            if self._manual_pos is not None:
                self._manual_pos = (cx, cy)

    def _on_scale_changed(self) -> None:
        """서브클래스 오버라이드."""
        pass

    def set_user_scale(self, factor: float) -> None:
        """사용자 수동 크기 배율 (0.5~2.5). 해상도 scale 과 곱해져 적용.

        호출 시 서브클래스 `_on_scale_changed`(레이아웃/앵커 재계산)까지 연쇄.
        """
        try:
            v = float(factor)
        except Exception:
            v = 1.0
        v = max(0.5, min(2.5, v))
        if abs(v - self._user_scale) < 1e-3:
            return
        self._user_scale = v
        self._on_scale_changed()
        self.update()

    def _eff_scale(self) -> float:
        return self._scale * self._user_scale

    def _px(self, base: int) -> int:
        return max(1, int(round(base * self._eff_scale())))

    def _font(self, pt_base: int, bold: bool = True) -> QtGui.QFont:
        f = QtGui.QFont(
            "Malgun Gothic",
            max(7, int(round(pt_base * self._eff_scale()))))
        f.setBold(bold)
        return f

    # ── 공통 글래스 위젯 스타일 (폰 위젯풍, 2026-06-12) ────────────────────
    # 설계: 패널 배경은 투명도(_a) 영향 — 0%면 완전히 사라짐. 단 모든 본문
    # 텍스트는 `_text`/`_text_rect` 의 검은 halo(외곽선)로 그려 배경이 없어도
    # 어떤 게임 화면 위에서든 또렷하게 읽힘(폰 위젯 느낌의 핵심).
    _PANEL_ACCENT = ACCENT_CD

    def _draw_panel_bg(self, qp: QtGui.QPainter, accent=None) -> None:
        """글래스 위젯 패널: 수직 그라데이션 본체 + 좌상단 액센트 글로우 +
        상단 sheen + 좌측 액센트 글로우 바 + 얇은 라운드 테두리.
        전부 self._a() 투명도 반영 → 0%면 패널이 사라지고 halo 텍스트만 남음.
        """
        ar, ag, ab = accent or self._PANEL_ACCENT
        r = self.rect()
        radius = self._px(14)
        rf = QtCore.QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QtGui.QPainterPath()
        path.addRoundedRect(rf, float(radius), float(radius))
        qp.save()
        qp.setClipPath(path)
        # 본체 그라데이션 (상단 살짝 밝게 → 유리 질감).
        g = QtGui.QLinearGradient(0.0, float(r.top()), 0.0, float(r.bottom()))
        g.setColorAt(0.0, QtGui.QColor(42, 47, 58, self._a(206)))
        g.setColorAt(0.14, QtGui.QColor(25, 28, 37, self._a(206)))
        g.setColorAt(1.0, QtGui.QColor(12, 14, 19, self._a(216)))
        qp.fillRect(r, QtGui.QBrush(g))
        # 좌상단 액센트 글로우 (은은한 컬러 번짐).
        rad = float(self._px(150))
        rg = QtGui.QRadialGradient(float(r.left()), float(r.top()), rad)
        rg.setColorAt(0.0, QtGui.QColor(ar, ag, ab, self._a(58)))
        rg.setColorAt(1.0, QtGui.QColor(ar, ag, ab, 0))
        qp.fillRect(r, QtGui.QBrush(rg))
        # 상단 sheen 하이라이트.
        qp.setPen(QtGui.QPen(
            QtGui.QColor(255, 255, 255, self._a(36)), self._px(1)))
        qp.drawLine(r.left() + radius, r.top() + self._px(1),
                    r.right() - radius, r.top() + self._px(1))
        # 좌측 액센트 글로우 바.
        lg = QtGui.QLinearGradient(
            float(r.left()), 0.0, float(r.left() + self._px(5)), 0.0)
        lg.setColorAt(0.0, QtGui.QColor(ar, ag, ab, self._a(240)))
        lg.setColorAt(1.0, QtGui.QColor(ar, ag, ab, 0))
        qp.setPen(QtCore.Qt.NoPen)
        qp.fillRect(QtCore.QRect(r.left(), r.top(), self._px(5), r.height()),
                    QtGui.QBrush(lg))
        qp.restore()
        # 얇은 액센트 테두리.
        qp.setPen(QtGui.QPen(
            QtGui.QColor(ar, ag, ab, self._a(140)), self._px(1)))
        qp.setBrush(QtCore.Qt.NoBrush)
        qp.drawRoundedRect(rf, float(radius), float(radius))

    # 8방향 halo 오프셋 (배경 무관 가독성).
    _HALO_OFF = ((-1, -1), (0, -1), (1, -1), (-1, 0),
                 (1, 0), (-1, 1), (0, 1), (1, 1))

    def _text(self, qp: QtGui.QPainter, x: int, y: int, s: str,
              color, halo: bool = True, halo_alpha: int = 240,
              shadow: bool = True, outline: int = 1) -> None:
        """입체 HUD 텍스트: 드롭섀도(깊이) + 검은 외곽선(가독) + 본문.
        투명도와 무관하게 어떤 배경에도 또렷. outline=링 두께(px단위 반복)."""
        if shadow:
            sh = max(1, self._px(2))
            qp.setPen(QtGui.QColor(0, 0, 0, 150))
            qp.drawText(int(x + sh), int(y + sh), s)
        if halo:
            o = max(1, self._px(1))
            qp.setPen(QtGui.QColor(0, 0, 0, halo_alpha))
            for k in range(1, max(1, int(outline)) + 1):
                for dx, dy in self._HALO_OFF:
                    qp.drawText(int(x + dx * o * k), int(y + dy * o * k), s)
        qp.setPen(color)
        qp.drawText(int(x), int(y), s)

    def _text_rect(self, qp: QtGui.QPainter, rect, flags, s: str,
                   color, halo: bool = True, halo_alpha: int = 240,
                   shadow: bool = True, outline: int = 1) -> None:
        """입체 HUD 텍스트 (정렬 rect 버전)."""
        if shadow:
            sh = max(1, self._px(2))
            qp.setPen(QtGui.QColor(0, 0, 0, 150))
            qp.drawText(rect.translated(sh, sh), flags, s)
        if halo:
            o = max(1, self._px(1))
            qp.setPen(QtGui.QColor(0, 0, 0, halo_alpha))
            for k in range(1, max(1, int(outline)) + 1):
                for dx, dy in self._HALO_OFF:
                    qp.drawText(rect.translated(int(dx * o * k),
                                                int(dy * o * k)), flags, s)
        qp.setPen(color)
        qp.drawText(rect, flags, s)

    def _chip(self, qp: QtGui.QPainter, rect, fill, *, radius=None,
              border=None, shadow=True) -> None:
        """입체 칩(둥근 박스): 드롭섀도 + 채움 + 상단 글로스 + 테두리.
        fill 은 QColor 또는 QBrush. 투명도 비의존(항상 또렷)."""
        if radius is None:
            radius = self._px(7)
        rf = rect if isinstance(rect, QtCore.QRectF) else QtCore.QRectF(rect)
        if shadow:
            sh = max(1, self._px(2))
            qp.setPen(QtCore.Qt.NoPen)
            qp.setBrush(QtGui.QColor(0, 0, 0, 110))
            qp.drawRoundedRect(rf.translated(sh, sh + self._px(1)),
                               radius, radius)
        qp.setPen(QtCore.Qt.NoPen)
        qp.setBrush(fill)
        qp.drawRoundedRect(rf, radius, radius)
        # 상단 글로스 하이라이트.
        qp.save()
        gpath = QtGui.QPainterPath()
        gpath.addRoundedRect(rf, radius, radius)
        qp.setClipPath(gpath)
        gloss = QtGui.QLinearGradient(0.0, rf.top(),
                                      0.0, rf.top() + rf.height() * 0.55)
        gloss.setColorAt(0.0, QtGui.QColor(255, 255, 255, 46))
        gloss.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
        qp.fillRect(rf, QtGui.QBrush(gloss))
        qp.restore()
        if border is not None:
            qp.setPen(border)
            qp.setBrush(QtCore.Qt.NoBrush)
            qp.drawRoundedRect(rf, radius, radius)

    def _draw_title(self, qp: QtGui.QPainter, text: str,
                    baseline_y: int = None, accent=None) -> None:
        """소형 캡션 타이틀 (액센트 점 + 밝은 흰색, halo)."""
        ar, ag, ab = accent or self._PANEL_ACCENT
        by = baseline_y if baseline_y is not None else self._px(20)
        dot = self._px(5)
        left = self._px(13)
        qp.setPen(QtCore.Qt.NoPen)
        qp.setBrush(QtGui.QColor(ar, ag, ab, 255))
        qp.drawEllipse(left, by - self._px(8), dot, dot)
        qp.setFont(self._font(9))
        self._text(qp, left + dot + self._px(6), by, text,
                   QtGui.QColor(225, 233, 246))

    def _draw_edit_hint(self, qp: QtGui.QPainter) -> None:
        """편집 모드 시 노란 점선 테두리 + 좌상단 작은 힌트."""
        if not self._edit_mode:
            return
        pen = QtGui.QPen(QtGui.QColor(255, 220, 60), 2, QtCore.Qt.DashLine)
        qp.setPen(pen)
        qp.setBrush(QtCore.Qt.NoBrush)
        qp.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 6, 6)
        qp.setFont(self._font(8, bold=True))
        qp.setPen(QtGui.QColor(255, 220, 60))
        qp.drawText(self._px(6), self._px(12), "드래그로 이동")

    def mousePressEvent(self, ev):
        if self._edit_mode and ev.button() == QtCore.Qt.LeftButton:
            self._drag_offset = ev.globalPos() - self.pos()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if (self._edit_mode
                and (ev.buttons() & QtCore.Qt.LeftButton)
                and self._drag_offset is not None):
            g = ev.globalPos() - self._drag_offset
            cx, cy = self._clamp_to_bound(g.x(), g.y())
            self.move(cx, cy)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if (self._edit_mode and ev.button() == QtCore.Qt.LeftButton
                and self._drag_offset is not None):
            self._drag_offset = None
            p = self.pos()
            cx, cy = self._clamp_to_bound(p.x(), p.y())
            if (cx, cy) != (p.x(), p.y()):
                self.move(cx, cy)
            self._manual_pos = (cx, cy)
            try:
                self.position_changed.emit(cx, cy)
            except Exception:
                pass
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


class GameOverlay(_ScaledOverlay):
    """힐러/쩔캐별 스킬 쿨 상태 + 시간당 경험치 (왼쪽 고정).

    2026-06-12: 사냥 분석/맵 히스토리 섹션은 HuntOverlay 로 분리.
    쿨 표기: 준비됨(쿨0) / 잔여초 — 측정된 스킬은 항상 줄 표시.
    """

    def __init__(self):
        super().__init__()
        self._rows: dict[int, dict] = {}
        self._base_w = 240
        self.setFixedSize(self._base_w, 80)
        self._tick_timer = QtCore.QTimer(self)
        self._tick_timer.setInterval(500)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

    def _on_tick(self) -> None:
        # 로컬 감산으로 줄 수가 바뀔 수 있으니 레이아웃도 주기 재계산.
        self._relayout()
        self.update()

    def _on_scale_changed(self) -> None:
        self._relayout()
        self._reanchor()

    def _reanchor(self) -> None:
        """우선순위: 수동 위치 > game_rect 좌상단 > 유지. 모두 bound로 clamp."""
        if self._manual_pos is not None:
            mx, my = self._manual_pos
            cx, cy = self._clamp_to_bound(mx, my)
            self.move(cx, cy)
            return
        if self._game_rect:
            gx, gy, _, _ = self._game_rect
            cx, cy = self._clamp_to_bound(int(gx), int(gy))
            self.move(cx, cy)

    def clear_rows(self) -> None:
        self._rows.clear()
        self.update()

    def update_healer(self, idx: int, d: dict) -> None:
        key = int(idx)
        cur = self._rows.get(key, {})
        new_nick = str(d.get("nickname", "") or "").strip()
        prev_nick = str(cur.get("nick", "") or "").strip()
        if not new_nick:
            new_nick = prev_nick
        # p/b에 음수(정보 없음)가 들어오면 이전 유효값을 그대로 유지 —
        # 워커가 잠시 OCR 실패한 프레임에서 카운트다운이 끊기지 않게.
        new_p = int(d.get("cd_parlyuk", -1))
        new_b = int(d.get("cd_baekho", -1))
        now = time.monotonic()
        p_ts = float(cur.get("p_ts", 0.0) or 0.0)
        b_ts = float(cur.get("b_ts", 0.0) or 0.0)
        if new_p < 0:
            new_p = int(cur.get("p", -1))
        else:
            p_ts = now
        if new_b < 0:
            new_b = int(cur.get("b", -1))
        else:
            b_ts = now
        # 지폭지술 (쩔캐 현인, 2026-06-12). -1=미해당 → stick 없이 그대로
        # 반영해 현인 OFF 시 줄이 즉시 사라지게 (타이머 기반이라 OCR 실패
        # 프레임 개념 없음).
        new_j = int(d.get("cd_jipok", -1))
        j_ts = now if new_j >= 0 else 0.0
        self._rows[key] = {
            "nick": new_nick,
            "p": new_p,
            "b": new_b,
            "j": new_j,
            "p_ts": p_ts,
            "b_ts": b_ts,
            "j_ts": j_ts,
            "armed": bool(d.get("armed", False)),
            "has_armed": "armed" in d,
            "xph": int(d.get("xp_per_hour", 0) or 0),
        }
        self._relayout()
        self.update()

    @staticmethod
    def _eff_cd(cd: int, ts: float) -> int:
        """로컬 시계 기반 잔여 쿨 감산. ts=0이면 감산 안 함(값 없음).

        워커를 정지해도 오버레이 자체 타이머로 카운트가 흘러가도록 하는 핵심.
        """
        if cd is None or cd < 0:
            return -1
        if ts <= 0:
            return int(cd)
        elapsed = int(max(0.0, time.monotonic() - ts))
        return max(0, int(cd) - elapsed)

    def _visible_lines(self) -> list[tuple[int, str, str, int]]:
        """측정된(>=0) 스킬은 쿨 0이어도 항상 표시 — '준비됨' (2026-06-12).
        -1(미측정/미해당)만 숨김. 지폭지술은 쩔캐(현인) 행에서만 >=0."""
        lines: list[tuple[int, str, str, int]] = []
        for idx in sorted(self._rows.keys()):
            r = self._rows[idx]
            nick = r["nick"] or f"힐러{idx + 1}"
            p = self._eff_cd(r.get("p", -1), r.get("p_ts", 0.0))
            b = self._eff_cd(r.get("b", -1), r.get("b_ts", 0.0))
            j = self._eff_cd(r.get("j", -1), r.get("j_ts", 0.0))
            xph = r["xph"]
            if p >= 0:
                lines.append((idx, nick, f"파력무참 : {_fmt_cd_kr(p)}", p))
            if b >= 0:
                lines.append((idx, nick, f"백호의희원 : {_fmt_cd_kr(b)}", b))
            if j >= 0:
                lines.append((idx, nick, f"지폭지술 : {_fmt_cd_kr(j)}", j))
            if xph > 0:
                lines.append((idx, nick, _fmt_xph(xph), -1))
        return lines

    def _relayout(self) -> None:
        w = self._px(self._base_w)
        lines = self._visible_lines()
        n = len(lines)
        row_h = self._px(22)
        head_h = self._px(30)
        pad = self._px(8)
        h = head_h + (row_h * max(1, n)) + pad
        self.setFixedSize(w, h)

    def paintEvent(self, _ev):
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self._draw_panel_bg(qp, ACCENT_CD)
        left_pad = self._px(13)
        nick_col = self._px(92)
        self._draw_title(qp, "힐러 상태", accent=ACCENT_CD)
        lines = self._visible_lines()
        y = self._px(42)
        row_h = self._px(22)
        if not lines:
            qp.setFont(self._font(9, bold=False))
            self._text(qp, left_pad, y, "힐러 수신 대기",
                       QtGui.QColor(160, 166, 178))
            y += row_h
        else:
            last_idx = -999
            for idx, nick, text, cd in lines:
                if idx != last_idx:
                    qp.setFont(self._font(10))
                    self._text(qp, left_pad, y, f"{nick}",
                               QtGui.QColor(135, 210, 255))
                    last_idx = idx
                # cd<=0: 준비됨/xph(녹색). 쿨 임박~진행은 잔여초별 색.
                if cd <= 0:
                    color = QtGui.QColor(140, 240, 158)
                elif cd <= 5:
                    color = QtGui.QColor(255, 105, 105)
                elif cd <= 15:
                    color = QtGui.QColor(255, 190, 85)
                else:
                    color = QtGui.QColor(224, 228, 238)
                qp.setFont(self._font(11))
                self._text(qp, nick_col, y, text, color)
                y += row_h
        self._draw_edit_hint(qp)


class HuntOverlay(_ScaledOverlay):
    """사냥 분석 + 맵 히스토리 전용 오버레이 (2026-06-12 GameOverlay에서 분리).

    격수 모드 전용. MainWindow._tick_analytics 가 update_analytics(snap) 주입.
    위치는 'hunt' 키로 별도 저장/드래그 (위치 편집 모드 공통).
    """

    def __init__(self):
        super().__init__()
        self._analytics: Optional[dict] = None
        self._base_w = 240
        self.setFixedSize(self._base_w, 60)

    def update_analytics(self, snap: Optional[dict]) -> None:
        self._analytics = snap or None
        self._relayout()
        self.update()

    def _on_scale_changed(self) -> None:
        self._relayout()
        self._reanchor()

    def _reanchor(self) -> None:
        """우선순위: 수동 위치 > game_rect 좌상단+아래 오프셋 > 유지."""
        if self._manual_pos is not None:
            mx, my = self._manual_pos
            cx, cy = self._clamp_to_bound(mx, my)
            self.move(cx, cy)
            return
        if self._game_rect:
            gx, gy, _, _ = self._game_rect
            # GameOverlay(쿨 상태)가 좌상단을 쓰므로 그 아래 기본 배치.
            cx, cy = self._clamp_to_bound(int(gx), int(gy) + self._px(180))
            self.move(cx, cy)

    def _session_stats(self) -> Optional[dict]:
        """활성 세션 핵심 수치. 비활성/없음이면 None."""
        a = self._analytics
        if not a:
            return None
        sess = a.get("session") or {}
        if not sess.get("active"):
            return None
        return {
            "dur": int(sess.get("duration_sec") or 0),
            "gain": int(sess.get("xp_gain") or 0),
            "xph": int(sess.get("xp_per_hour") or 0),
        }

    def _secondary_lines(self) -> list:
        """보조 정보 (사냥시간 / 바퀴 / 이번 바퀴) — 작은 글씨."""
        a = self._analytics
        if not a:
            return []
        out: list = []
        st = self._session_stats()
        laps = a.get("laps") or {}
        if st:
            out.append(f"사냥 {_fmt_dur(st['dur'])}")
        n = int(laps.get("count") or 0)
        if n >= 1:
            avg_d = int(laps.get("avg_duration_sec") or 0)
            avg_g = int(laps.get("avg_xp_gain") or 0)
            out.append(
                f"바퀴 {n}회 · 평균 {_fmt_dur(avg_d)} · {_fmt_xp(avg_g)}")
        if laps.get("in_progress"):
            el = int(laps.get("cur_elapsed_sec") or 0)
            out.append(f"이번 바퀴 {_fmt_dur(el)}")
        return out

    def _map_history_rows(self) -> list:
        """맵 히스토리 (최근 5개, (이름, 체류, 획득, 현재여부))."""
        a = self._analytics
        if not a:
            return []
        sess = a.get("session") or {}
        hist = sess.get("map_history") or []
        if not hist:
            return []
        stats = sess.get("map_stats") or {}
        recent = list(hist)[-5:]
        rows: list = []
        for i, m in enumerate(recent):
            st = stats.get(m) or {}
            rows.append((
                str(m),
                _fmt_dur(int(st.get("duration_sec") or 0)),
                _fmt_xp(int(st.get("xp_gain") or 0)),
                i == len(recent) - 1,
            ))
        return rows

    def _section(self, qp, y: int, left: int, title: str,
                 accent=ACCENT_HUNT) -> int:
        """섹션 헤더(액센트 점 + halo 타이틀 + 페이드 라인). 다음 본문 y 반환.
        qp=None 이면 측정만(높이 계산용)."""
        ar, ag, ab = accent
        y2 = y + self._px(18)
        if qp is not None:
            dot = self._px(5)
            qp.setPen(QtCore.Qt.NoPen)
            qp.setBrush(QtGui.QColor(ar, ag, ab, 255))
            qp.drawEllipse(left, y2 - self._px(8), dot, dot)
            tx = left + dot + self._px(6)
            qp.setFont(self._font(9))
            self._text(qp, tx, y2, title, QtGui.QColor(212, 224, 242))
            fm = qp.fontMetrics()
            lx = tx + fm.horizontalAdvance(title) + self._px(8)
            rx = self.width() - left
            if rx > lx:
                grad = QtGui.QLinearGradient(float(lx), 0.0, float(rx), 0.0)
                grad.setColorAt(0.0, QtGui.QColor(ar, ag, ab, 150))
                grad.setColorAt(1.0, QtGui.QColor(ar, ag, ab, 0))
                qp.setPen(QtGui.QPen(QtGui.QBrush(grad), self._px(1)))
                qp.drawLine(lx, y2 - self._px(4), rx, y2 - self._px(4))
        return y2 + self._px(18)

    def _compose(self, qp) -> int:
        """레이아웃 1패스 — qp 있으면 렌더, None 이면 높이만 측정.
        _relayout 과 paintEvent 가 같은 코드를 공유해 어긋남 방지."""
        left = self._px(13)
        col2 = self._px(126)
        if qp is not None:
            self._draw_title(qp, "사냥 분석", accent=ACCENT_HUNT)
        stats = self._session_stats()
        sec = self._secondary_lines()
        rows = self._map_history_rows()
        y = self._px(36)
        if stats:
            lab_y = y + self._px(11)
            num_y = lab_y + self._px(24)
            if qp is not None:
                qp.setFont(self._font(8, bold=False))
                self._text(qp, left, lab_y, "총 획득",
                           QtGui.QColor(150, 200, 170))
                self._text(qp, col2, lab_y, "시간당 예상",
                           QtGui.QColor(150, 200, 170))
                qp.setFont(self._font(17))
                self._text(qp, left, num_y, _fmt_xp(stats["gain"]),
                           QtGui.QColor(120, 245, 160), outline=2)
                xph = stats["xph"]
                qp.setFont(self._font(15))
                self._text(qp, col2, num_y,
                           (_fmt_xph(xph) if xph > 0 else "—"),
                           QtGui.QColor(240, 246, 254), outline=2)
            y = num_y + self._px(12)
            for s in sec:
                if qp is not None:
                    qp.setFont(self._font(9, bold=False))
                    self._text(qp, left, y, s, QtGui.QColor(190, 196, 208))
                y += self._px(16)
        else:
            if qp is not None:
                qp.setFont(self._font(9, bold=False))
                self._text(qp, left, y + self._px(6), "사냥 데이터 대기",
                           QtGui.QColor(160, 166, 178))
            y += self._px(22)
        if rows:
            y = self._section(qp, y, left, "맵 히스토리")
            for (m, dur, gain, cur) in rows:
                if qp is not None:
                    mark = "▶" if cur else "·"
                    nm_c = (QtGui.QColor(255, 222, 140) if cur
                            else QtGui.QColor(214, 219, 230))
                    qp.setFont(self._font(9, bold=cur))
                    self._text(qp, left, y, f"{mark} {m}", nm_c)
                    rect = QtCore.QRect(
                        left, y - self._px(12),
                        self.width() - left - self._px(12), self._px(14))
                    qp.setFont(self._font(8, bold=False))
                    self._text_rect(
                        qp, rect,
                        int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter),
                        f"{dur} · {gain}", QtGui.QColor(178, 184, 198))
                y += self._px(17)
        return y + self._px(10)

    def _relayout(self) -> None:
        w = self._px(self._base_w)
        # 폭 먼저 고정 → 측정 패스의 우측정렬 계산 일관.
        self.setFixedSize(w, self.height())
        h = max(self._px(40), self._compose(None))
        self.setFixedSize(w, h)

    def paintEvent(self, _ev):
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self._draw_panel_bg(qp, ACCENT_HUNT)
        self._compose(qp)
        self._draw_edit_hint(qp)


class SkillAlertOverlay(_ScaledOverlay):
    """스킬 시전 3초전 1회 알림 + 3초 후 자동 소멸.

    트리거는 외부(격수 MainWindow)에서 edge detect 후 push_alert 호출.
    자체는 duration(기본 3s) 지나면 리스트에서 제거 + repaint.
    """

    def __init__(self):
        super().__init__()
        self._alerts: list[dict] = []
        self._dup_window = 2.0
        self._base_w = 520
        self.setFixedSize(self._base_w, 1)
        self._anchor_timer = QtCore.QTimer(self)
        self._anchor_timer.setInterval(500)
        self._anchor_timer.timeout.connect(self._reanchor)
        self._anchor_timer.start()
        self._expire_timer = QtCore.QTimer(self)
        self._expire_timer.setInterval(250)
        self._expire_timer.timeout.connect(self._expire_tick)
        self._expire_timer.start()

    def _on_scale_changed(self) -> None:
        self._relayout()
        self._reanchor()

    def _reanchor(self) -> None:
        """우선순위: 수동 위치 > 자동 앵커 > 유지. 모두 bound 안으로 clamp."""
        if self._manual_pos is not None:
            mx, my = self._manual_pos
            cx, cy = self._clamp_to_bound(mx, my)
            self.move(cx, cy)
            return
        if not self._game_rect:
            return
        try:
            gx, gy, gw, gh = self._game_rect
            mid = gx + gw // 2
            if self._map_rect:
                mx, my, mw, mh = self._map_rect
                y = my + mh + self._px(8)
            else:
                y = gy + int(gh * 0.15)
            x = mid - self.width() // 2
            cx, cy = self._clamp_to_bound(int(x), int(y))
            self.move(cx, cy)
        except Exception:
            pass

    def push_alert(self, msg: str, duration_sec: float = 3.0,
                   color: Optional[QtGui.QColor] = None) -> None:
        if not msg:
            return
        now = time.monotonic()
        for a in self._alerts:
            if a["msg"] == msg and (now - a.get("created_ts", 0)) < self._dup_window:
                a["expire_ts"] = now + duration_sec
                self.update()
                return
        self._alerts.append({
            "msg": str(msg),
            "key": None,
            "created_ts": now,
            "expire_ts": now + float(duration_sec),
            "color": color or QtGui.QColor(255, 230, 80),
        })
        self._relayout()
        self._reanchor()
        self.update()

    def push_countdown(self, key: str, msg: str,
                       duration_sec: float = 1.5,
                       color: Optional[QtGui.QColor] = None) -> None:
        """동일 key의 기존 알림을 덮어써 카운트다운처럼 매 초 텍스트만 갱신.

        main_window가 cd 4→3→2→1 변화마다 같은 key로 호출하면 한 줄이
        '3초전 → 2초전 → 1초전' 으로 바뀌며 유지됨. duration_sec은 다음 갱신이
        오지 않을 때의 잔존 시간(기본 1.5s: 1초 tick 한 번 거르는 여유).
        """
        if not key or not msg:
            return
        now = time.monotonic()
        col = color or QtGui.QColor(255, 230, 80)
        for a in self._alerts:
            if a.get("key") == key:
                a["msg"] = str(msg)
                a["expire_ts"] = now + float(duration_sec)
                a["color"] = col
                self.update()
                return
        self._alerts.append({
            "msg": str(msg),
            "key": str(key),
            "created_ts": now,
            "expire_ts": now + float(duration_sec),
            "color": col,
        })
        self._relayout()
        self._reanchor()
        self.update()

    def drop_countdown(self, key: str) -> None:
        """카운트다운 즉시 제거 (cd=0 또는 값 소실 시)."""
        if not key:
            return
        before = len(self._alerts)
        self._alerts = [a for a in self._alerts if a.get("key") != key]
        if len(self._alerts) != before:
            self._relayout()
            self._reanchor()
            self.update()

    def _expire_tick(self) -> None:
        now = time.monotonic()
        before = len(self._alerts)
        self._alerts = [a for a in self._alerts if a["expire_ts"] > now]
        if len(self._alerts) != before:
            self._relayout()
            self._reanchor()
            self.update()

    def _relayout(self) -> None:
        w = self._px(self._base_w)
        n = len(self._alerts)
        if n == 0:
            # 편집 모드면 드래그 잡을 수 있게 일정 높이 확보.
            self.setFixedSize(w, self._px(40) if self._edit_mode else 1)
        else:
            h = self._px(16) + self._px(32) * n
            self.setFixedSize(w, h)

    def paintEvent(self, _ev):
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if not self._alerts:
            if self._edit_mode:
                # 편집 모드 placeholder — 배경만 투명도 적용.
                qp.setPen(QtCore.Qt.NoPen)
                qp.setBrush(QtGui.QColor(0, 0, 0, self._a(120)))
                qp.drawRoundedRect(self.rect(), self._px(8), self._px(8))
                qp.setFont(self._font(10, bold=False))
                qp.setPen(QtGui.QColor(200, 200, 200))
                qp.drawText(self.rect(), QtCore.Qt.AlignCenter,
                            "스킬 알림 영역 (드래그로 이동)")
                self._draw_edit_hint(qp)
            return
        radius = self._px(11)
        ar, ag, ab = ACCENT_ALERT
        # 배경·테두리 투명도 적용 (가운데 강조형 — 액센트 글로우 테두리).
        r = self.rect()
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(r), float(radius), float(radius))
        qp.save()
        qp.setClipPath(path)
        qp.setPen(QtCore.Qt.NoPen)
        qp.setBrush(QtGui.QColor(10, 11, 15, self._a(206)))
        qp.drawRect(r)
        grad = QtGui.QLinearGradient(0.0, float(r.top()), 0.0, float(r.bottom()))
        grad.setColorAt(0.0, QtGui.QColor(ar, ag, ab, self._a(34)))
        grad.setColorAt(1.0, QtGui.QColor(ar, ag, ab, self._a(0)))
        qp.fillRect(r, QtGui.QBrush(grad))
        qp.restore()
        qp.setPen(QtGui.QPen(QtGui.QColor(ar, ag, ab, self._a(200)), self._px(2)))
        qp.setBrush(QtCore.Qt.NoBrush)
        qp.drawRoundedRect(r.adjusted(1, 1, -2, -2), radius, radius)
        # 메시지 글씨 — halo 로 배경 무관 또렷.
        qp.setFont(self._font(14))
        line_h = self._px(32)
        y = self._px(6)
        for a in self._alerts:
            col = a["color"]
            text_col = QtGui.QColor(col.red(), col.green(), col.blue(), 255)
            rect = QtCore.QRect(0, y, self.width(), line_h)
            self._text_rect(qp, rect, int(QtCore.Qt.AlignCenter), a["msg"],
                            text_col, halo_alpha=235)
            y += line_h
        self._draw_edit_hint(qp)
