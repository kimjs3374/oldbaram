"""dist(Nuitka) 배포 업로더 — 런처용 자동업데이트 채널.

nuitka_build/run_sunbi_healer.dist/ 의 파일들을 Supabase storage 의
sunbi-releases/app/<상대경로> 로 증분 업로드(바뀐 것만)하고, releases 테이블에
build_version + dist_manifest(전체 파일 sha256 목록)를 기록한다. 런처(launcher.py)
가 이 dist_manifest 를 받아 로컬 app/ 과 비교해 변경분만 내려받는다.

소스(.py) 업로더는 tools/cloud_uploader.py(레거시). 이쪽은 exe 배포 전용.
실행(D머신, service_role): py -m src.tools.cloud_uploader_dist --changelog "..."
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parents[2]
DIST = ROOT / "nuitka_build" / "run_sunbi_healer.dist"
BUCKET = "sunbi-releases"
PREFIX = "app"                      # storage 경로 prefix (런처도 app/ 로 받음)
MAX_BYTES = 50 * 1024 * 1024        # Supabase 무료 파일 50MB 제한
_TIMEOUT = 300
_ADMIN = pathlib.Path.home() / ".oldbaram_cloud_admin.json"


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
    out = []
    for p in sorted(DIST.rglob("*")):
        if p.is_file():
            out.append((p.relative_to(DIST).as_posix(), p))
    return out


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
    r = requests.get(
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
    manifest, changed, toobig = [], [], []
    for rel, p in files:
        sz = p.stat().st_size
        if sz > MAX_BYTES:
            toobig.append((rel, sz))
            continue
        h = sha256(p)
        manifest.append({"path": rel, "sha256": h, "size": sz})
        if prev.get(rel) != h:
            changed.append((rel, p))

    if toobig:
        print("[ERROR] 50MB 초과 파일 — 업로드 불가:")
        for rel, sz in toobig:
            print(f"  {rel}  ({sz/1024/1024:.1f}MB)")
        sys.exit(1)

    total_mb = sum(e["size"] for e in manifest) / 1024 / 1024
    chg_mb = sum(p.stat().st_size for _, p in changed) / 1024 / 1024
    print(f"전체 {len(files)}개 / 변경 {len(changed)}개({chg_mb:.1f}MB) / "
          f"build={build_ver} / dist={total_mb:.0f}MB")
    if args.dry_run:
        print("(dry-run — 업로드 안 함)")
        return

    for i, (rel, p) in enumerate(changed, 1):
        hu = dict(H)
        hu["x-upsert"] = "true"
        hu["Content-Type"] = "application/octet-stream"
        with open(p, "rb") as f:
            rr = requests.post(
                f"{url}/storage/v1/object/{bucket}/{PREFIX}/{rel}",
                headers=hu, data=f, timeout=_TIMEOUT)
        rr.raise_for_status()
        print(f"[{i}/{len(changed)}] ↑ {rel}")

    new_ver = prev_ver + 1
    hr = dict(H)
    hr["Content-Type"] = "application/json"
    hr["Prefer"] = "return=minimal"
    payload = {
        "version": new_ver, "changelog": args.changelog,
        "build_version": build_ver, "dist_manifest": manifest,
        "manifest": [],   # 소스 채널 미사용(런처는 dist_manifest 만 봄)
    }
    rr = requests.post(
        f"{url}/rest/v1/releases", headers=hr,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=_TIMEOUT)
    rr.raise_for_status()
    print(f"[OK] 릴리스 v{new_ver} build={build_ver} — "
          f"업로드 {len(changed)}/{len(files)}개")


if __name__ == "__main__":
    main()
