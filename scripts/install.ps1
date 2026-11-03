# BUSY MCP - Windows installer.
#
# Normally started by double-clicking install.cmd in the project folder.
# Advanced use:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 [-DataDir C:\BusyWin\DATA] [-SqlServer HOST\INSTANCE]
#              [-Python C:\path\to\python.exe | -BootstrapPython] [-Yes] [-SkipSetup]
#
# Which Python is used, when several are installed:
#   1. a version for which every dependency has ready-made Windows packages (3.10 to 3.14 today);
#   2. the bitness (32/64) of the Microsoft Access driver on this PC, because BUSY's data needs a matching one;
#   3. the private Python in .python32 (see below), once the installer has downloaded it;
#   4. the newest version.
# When that leaves no Python that can work (none at all, only Python 3.15 or newer, or only 64-bit Python while
# the Access driver is 32-bit), the installer offers to download the private Python and then uses it.
# -BootstrapPython uses the private Python whatever else is installed, downloading it if needed.
# Running the installer again with a different Python rebuilds the private environment (.venv).
param(
    [string]$DataDir = "",
    [string]$SqlServer = "",
    [string]$Python = "",       # use this python.exe instead of choosing one automatically
    [switch]$BootstrapPython,   # use the private Python in .python32, downloading it if needed
    [switch]$Yes,               # accept the defaults: download without asking, and "--yes" for setup
    [switch]$SkipSetup          # install only; run "python -m busy_mcp setup" later (used by CI)
)
$ErrorActionPreference = "Stop"

$MinPython = [version]"3.10"
$NewestTested = [version]"3.14"   # newest version with ready-made packages for all dependencies; tested in CI
$Download = "https://www.python.org/downloads/windows/"

# The private Python: the official 32-bit CPython that the Python Software Foundation publishes on nuget.org as
# the package "pythonx86", for build systems (CPython's Windows documentation links to it). It is unpacked into
# .python32 in the project folder, with python.exe in .python32\tools. Nothing is installed on the PC itself.
# To update it:
#   1. take the newest 3.14.x listed at https://api.nuget.org/v3-flatcontainer/pythonx86/index.json
#   2. download https://api.nuget.org/v3-flatcontainer/pythonx86/<version>/pythonx86.<version>.nupkg
#   3. set the version, the file's SHA256 (PowerShell: Get-FileHash <file>; Linux: sha256sum <file>) and its
#      size in MB below. CI job "installer-bootstrap" then downloads and checks it on Windows.
$PrivateVersion = "3.14.10"
$PrivateSha256 = "fd47b2d9fa6a5585c36ac87b65e172461fc66ee57b3b41fe2d96a3078433c9f6"
$PrivateSizeMB = 14
$PrivateDir = ".python32"

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

function Get-PythonPaths([string]$Root = "") {
    # Where Python may be: the private one in <Root>\.python32, registered installs (python.org, per user and for
    # all users, 32- and 64-bit), anything on PATH, and the usual install folders, including the Python install
    # manager's. The py launcher isn't used: it may stop to ask questions.
    $ErrorActionPreference = "Continue"
    $paths = New-Object System.Collections.Generic.List[string]
    if ($Root) {
        $ownPy = Join-Path $Root "$PrivateDir\tools\python.exe"
        if (Test-Path -LiteralPath $ownPy -PathType Leaf) { $paths.Add($ownPy) }
    }
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
    $paths
}

function Get-InstalledPythons([string]$Root = "") {
    # Every working Python that Get-PythonPaths finds, once each (see Get-PythonInfo).
    $ErrorActionPreference = "Continue"
    $seen = @{}
    foreach ($p in @(Get-PythonPaths $Root)) {
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

function Test-PrivatePython([string]$Path) {
    # True for the python.exe of a private Python (<project>\.python32\tools\python.exe).
    $Path -like "*\$PrivateDir\tools\python.exe"
}

function Select-Python($Candidates, $DriverScore) {
    # The Python to use, by the rules at the top of this file; $null if none is new enough.
    $order = @(
        @{ Expression = { $_.Minor -le $NewestTested } },
        @{ Expression = { $DriverScore[$_.Bits] } },
        @{ Expression = { Test-PrivatePython $_.Path } },
        @{ Expression = { $_.Version } }
    )
    @($Candidates | Where-Object { $_.Minor -ge $MinPython } | Sort-Object -Property $order -Descending) |
        Select-Object -First 1
}

function Format-DriverScore($DriverScore) {
    $names = @{ 0 = "none"; 1 = "Jet, *.mdb"; 2 = "Access Database Engine" }
    "32-bit: $($names[$DriverScore[32]]); 64-bit: $($names[$DriverScore[64]])"
}

function Get-PythonProblem($Chosen, $DriverScore) {
    # In one sentence, why the Python that Select-Python chose can't work with BUSY, when the private 32-bit
    # Python would fix it; "" otherwise.
    if (-not $Chosen) { return "No Python $MinPython to $NewestTested was found on this PC." }
    if ($Chosen.Minor -gt $NewestTested) {
        return "Python $($Chosen.Minor) is too new: pyodbc, the database driver, has no ready-made Windows package for it yet."
    }
    if ($Chosen.Bits -eq 64 -and $DriverScore[64] -eq 0 -and $DriverScore[32] -gt 0) {
        return "This PC's Access driver is 32-bit, so 64-bit Python $($Chosen.Minor) cannot read BUSY's standard (Access) data."
    }
    return ""
}

function Get-VenvHome([string]$Venv) {
    # The folder of the Python that a virtual environment was made with ("home" in its pyvenv.cfg), or "".
    $cfg = Join-Path $Venv "pyvenv.cfg"
    if (-not (Test-Path -LiteralPath $cfg -PathType Leaf)) { return "" }
    foreach ($line in @(Get-Content -LiteralPath $cfg -Encoding UTF8)) {
        if ($line -match '^\s*home\s*=\s*(.*?)\s*$') { return $Matches[1] }
    }
    return ""
}

function Get-PrivatePython([string]$Root) {
    # The private Python in <Root>\.python32 if it runs, is 32-bit and is a tested version; otherwise nothing.
    $info = Get-PythonInfo (Join-Path $Root "$PrivateDir\tools\python.exe")
    if ($info -and $info.Bits -eq 32 -and $info.Minor -ge $MinPython -and $info.Minor -le $NewestTested) { $info }
}

function Get-PrivatePythonUrl([string]$Version) {
    # Where nuget.org serves the package (its "flat container" wants the id and the version in lower case).
    $v = $Version.ToLowerInvariant()
    "https://api.nuget.org/v3-flatcontainer/pythonx86/$v/pythonx86.$v.nupkg"
}

function Test-Sha256([string]$Path, [string]$Expected) {
    # True only if the file exists and its SHA256 is $Expected (hex digits, any case).
    if (-not $Expected -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    return ($actual -eq $Expected.Trim())
}

function Save-Download([string]$Url, [string]$OutFile) {
    # Downloads $Url to $OutFile with Windows' proxy settings; throws on failure.
    $ProgressPreference = "SilentlyContinue"   # Windows PowerShell 5.1 downloads slowly while it draws progress
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }   # 5.1 may not offer it
    try {
        $proxy = [Net.WebRequest]::DefaultWebProxy   # office proxies that ask for the Windows login
        if ($proxy) { $proxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials }
    } catch { }
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing -ErrorAction Stop
}

function Expand-PrivatePython([string]$Package, [string]$Sha256, [string]$Destination) {
    # Unpacks the Python of a downloaded pythonx86 package (its "tools" folder) into $Destination\tools. Refuses a
    # file whose SHA256 isn't $Sha256 before unpacking anything. tools\python.exe appears only when all is there.
    if (-not (Test-Sha256 $Package $Sha256)) {
        throw "The downloaded file is not the expected one (its SHA256 fingerprint differs), so it was not used."
    }
    $unpacked = Join-Path $Destination "unpacked"
    $tools = Join-Path $Destination "tools"
    try {
        Add-Type -AssemblyName System.IO.Compression
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        foreach ($old in $unpacked, $tools) {
            if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
        }
        [System.IO.Compression.ZipFile]::ExtractToDirectory($Package, $unpacked)
        [System.IO.Directory]::Move((Join-Path $unpacked "tools"), $tools)
    } catch {
        throw "Unpacking the download into $Destination failed: $($_.Exception.Message)"
    }
    Remove-Item -LiteralPath $unpacked -Recurse -Force -ErrorAction SilentlyContinue   # package metadata only
}

function Install-PrivatePython([string]$Root) {
    # Downloads the pinned private Python into <Root>\.python32 and returns it (see Get-PythonInfo). If that
    # fails, says why and returns nothing. Nothing is written outside the project folder.
    $dir = Join-Path $Root $PrivateDir
    try {
        if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }   # left by a failed try
        [void][System.IO.Directory]::CreateDirectory($dir)
    } catch {
        Write-Host "The folder $PrivateDir is in use. Quit Claude Desktop (also from the system tray) and any other" -ForegroundColor Red
        Write-Host "AI app that uses BUSY, then run install.cmd again." -ForegroundColor Red
        return
    }
    $package = Join-Path $dir "pythonx86.$PrivateVersion.nupkg"
    Write-Host "Downloading Python $PrivateVersion (32-bit) from nuget.org, where the Python Software Foundation publishes it..."
    try { Save-Download (Get-PrivatePythonUrl $PrivateVersion) $package }
    catch {
        Write-Host "The download failed: $($_.Exception.Message)" -ForegroundColor Red
        Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
        return
    }
    Write-Host "Checking the download and unpacking it into $dir"
    try { Expand-PrivatePython $package $PrivateSha256 $dir }
    catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
        Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
        return
    }
    Remove-Item -LiteralPath $package -Force -ErrorAction SilentlyContinue
    $info = Get-PrivatePython $Root
    if (-not $info) { Write-Host "The downloaded Python does not run on this PC." -ForegroundColor Red; return }
    $info
}

function Confirm-Download([string]$Question) {
    # Asks before downloading; Enter means yes. -Yes answers yes. In CI, or when no one can type an answer
    # (input redirected), the answer is no.
    if ($Yes) { return $true }
    $redirected = $true
    try { $redirected = [Console]::IsInputRedirected } catch { }
    if ($env:CI -or $redirected) {
        Write-Host "$Question Not without -Yes, because no one can answer here."
        return $false
    }
    $answer = Read-Host "$Question [Y/n]"
    return ("$answer".Trim() -notmatch "^n")
}

function Write-PythonHelp {
    # How to install Python by hand, in python.org's words (README.md, "Install", says the same).
    $help = @(
        "To install Python yourself: any Python $MinPython to $NewestTested works, and 32-bit is best because",
        "BUSY's Access driver is 32-bit. On $Download either",
        "  - get the Python install manager, then run in a Command Prompt:  py install $($NewestTested)-32",
        "  - or, under a Python $NewestTested release, click ""Windows installer (32-bit)"".",
        "Then run install.cmd again."
    )
    foreach ($line in $help) { Write-Host $line -ForegroundColor Yellow }
}

# Dot-sourced by tests\windows\test_install.ps1: define the functions above, install nothing.
if ($MyInvocation.InvocationName -eq ".") { return }

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "BUSY MCP installer" -ForegroundColor Cyan

# 1. Choose Python
$driverScore = Get-AccessDriverScore
Write-Host "Microsoft Access driver on this PC: $(Format-DriverScore $driverScore)"
if ($Python -and $BootstrapPython) { Write-Host "Use -Python or -BootstrapPython, not both." -ForegroundColor Red; exit 1 }
if ($Python) {
    $chosen = Get-PythonInfo $Python
    if (-not $chosen) { Write-Host "'$Python' is not a working Python." -ForegroundColor Red; exit 1 }
    if ($chosen.Minor -lt $MinPython) {
        Write-Host "Python $MinPython or newer is required; '$Python' is $($chosen.Version)." -ForegroundColor Red
        exit 1
    }
} else {
    $ownVenv = Join-Path $Root ".venv"
    $installed = @(Get-InstalledPythons $Root | Where-Object { -not $_.Path.StartsWith($ownVenv, [StringComparison]::OrdinalIgnoreCase) })
    $chosen = Select-Python $installed $driverScore
    $problem = Get-PythonProblem $chosen $driverScore
    if ($BootstrapPython -or $problem) {
        # The private 32-bit Python fixes it: the one downloaded earlier, or offer to download it now.
        $own = Get-PrivatePython $Root
        if (-not $own) {
            if ($problem) { Write-Host $problem -ForegroundColor Yellow }
            else { Write-Host "-BootstrapPython: busy-mcp will use its own copy of the official 32-bit Python." }
            if (Confirm-Download "Download the official 32-bit Python $PrivateVersion (about $PrivateSizeMB MB) into this folder?") {
                $own = Install-PrivatePython $Root
                if (-not $own) { Write-PythonHelp; exit 1 }
            }
        }
        if ($own) { $chosen = $own }
        elseif (-not $chosen) { Write-PythonHelp; exit 1 }
    }
}
Write-Host "Using Python $($chosen.Version) ($($chosen.Bits)-bit): $($chosen.Path)"
if ($chosen.Minor -gt $NewestTested) {
    Write-Host "Note: Python $($chosen.Minor) is newer than the versions busy-mcp is tested with ($MinPython to $NewestTested)." -ForegroundColor Yellow
}
$otherBits = if ($chosen.Bits -eq 32) { 64 } else { 32 }
if ($driverScore[$chosen.Bits] -eq 0 -and $driverScore[$otherBits] -gt 0) {
    Write-Host "Note: the Access driver on this PC is $otherBits-bit only, so this $($chosen.Bits)-bit Python cannot read the data" -ForegroundColor Yellow
    if ($otherBits -eq 32) {
        Write-Host "of BUSY's standard (Access) edition. Run install.cmd again and answer Y to get the 32-bit Python." -ForegroundColor Yellow
    } else {
        Write-Host "of BUSY's standard (Access) edition. Install Python $NewestTested (64-bit) from $Download and run install.cmd again." -ForegroundColor Yellow
    }
    Write-Host "If you use BUSY's SQL Server edition, you can ignore this." -ForegroundColor Yellow
}

# 2. Private environment (nothing is installed system-wide), rebuilt when a different Python was chosen
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath ".venv") {
    $current = Get-PythonInfo $venvPy
    $venvHome = Get-VenvHome (Join-Path $Root ".venv")
    $fromOwn = [bool]$venvHome -and (Test-PrivatePython "$venvHome\python.exe")
    if (-not $current -or $current.Minor -ne $chosen.Minor -or $current.Bits -ne $chosen.Bits -or
            $fromOwn -ne (Test-PrivatePython $chosen.Path)) {
        if (-not $current) { $was = "a Python that no longer works" }
        elseif ($fromOwn) { $was = "the downloaded Python in $PrivateDir" }
        else { $was = "Python $($current.Minor) ($($current.Bits)-bit) from $venvHome" }
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
        Write-Host "Run install.cmd again and answer Y to download the official 32-bit Python $PrivateVersion into this folder." -ForegroundColor Red
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
