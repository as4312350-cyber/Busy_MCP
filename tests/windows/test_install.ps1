# Checks how the installer chooses Python and how it handles its private Python, without installing or
# downloading anything. Runs in CI on Windows:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tests\windows\test_install.ps1
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\..\scripts\install.ps1")   # defines the installer's functions only

function New-Candidate([string]$Version, [int]$Bits, [string]$Path = "") {
    $v = [version]$Version
    if (-not $Path) { $Path = "Python$Version-$Bits" }
    [pscustomobject]@{
        Path    = $Path
        Version = $v
        Minor   = [version]("{0}.{1}" -f $v.Major, $v.Minor)
        Bits    = $Bits
    }
}

$script:failures = 0
function Assert-Choice([string]$Name, $Candidates, $DriverScore, [string]$Expected) {
    $chosen = Select-Python $Candidates $DriverScore
    $got = if ($chosen) { $chosen.Path } else { "(none)" }
    if ($got -eq $Expected) { Write-Host "ok    $Name" }
    else { Write-Host "FAIL  ${Name}: expected $Expected, got $got"; $script:failures++ }
}
function Assert-True([string]$Name, $Condition) {
    if ($Condition) { Write-Host "ok    $Name" }
    else { Write-Host "FAIL  $Name"; $script:failures++ }
}

$jet32 = @{ 32 = 1; 64 = 0 }   # a typical PC: only Windows' own 32-bit Jet driver
$ace64 = @{ 32 = 1; 64 = 2 }   # 64-bit Office or the x64 Access Database Engine is installed
$none = @{ 32 = 0; 64 = 0 }

Assert-Choice "skips a version without ready-made packages" `
    @((New-Candidate "3.15.2" 64), (New-Candidate "3.12.10" 64)) $none "Python3.12.10-64"
Assert-Choice "prefers the bitness of the Access driver" `
    @((New-Candidate "3.14.1" 64), (New-Candidate "3.13.9" 32)) $jet32 "Python3.13.9-32"
Assert-Choice "prefers the Access Database Engine over Jet" `
    @((New-Candidate "3.14.1" 32), (New-Candidate "3.14.1" 64)) $ace64 "Python3.14.1-64"
Assert-Choice "takes the newest when all else is equal" `
    @((New-Candidate "3.11.9" 32), (New-Candidate "3.14.1" 32)) $jet32 "Python3.14.1-32"
Assert-Choice "a version with ready-made packages beats the driver's bitness" `
    @((New-Candidate "3.15.2" 32), (New-Candidate "3.12.10" 64)) $jet32 "Python3.12.10-64"
Assert-Choice "an untested version is the last resort" `
    @(New-Candidate "3.15.2" 64) $jet32 "Python3.15.2-64"
Assert-Choice "never a version older than 3.10" `
    @(New-Candidate "3.9.13" 32) $jet32 "(none)"
Assert-Choice "nothing installed" @() $jet32 "(none)"

# The private Python (.python32): when it is offered, and that it is kept once it is there.
$ownExample = "C:\Busy_MCP\.python32\tools\python.exe"
$own = New-Candidate "3.14.10" 32 $ownExample
Assert-Choice "prefers the private Python among equals, even if older" `
    @((New-Candidate "3.14.11" 32), $own) $jet32 $ownExample
Assert-Choice "but not over the bitness of the Access driver" `
    @((New-Candidate "3.13.9" 64), $own) $ace64 "Python3.13.9-64"
Assert-Choice "and not over a version with ready-made packages" `
    @((New-Candidate "3.12.10" 32), (New-Candidate "3.15.2" 32 "D:\old\.python32\tools\python.exe")) $jet32 "Python3.12.10-32"
Assert-True "recognises the private python.exe" (Test-PrivatePython $ownExample)
Assert-True "and no other" (-not (Test-PrivatePython "C:\Python314-32\python.exe"))
Assert-True "offers it when no Python is installed" ((Get-PythonProblem $null $jet32) -ne "")
Assert-True "offers it when only Python 3.15 is installed" ((Get-PythonProblem (New-Candidate "3.15.2" 32) $jet32) -ne "")
Assert-True "offers it for 64-bit Python when the Access driver is 32-bit" `
    ((Get-PythonProblem (New-Candidate "3.14.1" 64) $jet32) -ne "")
Assert-True "not when a suitable Python is installed" ((Get-PythonProblem (New-Candidate "3.12.10" 32) $jet32) -eq "")
Assert-True "not for 64-bit Python with a 64-bit Access driver" `
    ((Get-PythonProblem (New-Candidate "3.14.1" 64) $ace64) -eq "")
Assert-True "not when there is no Access driver (SQL Server edition)" `
    ((Get-PythonProblem (New-Candidate "3.14.1" 64) $none) -eq "")

# Its download, without the network: address, pinned values, fingerprint check, unpacking, discovery.
Assert-True "download address on nuget.org" `
    ((Get-PrivatePythonUrl "3.14.10") -eq "https://api.nuget.org/v3-flatcontainer/pythonx86/3.14.10/pythonx86.3.14.10.nupkg")
$pinned = [version]$PrivateVersion
Assert-True "the pinned Python $PrivateVersion is the newest tested version, $NewestTested" `
    ($pinned.Major -eq $NewestTested.Major -and $pinned.Minor -eq $NewestTested.Minor)
Assert-True "the pinned SHA256 has 64 hex digits" ($PrivateSha256 -match '^[0-9a-fA-F]{64}$')

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("busy-mcp-test-" + [guid]::NewGuid().ToString("N"))
[void][System.IO.Directory]::CreateDirectory($tmp)
try {
    $file = Join-Path $tmp "file.bin"
    [System.IO.File]::WriteAllBytes($file, [System.Text.Encoding]::ASCII.GetBytes("busy-mcp"))
    Assert-True "accepts the right SHA256, in any case" `
        (Test-Sha256 $file "8A0909513FEBB1BF10EB94DFAA7B36FDB0BB8F3FFCA9F27B1D341560035BF0F3")
    Assert-True "refuses a wrong SHA256" (-not (Test-Sha256 $file ("0" * 64)))
    Assert-True "refuses a missing file" (-not (Test-Sha256 (Join-Path $tmp "missing.bin") ("0" * 64)))

    # A small package laid out like pythonx86 (python.exe in tools\), made here.
    $layout = Join-Path $tmp "layout"
    [void][System.IO.Directory]::CreateDirectory((Join-Path $layout "tools"))
    [System.IO.File]::WriteAllText((Join-Path $layout "tools\python.exe"), "not a real python")
    [System.IO.File]::WriteAllText((Join-Path $layout "pythonx86.nuspec"), "<package />")
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $package = Join-Path $tmp "pythonx86.nupkg"
    [System.IO.Compression.ZipFile]::CreateFromDirectory($layout, $package)
    $sha = (Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash

    $bad = Join-Path $tmp "bad"
    $refused = $false
    try { Expand-PrivatePython $package ("0" * 64) $bad } catch { $refused = $true }
    Assert-True "refuses a package whose SHA256 differs, before unpacking anything" `
        ($refused -and -not (Test-Path -LiteralPath $bad))

    $project = Join-Path $tmp "project"
    $ownPy = Join-Path $project "$PrivateDir\tools\python.exe"
    Expand-PrivatePython $package $sha (Join-Path $project $PrivateDir)
    Assert-True "unpacks python.exe into .python32\tools and removes the rest" `
        ((Test-Path -LiteralPath $ownPy) -and -not (Test-Path -LiteralPath (Join-Path $project "$PrivateDir\unpacked")))
    Assert-True "an existing .python32 is a candidate on the next run" (@(Get-PythonPaths $project) -contains $ownPy)
    Assert-True "no .python32, no such candidate" `
        (-not (@(Get-PythonPaths $tmp) -contains (Join-Path $tmp "$PrivateDir\tools\python.exe")))
    Assert-True "a .python32 whose python.exe doesn't run is not used" ($null -eq (Get-PrivatePython $project))

    $venv = Join-Path $tmp "venv"
    [void][System.IO.Directory]::CreateDirectory($venv)
    $cfgLines = [string[]]@("home = C:\Busy_MCP\.python32\tools", "include-system-site-packages = false")
    [System.IO.File]::WriteAllLines((Join-Path $venv "pyvenv.cfg"), $cfgLines)
    Assert-True "reads which Python made .venv (pyvenv.cfg)" ((Get-VenvHome $venv) -eq "C:\Busy_MCP\.python32\tools")
    Assert-True "no pyvenv.cfg, no answer" ((Get-VenvHome $tmp) -eq "")
} finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

# Probing a real interpreter and reading the driver registry must work on this machine too.
$python = (Get-Command python -CommandType Application | Select-Object -First 1).Path
$self = Get-PythonInfo $python
if (-not $self -or $self.Bits -notin 32, 64 -or $self.Minor -lt [version]"3.10") {
    Write-Host "FAIL  probing $python"; $script:failures++
} else { Write-Host "ok    probes $($self.Path): $($self.Version), $($self.Bits)-bit" }
if ($null -ne (Get-PythonInfo "C:\no\such\python.exe")) { Write-Host "FAIL  a missing python.exe"; $script:failures++ }
else { Write-Host "ok    ignores a missing python.exe" }
$score = Get-AccessDriverScore
if ($score.Keys.Count -ne 2) { Write-Host "FAIL  driver score"; $script:failures++ }
else { Write-Host "ok    Access driver: $(Format-DriverScore $score)" }

if ($script:failures) { Write-Host "$($script:failures) check(s) failed"; exit 1 }
Write-Host "All installer checks passed"
exit 0
