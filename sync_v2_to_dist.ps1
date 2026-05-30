# sync src_v2/ → dist_dosa/src_v2/ (Phase 2 통합 후)
# 사용법 (PowerShell, 관리자 권한 불필요):
#   PS> cd D:\oldbaram
#   PS> .\sync_v2_to_dist.ps1
$ErrorActionPreference = "Stop"
$src = "D:\oldbaram\src_v2"
$dst = "D:\oldbaram\dist_dosa\src_v2"

if (-not (Test-Path $src)) { throw "$src not found" }
if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Path $dst | Out-Null }

# robocopy: /MIR 미러링 (삭제 포함) — 단, __pycache__ 제외.
$rc = Start-Process -FilePath robocopy.exe `
    -ArgumentList @($src, $dst, "/MIR", "/XD", "__pycache__", "/NFL", "/NDL", "/NP") `
    -Wait -PassThru -NoNewWindow

# robocopy: 0,1,2,3 = success codes. 4+ = error.
if ($rc.ExitCode -ge 8) {
    throw "robocopy failed exit=$($rc.ExitCode)"
}
Write-Host "[SYNC] src_v2 → dist_dosa/src_v2 완료 (exit=$($rc.ExitCode))"

# __pycache__ 정리.
Get-ChildItem -Path $dst -Recurse -Force -Filter "__pycache__" -Directory `
    | Remove-Item -Recurse -Force
Write-Host "[CLEAN] __pycache__ 정리 완료"
