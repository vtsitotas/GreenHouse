<#
.SYNOPSIS
    Greenhouse IoT -- Factory Flash Helper
.DESCRIPTION
    After flashing a fresh Pi OS with Pi Imager, run this script.
    It patches D:\user-data and D:\network-config with your bench WiFi
    so the Pi can self-provision on first boot.

.USAGE
    .\flash.ps1
    .\flash.ps1 -SSID "MyWifi" -Password "mypass"   # non-interactive
#>

param(
    [string]$SSID     = "",
    [string]$Password = "",
    [string]$Drive    = "D:"
)

$ErrorActionPreference = "Stop"

# -- Banner -------------------------------------------------------------------
Write-Host ""
Write-Host "  Greenhouse IoT -- Factory Flash Helper" -ForegroundColor Green
Write-Host "  ----------------------------------------" -ForegroundColor DarkGreen
Write-Host ""

# -- Check SD card is mounted -------------------------------------------------
if (-not (Test-Path "$Drive\cmdline.txt")) {
    Write-Host "  [ERROR] Cannot find $Drive\cmdline.txt" -ForegroundColor Red
    Write-Host "  Make sure the SD card is inserted and mounted as $Drive" -ForegroundColor Yellow
    Write-Host "  (Flash with Pi Imager first, then run this script)" -ForegroundColor Yellow
    exit 1
}

# -- Get bench WiFi credentials -----------------------------------------------
if (-not $SSID) {
    Write-Host "  Enter your BENCH WiFi credentials." -ForegroundColor Cyan
    Write-Host "  (The Pi needs internet on first boot to install packages)" -ForegroundColor DarkGray
    Write-Host ""
    $SSID = Read-Host "  WiFi SSID"
}
if (-not $Password) {
    $securePass = Read-Host "  WiFi Password" -AsSecureString
    $Password   = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                      [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePass))
}

if (-not $SSID) {
    Write-Host "  [ERROR] SSID cannot be empty." -ForegroundColor Red
    exit 1
}

# -- Locate factory files -----------------------------------------------------
$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$userData   = Join-Path $scriptDir "user-data"
$netConfig  = Join-Path $scriptDir "network-config"

if (-not (Test-Path $userData)) {
    Write-Host "  [ERROR] Missing: $userData" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $netConfig)) {
    Write-Host "  [ERROR] Missing: $netConfig" -ForegroundColor Red
    exit 1
}

# -- Write user-data ----------------------------------------------------------
Write-Host "  [1/3] Writing user-data..." -ForegroundColor Cyan
Copy-Item -Path $userData -Destination "$Drive\user-data" -Force

# -- Write network-config (with WiFi credentials substituted) -----------------
Write-Host "  [2/3] Writing network-config (SSID: $SSID)..." -ForegroundColor Cyan
$nc = Get-Content $netConfig -Raw
$nc = $nc -replace "BENCH_WIFI_SSID",     $SSID
$nc = $nc -replace "BENCH_WIFI_PASSWORD", $Password
[System.IO.File]::WriteAllText("$Drive\network-config", $nc)

# -- Update meta-data to force cloud-init re-run ------------------------------
Write-Host "  [3/3] Updating meta-data..." -ForegroundColor Cyan
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
[System.IO.File]::WriteAllText("$Drive\meta-data", "instance-id: greenhouse-factory-$timestamp`n")

# -- Done ---------------------------------------------------------------------
Write-Host ""
Write-Host "  [OK] SD card is ready!" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor White
Write-Host "  1. Eject the SD card (right-click D: -> Eject)" -ForegroundColor Gray
Write-Host "  2. Insert into Pi and power on" -ForegroundColor Gray
Write-Host "  3. Wait ~3-5 minutes (packages install, TLS certs generate)" -ForegroundColor Gray
Write-Host "  4. Green LED blinks 5x -> provisioning complete" -ForegroundColor Gray
Write-Host "  5. Pi powers off automatically" -ForegroundColor Gray
Write-Host "  6. Remove SD card, box it, ship it!" -ForegroundColor Gray
Write-Host ""
Write-Host "  The customer plugs it in -> 'Greenhouse-XXXX' WiFi appears." -ForegroundColor DarkGreen
Write-Host ""

# -- Offer to eject -----------------------------------------------------------
$eject = Read-Host "  Eject SD card now? [Y/n]"
if ($eject -ne 'n' -and $eject -ne 'N') {
    try {
        $shell = New-Object -ComObject Shell.Application
        $shell.Namespace(17).ParseName($Drive).InvokeVerb("Eject")
        Write-Host "  [OK] Ejected. Safe to remove." -ForegroundColor Green
    } catch {
        Write-Host "  [!] Could not auto-eject. Please eject manually." -ForegroundColor Yellow
    }
}
