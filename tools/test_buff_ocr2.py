"""이미지를 상/하 분할해 각 라인 인식 결과 확인."""
import sys
import cv2
from pathlib import Path

sys.path.insert(0, r"D:/oldbaram/dist_dosa")

IMG = r"C:/Users/ENG/.claude/image-cache/9d9315db-72e8-4c8f-83dd-ab97eee020cf/1.png"
img = cv2.imread(IMG)
h, w = img.shape[:2]
print(f"[IMG] {w}x{h}")

# 상/하 분할.
top = img[: h // 2, :]
bot = img[h // 2 :, :]

# 수동 업스케일.
def upscale(im, k=3):
    return cv2.resize(im, (im.shape[1] * k, im.shape[0] * k),
                      interpolation=cv2.INTER_CUBIC)

from paddleocr import TextRecognition

_MODEL_DIR = Path(r"D:/oldbaram/dist_dosa/models/korean_PP-OCRv5_mobile_rec")
rec = TextRecognition(
    model_name="korean_PP-OCRv5_mobile_rec",
    model_dir=str(_MODEL_DIR),
)
print("[REC] 초기화 완료")


def extract(res):
    """predict 결과 raw dump."""
    return repr(res)[:300]


for name, im in [("FULL", img), ("TOP", top), ("BOT", bot),
                 ("FULL_x3", upscale(img, 3)),
                 ("TOP_x3", upscale(top, 3)),
                 ("BOT_x3", upscale(bot, 3))]:
    try:
        r = rec.predict(im)
        print(f"[{name}]  shape={im.shape[:2]}  → {extract(r)}")
    except Exception as e:
        print(f"[{name}] 실패: {e}")
