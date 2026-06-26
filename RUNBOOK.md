# Greenhouse Unit — Build Runbook

## A. Flash base OS (Raspberry Pi Imager)
- OS: **Raspberry Pi OS Lite (32-bit)** — required for Pi Zero W (ARMv6).
- Edit Settings: hostname `greenhouse`; enable SSH (password); user `pi` /
  `greenhouse2026`; Wireless LAN = a WiFi *with internet* (for apt) + country `GR`;
  locale `Europe/Athens`.
- Write, boot the Pi, wait ~1 min.

## B. Install (from the PC)
```powershell
$pi = "C:\Users\billy\Desktop\diplomatikh\pi"
ssh pi@greenhouse.local "mkdir -p /home/pi/greenhouse"
scp -r "$pi\scripts" "$pi\systemd" "$pi\portal" "$pi\mosquitto" "$pi\install.sh" pi@greenhouse.local:/home/pi/greenhouse/
ssh pi@greenhouse.local 'find /home/pi/greenhouse -type f \( -name "*.sh" -o -name "*.py" -o -name "*.service" -o -name "*.conf" \) -exec sed -i "s/\r$//" {} +; sudo bash /home/pi/greenhouse/install.sh && sudo bash /home/pi/greenhouse/scripts/selftest.sh'
```
Expect `RESULT: <n> passed, 0 failed`. install.sh randomizes the pi password
(see `/boot/firmware/INITIAL_PASSWORD.txt`); use the baked admin SSH key for
subsequent access.

## C. Make the golden image (once)
```powershell
ssh pi@greenhouse.local "sudo systemd-run --collect --unit=prep bash /home/pi/greenhouse/scripts/prep_image.sh"
```
Wait for power-off, pull the SD, read it to `greenhouse.img` (Win32DiskImager → Read).

## D. Mass-produce
Flash `greenhouse.img` to any SD (Pi Imager *Use custom* / balenaEtcher) → boot.
Each unit auto-generates its own TLS certs, MQTT password, OS password, and
`Greenhouse-XXXX` SSID on first boot. Customer joins the hotspot → setup page
auto-pops → enters home WiFi → app "Find my greenhouse" → dashboard.

## Security notes
- Per-unit: TLS CA+key, MQTT password, OS password (in `/boot`), AP SSID.
- Admin access: baked SSH key (`install.sh` `ADMIN_KEY`).
- Deferred: `/pair` has no proof-of-possession (5-min LAN window only). Add a
  PIN/QR before any public deployment.
