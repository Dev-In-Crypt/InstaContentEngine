# Build the standalone Windows onedir app.
#
#   pip install pyinstaller
#   .\build_exe.ps1
#
# Produces:  dist\InstaContentEngine\  (InstaContentEngine.exe + _internal\ + backend\)
#      and:  InstaContentEngine-standalone.zip
#
# The client unzips it, edits backend\.env (adds OPENROUTER_API_KEY), and
# double-clicks InstaContentEngine.exe. No Python required.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$distApp = Join-Path $root "dist\InstaContentEngine"

# A previous run's exe (or antivirus scanning the fresh binaries) can hold the
# dist folder. Stop our process and retry the removal with backoff so PyInstaller's
# own --clean doesn't hit a lock.
Get-Process -Name InstaContentEngine -ErrorAction SilentlyContinue | Stop-Process -Force
foreach ($d in @($distApp, (Join-Path $root "build"))) {
  for ($i = 0; $i -lt 6 -and (Test-Path $d); $i++) {
    try { Remove-Item -Recurse -Force $d -ErrorAction Stop }
    catch { Write-Host "   waiting for lock on $d ..." ; Start-Sleep -Seconds 3 }
  }
}

Write-Host "==> PyInstaller build (onedir, windowed)..." -ForegroundColor Cyan
python -m PyInstaller InstaContentEngine.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$destBackend = Join-Path $distApp "backend"

Write-Host "==> Copying loose backend\ next to the exe..." -ForegroundColor Cyan
if (Test-Path $destBackend) { Remove-Item -Recurse -Force $destBackend }
# Copy backend/ but drop test/dev/runtime artifacts the client doesn't need.
$exclude = @("tests", "__pycache__", ".env", "insta.db", "insta.db-journal",
             "insta.db-wal", "insta.db-shm", "uploads", ".venv", ".pytest_cache",
             ".ruff_cache")
robocopy "$root\backend" $destBackend /E /XD ($exclude) /XF "insta.db*" ".env" | Out-Null
# robocopy exit codes 0-7 are success; 8+ is error.
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
$global:LASTEXITCODE = 0

Write-Host "==> Zipping deliverable..." -ForegroundColor Cyan
$zip = Join-Path $root "InstaContentEngine-standalone.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path $distApp -DestinationPath $zip

$sizeMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "==> Done: $zip ($sizeMB MB)" -ForegroundColor Green
Write-Host "    Client: unzip -> edit backend\.env -> double-click InstaContentEngine.exe"
