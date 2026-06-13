"""클라우드 maps 묶음 pull + merge → maps_cloud/ (D 작업기 분석용).

앱이 정지 시 sunbi-logs/maps/{sid}.json 으로 올린 묶음들을 받아, 빈 MapGrid 에
모두 import(카운트 합산) 후 flush. 멱등(매번 빈 grid 에서 시작 → 중복 합산 X).
백필 maps/ 와 분리해 maps_cloud/ 에 둔다 (실시간 수집 통합본).

  py _cloud_maps.py            # pull + merge + 리포트
  py _cloud_maps.py --list     # 클라우드 maps 묶음 목록만
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
OUT_DIR = ROOT / "maps_cloud"

sys.path.insert(0, str(ROOT / "dist_dosa"))
from src.net.cloud_sync import CloudClient  # noqa: E402
from src.fsm.map_grid import MapGrid  # noqa: E402


def _is_folder(e: dict) -> bool:
    return e.get("id") is None


def main() -> None:
    list_only = "--list" in sys.argv
    c = CloudClient()
    entries = [e for e in c.list_logs("maps/") if not _is_folder(e)]
    if not entries:
        print("클라우드에 maps 묶음 없음 (앱이 아직 정지-업로드 안 함).")
        return

    print(f"클라우드 maps 묶음 {len(entries)}개:")
    for e in entries:
        sz = (e.get("metadata") or {}).get("size", "?")
        print(f"   maps/{e['name']}  ({sz} B)")
    if list_only:
        return

    grid = MapGrid(OUT_DIR)  # 빈 grid (OUT_DIR 비어있다는 전제 → 멱등)
    # 기존 maps_cloud 제거(멱등 보장): import 는 누적이므로 재실행 시 2배 방지.
    if OUT_DIR.is_dir():
        for old in OUT_DIR.glob("*.json"):
            old.unlink()

    total_bundles = 0
    for e in entries:
        try:
            data = json.loads(c.download_log(f"maps/{e['name']}"))
            grid.import_bundle(data)
            total_bundles += 1
        except Exception as ex:  # noqa: BLE001
            print(f"   ! {e['name']} 실패: {ex}")
    n = grid.flush()
    print(f"\n묶음 {total_bundles}개 병합 → {OUT_DIR} ({n}개 맵 저장)")


if __name__ == "__main__":
    main()
