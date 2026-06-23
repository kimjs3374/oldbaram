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

    # 직전 최신 dist_manifest (증분 기준)
    r = _S.get(
        f"{url}/rest/v1/releases",
        headers=H,
        params={"select": "version,dist_manifest",
                "order": "version.desc", "limit": "1"},
        timeout=_TIMEOUT)
    r.raise_for_status()
    rows = r.json()
    prev_ver = int(rows[0]["version"]) if rows else 0
    prev = ({e["path"]: e["sha256"] for e in (rows[0].get("dist_manifest") or [])}
            if rows else {})

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

    first_deploy = not prev   # 첫 배포면 중단 재개를 위해 기존 파일 skip 허용
    for i, (rel, p, entry) in enumerate(changed, 1):
        n_parts = entry.get("parts")
        if n_parts:
            if first_deploy and _exists(url, H, f"{bucket}/{PREFIX}/{rel}.part{n_parts-1}"):
                continue
            with open(p, "rb") as f:
                for idx in range(n_parts):
                    chunk = f.read(CHUNK_BYTES)
                    _put(url, H, f"{bucket}/{PREFIX}/{rel}.part{idx}", chunk)
            print(f"[{i}/{len(changed)}] up {rel} ({n_parts}청크)", flush=True)
        else:
            if first_deploy and _exists(url, H, f"{bucket}/{PREFIX}/{rel}"):
                continue
            with open(p, "rb") as f:
                _put(url, H, f"{bucket}/{PREFIX}/{rel}", f)
            print(f"[{i}/{len(changed)}] up {rel}", flush=True)

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
