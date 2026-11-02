# BUSY MCP - Windows installer.
# Usage (from the repository folder):
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 [-DataDir C:\BusyWin\DATA] [-SqlServer localhost\SQLEXPRESS]
param(
    [string]$DataDir = "",
    [string]$SqlServer = ""
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# 1. Find Python 3.10+
$pyCmd = $null; $pyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $pyCmd = "py"; $pyArgs = @("-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $pyCmd = "python" }
else { throw "Python 3.10+ not found. Install it from https://www.python.org/downloads/windows/ (tick 'Add to PATH')." }
$info = (& $pyCmd @pyArgs -c "import sys,struct;print('%d.%d %d' % (sys.version_info[0], sys.version_info[1], struct.calcsize('P')*8))").Trim()
$parts = $info.Split(" ")
Write-Host "Using Python $($parts[0]) ($($parts[1])-bit)"
if ([version]$parts[0] -lt [version]"3.10") { throw "Python 3.10 or newer is required." }

# 2. Virtual environment + package
if (-not (Test-Path ".venv")) { & $pyCmd @pyArgs -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install . --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
$exe = Join-Path $Root ".venv\Scripts\busy-mcp.exe"

# 3. Connection check
if ($DataDir)   { $env:BUSY_DATA_DIR = $DataDir }
if ($SqlServer) { $env:BUSY_SQL_SERVER = $SqlServer }
Write-Host "`nChecking connection to BUSY data..."
& $exe --check
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Connection check failed - see README 'Troubleshooting'. The server is installed; fix the settings and re-run: $exe --check"
}

# 4. Print config for Claude Desktop
$envBlock = @{}
if ($DataDir)   { $envBlock["BUSY_DATA_DIR"] = $DataDir }
if ($SqlServer) { $envBlock["BUSY_SQL_SERVER"] = $SqlServer }
$cfg = @{ mcpServers = @{ busy = @{ command = $exe; env = $envBlock } } } | ConvertTo-Json -Depth 5
Write-Host "`nAdd this to Claude Desktop (Settings > Developer > Edit Config), then restart Claude:`n"
Write-Host $cfg
