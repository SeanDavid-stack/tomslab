# Tom's Lab -- PyInstaller build script (Phase 1)
#
# Usage (from repo root):
#     .\build.ps1              # build only
#     .\build.ps1 -Run         # build then launch the packaged exe
#     .\build.ps1 -Clean       # wipe build/ and dist/ first
#
# The activated venv must have PyInstaller installed:
#     .\.venv\Scripts\python.exe -m pip install pyinstaller

[CmdletBinding()]
param(
    [switch]$Run,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

$Venv    = Join-Path $RepoRoot ".venv"
$Python  = Join-Path $Venv "Scripts\python.exe"
$Spec    = Join-Path $RepoRoot "tomslab.spec"
$DistDir = Join-Path $RepoRoot "dist\tomslab"
$ExePath = Join-Path $DistDir "tomslab.exe"

if (-not (Test-Path $Python)) {
    throw "venv python not found at $Python. Create the venv first (python -m venv .venv)."
}
if (-not (Test-Path $Spec)) {
    throw "tomslab.spec not found at $Spec."
}

if ($Clean) {
    Write-Host "[build] cleaning dist/ and build/" -ForegroundColor Yellow
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $RepoRoot "dist")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $RepoRoot "build")
}

# Ensure PyInstaller is present
& $Python -m pip show pyinstaller *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[build] installing pyinstaller into venv" -ForegroundColor Yellow
    & $Python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed" }
}

Write-Host "[build] running pyinstaller --clean tomslab.spec" -ForegroundColor Cyan
& $Python -m PyInstaller --clean --noconfirm $Spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

if (-not (Test-Path $ExePath)) {
    throw "Expected exe not found at $ExePath. Check PyInstaller output above."
}

# Report size
$size = (Get-ChildItem -Recurse -File $DistDir | Measure-Object -Sum Length).Sum
$sizeGB = [math]::Round($size / 1GB, 2)
Write-Host ""
Write-Host "[build] OK" -ForegroundColor Green
Write-Host "         exe : $ExePath"
Write-Host "         size: $sizeGB GB (on-disk)"

if ($Run) {
    Write-Host ""
    Write-Host "[build] launching packaged exe -- stderr will be streamed below" -ForegroundColor Cyan
    Write-Host "         close the window or ctrl+c to stop"
    # Redirect stderr to console so PyInstaller/Qt import errors surface.
    # Use Start-Process + -PassThru so we can attach and wait.
    $p = Start-Process -FilePath $ExePath `
                       -PassThru `
                       -RedirectStandardError (Join-Path $DistDir "last-stderr.log") `
                       -RedirectStandardOutput (Join-Path $DistDir "last-stdout.log") `
                       -NoNewWindow
    Write-Host "         pid: $($p.Id)"
    $p.WaitForExit()
    Write-Host "[build] exe exited with code $($p.ExitCode)" -ForegroundColor Yellow
    Write-Host "         stderr log: $(Join-Path $DistDir 'last-stderr.log')"
    if ($p.ExitCode -ne 0) {
        Get-Content (Join-Path $DistDir "last-stderr.log") -Tail 40
        exit $p.ExitCode
    }
}
