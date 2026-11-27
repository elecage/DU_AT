param(
    [string]$IdfVersion = "v5.4.1",
    [string]$IdfPath = "",
    [switch]$InstallApps
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if ([string]::IsNullOrWhiteSpace($IdfPath)) {
    $IdfPath = Join-Path $ProjectRoot ".espressif\esp-idf-$($IdfVersion.TrimStart('v'))"
}

function Test-Command {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Install-WingetPackage {
    param(
        [string]$Id,
        [string]$Name
    )

    if (-not (Test-Command winget)) {
        throw "winget is not available. Install $Name manually, then run this script again."
    }

    Write-Host "Installing $Name with winget..."
    winget install --id $Id --exact --source winget --accept-package-agreements --accept-source-agreements
}

function Update-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

function Test-IdfPythonVenv {
    if ([string]::IsNullOrWhiteSpace($env:IDF_PYTHON_ENV_PATH)) {
        throw "IDF_PYTHON_ENV_PATH is not set after export.ps1."
    }

    $venvPython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "ESP-IDF Python venv interpreter not found: $venvPython"
    }

    & $venvPython -c "import sys; raise SystemExit(0 if sys.prefix != getattr(sys, 'base_prefix', sys.prefix) else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "ESP-IDF Python is not running inside a virtual environment: $venvPython"
    }

    Write-Host "ESP-IDF Python venv: $env:IDF_PYTHON_ENV_PATH"
}

Write-Host "Project root: $ProjectRoot"
Write-Host "ESP-IDF path: $IdfPath"

$env:IDF_TOOLS_PATH = Join-Path $ProjectRoot ".espressif\tools"
Write-Host "ESP-IDF tools path: $env:IDF_TOOLS_PATH"

if (-not (Test-Command git)) {
    if ($InstallApps) {
        Install-WingetPackage -Id "Git.Git" -Name "Git"
        Update-ProcessPath
    } else {
        throw "Git is required. Install Git or rerun with -InstallApps."
    }
}

if (-not (Test-Command git)) {
    throw "Git was installed, but it is not available in this PowerShell session. Open a new terminal and rerun this script."
}

if (-not (Test-Command python)) {
    if ($InstallApps) {
        Install-WingetPackage -Id "Python.Python.3.11" -Name "Python 3.11"
        Update-ProcessPath
    } else {
        throw "Python is required. Install Python 3 or rerun with -InstallApps."
    }
}

if (-not (Test-Command python)) {
    throw "Python was installed, but it is not available in this PowerShell session. Open a new terminal and rerun this script."
}

if (-not (Test-Path $IdfPath)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $IdfPath) | Out-Null
    Write-Host "Cloning ESP-IDF $IdfVersion..."
    git clone --branch $IdfVersion --recursive https://github.com/espressif/esp-idf.git $IdfPath
} else {
    Write-Host "ESP-IDF directory already exists. Skipping clone."
}

Write-Host "Installing ESP-IDF tools for ESP32-C3..."
& (Join-Path $IdfPath "install.ps1") esp32c3

Write-Host "Exporting ESP-IDF environment and setting project target..."
& (Join-Path $IdfPath "export.ps1")
Test-IdfPythonVenv
idf.py set-target esp32c3

Write-Host "Resolving project managed components..."
idf.py reconfigure

Write-Host ""
Write-Host "Prerequisites are ready."
Write-Host "For a new shell, run:"
Write-Host "  `$env:IDF_TOOLS_PATH = `"$env:IDF_TOOLS_PATH`""
Write-Host "  . `"$IdfPath\export.ps1`""
Write-Host "Then build with:"
Write-Host "  idf.py build"
