"""클라우드 로그 다운로드 (D머신 디버그 전용).

앱이 sunbi-logs/{계정}/{역할-IP끝자리}/ 에 올린 로그를 받아 분석한다.
(게이트 미사용 시엔 계정 폴더 없이 sunbi-logs/{역할-IP끝자리}/.)

  py -m src.tools.cloud_logs --list                 # sid(leaf) 별 로그 목록
  py -m src.tools.cloud_logs --pull                 # 모든 sid 최신 1개씩
  py -m src.tools.cloud_logs --pull kimjs           # 특정 계정 전체 최신
  py -m src.tools.cloud_logs --pull kimjs/healer-0  # 특정 sid 최신 1개

받은 파일: <ROOT>/logs_cloud/{sid의 / → _}__{filename}
"""
from __future__ import annotations

import argparse
import pathlib

from ..net.cloud_sync import CloudClient

_DEST = pathlib.Path(__file__).resolve().parents[2] / "logs_cloud"


def _is_folder(e: dict) -> bool:
    # Supabase storage list: 폴더는 id/metadata 가 None.
    return e.get("id") is None


def _leaf_sids(c: CloudClient, prefix: str = "") -> list:
    """파일이 실제로 들어있는 폴더 경로(leaf sid)들을 재귀로 수집.

    계정별 정리로 계층이 깊어졌으므로(계정/역할-IP) 폴더만 있는 중간 단계는
    더 내려가고, 파일이 있는 폴더를 leaf 로 본다."""
    entries = c.list_logs(prefix)
    files = [e for e in entries if not _is_folder(e)]
    folders = [e for e in entries if _is_folder(e)]
    out = []
    if files:
        out.append(prefix.rstrip("/"))
    for f in folders:
        out.extend(_leaf_sids(c, prefix + f["name"] + "/"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="목록만 출력")
    ap.add_argument("--pull", nargs="?", const="__all__", default=None,
                    help="다운로드 (sid 생략 시 전체 최신, 계정만 줘도 됨)")
    args = ap.parse_args()

    c = CloudClient()

    if args.list or args.pull is None:
        sids = _leaf_sids(c)
        if not sids:
            print("로그 없음 (앱에서 아직 안 올림).")
        for sid in sids:
            files = [e for e in c.list_logs(sid + "/") if not _is_folder(e)]
            print(f"[{sid}] {len(files)}개")
            for e in files[:10]:
                sz = (e.get("metadata") or {}).get("size", "?")
                print(f"   {sid}/{e['name']}  ({sz} B)")
        if args.pull is None:
            return

    if args.pull == "__all__":
        targets = _leaf_sids(c)
    else:
        # 계정만 주면 그 아래 leaf 전부, leaf 를 직접 주면 그 하나.
        targets = _leaf_sids(c, args.pull.rstrip("/") + "/") or [args.pull]
    _DEST.mkdir(exist_ok=True)
    for sid in targets:
        files = [e for e in c.list_logs(sid + "/") if not _is_folder(e)]
        if not files:
            print(f"[{sid}] 파일 없음")
            continue
        latest = files[0]  # name desc 정렬 → 파일명(타임스탬프) 최신이 첫번째
        key = f"{sid}/{latest['name']}"
        data = c.download_log(key)
        out = _DEST / f"{sid.replace('/', '_')}__{latest['name']}"
        out.write_bytes(data)
        print(f"받음: {out}  ({len(data)} B)")


if __name__ == "__main__":
    main()
