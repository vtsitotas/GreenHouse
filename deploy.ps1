param(
    [string]$PiHost = "greenhouse.local"
)

$SRC = "$PSScriptRoot\pi"

# Resolve hostname to IP via ping (Windows OpenSSH can't resolve .local via mDNS)
Write-Host "==> Resolving $PiHost ..."
$pingOut = ping -n 1 $PiHost 2>&1 | Out-String
if ($pingOut -match '\[(\d+\.\d+\.\d+\.\d+)\]') {
    $PiIP = $Matches[1]
} elseif ($pingOut -match 'Reply from (\d+\.\d+\.\d+\.\d+)') {
    $PiIP = $Matches[1]
} else {
    Write-Host "ERROR: Cannot resolve $PiHost -- is the Pi on the network?"
    exit 1
}
Write-Host "==> Resolved to $PiIP"

$target = 'pi@' + $PiIP

Write-Host "==> Copying files..."
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null $target "rm -rf /home/pi/greenhouse"
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: could not prepare remote dir"; exit 1 }
scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -r $SRC ($target + ':/home/pi/greenhouse')
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: scp failed"; exit 1 }

Write-Host "==> Running install..."
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null $target "sudo bash /home/pi/greenhouse/install.sh"
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: install failed"; exit 1 }

Write-Host "==> Running selftest..."
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null $target "sudo bash /home/pi/greenhouse/scripts/selftest.sh"

Write-Host ""
Write-Host "==> Done. Pi is at $PiIP"
Write-Host ('    To SSH: ssh ' + $target)
