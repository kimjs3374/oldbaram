"""dist(Nuitka) 배포 업로더 — 런처용 자동업데이트 채널.

nuitka_build/run_sunbi_healer.dist/ 의 파일들을 Supabase storage 의
sunbi-releases/app/<상대경로> 로 증분 업로드(바뀐 것만)하고, releases 테이블에
build_version + dist_manifest(전체 파일 sha256 목록)를 기록한다. 런처
(launcher.py)가 이 dist_manifest 를 받아 로컬 app/ 과 비교해 변경분만 내려받는다.

Supabase 무료는 파일 1개 50MB 제한 → 그 이상(예: cv2.pyd 71MB)은 45MB 청크로
분할 업로드(app/<path>.part0, .part1, ...). manifest entry 에 parts=N 표시.
런처가 parts 있으면 청크를 받아 재조립한다.

소스(.py) 업로더는 tools/cloud_uploader.py(레거시). 이쪽은 exe 배포 전용.
실행(D머신, service_role): py -m src.tools.cloud_uploader_dist --changelog "..."
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
DIST = ROOT / "nuitka_build" / "run_sunbi_healer.dist"
BUCKET = "sunbi-releases"
PREFIX = "app"                       # storage 경로 prefix (런처도 app/ 로 받음)
SAFE_BYTES = 50 * 1024 * 1024        # Supabase 무료 파일 50MB 제한
CHUNK_BYTES = 45 * 1024 * 1024       # 초과 파일 분할 단위(< 50MB)
_TIMEOUT = 300
_ADMIN = pathlib.Path.home() / ".oldbaram_cloud_admin.json"
_S = requests.Session()   # 연결 재사용(매 파일 TLS 핸드셰이크 제거 → 대폭 가속)


def load_admin() -> dict:
    adm = json.loads(_ADMIN.read_text(encoding="utf-8"))
    url = str(adm["url"]).rstrip("/")
    key = adm.get("service_key") or adm.get("service_role") or adm.get("secret_key")
    if not key:
        sys.exit("admin 설정에 service_key 없음")
    return {"url": url, "key": key, "bucket": adm.get("bucket", BUCKET)}


def sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def collect():
    out, skipped = [], []
    for p in sorted(DIST.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(DIST).as_posix()
        # Supabase storage key 는 ASCII 만 허용(한글 등 → 400). 비ASCII 경로 제외.
        # 실제 대상은 maps/_noise_backup 의 오독맵 격리본(한글 파일명, 앱 무관).
        try:
            rel.encode("ascii")
        except UnicodeEncodeError:
            skipped.append(rel)
            continue
        out.append((rel, p))
    if skipped:
        print(f"[제외] 비ASCII 경로 {len(skipped)}개(storage 한글 불가, 앱 무관)")
    return out


def _put(url, headers, path_in_bucket, data):
    hu = dict(headers)
    hu["x-upsert"] = "true"
    hu["Content-Type"] = "application/octet-stream"
    r = _S.post(
        f"{url}/storage/v1/object/{path_in_bucket}",
        headers=hu, data=data, timeout=_TIMEOUT)
    r.raise_for_status()


def _exists(url, headers, path_in_bucket) -> bool:
    """이미 업로드된 파일인지(재개용). public HEAD 200 이면 존재."""
    try:
        r = _S.head(
            f"{url}/storage/v1/object/public/{path_in_bucket}",
            headers=headers, timeout=15)
        return r.status_code == 200
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=None,
                    help="배포 semver(미지정 시 src/version.py BUILD_VERSION)")
    ap.add_argument("--changelog", default="")
    ap.add_argument("--dry-run", action="store_true")
    # 2026-07-05 사고 수정: 존재-스킵(재개)은 명시 요청시에만.
    ap.add_argument("--resume", action="store_true",
                    help="중단된 첫 배포 재개: storage 에 이미 있는 파일 스킵")
    ap.add_argument("--force", action="store_true",
                    help="직전 manifest 무시하고 전 파일 강제 업로드 (복구용)")
    args = ap.parse_args()

    if not DIST.is_dir():
        sys.exit(f"dist 폴더 없음: {DIST} (build_nuitka.bat 먼저 실행)")

    build_ver = args.build
    if not build_ver:
        from ..version import BUILD_VERSION
        build_ver = BUILD_VERSION

    adm = load_admin()
    url, key, bucket = adm["url"], adm["key"], adm["bucket"]
    H = {"apikey": key, "Authorization": f"Bearer {key}"}

    # 직전 dist_manifest (증분 기준) — 🔴2026-07-05 사고 수정: releases 는
    # .py 채널(cloud_uploader, dist_manifest=[])과 공유 테이블이라 "최신 1행"
    # 만 보면 직전이 .py 행일 때 prev={} → 첫 배포 오판 → 존재-스킵으로
    # 전 파일 미업로드 + manifest 만 새 sha 기록 (v143/v147/v149 실사고:
    # storage exe 구버전 잔존 → 전 PC 0.1.16 미반영). dist_manifest 가
    # 비어있지 않은 최신 행을 찾는다.
    r = _S.get(
        f"{url}/rest/v1/releases",
        headers=H,
        params={"select": "version,dist_manifest",
                "order": "version.desc", "limit": "20"},
        timeout=_TIMEOUT)
    r.raise_for_status()
    rows = r.json()
    prev_ver = int(rows[0]["version"]) if rows else 0
    prev = {}
    for row in rows:
        dm = row.get("dist_manifest") or []
        if dm:
            prev = {e["path"]: e["sha256"] for e in dm}
            print(f"증분 기준: v{row['version']} (dist_manifest {len(dm)}개)")
            break
    if args.force:
        prev = {}
        print("--force: 직전 manifest 무시, 전 파일 업로드")

    files = collect()
    manifest, changed = [], []
    for rel, p in files:
        sz = p.stat().st_size
        h = sha256(p)
        entry = {"path": rel, "sha256": h, "size": sz}
        if sz > SAFE_BYTES:
            entry["parts"] = math.ceil(sz / CHUNK_BYTES)
        manifest.append(entry)
        if prev.get(rel) != h:
            changed.append((rel, p, entry))

    total_mb = sum(e["size"] for e in manifest) / 1024 / 1024
    chg_mb = sum(p.stat().st_size for _, p, _ in changed) / 1024 / 1024
    split = [e["path"] for e in manifest if e.get("parts")]
    print(f"전체 {len(files)}개 / 변경 {len(changed)}개({chg_mb:.1f}MB) / "
          f"build={build_ver} / dist={total_mb:.0f}MB")
    if split:
        print(f"분할 업로드 파일(>50MB): {split}")
    if args.dry_run:
        print("(dry-run, 업로드 안 함)")
        return

    # 🔴 존재-스킵(재개)은 --resume 명시시에만. "첫 배포" 자동판정 폐기 —
    # sha 검증 없는 존재-스킵이 stale storage + 새 manifest 조합(최악)을
    # 만든 실사고 원인.
    skipped = 0
    for i, (rel, p, entry) in enumerate(changed, 1):
        n_parts = entry.get("parts")
        if n_parts:
            if args.resume and _exists(
                    url, H, f"{bucket}/{PREFIX}/{rel}.part{n_parts-1}"):
                skipped += 1
                continue
            with open(p, "rb") as f:
                for idx in range(n_parts):
                    chunk = f.read(CHUNK_BYTES)
                    _put(url, H, f"{bucket}/{PREFIX}/{rel}.part{idx}", chunk)
            print(f"[{i}/{len(changed)}] up {rel} ({n_parts}청크)", flush=True)
        else:
            if args.resume and _exists(url, H, f"{bucket}/{PREFIX}/{rel}"):
                skipped += 1
                continue
            with open(p, "rb") as f:
                _put(url, H, f"{bucket}/{PREFIX}/{rel}", f)
            print(f"[{i}/{len(changed)}] up {rel}", flush=True)
    if skipped:
        print(f"(--resume: 기존 존재 {skipped}개 스킵)")

    # 🔴 업로드 사후 검증 — 통과 전엔 release 행을 절대 쓰지 않는다.
    # ① 변경 파일 전수: HEAD Content-Length == 로컬 크기 (스킵/부분업로드 탐지)
    # ② 메인 exe: 바이트 재다운로드 → sha256 완전 대조 (런처가 받는 그대로)
    print("업로드 검증 중...", flush=True)
    for rel, p, entry in changed:
        n_parts = entry.get("parts")
        if n_parts:
            continue  # 청크는 재조립 검증 비용 큼 — 크기검증 생략(런처가 sha 검증)
        try:
            hv = _S.head(
                f"{url}/storage/v1/object/public/{bucket}/{PREFIX}/{rel}",
                headers=H, timeout=30)
            remote_sz = int(hv.headers.get("Content-Length", -1))
        except Exception as e:
            sys.exit(f"[FAIL] 검증 요청 실패 {rel}: {e} — release 기록 안 함")
        if remote_sz != entry["size"]:
            sys.exit(f"[FAIL] 크기 불일치 {rel}: storage {remote_sz} != "
                     f"로컬 {entry['size']} — release 기록 안 함")
    exe_entry = next((e for e in manifest
                      if e["path"] == "oldbaram_sunbi_healer.exe"), None)
    if exe_entry and any(rel == "oldbaram_sunbi_healer.exe"
                         for rel, _p, _e in changed):
        rb = _S.get(
            f"{url}/storage/v1/object/public/{bucket}/{PREFIX}/"
            f"oldbaram_sunbi_healer.exe", headers=H, timeout=_TIMEOUT)
        rb.raise_for_status()
        got = hashlib.sha256(rb.content).hexdigest()
        if got != exe_entry["sha256"]:
            sys.exit(f"[FAIL] exe 바이트 불일치: storage {got[:12]} != "
                     f"로컬 {exe_entry['sha256'][:12]} — release 기록 안 함")
        print(f"exe 바이트 검증 OK ({got[:12]})")
    print("업로드 검증 통과")

    new_ver = prev_ver + 1
    hr = dict(H)
    hr["Content-Type"] = "application/json"
    hr["Prefer"] = "return=minimal"
    payload = {
        "version": new_ver, "changelog": args.changelog,
        "build_version": build_ver, "dist_manifest": manifest,
        "manifest": [],   # 소스 채널 미사용(런처는 dist_manifest 만 봄)
    }
    rr = _S.post(
        f"{url}/rest/v1/releases", headers=hr,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=_TIMEOUT)
    rr.raise_for_status()
    print(f"[OK] release v{new_ver} build={build_ver} "
          f"up {len(changed)}/{len(files)}")


if __name__ == "__main__":
    main()
