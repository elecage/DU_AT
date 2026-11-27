param(
    [string]$Port = "COM10",
    [int]$Baud = 460800
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$IdfPath = if ($env:IDF_PATH) {
    $env:IDF_PATH
} else {
    Join-Path $ProjectRoot ".espressif\esp-idf-v5.4.1"
}

$EspTool = Join-Path $IdfPath "components\esptool_py\esptool\esptool.py"
$PythonCandidates = @(
    (Join-Path $ProjectRoot ".espressif\tools\python_env\idf5.4_py3.9_env\Scripts\python.exe"),
    (Join-Path $ProjectRoot ".espressif\tools\python_env\idf5.4_py3.11_env\Scripts\python.exe"),
    (Join-Path $env:USERPROFILE ".espressif\python_env\idf5.4_py3.9_env\Scripts\python.exe"),
    (Join-Path $env:USERPROFILE ".espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe")
)

$Python = $PythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $Python) {
    throw "ESP-IDF Python environment not found. Run .\tools\install_prereqs_windows.ps1 first, or export ESP-IDF before running this script."
}

if (-not (Test-Path $EspTool)) {
    throw "esptool.py not found: $EspTool"
}

Write-Host "Erasing ESP32-C3 NVS partition on $Port..."
Write-Host "This removes Wi-Fi credentials, Matter fabrics, and commissioning state."

& $Python $EspTool `
    --chip esp32c3 `
    -p $Port `
    -b $Baud `
    --before default_reset `
    --after hard_reset `
    erase_region 0x9000 0x6000

Write-Host "Done. The device should reboot and advertise as MATTER-3840."
