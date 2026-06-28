"""배포 버전(semver). 사용자 표시 + 라이선스 강제 업데이트 기준.

- BUILD_VERSION: 배포 exe/패키지의 사람이 읽는 버전(v0.1.0 부터). app_login 의
  min_build 비교에 쓰여 "최신 아니면 실행 차단" 판정에 사용된다.
- 증분 자동업데이트의 파일 버전(releases.version, .version 파일)은 정수 카운터로
  별도 관리(net/updater.py). 둘은 목적이 다르다(표시/강제 vs 증분 다운로드).
"""
BUILD_VERSION = "0.1.4"
