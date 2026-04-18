# Moves Tom's Lab runtime data from %LOCALAPPDATA%\TomsLab to D:\Toms Lab\data
# and rewrites absolute paths inside the SQLite DB to match the new location.
# Run once: right-click -> Run with PowerShell, OR from a PS prompt:
#   powershell -ExecutionPolicy Bypass -File "D:\Toms Lab\migrate_data_to_d.ps1"

$ErrorActionPreference = "Stop"

Write-Host "Stopping any running Tom's Lab..."
Get-Process tomslab -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 300

$oldDir = Join-Path $env:LOCALAPPDATA "TomsLab\data"
$newDir = "D:\Toms Lab\data"
$projectPy = "D:\Toms Lab\.venv\Scripts\python.exe"

if (-not (Test-Path "$oldDir\tomslab.db")) {
    if (Test-Path "$newDir\tomslab.db") {
        Write-Host "Nothing to migrate — DB is already at $newDir."
    } else {
        Write-Warning "No DB found at $oldDir\tomslab.db. Nothing to do."
    }
    exit 0
}

Write-Host "Creating $newDir..."
New-Item -ItemType Directory -Force -Path $newDir | Out-Null

Write-Host "Moving DB..."
if (Test-Path "$newDir\tomslab.db") { Remove-Item "$newDir\tomslab.db*" -Force }
Move-Item -Force "$oldDir\tomslab.db"     "$newDir\tomslab.db"
if (Test-Path "$oldDir\tomslab.db-shm") { Move-Item -Force "$oldDir\tomslab.db-shm" "$newDir\" }
if (Test-Path "$oldDir\tomslab.db-wal") { Move-Item -Force "$oldDir\tomslab.db-wal" "$newDir\" }

if (Test-Path "$oldDir\doc_images") {
    Write-Host "Moving doc_images..."
    if (Test-Path "$newDir\doc_images") { Remove-Item -Recurse -Force "$newDir\doc_images" }
    Move-Item -Force "$oldDir\doc_images" "$newDir\doc_images"
}

Write-Host "Rewriting absolute paths inside the DB..."
$env:OLD_PREFIX = $oldDir
$env:NEW_PREFIX = $newDir
& $projectPy -c @'
import os, sqlite3
old = os.environ["OLD_PREFIX"]
new = os.environ["NEW_PREFIX"]
db = os.path.join(new, "tomslab.db")
c = sqlite3.connect(db)
cur = c.execute(
    "UPDATE document_pages SET rendered_path = REPLACE(rendered_path, ?, ?)",
    (old, new),
)
n = cur.rowcount
c.commit()
c.close()
print(f"Rewrote {n} rendered_path entries")
'@

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "DB now lives at: $newDir\tomslab.db"
Write-Host ""
Write-Host "You can safely delete the now-empty $oldDir folder if you want."
Write-Host "Launch via 'Run Tom's Lab.bat' — the launcher pins TOMSLAB_DATA_DIR=D:\Toms Lab\data automatically."
