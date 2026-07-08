"""옛바 런처 — Supabase 에서 app(Nuitka dist) 최신본을 증분 다운로드 후 메인 exe 실행.

배포 구조:
    oldbaram_sunbi/
      oldbaram_launcher.exe   ← 이 파일(거의 안 바뀜, 자기 갱신 회피)
      launcher.log            ← 런처 동작 기록 (v2: 문제 진단용 — 콘솔이
                                 disable 라 파일이 유일한 로그)
      app/                    ← Nuitka dist (메인 exe + 라이브러리)
        oldbaram_sunbi_healer.exe
        .version              ← 현재 build_version (적용 "완료" 후에만 기록)
        ...

동작 (LAUNCHER_VER 3, 2026-07-08 근본수정 — .py 채널 행이 최신일 때
자동업데이트 전면 정지 사고 대응):
1. releases 에서 dist_manifest 가 있는 최신 행의 build_version + dist_manifest
   조회 (최신 1행만 보면 .py 채널 행에 막힘 — v163 실사고).
2. manifest sha 와 로컬 파일 전수 대조 → 변경분을 전부 *.new 로 스테이징
   다운로드(받은 바이트 sha 사전 검증 — 손상 다운로드 차단).
3. 전부 성공했을 때만 일괄 교체. 메인 exe 부터 교체(잠금 위험 최대) —
   봇 실행 중(PermissionError)이면 아무것도 안 바꾸고 기존 버전으로 실행
   (.version 미기록 → 다음 실행 때 자동 재시도).
4. 교체 후 exe sha 재검증 → 그때만 .version 기록.
오프라인/실패는 전부 무시하고 기존 app 으로 실행(앱이 안 뜨는 일 방지).

v1 문제(실측): 다운로드하며 즉시 교체 + 마지막에 무조건 .version 기록 +
콘솔 로그 증발 → 중간 실패 시 혼합 버전 실행/버전 오기록/진단 불가.

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
import time
import urllib.request

URL = "https://ljuimshccxilqgquqezf.supabase.co"
KEY = "sb_publishable_FvvU8TE_JjCAOfwgX_ywaA_61IZlGT5"
BUCKET = "sunbi-releases"
LAUNCHER_VER = "3"

# 런처 exe 위치 기준 (onefile 은 sys.argv[0], standalone 은 실행 파일 경로).
BASE = pathlib.Path(sys.argv[0]).resolve().parent
APP = BASE / "app"
VERSION_FILE = APP / ".version"
MAIN_EXE = APP / "oldbaram_sunbi_healer.exe"
LOG_FILE = BASE / "launcher.log"

_HDRS = {"apikey": KEY, "Authorization": "Bearer " + KEY}


def _log(msg: str) -> None:
    line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"[런처v{LAUNCHER_VER}] {msg}")
    try:
        print(line, flush=True)
    except Exception:
        pass
    # 콘솔 disable(onefile) 환경에서 유일한 진단 수단 — 파일에도 기록.
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 512_000:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))  # 단순 로테이트
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _get_json(path: str, timeout: int = 15):
    req = urllib.request.Request(URL + path, headers=_HDRS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _fetch(storage_path: str, timeout: int = 300) -> bytes:
    url = f"{URL}/storage/v1/object/public/{BUCKET}/{storage_path}"
    req = urllib.request.Request(url, headers=_HDRS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


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


def _ensure_cloud_config() -> None:
    """메인 exe 의 로그인 게이트가 읽을 anon 설정(~/.oldbaram_cloud.json) 보장.
    install.bat 이 하던 일을 런처가 대신 → 설정 없는 새 PC 도 바로 동작."""
    cfg = pathlib.Path.home() / ".oldbaram_cloud.json"
    if cfg.exists():
        return
    try:
        cfg.write_text(
            json.dumps({"url": URL, "anon_key": KEY, "bucket": BUCKET},
                       ensure_ascii=False),
            encoding="utf-8")
        _log("클라우드 설정 생성 완료")
    except Exception as e:
        _log(f"클라우드 설정 생성 실패: {e}")


def _cleanup(staged: list) -> None:
    for _dest, tmp, _rel in staged:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def update() -> None:
    # 🔴 2026-07-08 사고 수정: releases 는 exe 채널(cloud_uploader_dist,
    # dist_manifest 채움)과 .py 채널(cloud_uploader, dist_manifest 비움)이
    # version 카운터를 공유하는 테이블이다. 최신 1행만 보면 그 행이 .py 채널
    # 행일 때 dist_manifest=[] → "배포 manifest 없음" 으로 전 사용자 자동
    # 업데이트가 통째로 죽는다 (실사고: v163 이 .py 행이라 v162 의 정상
    # manifest 122개를 두고도 스킵. NavNet 자동 재학습이 도는 매일 05시 재발).
    # dist_manifest 가 비어있지 않은 최신 행을 찾는다 (uploader_dist 와 동일 방어).
    rows = _get_json(
        "/rest/v1/releases?select=version,build_version,dist_manifest"
        "&order=version.desc&limit=20")
    if not rows:
        return
    rel = next((r for r in rows if r.get("dist_manifest")), None)
    if rel is None:
        _log(f"배포 manifest 없음(최근 {len(rows)}개 릴리스 전부) → 기존 실행")
        return
    if rel is not rows[0]:
        _log(f"주의: 최신 v{rows[0].get('version')} 에 dist_manifest 없음"
             f"(.py 채널 행) → v{rel.get('version')} 의 manifest 사용")
    bv = rel.get("build_version")
    manifest = rel.get("dist_manifest") or []
    if not bv:
        _log(f"경고: v{rel.get('version')} build_version 없음 → 기존 실행")
        return
    # 2026-06-29: build_version 문자열만으로 스킵하지 않는다 — 항상 manifest
    # sha 와 로컬 파일을 전수 대조해 실제 변경분만 받는다 (버전 문자열은 기록용).
    _log(f"버전 확인: 서버 v{rel.get('version')}/{bv} "
         f"(로컬 .version={_local_version() or '-'}) — sha 전수 대조")

    # ① 변경분 스테이징 다운로드 (교체는 아직 안 함 — 혼합 버전 방지).
    staged: list = []   # (dest, tmp, rel_path)
    try:
        for entry in manifest:
            rel_path = entry.get("path")
            want = str(entry.get("sha256") or "")
            if not rel_path:
                continue
            dest = APP / rel_path
            if dest.exists() and _sha256(dest) == want:
                continue
            n_parts = entry.get("parts")
            if n_parts:
                _log(f"  down {rel_path} ({n_parts}청크)")
                buf = bytearray()
                for i in range(int(n_parts)):
                    buf += _fetch(f"app/{rel_path}.part{i}")
                data = bytes(buf)
            else:
                _log(f"  down {rel_path}")
                data = _fetch(f"app/{rel_path}")
            got = hashlib.sha256(data).hexdigest()
            if want and got != want:
                raise RuntimeError(
                    f"다운로드 손상: {rel_path} sha 불일치 "
                    f"(기대 {want[:12]} 수신 {got[:12]})")
            tmp = dest.with_suffix(dest.suffix + ".new")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            staged.append((dest, tmp, rel_path))
    except Exception:
        _cleanup(staged)
        raise

    if not staged:
        VERSION_FILE.write_text(bv, encoding="utf-8")
        _log(f"최신 v{bv} (변경 없음)")
        return

    # ② 일괄 교체 — 메인 exe(잠금 위험 최대) 먼저. 첫 파일에서 잠기면
    #    아무것도 안 바꾼 상태로 중단 = 완전한 구버전으로 실행(혼합 없음).
    staged.sort(key=lambda t: 0 if t[0] == MAIN_EXE else 1)
    replaced = 0
    for dest, tmp, rel_path in staged:
        try:
            tmp.replace(dest)
            replaced += 1
        except PermissionError:
            if replaced == 0:
                _log(f"교체 불가(파일 사용 중): {rel_path} — 봇/앱을 완전히 "
                     f"종료한 뒤 런처를 다시 실행하세요. 이번엔 기존 버전으로 "
                     f"실행합니다 (.version 미기록 → 다음 실행 때 재시도)")
                _cleanup(staged)
                return
            # 일부 교체 후 잠금 = 혼합 상태. 다음 실행의 sha 전수 대조가
            # 이어받아 복구하므로 .version 만 미기록하고 보고.
            _log(f"경고: {replaced}개 교체 후 {rel_path} 잠김 — 봇 종료 후 "
                 f"런처 재실행 필요 (.version 미기록)")
            _cleanup(staged)
            return

    # ③ 최종 검증 후에만 .version 기록 (v1 은 무조건 기록 → 오기록 사고).
    exe_want = next((str(e.get("sha256") or "") for e in manifest
                     if e.get("path") == MAIN_EXE.name), "")
    if exe_want and MAIN_EXE.exists() and _sha256(MAIN_EXE) != exe_want:
        _log(f"경고: 교체 후 exe sha 불일치 — .version 미기록, 다음 실행 재시도")
        return
    VERSION_FILE.write_text(bv, encoding="utf-8")
    _log(f"업데이트 완료 v{bv} — {replaced}개 교체, exe sha 확인")


def launch_main() -> None:
    if not MAIN_EXE.exists():
        _log("메인 앱 파일이 없습니다. 재설치하세요.")
        sys.exit(1)
    env = dict(os.environ)
    env["OB_LAUNCHER"] = "1"
    subprocess.Popen([str(MAIN_EXE)], cwd=str(APP), env=env)


def main() -> None:
    _ensure_cloud_config()    # 메인 게이트가 읽을 anon 설정 보장(install.bat 역할)
    try:
        update()
    except Exception as e:
        _log(f"업데이트 스킵(오프라인/실패해도 실행): {e}")
    launch_main()


if __name__ == "__main__":
    main()
