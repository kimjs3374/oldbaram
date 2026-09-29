# ob_macro 개발환경 이전 안내 (2026-09-29, D:\oldbaram → D:\ob_macro)

## 새 PC 절차
1. 이 폴더(`D:\ob_macro`) 통째로 새 PC 로 복사. **경로는 `D:\ob_macro` 권장**
   (bat 는 어디서든 동작하지만 Claude 메모리 키가 경로 기반 = `D--ob_macro`).
2. 설치: **Python 3.12.10 64bit** (py launcher 체크), **Git for Windows**.
3. 실행: `_migration\setup_new_pc.bat` (GPU/학습 안 할 PC 면 `setup_new_pc.bat nodev`)
   → venv 생성 · 패키지 설치 · 키/설정/메모리 복원 · 스모크 테스트 자동.
   마지막 줄 `스모크 테스트 전부 PASS` 확인.

## 새 PC 수동 작업
- GitHub: 첫 `git push` 때 로그인 창 (remote: origin=oldbaram, sunbi=oldbaram_sunbi 설정됨).
- Tailscale 로그인 (UDP 피어 100.x — config.yaml `net.peers`).
- (선택) NavBrain 자동학습 스케줄러 — **현재 PC 에도 등록 안 돼 있음**
  (nav_auto_log.txt 마지막 2026-08-25, `FAIL cloud_logs pull`). 다시 쓸 때:
  `schtasks /create /tn oldbaram_nav_auto /tr "D:\ob_macro\_nav_auto.bat" /sc daily /st 05:00`
- Nuitka 첫 빌드 시 gcc 자동 다운로드(수 분).
- CLAUDE.md / 메모리 본문의 `D:\oldbaram` 표기는 옛 경로 = 새 PC 에선 `D:\ob_macro` 로 읽을 것.

## 포함 (약 5 GB)
| 항목 | 비고 |
|---|---|
| git 저장소 전체 + 이력(.git) | origin/main v161 동기, 추적파일 원본 바이트 동일 |
| 소스 src / dist_dosa(src, src_v2) / docs / tools / 문서 md·반성문 | untracked `_verify_*` 등 스크립트 포함 |
| 모델 (git ignore 됨) | src·dist_dosa 의 korean_rec / digit_cnn / nav_policy .onnx, dataset\runs(전 가중치), yolov8s/n·yolo26n.pt |
| 맵/네비 데이터 | maps, maps_cloud, nav_dataset, templates, portals_v2.json(C:\ob_sunbi 최신본) |
| 학습 데이터 | logs_cloud(NavBrain 원천), digit_dataset, mapcrops_*, map_crops, coco 4종(라벨 원본), dataset\full_yolo_v3(.npy 캐시 제외) |
| `_migration\home\` | `~\.oldbaram_*.json` 7개 (**Supabase anon/admin 키 포함 — 외부 공유 금지**) |
| `_migration\claude_memory\` | Claude Code 메모리 82개 |
| `_migration\requirements_venv_lock.txt` | dist_dosa\.venv 정확 버전 (빌드용, 41패키지) |
| `_migration\requirements_dev.txt` | 글로벌 py3.12 개발/학습용 (torch cu124 등) |
| `_migration\requirements_global_lock.txt` | 현 PC 글로벌 전체 freeze (참고용, 189개) |

## 제외 (D:\oldbaram 에 그대로 남아있음)
logs\(46.6GB OCR 실패 덤프), dataset 구버전(full_yolo/v2, raw, pseudo, preview), .npy 캐시,
nuitka_build/dist/build(재빌드 산출물), .venv(재생성), dist_dosa\logs·logs_cloud(구로그),
poc, memory_reader, _src_v2_legacy, 각종 _grid/_vis 디버그 폴더, 녹화 mp4, *.bak.

## 이관 중 코드 변경 (D:\ob_macro 에만, 미커밋)
하드코딩 `D:\oldbaram` → bat 자기 위치(`%~dp0`) 기준. 어느 경로에서도 빌드 가능.
- build_nuitka.bat, dist_dosa\build_nuitka.bat: PY/cd 상대화 + portals_v2.json 을
  `C:\ob_sunbi` 있으면 그것, 없으면 저장소 루트 사본 사용.
- build_launcher.bat, dist_dosa\build_launcher.bat, build_admin.bat, _nav_auto.bat: PY/cd/LOG 상대화.
- oldbaram_sunbi_healer.spec: ROOT=spec 위치, portals 동일 fallback.
- 모든 .bat 작업파일 CRLF 통일 (LF+한글주석 조합에서 cmd 가 줄을 삼켜 `%PY%` 가 비는 현상 실측 → 수정).
  저장소 blob 은 LF 그대로라 git diff 엔 안 잡힘.

## 검증 결과 (이 PC 에서 새 venv 로 재현)
- setup_new_pc.bat nodev: venv 새로 생성 → lock 41패키지 설치 OK.
- smoke_test (루트 / dist_dosa): 필수파일 · src 전모듈 import · YOLO/digit_cnn/RapidOCR/NavBrain 실추론 · Qt MainWindow 생성 **PASS**.
  (dist_dosa 는 원본도 yolo_onnx 없는 구트리 — torch 필요 3모듈은 원본과 동일하게 건너뜀)
- `_verify_*.py` 27개: 원본 D:\oldbaram 과 **결과 27/27 동일** (원래 FAIL 이던 구 스크립트 포함 동일).
- build_nuitka.bat 로 D:\ob_macro 에서 exe 실빌드 성공 (BUILD_DONE exit=0, exe 51.9MB) → 실행 시 로그인창 `옛바 로그인 (v0.1.30)` 정상 표시.
