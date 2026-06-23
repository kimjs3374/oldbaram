"""옛바 런처 — Supabase 에서 app(Nuitka dist) 최신본을 증분 다운로드 후 메인 exe 실행.

배포 구조:
    oldbaram_sunbi/
      oldbaram_launcher.exe   ← 이 파일(거의 안 바뀜, 자기 갱신 회피)
      app/                    ← Nuitka dist (메인 exe + 라이브러리)
        oldbaram_sunbi_healer.exe
        .version              ← 현재 build_version
        ...

동작:
1. releases 최신 build_version + dist_manifest 조회.
2. 로컬 app/.version 과 다르면 manifest 의 파일 중 sha256 불일치분만 다운로드.
   (코드만 바뀌면 메인 exe 1개만 받음 — 라이브러리는 고정)
3. app/oldbaram_sunbi_healer.exe 실행(OB_LAUNCHER=1 → 메인은 자체 업데이트 스킵).
오프라인/실패는 전부 무시하고 기존 app 으로 실행(앱이 안 뜨는 일 방지).

표준 라이브러리만 사용(requests/PyQt 불필요) → 런처 exe 를 작게 유지.
anon 공개키는 내장(어차피 공개되는 키).
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import urllib.request

URL = "https://ljuimshccxilqgquqezf.supabase.co"
KEY = "sb_publishable_FvvU8TE_JjCAOfwgX_ywaA_61IZlGT5"
BUCKET = "sunbi-releases"

# 런처 exe 위치 기준 (onefile 은 sys.argv[0], standalone 은 실행 파일 경로).
BASE = pathlib.Path(sys.argv[0]).resolve().parent
APP = BASE / "app"
VERSION_FILE = APP / ".version"
MAIN_EXE = APP / "oldbaram_sunbi_healer.exe"

_HDRS = {"apikey": KEY, "Authorization": "Bearer " + KEY}


def _log(msg: str) -> None:
    try:
        print(f"[런처] {msg}", flush=True)
    except Exception:
        pass


def _get_json(path: str, timeout: int = 15):
    req = urllib.request.Request(URL + path, headers=_HDRS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _download(storage_path: str, dest: pathlib.Path, timeout: int = 180) -> None:
    url = f"{URL}/storage/v1/object/public/{BUCKET}/{storage_path}"
    req = urllib.request.Request(url, headers=_HDRS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)   # 원자적 교체(부분 다운로드 방지)


def _sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _local_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def update() -> None:
    rows = _get_json(
        "/rest/v1/releases?select=build_version,dist_manifest"
        "&order=version.desc&limit=1")
    if not rows:
        return
    rel = rows[0]
    bv = rel.get("build_version")
    manifest = rel.get("dist_manifest") or []
    if not bv or not manifest:
        _log("배포 manifest 없음 → 기존 실행")
        return
    if _local_version() == bv:
        _log(f"최신 v{bv}")
        return
    _log(f"업데이트 → v{bv} (변경분 확인...)")
    got = 0
    for entry in manifest:
        rel_path = entry.get("path")
        if not rel_path:
            continue
        dest = APP / rel_path
        if dest.exists() and _sha256(dest) == entry.get("sha256"):
            continue
        _log(f"  ↓ {rel_path}")
        _download(f"app/{rel_path}", dest)
        got += 1
    VERSION_FILE.write_text(bv, encoding="utf-8")
    _log(f"업데이트 완료 — {got}개 파일")


def launch_main() -> None:
    if not MAIN_EXE.exists():
        _log("메인 앱 파일이 없습니다. 재설치하세요.")
        sys.exit(1)
    env = dict(os.environ)
    env["OB_LAUNCHER"] = "1"
    subprocess.Popen([str(MAIN_EXE)], cwd=str(APP), env=env)


def main() -> None:
    try:
        update()
    except Exception as e:
        _log(f"업데이트 스킵(오프라인/실패해도 실행): {e}")
    launch_main()


if __name__ == "__main__":
    main()
