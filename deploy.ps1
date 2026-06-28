param(
    [string]$PiHost = "greenhouse.local"
)

$SRC = "$PSScriptRoot\pi"

Write-Host "==> Deploying to $PiHost ..."

Write-Host "==> Copying files..."
scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -r $SRC "pi@${PiHost}:/home/pi/greenhouse"
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: scp failed"; exit 1 }

Write-Host "==> Running install..."
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "pi@$PiHost" "sudo bash /home/pi/greenhouse/install.sh"
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: install failed"; exit 1 }

Write-Host "==> Running selftest..."
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "pi@$PiHost" "sudo bash /home/pi/greenhouse/scripts/selftest.sh"

Write-Host ""
Write-Host "==> Done. To test AP mode:"
Write-Host "    ssh greenhouse.local 'sudo bash /home/pi/greenhouse/scripts/reset.sh'"
