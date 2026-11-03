# BUSY MCP - one-command install for Windows.
#
# Paste this into PowerShell (it needs no Python, no download by hand, no unzipping):
#
#   irm https://raw.githubusercontent.com/as4312350-cyber/Busy_MCP/main/scripts/get.ps1 | iex
#
# It downloads busy-mcp into %USERPROFILE%\Busy_MCP, then runs the installer (scripts\install.ps1), which
#   - finds or downloads a Python that suits BUSY (32-bit, nothing installed system-wide),
#   - installs busy-mcp and its dependencies,
#   - installs Microsoft's Access Database Engine only if your BUSY files need it,
#   - finds your BUSY data, tests it and connects Claude Desktop.
# Run it again any time to update: your environment and settings are kept.
#
# Options (when you save the file and run it with -File):
#   -Dir <folder>        where to put busy-mcp (default %USERPROFILE%\Busy_MCP)
#   -Branch <name>       which branch to download (default main)
#   -ZipPath <file.zip>  use this zip instead of downloading (tests)
#   -SkipSetup, -BootstrapPython   passed on to the installer
param(
    [string]$Dir = "",
    [string]$Branch = "main",
    [string]$ZipPath = "",
    [switch]$SkipSetup,
    [switch]$BootstrapPython
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Windows PowerShell 5.1 downloads slowly while it draws progress
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$Repo = "as4312350-cyber/Busy_MCP"
if (-not $Dir) { $Dir = Join-Path $env:USERPROFILE "Busy_MCP" }

Write-Host "BUSY MCP one-command install" -ForegroundColor Cyan
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("busy-mcp-get-" + [guid]::NewGuid().ToString("N"))
[void][System.IO.Directory]::CreateDirectory($work)
try {
    # 1. The project files
    $zip = $ZipPath
    if (-not $zip) {
        $zip = Join-Path $work "busy-mcp.zip"
        $url = "https://github.com/$Repo/archive/refs/heads/$Branch.zip"
        Write-Host "Downloading busy-mcp ($Branch) from GitHub..."
        try {
            $proxy = [Net.WebRequest]::DefaultWebProxy   # office proxies that ask for the Windows login
            if ($proxy) { $proxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials }
        } catch { }
        try { Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -ErrorAction Stop }
        catch { throw "The download from GitHub failed: $($_.Exception.Message). Check the internet connection (and proxy), then run the command again." }
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $unpacked = Join-Path $work "unpacked"
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $unpacked)
    # GitHub's zip holds one folder (Busy_MCP-main); a zip made by hand may hold the files directly.
    $top = @(Get-ChildItem -LiteralPath $unpacked)
    $source = if ($top.Count -eq 1 -and $top[0].PSIsContainer) { $top[0].FullName } else { $unpacked }
    if (-not (Test-Path -LiteralPath (Join-Path $source "scripts\install.ps1"))) {
        throw "The downloaded file does not look like busy-mcp (scripts\install.ps1 is missing)."
    }

    # 2. Copy over the install folder. Files that are not in the zip (.venv, .python32) stay as they are.
    Write-Host "Putting busy-mcp in $Dir"
    [void][System.IO.Directory]::CreateDirectory($Dir)
    Copy-Item -Path (Join-Path $source "*") -Destination $Dir -Recurse -Force
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

# 3. The installer does the rest. -Yes: no questions, the defaults are right for almost everyone.
$installArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Dir "scripts\install.ps1"), "-Yes")
if ($SkipSetup) { $installArgs += "-SkipSetup" }
if ($BootstrapPython) { $installArgs += "-BootstrapPython" }
& powershell @installArgs
if ($LASTEXITCODE -ne 0) {
    throw "The installer did not finish (code $LASTEXITCODE). The messages above say why; fix that and run the same command again."
}
Write-Host ""
Write-Host "Done. busy-mcp is in $Dir" -ForegroundColor Green
