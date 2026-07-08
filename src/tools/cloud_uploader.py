"""Supabase 업로더 (D머신/배포자 전용).

배포 대상 파일을 증분 업로드(바뀐 것만)하고 releases 테이블에 새 버전을 만든다.
앱(C머신)은 이 릴리스의 manifest 를 보고 바뀐 파일만 내려받는다.

admin 설정: ~/.oldbaram_cloud_admin.json   (repo/채팅 노출 금지)
  {
    "url": "https://<project>.supabase.co",
    "service_key": "<service_role key>",
    "bucket": "sunbi-releases"
  }

사용:
  py -m src.tools.cloud_uploader --changelog "격수 OCR 지연 수정"
  py -m src.tools.cloud_uploader --dry-run        # 업로드 없이 변경분만 출력
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from typing import List

import requests

from ..net.cloud_sync import sha256_file

_ADMIN_PATH = pathlib.Path.home() / ".oldbaram_cloud_admin.json"
_ROOT = pathlib.Path(__file__).resolve().parents[2]  # dist_dosa = 배포 루트
_TIMEOUT = 300

# 배포 포함 대상 (repo flat 구조와 동일: 루트 = 실행 루트)
_INCLUDE_DIRS = ["src", "models"]
_INCLUDE_FILES = [
    "config.yaml",
    "knownmaps.txt",
    "requirements.txt",
    "dataset/runs/full_v3/weights/best.pt",
]
_EXCLUDE_SUFFIX = (".pyc", ".bak", ".md")
_EXCLUDE_PARTS = ("__pycache__",)


def load_admin() -> dict:
    if not _ADMIN_PATH.exists():
        sys.exit(f"[에러] admin 설정 없음: {_ADMIN_PATH}\n"
                 f"      url/service_key/bucket 을 넣어 만드세요.")
    d = json.loads(_ADMIN_PATH.read_text(encoding="utf-8"))
    for k in ("url", "service_key", "bucket"):
        if not d.get(k):
            sys.exit(f"[에러] admin 설정 항목 누락: {k}")
    d["url"] = str(d["url"]).rstrip("/")
    return d


def collect_files() -> List[pathlib.Path]:
    out: List[pathlib.Path] = []
    for d in _INCLUDE_DIRS:
        base = _ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix in _EXCLUDE_SUFFIX:
                continue
            if any(part in _EXCLUDE_PARTS for part in p.parts):
                continue
            if ".bak" in p.name:  # .bak / .bak.v6 / .bak.pre_rollback 등 백업 전부
                continue
            if ("easyocr" in p.parts
                    or "korean_PP-OCRv5_mobile_rec" in p.parts):
                # 2026-06-11 PaddleOCR/EasyOCR 제거 → 죽은 모델 디렉토리.
                # 사용자 머신에 잔존해도 업로드/배포에서 제외(경량화).
                continue
            out.append(p)
    for f in _INCLUDE_FILES:
        p = _ROOT / f
        if p.is_file():
            out.append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--changelog", default="", help="이번 버전 변경 내역")
    ap.add_argument("--build", default=None,
                    help="배포 semver(미지정 시 src/version.py BUILD_VERSION)")
    ap.add_argument("--dry-run", action="store_true", help="업로드 없이 변경분만 표시")
    args = ap.parse_args()

    build_ver = args.build
    if not build_ver:
        try:
            from ..version import BUILD_VERSION
            build_ver = BUILD_VERSION
        except Exception:
            build_ver = None

    adm = load_admin()
    url, key, bucket = adm["url"], adm["service_key"], adm["bucket"]
    H = {"apikey": key, "Authorization": f"Bearer {key}"}

    # 🔴 2026-07-08 사고 수정: releases 는 exe 채널(cloud_uploader_dist)과
    # 공유 테이블이고 version 카운터도 공유한다. 최신 1행만 보면:
    #   ① 직전이 exe 채널 행(manifest=[])일 때 prev={} → 전 파일 "변경" 오판
    #   ② 새 행에 dist_manifest 를 안 넣어 → 런처가 "배포 manifest 없음" 으로
    #      전 사용자 자동업데이트 정지 (v163 실사고, 매일 05시 nav_auto 재발)
    # 각 컬럼별로 "비어있지 않은 최신 행"을 따로 찾고, dist_manifest 는 그대로
    # 승계해 새 행에 다시 기록한다 (exe 채널 배포를 .py 배포가 덮지 않도록).
    r = requests.get(
        f"{url}/rest/v1/releases",
        headers=H,
        params={"select": "version,manifest,dist_manifest,build_version",
                "order": "version.desc", "limit": "20"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    prev_ver = int(rows[0]["version"]) if rows else 0
    prev = {}
    for row in rows:
        m = row.get("manifest") or []
        if m:
            prev = {e["path"]: e["sha256"] for e in m}
            print(f"증분 기준: v{row['version']} (manifest {len(m)}개)")
            break
    # exe 채널 배포물 승계 — 이 값을 새 행에 그대로 실어야 런처가 계속 본다.
    dist_manifest, dist_bv = [], None
    for row in rows:
        dm = row.get("dist_manifest") or []
        if dm:
            dist_manifest = dm
            dist_bv = row.get("build_version")
            print(f"dist_manifest 승계: v{row['version']} ({len(dm)}개, "
                  f"build={dist_bv})")
            break
    if rows and not dist_manifest:
        print("[WARN] 최근 20개 릴리스에 dist_manifest 없음 — 런처 채널 미배포 "
              "상태. 이 릴리스도 런처엔 안 보인다.")

    files = collect_files()
    manifest: List[dict] = []
    changed: List[pathlib.Path] = []
    for p in files:
        rel = p.relative_to(_ROOT).as_posix()
        digest = sha256_file(p)
        manifest.append({"path": rel, "sha256": digest, "size": p.stat().st_size})
        if prev.get(rel) != digest:
            changed.append(p)

    print(f"전체 {len(files)} 파일 / 변경 {len(changed)} 파일 (이전 v{prev_ver})")
    for p in changed:
        print(f"  변경: {p.relative_to(_ROOT).as_posix()} ({p.stat().st_size} B)")

    if args.dry_run:
        print("[dry-run] 업로드/릴리스 생략.")
        return
    if not changed and rows:
        print("변경 없음 → 새 릴리스 생략.")
        return

    # 변경분 업로드 (upsert)
    for p in changed:
        rel = p.relative_to(_ROOT).as_posix()
        hu = dict(H)
        hu["x-upsert"] = "true"
        hu["Content-Type"] = "application/octet-stream"
        last_err = None
        for attempt in range(4):
            try:
                with open(p, "rb") as f:
                    ru = requests.post(
                        f"{url}/storage/v1/object/{bucket}/{rel}",
                        headers=hu, data=f, timeout=_TIMEOUT,
                    )
                ru.raise_for_status()
                last_err = None
                break
            except requests.RequestException as e:  # 504/네트워크 일시 오류 재시도
                last_err = e
                if attempt < 3:
                    print(f"  재시도({attempt + 1}/3) {rel}: {e}")
                    time.sleep(2 * (attempt + 1))
        if last_err is not None:
            raise last_err
        print(f"  업로드 완료: {rel}")

    # 새 릴리스 생성
    new_ver = prev_ver + 1
    hr = dict(H)
    hr["Content-Type"] = "application/json"
    hr["Prefer"] = "return=minimal"
    payload = {"version": new_ver, "changelog": args.changelog, "manifest": manifest}
    # dist_manifest 는 exe 채널 배포물 목록 — 그대로 승계(런처가 이 행을 본다).
    if dist_manifest:
        payload["dist_manifest"] = dist_manifest
    # build_version 은 dist_manifest 와 짝이다. 승계한 exe 배포물의 버전을 쓴다.
    # (.py 채널은 exe 를 안 바꾸므로 src/version.py 값을 쓰면 런처가 .version 에
    #  실제 exe 와 다른 버전을 기록 → 다음 배포가 "이미 최신" 오판.)
    _bv_use = dist_bv or build_ver
    if _bv_use:
        payload["build_version"] = _bv_use

    def _post(p):
        return requests.post(
            f"{url}/rest/v1/releases", headers=hr,
            data=json.dumps(p, ensure_ascii=False).encode("utf-8"),
            timeout=_TIMEOUT,
        )

    rr = _post(payload)
    # releases.build_version 컬럼이 아직 없으면(스키마 SQL 미실행) 빼고 재시도.
    if rr.status_code >= 400 and "build_version" in payload:
        print("[WARN] build_version 컬럼 없음 → 제외하고 재시도 "
              "(license_schema.sql 실행 시 기록됨).")
        payload.pop("build_version")
        rr = _post(payload)
    rr.raise_for_status()
    _bv = f" build={_bv_use}" if _bv_use and "build_version" in payload else ""
    _dm = f" dist_manifest승계={len(dist_manifest)}개" if dist_manifest else ""
    print(f"[OK] 릴리스 v{new_ver}{_bv}{_dm} 생성 완료. "
          f"업로드 {len(changed)}개 / 총 {len(files)}개.")


if __name__ == "__main__":
    main()
