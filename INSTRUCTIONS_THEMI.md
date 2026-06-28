# Greenhouse — Dev Instructions

## Terminal cheat sheet

| Prompt | You are on… |
|--------|-------------|
| `PS C:\Users\themi>` | Your PC |
| `pi@greenhouse:~ $` | Inside the Pi (after `ssh greenhouse.local`) |

---

## Set up a new Pi from scratch

### 1. Flash Pi OS Lite

Open **Raspberry Pi Imager** and in the ⚙️ settings set:
- OS: **Raspberry Pi OS Lite (64-bit)**
- Hostname: `greenhouse`
- Username: `pi` / Password: *(whatever you want — it won't change)*
- WiFi: your network + Country: GR
- SSH: ✅ enabled

Write to SD, insert in Pi, power on, wait ~45 seconds.

### 2. Deploy everything (one command from PC)

```powershell
cd C:\Users\themi\Documents\GreenHouse
powershell -ExecutionPolicy Bypass -File deploy.ps1
```

Copies all files, runs `install.sh`, runs selftest.
Success = **`RESULT: 18 passed, 0 failed`**

### 3. Test the AP setup flow

```powershell
ssh greenhouse.local "sudo bash /home/pi/greenhouse/scripts/reset.sh"
```

Pi reboots into AP mode. Connect your phone to `Greenhouse-XXXX` → browser opens automatically (or go to `192.168.4.1` manually) → enter home WiFi → Pi reboots and connects.

### 4. Test the app

Restart the pairing window (it expires after 5 min):
```powershell
ssh greenhouse.local "sudo systemctl restart greenhouse-portal"
```

Open the Greenhouse app → tap **Find my greenhouse**.

---

## Day-to-day shortcuts

| Task | Command |
|------|---------|
| SSH into Pi | `ssh greenhouse.local` |
| Deploy updated code | `powershell -ExecutionPolicy Bypass -File deploy.ps1` |
| Reset Pi to AP mode | `ssh greenhouse.local "sudo bash /home/pi/greenhouse/scripts/reset.sh"` |
| Run selftest | `ssh greenhouse.local "sudo bash /home/pi/greenhouse/scripts/selftest.sh"` |
| Reopen pairing window | `ssh greenhouse.local "sudo systemctl restart greenhouse-portal"` |
| View portal logs | `ssh greenhouse.local "sudo journalctl -u greenhouse-portal -f"` |

---

## Build & install the Flutter app

Phone connected via USB with USB debugging on:

```powershell
cd C:\Users\themi\Documents\GreenHouse\app
C:\flutter\bin\flutter.bat run -d AAXGHE99DQGAQO8X
```

Or build APK only:
```powershell
C:\flutter\bin\flutter.bat build apk --debug
```

---

## Key facts

| Thing | Value |
|-------|-------|
| Pi SSH | `ssh greenhouse.local` (password = what you set in Imager) |
| MQTT credentials | printed by selftest, also in `/etc/greenhouse/device.json` on Pi |
| AP hotspot | open network `Greenhouse-XXXX`, setup page at `http://192.168.4.1` |
| Pairing window | 5 minutes after boot — restart portal service to reset |
| Watchdog | if Pi can't reach home WiFi within 60s after reboot, it goes back to AP mode automatically |

---

## Troubleshooting

**`Connection refused` on SSH**
→ Pi still booting. Wait 30-45 seconds.

**`greenhouse.local` not found**
→ Find Pi IP from router device list: `ssh pi@192.168.x.x`

**`WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED`**
→ Already handled — SSH config is set to ignore this for greenhouse.local.

**`18 passed, 0 failed` but app says "Greenhouse not found"**
→ Check phone and Pi are on the same WiFi (not on hotspot/mobile data). Then restart the portal and try within 5 minutes.

**Greenhouse-XXXX disappears right after entering WiFi**
→ That's correct — Pi saved credentials and rebooted. Check if it reconnected to your WiFi.

**Portal page doesn't pop up automatically**
→ Open browser manually and go to `192.168.4.1`
