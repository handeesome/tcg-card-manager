param(
  [int]$Port = 8080,
  [string]$CaptureDir = "",
  [switch]$KeepRawData,
  [switch]$Web
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
if (-not $CaptureDir) {
  $CaptureDir = Join-Path $scriptDir "captured_requests"
}

$env:JHS_CAPTURE_DIR = $CaptureDir
if ($KeepRawData) {
  $env:JHS_KEEP_RAW_DATA = "1"
} else {
  Remove-Item Env:\JHS_KEEP_RAW_DATA -ErrorAction SilentlyContinue
}

$addon = Join-Path $scriptDir "jihuanshe_mitm_addon.py"
$mitm = if ($Web) { "mitmweb" } else { "mitmdump" }

Write-Host "Starting $mitm for JiHuanShe capture..."
Write-Host "Listen: 0.0.0.0:$Port"
Write-Host "Output: $CaptureDir"
Write-Host "Install the mitmproxy CA certificate on the phone, then set Wi-Fi proxy to this PC:$Port."

& $mitm -s $addon --listen-host 0.0.0.0 --listen-port $Port --set block_global=false
