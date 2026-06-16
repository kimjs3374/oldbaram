"""PyInstaller exe 진입점 런처 (선비족 힐러).

healer_gui.py 는 relative import(`from ..config import ...`)라 직접 진입점으로
쓰면 __package__ 가 없어 import 가 깨진다. 패키지로 import 하는 얇은 런처를 둔다.
일반 실행은 기존대로 `py -m src.app.healer_gui` 를 쓰면 된다.
"""
import multiprocessing
from src.app.healer_gui import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # onnxruntime/스레드풀 자식 프로세스 안전.
    main()
