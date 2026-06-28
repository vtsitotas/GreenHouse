param(
    [string]$Host = "greenhouse.local"
)

$SRC = "$PSScriptRoot\pi"
$DEST = "pi@${Host}:/home/pi/greenhouse"

Write-Host "==> Deploying to $Host ..."

Write-Host "==> Copying files..."
scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -r $SRC "pi@${Host}:/home/pi/greenhouse"
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: scp failed"; exit 1 }

Write-Host "==> Running install..."
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "pi@$Host" "sudo bash /home/pi/greenhouse/install.sh"
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: install failed"; exit 1 }

Write-Host "==> Running selftest..."
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "pi@$Host" "sudo bash /home/pi/greenhouse/scripts/selftest.sh"

Write-Host ""
Write-Host "==> Done. To test AP mode:"
Write-Host "    ssh pi@$Host 'sudo bash /home/pi/greenhouse/scripts/reset.sh'"
