"""사용자가 준 버프 영역 이미지로 cooldown_ocr 실제 파싱 결과 확인."""
import sys
import time
import cv2

sys.path.insert(0, r"D:/oldbaram/dist_dosa")
from src.vision.cooldown_ocr import CooldownOcr

IMG = r"C:/Users/ENG/.claude/image-cache/9d9315db-72e8-4c8f-83dd-ab97eee020cf/1.png"

img = cv2.imread(IMG)
if img is None:
    print(f"[FAIL] imread {IMG}")
    sys.exit(1)
h, w = img.shape[:2]
print(f"[IMG] {w}x{h}")

ocr = CooldownOcr(poll_sec=0.3)
# 격수 attacker.py 와 동일 설정.
ocr.set_target_skills(["혼마술", "무장", "보호"])
# 전체 이미지를 region 으로 지정.
ocr.set_region(0, 0, w, h)
ocr.start()

# PaddleOCR 초기화 대기.
print("[WAIT] PaddleOCR 초기화...")
for _ in range(30):
    if ocr.init_note():
        print(f"  init_note={ocr.init_note()!r}")
        break
    time.sleep(0.5)

# 반복 submit — 내부 스레드가 주기적 처리.
for i in range(60):
    ocr.submit_frame(img, (0, 0))
    time.sleep(0.3)
    r = ocr.latest()
    if getattr(r, "ts", 0) > 0 and getattr(r, "raw_text", ""):
        break

print(f"---RESULT after {(i+1)*0.3:.1f}s---")
print(f"ts         = {getattr(r, 'ts', 0)}")
print(f"skills     = {getattr(r, 'skills', {})}")
print(f"raw_text   = {getattr(r, 'raw_text', '')!r}")
print(f"diag       = {getattr(r, 'diag', '')!r}")
print(f"init_note  = {ocr.init_note()!r}")

try:
    ocr.stop()
except Exception:
    pass
