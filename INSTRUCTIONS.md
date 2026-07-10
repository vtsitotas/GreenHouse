# Greenhouse — Build & Flash Instructions

A step-by-step guide to turn a Raspberry Pi + SD card into a ready-to-ship greenhouse unit, make a reusable firmware image, and flash more units.

---

## 0. The one thing that confuses everyone: PC vs Pi

You use **one terminal window**, but it can be in two places. **Look at the prompt:**

| Prompt | You are on… | Run here… |
|---|---|---|
| `PS C:\Users\billy>` | **Your PC** | `scp`, and any line that starts with `ssh` |
| `pi@greenhouse:~ $` | **Inside the Pi** | commands meant to run *on* the Pi |

- Typing `ssh pi@greenhouse.local` **moves you into the Pi** (prompt changes).
- Typing `exit` **brings you back to your PC**.
- A command runs *on the Pi* **only** when it's inside the quotes of an `ssh ... "..."` call.

> Tip: keep **two terminals** open — one stays on the PC, one logged into the Pi.

---

## Part 1 — Build a unit from a blank SD

### Step 1. Flash the base OS (Raspberry Pi Imager)
1. Open **Raspberry Pi Imager**.
2. **Choose OS** → Raspberry Pi OS Lite **(32-bit)**. *(Required for Pi Zero W.)*
3. **Choose Storage** → your SD card.
4. Click **Next** → **Edit Settings**:
   - **Hostname:** `greenhouse`
   - **Enable SSH** → *Use password authentication*
   - **Username:** `pi`  **Password:** `greenhouse2026`
   - **Configure wireless LAN:** a WiFi network **with internet** (needed only for setup) + **Country: GR**
   - **Locale:** `Europe/Athens`
5. **Save** → **Write**. When done, put the SD in the Pi and power it on.
6. **Wait ~1–2 minutes** (first boot is slow).

### Step 2. Connect to the Pi (from your PC)
In the **PC terminal**:
```powershell
ssh pi@greenhouse.local
```
- If asked *"Are you sure you want to continue connecting?"* → type **`yes`**.
- Password is **`greenhouse2026`**.
- You should land on `pi@greenhouse:~ $`. Type `exit` to go back to your PC.

*(Troubleshooting for this step is in the cheat sheet at the bottom.)*

### Step 3. Install the greenhouse software (from your PC)
In the **PC terminal**, from the repo root:
```powershell
.\deploy.ps1                          # defaults to greenhouse.local
.\deploy.ps1 -PiHost 192.168.1.54     # or target a specific IP
```
This wipes `/home/pi/greenhouse` on the Pi, copies the `pi/` folder over, runs
`install.sh`, then runs `selftest.sh`. It will ask for the password
(`greenhouse2026`) once or twice the first time — that's normal.

> Don't `scp` the `pi/` folder over manually instead — if `/home/pi/greenhouse`
> already exists, a manual `scp -r` nests the copy into a subfolder instead of
> replacing it. `deploy.ps1` `rm -rf`s the remote dir first, which is why it's
> the supported path.

✅ **Success = `RESULT: <n> passed, 0 failed`** at the end (no failures).

> ⚠️ After install, the Pi's password is **changed to a random one** (security feature). From now on, SSH works automatically with your key — you don't need a password. The random password is saved on the SD card (see cheat sheet) if you ever need it.

---

## Part 2 — Make the firmware image (do this once)

This strips the unit's identity so every copy boots fresh, then powers off.

In the **PC terminal**:
```powershell
ssh pi@greenhouse.local "sudo systemd-run --collect --unit=prep bash /home/pi/greenhouse/scripts/prep_image.sh"
```
- SSH will drop and the **Pi powers off by itself** (~15 seconds). That's expected.
- Pull the SD card out of the Pi and **put it in your PC**.

### Read the card into a `.img` file (Win32DiskImager)
1. Open **Win32DiskImager**.
2. **Image File** box → type: `C:\Users\billy\Desktop\greenhouse.img`
3. **Device** dropdown → select the SD card (the small `bootfs` drive letter).
4. Click **Read**.
   > 🚨 **READ, not Write.** *Write* would erase the card. You want *Read* (card → file).
5. Wait ~5–10 min. You now have `greenhouse.img` — **this is your firmware.**

---

## Part 3 — Mass-produce (flash more units)

For each new unit:
1. Open **Raspberry Pi Imager** → **Choose OS** → scroll down → **Use custom** → pick `greenhouse.img`.
   *(Or use balenaEtcher.)*
2. **Choose Storage** → a blank SD (must be **same size or larger** than the original).
3. **Write.**
4. Put the SD in a Pi and power on.

Each unit automatically generates its **own** unique MQTT password, TLS certificate, OS password, and `Greenhouse-XXXX` WiFi name on first boot. Nothing else to do.

---

## Part 4 — What the customer does

1. Plug in the Pi.
2. On their phone, join the **`Greenhouse-XXXX`** WiFi (open network).
3. A setup page **opens automatically** ("🌿 Set up your Greenhouse").
4. They enter their home WiFi name + password → tap Connect.
5. The Pi reboots and joins their home WiFi.
6. They open the **Greenhouse app** → tap **"Find my greenhouse"** → dashboard.

---

## Part 5 — Updating after you change something

The `.img` is just a **snapshot for flashing new SD cards**. The **GitHub repo is the source of truth.** What you do depends on *what* changed:

### App changes → never need a new `.img`
The phone app is separate from the Pi. Just rebuild the APK and install it on the phone (command in the cheat sheet). Nothing on the Pi or in the image changes.

### Pi changes (scripts, services, new features like ESP-NOW, new packages)

| Goal | What you do | New `.img`? |
|---|---|---|
| Update a unit that's **already running** (reachable on the network) | SSH in → copy the new files (`scp`/`git pull`) → restart the service | ❌ No |
| Make sure **future units** ship with the change | Update the repo → run `install.sh` on a fresh master → `prep_image` → re-clone (Part 2) | ✅ Yes, re-cut it |

You re-cut the image **only** so newly-flashed cards start with the latest code. You never re-image units already in the field — those update over the network.

### ESP-NOW specifically (two separate pieces)
- **ESP32 firmware** (C3 sensors + bridge) → flashed *directly to the ESP32* with esptool/Arduino. **Never part of `greenhouse.img`.**
- **Pi-side bridge** (reads the ESP32 over USB serial `/dev/ttyACM0` → publishes to MQTT) → a new script + systemd unit (+ maybe `python3-serial`). Add it to `install.sh`, deploy to existing units over SSH, and fold it into the **next** image for new hardware.

### Mental model
```
GitHub repo ─(install.sh on a fresh Pi)→ master ─(prep_image + clone)→ greenhouse.img
     │                                                                       │
     └── update existing units over SSH / git pull ─────────────────────────┘  (no re-image)
```
Day-to-day you edit the repo and deploy over SSH. You only rebuild `greenhouse.img` when you want a fresh baseline for flashing **new** SD cards.

---

## Passwords & access reference

| What | Value |
|---|---|
| Initial Pi login (right after flashing) | user `pi`, password `greenhouse2026` |
| Pi login after `install.sh` | random — saved at `INITIAL_PASSWORD.txt` on the SD's `bootfs` partition (readable on any PC), or via SSH key |
| SSH key | `C:\Users\billy\.ssh\id_ed25519` (passwordless once installed) |
| MQTT (app ↔ Pi) | user `app`, per-unit password in `/etc/greenhouse/device.json` |
| Setup hotspot | open SSID `Greenhouse-XXXX`, page at `http://192.168.4.1:8080` |

---

## Security notes

- Per-unit secrets: TLS CA + key, MQTT password, OS password (in `/boot`), AP SSID — all generated fresh on first boot, nothing shared between units.
- Admin access: baked SSH key (`install.sh`'s `ADMIN_KEY`).
- Deferred: `/pair` has no proof-of-possession check (relies on the 5-minute LAN pairing window only). Add a PIN/QR confirmation before any public/production deployment.

---

## Troubleshooting cheat sheet

**`ssh: connect to host greenhouse.local port 22: Connection refused`**
→ The Pi is still booting. Wait 1–2 minutes and try again.

**`greenhouse.local` won't resolve / can't find it**
→ Find the Pi's IP in your router's device list and use it: `ssh pi@192.168.1.xx`.

**`WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!`**
→ A different/re-flashed Pi reused the name. Clear the old key, then reconnect (type `yes`):
```powershell
ssh-keygen -R greenhouse.local
```

**`deploy.ps1` prints `ERROR: Cannot resolve ... -- is the Pi on the network?`**
→ mDNS (`.local`) resolution can be flaky from Windows. Find the Pi's IP in
your router's device list and pass it directly: `.\deploy.ps1 -PiHost 192.168.1.xx`.

**Self-test shows `N-1 passed, 1 failed` (portal not responding)**
→ Harmless timing — the portal was still starting. Re-run the self-test:
```powershell
ssh pi@greenhouse.local "sudo bash /home/pi/greenhouse/scripts/selftest.sh"
```

**Pairing page says "pairing window expired"**
→ It's only open 5 minutes after boot. Reopen it:
```powershell
ssh pi@greenhouse.local "sudo systemctl restart greenhouse-portal"
```

**Build the phone app**
```powershell
$env:PATH = "C:\Users\billy\flutter\bin;$env:PATH"
cd C:\Users\billy\Desktop\diplomatikh\app
flutter build apk --debug
& "C:\Users\billy\AppData\Local\Android\Sdk\platform-tools\adb.exe" install -r "build\app\outputs\flutter-apk\app-debug.apk"
```
