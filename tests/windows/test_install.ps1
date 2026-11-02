# Checks how the installer chooses Python, without installing anything. Runs in CI on Windows:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tests\windows\test_install.ps1
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\..\scripts\install.ps1")   # defines the installer's functions only

function New-Candidate([string]$Version, [int]$Bits) {
    $v = [version]$Version
    [pscustomobject]@{
        Path    = "Python$Version-$Bits"
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
