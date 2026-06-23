"""기기 고유 식별자(HWID) — 라이선스 기기 등록/동시실행 판정용.

Windows MachineGuid(레지스트리)를 기본 지문으로 쓰고 sha256 으로 단방향
해시한다. 재설치/업데이트에도 유지되고, 원본 GUID 를 서버에 노출하지 않는다.
MachineGuid 조회 실패 시 MAC 주소(uuid.getnode()) 해시로 폴백.

주의: MachineGuid 는 레지스트리 조작으로 바꿀 수 있어 결정적 방어는 아니나,
일반 사용자에겐 충분(서버측 등록/동시 한도와 결합). 강화 필요 시 디스크
serial 등 복합 지문으로 확장.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import uuid


def _windows_machine_guid() -> str:
    """HKLM\\SOFTWARE\\Microsoft\\Cryptography MachineGuid 값. 실패 시 ''."""
    if not sys.platform.startswith("win"):
        return ""
    try:
        out = subprocess.run(
            ["reg", "query",
             r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        for line in out.splitlines():
            if "MachineGuid" in line:
                # 형식: "    MachineGuid    REG_SZ    <guid>"
                parts = line.split()
                if parts:
                    return parts[-1].strip()
    except Exception:
        pass
    return ""


def machine_id() -> str:
    """기기 지문 → sha256 앞 32자(hex). 어떤 환경에서도 비어있지 않은 값 반환."""
    raw = _windows_machine_guid()
    if not raw:
        # 폴백: MAC 주소(노드). getnode() 가 임의값일 수 있으나 없는 것보단 낫다.
        raw = f"mac-{uuid.getnode():012x}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
