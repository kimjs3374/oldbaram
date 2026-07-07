"""배포 버전(semver). 사용자 표시 + 라이선스 강제 업데이트 기준.

- BUILD_VERSION: 배포 exe/패키지의 사람이 읽는 버전(v0.1.0 부터). app_login 의
  min_build 비교에 쓰여 "최신 아니면 실행 차단" 판정에 사용된다.
- 증분 자동업데이트의 파일 버전(releases.version, .version 파일)은 정수 카운터로
  별도 관리(net/updater.py). 둘은 목적이 다르다(표시/강제 vs 증분 다운로드).

🔴🔴🔴 매 배포마다 아래 BUILD_VERSION 을 반드시 올릴 것 (patch +1).
  cloud_uploader_dist 가 이 값을 releases.build_version 에 기록하고, 런처/로그인
  게이트가 "값이 그대로면 최신 = 스킵" 으로 판정한다. 안 올리면 새 exe 를
  올려도 사용자 런처가 자동업데이트를 스킵 → 미반영. (v150 '0.1.16 미반영',
  v151 '존재-스킵 오판' 사고의 근본원인.)
  ⚠️ dist_dosa/src/version.py 도 같은 값으로 동기.
"""
BUILD_VERSION = "0.1.23"
