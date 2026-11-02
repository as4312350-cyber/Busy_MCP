# BUSY MCP - Windows installer.
#
# Normally started by double-clicking install.cmd in the project folder.
# Advanced use:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 [-DataDir C:\BusyWin\DATA] [-SqlServer HOST\INSTANCE]
#              [-Python C:\path\to\python.exe] [-Yes] [-SkipSetup]
#
# Which Python is used, when several are installed:
#   1. a version for which every dependency has ready-made Windows packages (3.10 to 3.14 today);
#   2. the bitness (32/64) of the Microsoft Access driver on this PC, because BUSY's data needs a matching one;
#   3. the newest version.
# Running the installer again with a different Python rebuilds the private environment (.venv).
param(
    [string]$DataDir = "",
    [string]$SqlServer = "",
    [string]$Python = "",   # use this python.exe instead of choosing one automatically
    [switch]$Yes,
    [switch]$SkipSetup      # install only; run "python -m busy_mcp setup" later (used by CI)
)
$ErrorActionPreference = "Stop"

$MinPython = [version]"3.10"
$NewestTested = [version]"3.14"   # newest version with ready-made packages for all dependencies; tested in CI
$Recommended = "Python 3.14 (32-bit)"
$Download = "https://www.python.org/downloads/windows/"

function Get-PythonInfo([string]$Exe) {
    # Version and bitness of a python.exe, or $null if it doesn't run (e.g. the Microsoft Store placeholder).
    $ErrorActionPreference = "Continue"
    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe -PathType Leaf)) { return $null }
    $code = "import sys,struct,sysconfig;print('%d.%d.%d %d %d' % (sys.version_info[:3] + " +
            "(struct.calcsize('P')*8, bool(sysconfig.get_config_var('Py_GIL_DISABLED')))))"
    try { $out = @("" | & $Exe -c $code 2>$null) } catch { return $null }
    if ($LASTEXITCODE -ne 0 -or $out.Count -eq 0) { return $null }
    $f = "$($out[-1])".Trim().Split(" ")
    if ($f.Count -ne 3 -or $f[2] -ne "0") { return $null }   # free-threaded builds have no ready-made packages
    $v = [version]($f[0])
    [pscustomobject]@{
        Path    = (Resolve-Path -LiteralPath $Exe).Path
        Version = $v
        Minor   = [version]("{0}.{1}" -f $v.Major, $v.Minor)
        Bits    = [int]($f[1])
    }
}

function Get-InstalledPythons {
    # Every Python on this PC: registered installs (python.org, per user and for all users, 32- and 64-bit),
    # anything on PATH, and the usual install folders. The py launcher isn't used: it may stop to ask questions.
    $ErrorActionPreference = "Continue"
    $paths = New-Object System.Collections.Generic.List[string]
    foreach ($hive in "CurrentUser", "LocalMachine") {
        foreach ($view in "Registry64", "Registry32") {
            try {
                $core = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view).OpenSubKey("Software\Python\PythonCore")
                if (-not $core) { continue }
                foreach ($tag in $core.GetSubKeyNames()) {
                    $key = $core.OpenSubKey("$tag\InstallPath")
                    if (-not $key) { continue }
                    $exe = $key.GetValue("ExecutablePath")
                    if (-not $exe -and $key.GetValue("")) { $exe = Join-Path $key.GetValue("") "python.exe" }
                    if ($exe) { $paths.Add([string]$exe) }
                }
            } catch { }
        }
    }
    foreach ($cmd in @(Get-Command python, python3 -CommandType Application -All -ErrorAction SilentlyContinue)) {
        $paths.Add($cmd.Path)
    }
    foreach ($pattern in "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
                         "$env:LOCALAPPDATA\Python\pythoncore-3*\python.exe",
                         "$env:ProgramFiles\Python3*\python.exe",
                         "${env:ProgramFiles(x86)}\Python3*\python.exe",
                         "$env:SystemDrive\Python3*\python.exe") {
        foreach ($item in @(Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue)) { $paths.Add($item.FullName) }
    }
    $seen = @{}
    foreach ($p in $paths) {
        if ($seen.ContainsKey($p.ToLowerInvariant())) { continue }
        $seen[$p.ToLowerInvariant()] = $true
        $info = Get-PythonInfo $p
        if ($info -and -not $seen.ContainsKey("resolved:" + $info.Path.ToLowerInvariant())) {
            $seen["resolved:" + $info.Path.ToLowerInvariant()] = $true
            $info
        }
    }
}

function Get-AccessDriverScore {
    # Per bitness: 2 = Access Database Engine (*.mdb, *.accdb), 1 = older Jet driver (*.mdb), 0 = none.
    $score = @{ 32 = 0; 64 = 0 }
    $views = @(, @("Registry32", 32))   # the leading comma keeps the pair as one element
    if ([Environment]::Is64BitOperatingSystem) { $views += , @("Registry64", 64) }
    foreach ($v in $views) {
        try {
            $key = [Microsoft.Win32.RegistryKey]::OpenBaseKey("LocalMachine", $v[0]).OpenSubKey(
                "SOFTWARE\ODBC\ODBCINST.INI\ODBC Drivers")
            if (-not $key) { continue }
            foreach ($name in $key.GetValueNames()) {
                if ($name -like "*Access Driver*") {
                    $s = if ($name -like "*accdb*") { 2 } else { 1 }
                    if ($s -gt $score[$v[1]]) { $score[$v[1]] = $s }
                }
            }
        } catch { }
    }
    $score
}

function Select-Python($Candidates, $DriverScore) {
    # The Python to use, by the rules at the top of this file; $null if none is new enough.
    $order = @(
        @{ Expression = { $_.Minor -le $NewestTested } },
        @{ Expression = { $DriverScore[$_.Bits] } },
        @{ Expression = { $_.Version } }
    )
    @($Candidates | Where-Object { $_.Minor -ge $MinPython } | Sort-Object -Property $order -Descending) |
        Select-Object -First 1
}

function Format-DriverScore($DriverScore) {
    $names = @{ 0 = "none"; 1 = "Jet, *.mdb"; 2 = "Access Database Engine" }
    "32-bit: $($names[$DriverScore[32]]); 64-bit: $($names[$DriverScore[64]])"
}

# Dot-sourced by tests\windows\test_install.ps1: define the functions above, install nothing.
if ($MyInvocation.InvocationName -eq ".") { return }

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "BUSY MCP installer" -ForegroundColor Cyan

# 1. Choose Python
$driverScore = Get-AccessDriverScore
Write-Host "Microsoft Access driver on this PC: $(Format-DriverScore $driverScore)"
if ($Python) {
    $chosen = Get-PythonInfo $Python
    if (-not $chosen) { Write-Host "'$Python' is not a working Python." -ForegroundColor Red; exit 1 }
    if ($chosen.Minor -lt $MinPython) {
        Write-Host "Python $MinPython or newer is required; '$Python' is $($chosen.Version)." -ForegroundColor Red
        exit 1
    }
} else {
    $ownVenv = Join-Path $Root ".venv"
    $installed = @(Get-InstalledPythons | Where-Object { -not $_.Path.StartsWith($ownVenv, [StringComparison]::OrdinalIgnoreCase) })
    $chosen = Select-Python $installed $driverScore
    if (-not $chosen) {
        Write-Host "Python $MinPython or newer was not found." -ForegroundColor Red
        Write-Host "Install $Recommended from $Download (tick 'Add python.exe to PATH')," -ForegroundColor Red
        Write-Host "then run this installer again." -ForegroundColor Red
        exit 1
    }
}
Write-Host "Using Python $($chosen.Version) ($($chosen.Bits)-bit): $($chosen.Path)"
if ($chosen.Minor -gt $NewestTested) {
    Write-Host "Note: Python $($chosen.Minor) is newer than the versions busy-mcp is tested with ($MinPython to $NewestTested)." -ForegroundColor Yellow
}
$otherBits = if ($chosen.Bits -eq 32) { 64 } else { 32 }
if ($driverScore[$chosen.Bits] -eq 0 -and $driverScore[$otherBits] -gt 0) {
    Write-Host "Note: the Access driver on this PC is $otherBits-bit only, so this $($chosen.Bits)-bit Python cannot read the data" -ForegroundColor Yellow
    Write-Host "of BUSY's standard (Access) edition. Install Python $NewestTested ($otherBits-bit) from $Download" -ForegroundColor Yellow
    Write-Host "and run install.cmd again. If you use BUSY's SQL Server edition, you can ignore this." -ForegroundColor Yellow
}

# 2. Private environment (nothing is installed system-wide), rebuilt when a different Python was chosen
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath ".venv") {
    $current = Get-PythonInfo $venvPy
    if (-not $current -or $current.Minor -ne $chosen.Minor -or $current.Bits -ne $chosen.Bits) {
        $was = if ($current) { "Python $($current.Minor) ($($current.Bits)-bit)" } else { "a Python that no longer works" }
        Write-Host "Rebuilding the private environment (.venv), which was made with $was."
        try { Remove-Item -LiteralPath ".venv" -Recurse -Force }
        catch {
            Write-Host "The old environment is in use. Quit Claude Desktop (also from the system tray) and any other" -ForegroundColor Red
            Write-Host "AI app that uses BUSY, then run install.cmd again." -ForegroundColor Red
            exit 1
        }
    }
}
if (-not (Test-Path -LiteralPath ".venv")) {
    $basePy = $chosen.Path
    & $basePy -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host "Creating the Python environment failed (see messages above)." -ForegroundColor Red; exit 1 }
}

# 3. busy-mcp and its dependencies, from ready-made packages only: a normal PC has no compiler to build them
& $venvPy -m pip install --upgrade pip --quiet --disable-pip-version-check
& $venvPy -m pip install . "--only-binary=:all:" --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing packages failed (see messages above)." -ForegroundColor Red
    if ($chosen.Minor -gt $NewestTested) {
        Write-Host "Python $($chosen.Minor) is too new: a required package (pyodbc) has no ready-made Windows version for it yet." -ForegroundColor Red
        Write-Host "Install $Recommended from $Download and run install.cmd again." -ForegroundColor Red
    } else {
        Write-Host "Check the internet connection (and proxy settings), then run install.cmd again." -ForegroundColor Red
    }
    exit 1
}

if ($SkipSetup) {
    Write-Host "Installed. Next step:  .venv\Scripts\python.exe -m busy_mcp setup" -ForegroundColor Green
    exit 0
}

# 4. Guided setup: finds BUSY, tests it, connects Claude Desktop
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
