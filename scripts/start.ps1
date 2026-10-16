# mathgen one-click launcher for Windows.
#
# ASCII only on purpose: Windows PowerShell 5.1 reads .ps1 files with the ANSI code page
# unless they carry a UTF-8 BOM, and a BOM would violate this repo's "LF, no BOM" rule
# (checked by backend/tests/contract/test_text_hygiene.py). All user-facing Chinese text
# lives in scripts/start.py instead -- Python reads UTF-8 source reliably.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Lan          # let students on the LAN connect
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Port 9000
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -NoMirror     # install from pypi.org
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Mirror https://your.mirror/simple

[CmdletBinding()]
param(
    [int]$Port = 0,
    [switch]$Lan,
    [switch]$NoBrowser,
    [switch]$Reinstall,
    [string]$Mirror = "",
    [switch]$NoMirror,
    [switch]$SkipChromium,
    [switch]$SkipFrontend,
    [switch]$PrepareOnly
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Double-click friendly: only pause when a human is actually watching, so the same script
# can run unattended from CI or a pipeline without hanging on Read-Host.
function Wait-IfInteractive {
    if ($Host.Name -eq "ConsoleHost" -and [Environment]::UserInteractive) {
        Read-Host "Press Enter to close"
    }
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$startScript = Join-Path $here "start.py"

# Find a usable Python launcher. `py -3.12` is preferred, but it fails loudly when that
# minor version is absent, so every candidate is probed before it is trusted.
$candidates = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += , @("py", "-3.12") }
if (Get-Command python -ErrorAction SilentlyContinue) { $candidates += , @("python") }
if (Get-Command python3 -ErrorAction SilentlyContinue) { $candidates += , @("python3") }

$python = $null
foreach ($candidate in $candidates) {
    $exe = $candidate[0]
    $prefix = @()
    if ($candidate.Count -gt 1) { $prefix = $candidate[1..($candidate.Count - 1)] }
    try {
        & $exe @prefix -c "import sys" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
    } catch {
        # try the next candidate
    }
}

if ($null -eq $python) {
    Write-Host ""
    Write-Host "  Python 3.12+ was not found on this machine." -ForegroundColor Yellow
    Write-Host "  Install it from https://www.python.org/downloads/ and run this script again."
    Write-Host ""
    try { Start-Process "https://www.python.org/downloads/" } catch { }
    Wait-IfInteractive
    exit 1
}

$exe = $python[0]
$prefix = @()
if ($python.Count -gt 1) { $prefix = $python[1..($python.Count - 1)] }

$forward = @($startScript)
if ($Port -gt 0) { $forward += @("--port", "$Port") }
if ($Lan) { $forward += @("--host", "0.0.0.0") }
if ($NoBrowser) { $forward += @("--no-browser") }
if ($Reinstall) { $forward += @("--reinstall") }
if ($Mirror -ne "") { $forward += @("--mirror", "$Mirror") }
if ($NoMirror) { $forward += @("--no-mirror") }
if ($SkipChromium) { $forward += @("--skip-chromium") }
if ($SkipFrontend) { $forward += @("--skip-frontend") }
if ($PrepareOnly) { $forward += @("--prepare-only") }

& $exe @prefix @forward
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ""
    Write-Host "  mathgen exited with code $code." -ForegroundColor Yellow
    Wait-IfInteractive
}
exit $code
