#!/bin/bash
# 힐러 프로세스 감지되면 자동으로 py-spy dump 연속 실행.
# 사용자가 격수 ON/OFF 전환할 때마다 시계열로 상태 변화 추적.

SCRIPTS="/c/Users/ENG/AppData/Local/Programs/Python/Python312/Scripts"
OUT_DIR="/tmp/healer_dumps"
mkdir -p "$OUT_DIR"
rm -f "$OUT_DIR"/*.txt

echo "[monitor] 힐러 프로세스 대기 중..."
PID=""
for i in {1..600}; do  # 최대 10분 대기
    PID=$(tasklist 2>/dev/null | grep -i "python.exe" | awk '{print $2}' | head -1)
    if [ -n "$PID" ]; then
        echo "[monitor] 힐러 감지 PID=$PID"
        break
    fi
    sleep 1
done

if [ -z "$PID" ]; then
    echo "[monitor] 타임아웃 — 힐러 못 찾음"
    exit 1
fi

# 연속 dump (3초 간격 × 40번 = 2분 모니터링)
for i in $(seq 1 40); do
    TS=$(date +%H%M%S)
    F="$OUT_DIR/dump_${i}_${TS}.txt"
    "$SCRIPTS/py-spy" dump --pid "$PID" > "$F" 2>&1
    if [ $? -ne 0 ]; then
        echo "[monitor] dump $i 실패 (프로세스 종료?)"
        break
    fi
    # 스레드 이름별 카운트 + active 요약
    ACTIVE_CNT=$(grep -cE "\(active\)" "$F")
    THREAD_CNT=$(grep -cE "^Thread " "$F")
    HPMP_CNT=$(grep -cE "\"hpmp_ocr\"" "$F")
    ASYNC_OCR_CNT=$(grep -cE "\"async-ocr\"" "$F")
    ASYNC_YOLO_CNT=$(grep -cE "\"async-yolo\"" "$F")
    ASYNC_GRAB_CNT=$(grep -cE "\"async-grabber\"" "$F")
    echo "[$i $TS] threads=$THREAD_CNT active=$ACTIVE_CNT hpmp=$HPMP_CNT ocr=$ASYNC_OCR_CNT yolo=$ASYNC_YOLO_CNT grab=$ASYNC_GRAB_CNT"
    sleep 3
done

echo "[monitor] 완료. 덤프 저장 위치: $OUT_DIR"
