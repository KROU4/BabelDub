<#
.SYNOPSIS
    Install the BabelDub CLI (babeldub) with uv and check the environment.

.DESCRIPTION
    1. Checks that uv is available. Without uv the script prints the official install command and
       stops; pass -InstallUv to run that installer for you.
    2. Installs the CLI with `uv tool install`: from the local runtime/ folder when the script runs
       from a BabelDub checkout or kit, otherwise from GitHub (git+https://...#subdirectory=runtime).
    3. Runs `babeldub doctor`. Exit code 4 from doctor only means that engines and models are not
       set up yet (`babeldub setup` does that); the installation itself succeeded.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -FromGit -Ref v0.1.0 -DryRun
#>
[CmdletBinding()]
param(
    # Git ref (tag or branch) used when installing from GitHub; env BABELDUB_REF overrides the default.
    [string]$Ref = $(if ($env:BABELDUB_REF) { $env:BABELDUB_REF } else { "main" }),
    # Repository used when installing from GitHub.
    [string]$Repo = "https://github.com/KROU4/BabelDub",
    # Install from GitHub even if a local runtime/ folder is present.
    [switch]$FromGit,
    # Install uv with the official installer when it is missing.
    [switch]$InstallUv,
    # Python version for the tool environment (uv downloads it when needed).
    [string]$Python = "3.12",
    # Do not run `babeldub doctor` at the end.
    [switch]$SkipDoctor,
    # Print the commands without running them.
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$UvInstallCommand = 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    Write-Host ("    $Exe " + ($Arguments -join " "))
    if ($DryRun) { return 0 }
    & $Exe @Arguments | Out-Host
    return $LASTEXITCODE
}

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
            (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
            (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"))) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Find-LocalRuntime {
    if (-not $PSScriptRoot) { return $null }
    foreach ($candidate in @((Join-Path $PSScriptRoot "runtime"), (Join-Path $PSScriptRoot "..\runtime"))) {
        $pyproject = Join-Path $candidate "pyproject.toml"
        if ((Test-Path $pyproject) -and (Select-String -Path $pyproject -Pattern '^name = "babeldub"' -Quiet)) {
            return (Resolve-Path $candidate).Path
        }
    }
    return $null
}

# 1. uv
Write-Step "Checking uv"
$uv = Find-Uv
if (-not $uv) {
    if (-not $InstallUv) {
        Write-Host "uv is not installed. Install it with:" -ForegroundColor Yellow
        Write-Host "    $UvInstallCommand"
        Write-Host "or run this script again with -InstallUv. Docs: https://docs.astral.sh/uv/"
        exit 1
    }
    Write-Step "Installing uv with the official installer"
    if (-not $DryRun) {
        powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
        $env:PATH = (Join-Path $env:USERPROFILE ".local\bin") + ";" + $env:PATH
        $uv = Find-Uv
        if (-not $uv) {
            Write-Host "uv was installed but is not on PATH yet. Open a new terminal and run this script again." -ForegroundColor Yellow
            exit 1
        }
    } else {
        Write-Host "    $UvInstallCommand"
        $uv = "uv"
    }
}
if (-not $DryRun) {
    Write-Host ("    " + (& $uv --version))
}

# 2. babeldub CLI
$runtime = $null
if (-not $FromGit) { $runtime = Find-LocalRuntime }
if ($runtime) {
    Write-Step "Installing babeldub from $runtime"
    $source = $runtime
} else {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "git is required to install from GitHub (https://git-scm.com/download/win)." -ForegroundColor Yellow
        if (-not $DryRun) { exit 1 }
    }
    $source = "git+$Repo@$Ref#subdirectory=runtime"
    Write-Step "Installing babeldub from $source"
}
$code = Invoke-Native $uv @("tool", "install", "--force", "--reinstall-package", "babeldub", "--python", $Python, $source)
if ($code -ne 0) {
    Write-Host "uv tool install failed with exit code $code" -ForegroundColor Red
    exit 1
}

# 3. PATH and doctor
$binDir = if ($DryRun) { Join-Path $env:USERPROFILE ".local\bin" } else { (& $uv tool dir --bin).Trim() }
$exe = Join-Path $binDir "babeldub.exe"
if (-not $DryRun -and -not (Test-Path $exe)) {
    $found = Get-Command babeldub -ErrorAction SilentlyContinue
    if (-not $found) {
        Write-Host "babeldub.exe was not found in $binDir after installation." -ForegroundColor Red
        exit 1
    }
    $exe = $found.Source
}
$onPath = ($env:PATH -split ";") | Where-Object { $_ -and ($_.TrimEnd("\") -ieq $binDir.TrimEnd("\")) }
if (-not $onPath) {
    Write-Host "Note: $binDir is not on PATH. Run 'uv tool update-shell' and open a new terminal." -ForegroundColor Yellow
}

if ($SkipDoctor) {
    Write-Step "Done. Next: babeldub doctor"
    exit 0
}
Write-Step "Running babeldub doctor"
$code = Invoke-Native $exe @("doctor", "--pretty")
switch ($code) {
    0 { Write-Step "Done: environment is ready" }
    3 { Write-Step "Done: doctor reports items that need attention (see above)" }
    4 { Write-Step "Done: the CLI is installed; engines/models are not set up yet. Next: babeldub setup (or ask your agent)" }
    default {
        Write-Host "babeldub doctor failed with exit code $code" -ForegroundColor Red
        exit 1
    }
}
exit 0
