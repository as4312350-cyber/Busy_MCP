# BUSY MCP - Windows installer.
#
# Normally started by double-clicking install.cmd in the project folder.
# Advanced use:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 [-DataDir C:\BusyWin\DATA] [-SqlServer HOST\INSTANCE] [-Yes]
param(
    [string]$DataDir = "",
    [string]$SqlServer = "",
    [switch]$Yes
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "BUSY MCP installer" -ForegroundColor Cyan

# 1. Find Python 3.10+
$pyCmd = $null; $pyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $pyCmd = "py"; $pyArgs = @("-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $pyCmd = "python" }
else {
    Write-Host "Python was not found. Install Python 3.10+ from https://www.python.org/downloads/windows/" -ForegroundColor Red
    Write-Host "(tick 'Add python.exe to PATH'), then run this installer again." -ForegroundColor Red
    exit 1
}
$info = (& $pyCmd @pyArgs -c "import sys,struct;print('%d.%d %d' % (sys.version_info[0], sys.version_info[1], struct.calcsize('P')*8))").Trim()
$parts = $info.Split(" ")
Write-Host "Using Python $($parts[0]) ($($parts[1])-bit)"
if ([version]$parts[0] -lt [version]"3.10") {
    Write-Host "Python 3.10 or newer is required." -ForegroundColor Red
    exit 1
}

# 2. Private environment + package (nothing is installed system-wide)
if (-not (Test-Path ".venv")) { & $pyCmd @pyArgs -m venv .venv }
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip --quiet --disable-pip-version-check
& $venvPy -m pip install . --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Write-Host "Installing packages failed (see messages above)." -ForegroundColor Red; exit 1 }

# 3. Guided setup: finds BUSY, tests it, connects Claude Desktop
$setupArgs = @("-m", "busy_mcp", "setup")
if ($DataDir)   { $setupArgs += @("--data-dir", $DataDir) }
if ($SqlServer) { $setupArgs += @("--sql-server", $SqlServer) }
if ($Yes)       { $setupArgs += "--yes" }
& $venvPy @setupArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Setup did not finish. Diagnose with:  .venv\Scripts\python.exe -m busy_mcp doctor" -ForegroundColor Yellow
    exit $LASTEXITCODE
}
