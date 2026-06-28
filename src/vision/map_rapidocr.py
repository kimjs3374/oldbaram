"""맵바/텍스트 OCR — RapidOCR(korean PP-OCR ONNX). 단일 OCR 엔진.

일반 OCR이라 숫자 조합 무관(x,y,z 학습 불필요), 한글 base도 정확.
실측: 한글+숫자 ~100%(끝 영문 G 후처리). onnxruntime CPU.
모델: korean_rec.onnx(13MB) + korean_dict.txt (같은 폴더).

맵명 보정 (2026-06-11 사용자 지시):
- 노이즈 제거: 맵명 유효 문자(한글/숫자/괄호/하이픈) 외 전부 삭제.
- 선비족 X-Y(Z) 구조 정규화: 격수/힐러 맵키가 글자 단위로 일치해야 trail/
  MAP-SEQ 가 동작. RapidOCR raw 는 정확하나 끝 영문(횃불→G)·괄호 한쪽 탈락
  같은 구조 노이즈가 가끔 끼므로 여기서만 정리. base 글자는 절대 바꾸지 않음
  (knownmaps 기반 base 보정은 ocr.py 가 담당 — 과보정 방지 책임 분리).
"""
import os
import re
import pathlib

_DIR = pathlib.Path(__file__).resolve().parent
_REC = _DIR / "korean_rec.onnx"
_DICT = _DIR / "korean_dict.txt"
_engine = None
_init_failed = False


def _intra_threads() -> int:
    """RapidOCR intra_op 스레드 수 (기본 2, env OB_OCR_INTRA_THREADS 조정).

    YOLO(onnx)·RapidOCR·digit_cnn 가 각자 전체코어를 잡으면 CPU 과구독으로
    YOLO predict spike → OCR 계열은 2스레드로 캡해 코어를 YOLO에 양보.
    rec-only(use_det=False)는 2스레드로도 ~14ms (전체코어 12ms와 사실상 동일).
    """
    try:
        return max(1, int(os.environ.get("OB_OCR_INTRA_THREADS", "2")))
    except Exception:
        return 2

# 맵명 유효 문자: 한글/숫자/괄호/하이픈. 끝 영문(횃불 오인 G 등) 제거.
_KEEP = re.compile(r"[^가-힣0-9()\-]")


def ready() -> bool:
    return _REC.exists() and _DICT.exists()


def _get_engine():
    global _engine, _init_failed
    if _engine is not None or _init_failed:
        return _engine
    try:
        from rapidocr_onnxruntime import RapidOCR
        # intra_op 스레드 캡 — det/cls/rec 세션 전부 동일 적용(전체코어 점유 방지).
        _nt = _intra_threads()
        _engine = RapidOCR(rec_model_path=str(_REC),
                           rec_keys_path=str(_DICT),
                           intra_op_num_threads=_nt,
                           inter_op_num_threads=1)
    except Exception:
        _init_failed = True
        _engine = None
    return _engine


def read_text(crop) -> str:
    """범용 rec-only 인식 (후처리 없음). cooldown/buff/xp 등 공용.

    rec-only(use_det=False): crop 영역 고정이라 글자검출(det) 불필요.
    벤치 12ms. torch/paddle 의존 0 (onnxruntime CPU 전용).
    """
    eng = _get_engine()
    if eng is None or crop is None:
        return ""
    try:
        r, _ = eng(crop, use_det=False, use_cls=False, use_rec=True)
        if not r:
            return ""
        return "".join(
            x[0] for x in r
            if isinstance(x, (list, tuple)) and x and isinstance(x[0], str))
    except Exception:
        return ""


def _normalize_struct(s: str) -> str:
    """선비족 X-Y(Z) 구조 노이즈 정리 (base 글자는 불변).

    1) 양끝 잉여 하이픈 제거 ('-선비족' / '선비족-' → '선비족').
    2) 괄호 밸런스 복원: '(' > ')' 면 부족분 뒤에 ')' 보충
       ('선비족2-4(1' → '선비족2-4(1)'). lap suffix 가 격수/힐러 간 글자 단위로
       일치해야 trail 키가 맞물림.
       반대(고립 ')')는 끝에서 초과분만 제거(정상 lap suffix 보존).
    옛바 맵명에 중첩 괄호 없음 → 단순 카운트로 충분.
    """
    if not s:
        return s
    s = s.strip("-")
    open_n = s.count("(")
    close_n = s.count(")")
    if open_n > close_n:
        s = s + ")" * (open_n - close_n)
    elif close_n > open_n:
        m = re.search(r"\)+$", s)
        if m:
            excess = close_n - open_n
            keep = max(0, len(m.group(0)) - excess)
            s = s[:m.start()] + ")" * keep
    # 2026-06-15: 정상 선비족 구조 뒤 잉여 한글(OCR 노이즈) 제거.
    # 허브/입구가 'X에'로 오독돼 known_maps 오염되던 것 차단 (20:52 로그
    # '선비족2예→선비족2에' 수천회). '선비족2에'→'선비족2'(허브),
    # '선비족입구예'→'선비족입구', '선비족2-4(1)예'→'선비족2-4(1)'.
    # 2026-06-16: 5층에서 끝 노이즈 '데' 발생('선비족5-1(6)데' 수백회, 힐러
    # 맵매칭 실패→STUCK/REJECT 폭주) → 노이즈 집합에 '데' 추가.
    # 정상('선비족2방','선비족2-4(1)')은 불변 — 노이즈 글자(에/예/이/데)만 제거.
    # [가-힣]+ 로 넓히면 백트래킹으로 '선비족2방'의 '방'까지 먹어 허브로
    # 오축소되므로, 관측된 노이즈 글자만 제한 집합으로 둔다('방'∉집합 → 안전).
    s = re.sub(
        r"^((?:제2)?선비족(?:입구|\d+방|\d+(?:-\d+)?(?:\(\d+\))?))[에예이데]+$",
        r"\1", s)
    # 2026-06-20: 허브('선비족4') 끝 노이즈 숫자 제거. OCR 이 허브 '선비족4' 에
    # 숫자 1개를 덧붙여 '선비족43' 으로 오독(164952 로그, 전이 시퀀스상 허브
    # 선비족4↔43 깜빡임, 굴 진입 전). 힐러는 자기맵과 '43'(구조검증 통과) 불일치
    # → map_neq → MAP-PAUSE 로 정지(힐러 가만히 서있음). 굴은 항상 '4-3(z)'
    # 괄호 포함이라, 괄호 없는 '선비족<1자리><1자리>' 끝은 허브 노이즈 → 뒷자리
    # 제거. '선비족4'(불변)·'선비족4-3'(하이픈,불변)·'선비족4-3(7)'(괄호,불변).
    s = re.sub(r"^((?:제2)?선비족\d)\d$", r"\1", s)
    # 2026-06-22: 굴 구조 끝괄호 뒤 잉여 숫자 제거. OCR 이 '선비족1-5(1)' 에
    # 숫자를 덧붙여 '선비족1-5(1)6' 으로 오독(격수 raw_m 115회 vs 정상 2회,
    # 힐러 a_map 도 노이즈형이 정상형보다 다수). 같은 굴이 (1)/(1)6 로 깜빡여
    # known_maps 오염→trail 키 불일치→추종 헛돔/멈춤. ')' 로 끝나는 정상 굴
    # 구조('...(\d+)') 뒤 잉여 숫자만 제거. 정상은 끝이 ')'라 불변.
    s = re.sub(r"^((?:제2)?선비족\d+(?:-\d+)?\(\d+\))\d+$", r"\1", s)
    # 2026-06-28: 본성입구/선녀의방/무사의방 끝 한글 노이즈 제거(선비족과 동일
    # [에예이데]). 정상은 숫자로 끝나므로 base/번호 불변.
    s = re.sub(r"^((?:제\d+)?(?:본성입구|선녀의방|무사의방)\d+)[에예이데]+$",
               r"\1", s)
    # 2026-06-28: 비밀통로/닌자의방은 번호 없는 단일맵인데 OCR 이 뒤에 잉여
    # 문자('....................' 등)를 붙여 매 프레임 달라짐 → 맵 식별/전환감지
    # 불가(사용자 신고). base 로 시작하면 뒤를 통째로 잘라 정규화(단일맵이라
    # 안전) → trail 추종으로 통과(본성입구3→비밀통로→본성입구4).
    s = re.sub(r"^((?:제\d+)?(?:비밀통로|닌자의방)).*$", r"\1", s)
    return s


def read_map(crop) -> str:
    """맵바 crop -> "선비족X-Y(Z)" (노이즈 제거 + 구조 정규화)."""
    return _normalize_struct(_KEEP.sub("", read_text(crop)))
