# 실행 가이드

## 의존성
```
py -m pip install pyyaml mss ultralytics easyocr
```
(torch, opencv-python 기설치 가정)

## 격수 PC (본인이 조작하는 PC)
```
cd D:\oldbaram
py -m src.app.attacker
```
- 옛바 실행 후, YOLO로 빨탭 검출 + OCR로 좌표/맵 읽어 힐러 PC들에게 30Hz UDP 송신
- Ctrl+C로 종료

## 힐러 PC
```
cd D:\oldbaram
py -m src.app.healer
```
- UDP 수신 → FSM → PostMessage로 옛바 창에 방향키
- 격수 PC IP를 `config.yaml > net.peers` 에 미리 적어야 함

## 디버그: 패킷 모니터
격수 실행 상태에서 별창으로:
```
py -m src.app.monitor
```

## 네트워크 설정
- 동일 LAN이면 `peers: [192.168.x.x, 192.168.x.y]` 식으로 힐러 IP 나열
- 같은 PC에서 3창 돌리는 테스트: `peers: [127.0.0.1]` + `bind_host: 127.0.0.1`
- Windows 방화벽: UDP 54545 inbound 허용 필요

## 포트 충돌 / 여러 힐러 창 한 PC에서
- 각 힐러가 다른 옛바 창을 PostMessage로 제어 → 현재 구조는 창 이름 1개만 찾음
- 2창 이상이면 `input.target_window` 에 정확한 창 제목 지정하거나 hwnd 선택 로직 추가 필요 (TODO)
