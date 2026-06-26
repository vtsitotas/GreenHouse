# Security Hardening + Captive-Portal Auto-Popup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every cloned greenhouse unit cryptographically and credentially unique, remove deprecated/leaky code, and make the WiFi-setup page auto-pop when a phone joins the `Greenhouse-XXXX` hotspot.

**Architecture:** All per-unit identity (TLS CA+key, OS password, MQTT password) is generated on the *customer's first boot* by `first_boot.sh`, not baked into the shared image. `prep_image.sh` strips that identity before cloning. The captive-portal popup is achieved by letting NetworkManager's shared-mode dnsmasq resolve every domain to the Pi (`192.168.4.1`) and redirecting port 80 → 8080 so the OS connectivity probe lands on the Flask portal.

**Tech Stack:** Raspberry Pi OS Trixie (Debian 13), NetworkManager (`nmcli`, shared mode), Mosquitto 2.x, Python 3 / Flask, OpenSSL, iptables (nft backend), systemd. Target hardware: Pi Zero W (armv6l).

## Global Constraints

- Pi OS is **Trixie + NetworkManager** owns `wlan0`. Never use hostapd/wpa_supplicant/cloud-init — they silently fail. AP = `nmcli ... ipv4.method shared`.
- Greenhouse code lives at **`/home/pi/greenhouse/`**; TLS certs at **`/etc/mosquitto/certs/`** (owned `mosquitto:mosquitto`, key `640`, crt `644`).
- AP is OPEN, static **`192.168.4.1/24`**, SSID `Greenhouse-XXXX` (XXXX = last 4 hex of wlan0 MAC, uppercase, derived at boot).
- All scripts are **idempotent** and safe to re-run.
- Deploy from the PC with: `scp` to `/home/pi/greenhouse/`, then strip CRLF: `find /home/pi/greenhouse -type f \( -name "*.sh" -o -name "*.py" -o -name "*.service" -o -name "*.conf" \) -exec sed -i "s/\r$//" {} +`. Test by SSHing from the PC (`ssh pi@greenhouse.local`).
- Admin access survives password/cert rotation via a baked-in **admin SSH public key** (see Task 3). Verify key SSH works before relying on it.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

**Out of scope (deferred, documented):** Finding #2 (unauthenticated `/pair`). For a thesis on a home LAN the existing 5-minute window + LAN-only exposure is acceptable; the product-grade fix (PIN/QR proof-of-possession) trades against pairing UX. Leave `/pair` as-is.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `pi/factory/` (dir) | Old cloud-init approach | **Delete** (leaks password hash) |
| `pi/scripts/provision.sh` | Old installer | **Delete** (superseded by install.sh) |
| `pi/scripts/ap_mode.sh` | Old hostapd AP | **Delete** (superseded by ap_up.sh) |
| `pi/scripts/gen_certs.sh` | Generate unique TLS CA+server cert if absent | **Create** |
| `pi/scripts/first_boot.sh` | Per-unit identity: certs, OS password, MQTT password, device.json | Modify |
| `pi/scripts/prep_image.sh` | Strip per-unit identity before cloning | Modify |
| `pi/scripts/ap_up.sh` | Bring up AP + captive redirect | Modify |
| `pi/install.sh` | Master installer | Modify |
| `pi/scripts/selftest.sh` | Verify a provisioned unit | Modify |

---

## Task 1: Remove deprecated / credential-leaking code

**Files:**
- Delete: `pi/factory/` (entire dir: `user-data`, `network-config`, `flash.ps1`)
- Delete: `pi/scripts/provision.sh`
- Delete: `pi/scripts/ap_mode.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. (`install.sh`, `ap_up.sh`, `first_boot.sh` are the only live entry points and do not reference these files.)

- [ ] **Step 1: Confirm nothing live references the doomed files**

Run from repo root:
```bash
grep -rn "provision.sh\|ap_mode.sh\|factory/" pi/install.sh pi/scripts/*.sh pi/systemd/*.service 2>/dev/null | grep -v "ap_mode.sh:" || echo "NO live references"
```
Expected: `NO live references` (the only matches, if any, are inside the files being deleted).

- [ ] **Step 2: Delete the files**

```bash
git rm -r pi/factory
git rm pi/scripts/provision.sh pi/scripts/ap_mode.sh
```

- [ ] **Step 3: Verify the working installer is intact**

```bash
git status --short
ls pi/scripts/
```
Expected: `pi/scripts/` still contains `first_boot.sh`, `ap_up.sh`, `prep_image.sh`, `selftest.sh`. Deletions staged.

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
chore: remove deprecated cloud-init factory + hostapd scripts

pi/factory/ baked a shared password hash into git and the cloud-init/
hostapd path does not work on Trixie. provision.sh and ap_mode.sh are
superseded by install.sh and ap_up.sh. Removes the source of the
hardcoded-credential findings (still in history; repo is private).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Per-unit TLS certificates (Finding #3)

**Files:**
- Create: `pi/scripts/gen_certs.sh`
- Modify: `pi/install.sh` (replace inline cert block with a call to `gen_certs.sh`; add mosquitto ordering drop-in)
- Modify: `pi/scripts/first_boot.sh` (generate certs before reading the fingerprint)
- Modify: `pi/scripts/prep_image.sh` (delete certs so first boot regenerates them)

**Interfaces:**
- Produces: `gen_certs.sh` — idempotent; creates `/etc/mosquitto/certs/{ca.key,ca.crt,server.key,server.crt}` only if `server.crt` is absent; exits 0 if present. Safe to call from both `install.sh` and `first_boot.sh`.
- Consumes (first_boot): the freshly generated `/etc/mosquitto/certs/server.crt` to compute `tls_fingerprint`.

- [ ] **Step 1: Create `pi/scripts/gen_certs.sh`**

```bash
#!/bin/bash
# Generates a UNIQUE self-signed CA + server certificate for THIS unit, if absent.
# Called by install.sh (master) and first_boot.sh (each cloned unit's first boot),
# so every shipped Pi has its own key material.
set -e
CERTS=/etc/mosquitto/certs
[ -f "$CERTS/server.crt" ] && exit 0

mkdir -p "$CERTS"
openssl genrsa -out "$CERTS/ca.key" 2048
openssl req -new -x509 -days 3650 -key "$CERTS/ca.key" -out "$CERTS/ca.crt" \
  -subj "/CN=GreenhouseCA"
openssl genrsa -out "$CERTS/server.key" 2048
openssl req -new -key "$CERTS/server.key" -out "$CERTS/server.csr" \
  -subj "/CN=greenhouse.local"
openssl x509 -req -days 3650 -in "$CERTS/server.csr" \
  -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial \
  -out "$CERTS/server.crt"
rm -f "$CERTS/server.csr"

chown -R mosquitto:mosquitto "$CERTS"
chmod 640 "$CERTS"/*.key
chmod 644 "$CERTS"/*.crt
echo "[gen_certs] generated unique CA + server cert in $CERTS"
```

- [ ] **Step 2: Test that two generations produce different fingerprints**

Run on the Pi (proves uniqueness — the core of Finding #3):
```bash
scp pi/scripts/gen_certs.sh pi@greenhouse.local:/home/pi/greenhouse/scripts/
ssh pi@greenhouse.local 'sed -i "s/\r$//" /home/pi/greenhouse/scripts/gen_certs.sh
  sudo rm -f /etc/mosquitto/certs/*; sudo bash /home/pi/greenhouse/scripts/gen_certs.sh
  FP1=$(sudo openssl x509 -fingerprint -sha256 -noout -in /etc/mosquitto/certs/server.crt)
  sudo rm -f /etc/mosquitto/certs/*; sudo bash /home/pi/greenhouse/scripts/gen_certs.sh
  FP2=$(sudo openssl x509 -fingerprint -sha256 -noout -in /etc/mosquitto/certs/server.crt)
  echo "$FP1"; echo "$FP2"; [ "$FP1" != "$FP2" ] && echo "UNIQUE: PASS" || echo "UNIQUE: FAIL"'
```
Expected: two different fingerprints and `UNIQUE: PASS`.

- [ ] **Step 3: Replace the inline cert block in `pi/install.sh`**

Find this block in `install.sh`:
```bash
echo "==> Generating TLS certificates (if missing)..."
CERTS=/etc/mosquitto/certs
if [ ! -f "$CERTS/server.crt" ]; then
  openssl genrsa -out "$CERTS/ca.key" 2048
  openssl req -new -x509 -days 3650 -key "$CERTS/ca.key" -out "$CERTS/ca.crt" \
    -subj "/CN=GreenhouseCA"
  openssl genrsa -out "$CERTS/server.key" 2048
  openssl req -new -key "$CERTS/server.key" -out "$CERTS/server.csr" \
    -subj "/CN=greenhouse.local"
  openssl x509 -req -days 3650 -in "$CERTS/server.csr" \
    -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial \
    -out "$CERTS/server.crt"
  rm -f "$CERTS/server.csr"
fi
chown -R mosquitto:mosquitto "$CERTS"
chmod 640 "$CERTS"/*.key
chmod 644 "$CERTS"/*.crt
```
Replace it entirely with:
```bash
echo "==> Generating TLS certificates (if missing)..."
bash "$REPO/scripts/gen_certs.sh"
```

- [ ] **Step 4: Add a mosquitto ordering drop-in in `pi/install.sh`**

In `install.sh`, immediately after the `echo "==> Installing systemd services..."` block that copies the three `.service` files and before `systemctl daemon-reload`, insert:
```bash
# Ensure Mosquitto starts AFTER first_boot has generated certs on a fresh unit.
mkdir -p /etc/systemd/system/mosquitto.service.d
cat > /etc/systemd/system/mosquitto.service.d/greenhouse.conf <<EOF
[Unit]
After=greenhouse-firstboot.service
EOF
```

- [ ] **Step 5: Make `first_boot.sh` generate certs before computing the fingerprint**

In `pi/scripts/first_boot.sh`, find:
```bash
mkdir -p "$CONFIG_DIR"

# Unique 20-char URL-safe password
PASSWORD=$(openssl rand -base64 21 | tr -d '/+=\n' | head -c 20)
```
Replace with:
```bash
mkdir -p "$CONFIG_DIR"

# Generate this unit's unique TLS certs (no-op if they already exist).
bash "$(dirname "$0")/gen_certs.sh"

# Unique 20-char URL-safe password
PASSWORD=$(openssl rand -base64 21 | tr -d '/+=\n' | head -c 20)
```

- [ ] **Step 6: Make `first_boot.sh` restart (not reload) mosquitto so new certs take effect**

In `pi/scripts/first_boot.sh`, change:
```bash
systemctl reload mosquitto 2>/dev/null || true
```
to:
```bash
systemctl restart mosquitto 2>/dev/null || true
```

- [ ] **Step 7: Make `prep_image.sh` delete the certs**

In `pi/scripts/prep_image.sh`, find:
```bash
echo "[prep] wiping per-unit identity (regenerated on first customer boot)..."
rm -f /etc/greenhouse/.wifi_configured
rm -f /etc/greenhouse/.provisioned
rm -f /etc/greenhouse/device.json
: > /etc/mosquitto/passwd
```
Replace with:
```bash
echo "[prep] wiping per-unit identity (regenerated on first customer boot)..."
rm -f /etc/greenhouse/.wifi_configured
rm -f /etc/greenhouse/.provisioned
rm -f /etc/greenhouse/device.json
: > /etc/mosquitto/passwd
# Per-unit TLS — regenerated uniquely by first_boot.sh on the customer's first boot.
rm -f /etc/mosquitto/certs/ca.key /etc/mosquitto/certs/ca.crt /etc/mosquitto/certs/ca.srl \
      /etc/mosquitto/certs/server.key /etc/mosquitto/certs/server.crt /etc/mosquitto/certs/server.csr
```

- [ ] **Step 8: Deploy and verify a full reprovision regenerates certs and mosquitto stays up**

```bash
scp pi/install.sh pi/scripts/first_boot.sh pi/scripts/prep_image.sh pi/scripts/gen_certs.sh pi@greenhouse.local:/home/pi/greenhouse/scripts/ 2>/dev/null
scp pi/install.sh pi@greenhouse.local:/home/pi/greenhouse/install.sh
ssh pi@greenhouse.local 'find /home/pi/greenhouse -type f \( -name "*.sh" \) -exec sed -i "s/\r$//" {} +
  sudo rm -f /etc/greenhouse/.provisioned /etc/mosquitto/certs/*
  sudo bash /home/pi/greenhouse/install.sh > /tmp/i.log 2>&1; echo "install exit=$?"
  systemctl is-active mosquitto
  sudo openssl x509 -fingerprint -sha256 -noout -in /etc/mosquitto/certs/server.crt'
```
Expected: `install exit=0`, `active`, and a fingerprint line. (`.provisioned` removal forces `first_boot` to re-run and regenerate.)

- [ ] **Step 9: Commit**

```bash
git add pi/scripts/gen_certs.sh pi/install.sh pi/scripts/first_boot.sh pi/scripts/prep_image.sh
git commit -m "$(cat <<'EOF'
feat: per-unit TLS certs generated on first boot (security #3)

Extract cert generation into gen_certs.sh and run it from first_boot.sh,
so each cloned unit mints its own CA + server key instead of sharing one.
prep_image.sh now deletes the certs; a mosquitto ordering drop-in ensures
the broker starts after first_boot has created them.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Per-unit OS password + admin SSH key (Finding #1)

**Files:**
- Modify: `pi/install.sh` (install admin SSH public key)
- Modify: `pi/scripts/first_boot.sh` (randomize the `pi` password, write it to the boot partition)
- Modify: `pi/scripts/prep_image.sh` (remove the password file so a clone writes its own)

**Interfaces:**
- Consumes: `DEVICE_ID` (already computed in `first_boot.sh`).
- Produces: `/boot/firmware/INITIAL_PASSWORD.txt` (mode 600) containing the unit's `device_id` and the random `pi` password — readable by popping the SD into any PC.

**Note on admin access:** Because the password is randomized, key-based SSH is the durable admin path. The admin public key below is baked into the image (it is a *public* key — safe to commit) so the manufacturer can always reach any unit. Replace the key value with your own if desired.

- [ ] **Step 1: Add admin SSH key install to `pi/install.sh`**

In `install.sh`, immediately after the `echo "==> Making scripts executable..."` line and its `chmod` command, insert:
```bash
echo "==> Installing admin SSH key (survives password rotation + cloning)..."
ADMIN_KEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJZTcXERkxSG6Zi/SA8So2tFS+AP3O2b+rfev8S9Ay5B claude-greenhouse'
install -d -m 700 -o pi -g pi /home/pi/.ssh
touch /home/pi/.ssh/authorized_keys
grep -qF "$ADMIN_KEY" /home/pi/.ssh/authorized_keys || echo "$ADMIN_KEY" >> /home/pi/.ssh/authorized_keys
chown pi:pi /home/pi/.ssh/authorized_keys
chmod 600 /home/pi/.ssh/authorized_keys
```

- [ ] **Step 2: Verify key SSH works BEFORE randomizing the password (avoid lockout)**

```bash
scp pi/install.sh pi@greenhouse.local:/home/pi/greenhouse/install.sh
ssh pi@greenhouse.local 'sed -i "s/\r$//" /home/pi/greenhouse/install.sh; sudo bash /home/pi/greenhouse/install.sh >/tmp/i.log 2>&1; echo exit=$?'
ssh -o PasswordAuthentication=no pi@greenhouse.local 'echo KEY_SSH_OK'
```
Expected: `exit=0` then `KEY_SSH_OK` (key-only auth confirmed working — safe to randomize password).

- [ ] **Step 3: Add password randomization to `pi/scripts/first_boot.sh`**

In `first_boot.sh`, find the end of the script:
```bash
touch "$SENTINEL"
echo "[first-boot] provisioned device ${DEVICE_ID} with unique password"
```
Replace with:
```bash
# Randomize the OS 'pi' password per unit; record it on the boot partition
# (readable by popping the SD into a PC). Key-based SSH remains the admin path.
OS_PASS=$(openssl rand -base64 12 | tr -d '/+=' | head -c 16)
echo "pi:${OS_PASS}" | chpasswd
BOOT=/boot/firmware
[ -d "$BOOT" ] || BOOT=/boot
printf 'Greenhouse unit %s\npi user password: %s\n' "${DEVICE_ID}" "${OS_PASS}" > "${BOOT}/INITIAL_PASSWORD.txt"
chmod 600 "${BOOT}/INITIAL_PASSWORD.txt"

touch "$SENTINEL"
echo "[first-boot] provisioned device ${DEVICE_ID}: unique MQTT password, TLS certs, OS password"
```

- [ ] **Step 4: Add boot password-file cleanup to `pi/scripts/prep_image.sh`**

In `prep_image.sh`, find:
```bash
echo "[prep] resetting machine-id (unique per clone)..."
```
Insert immediately BEFORE that line:
```bash
echo "[prep] removing per-unit OS password record..."
rm -f /boot/firmware/INITIAL_PASSWORD.txt /boot/INITIAL_PASSWORD.txt
```

- [ ] **Step 5: Deploy, force reprovision, and verify the password file is written + changed**

```bash
scp pi/scripts/first_boot.sh pi/scripts/prep_image.sh pi@greenhouse.local:/home/pi/greenhouse/scripts/
ssh pi@greenhouse.local 'find /home/pi/greenhouse -name "*.sh" -exec sed -i "s/\r$//" {} +
  sudo rm -f /etc/greenhouse/.provisioned
  sudo bash /home/pi/greenhouse/scripts/first_boot.sh
  BOOT=/boot/firmware; [ -d "$BOOT" ] || BOOT=/boot
  sudo test -f "$BOOT/INITIAL_PASSWORD.txt" && echo "PWFILE: present" || echo "PWFILE: MISSING"
  sudo cat "$BOOT/INITIAL_PASSWORD.txt"'
ssh -o PasswordAuthentication=no pi@greenhouse.local 'echo KEY_STILL_WORKS'
```
Expected: `PWFILE: present`, the file contents (device id + a 16-char password), and `KEY_STILL_WORKS` (admin key access intact after password change).

- [ ] **Step 6: Commit**

```bash
git add pi/install.sh pi/scripts/first_boot.sh pi/scripts/prep_image.sh
git commit -m "$(cat <<'EOF'
feat: per-unit OS password + baked admin SSH key (security #1)

first_boot.sh now randomizes the pi user's password and records it at
/boot/firmware/INITIAL_PASSWORD.txt (readable via the SD card). install.sh
bakes in an admin public key so key-based SSH survives the rotation and
cloning. prep_image.sh clears the password record before imaging.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Captive-portal auto-popup

**Files:**
- Modify: `pi/install.sh` (add `iptables` package; write NetworkManager dnsmasq-shared captive config)
- Modify: `pi/scripts/ap_up.sh` (redirect port 80 → 8080 when the AP is up)

**Interfaces:**
- Consumes: the existing portal catch-all route `@app.route("/<path:path>")` in `portal.py`, which already returns `wifi.html` for any path while in AP mode — this is what the OS connectivity probe receives. **No portal.py change required.**
- Produces: nothing consumed by later tasks.

**How it works:** NetworkManager runs dnsmasq for the `shared` AP connection and reads extra config from `/etc/NetworkManager/dnsmasq-shared.d/`. `address=/#/192.168.4.1` resolves every hostname to the Pi. The OS connectivity probe (Android `http://connectivitycheck.gstatic.com/generate_204`, iOS `http://captive.apple.com/...`) then hits the Pi on port 80; the iptables rule forwards it to the Flask portal on 8080, which serves the setup form — triggering the "Sign in to network" popup.

- [ ] **Step 1: Add `iptables` to the package list and the captive DNS config in `pi/install.sh`**

In `install.sh`, change the apt install list from:
```bash
apt-get install -y -qq \
  mosquitto mosquitto-clients \
  python3-flask \
  openssl \
  dnsmasq-base \
  rfkill
```
to:
```bash
apt-get install -y -qq \
  mosquitto mosquitto-clients \
  python3-flask \
  openssl \
  dnsmasq-base \
  iptables \
  rfkill
```

Then, in `install.sh` immediately after the `echo "==> Creating directories..."` `mkdir` line, insert:
```bash
echo "==> Installing captive-portal DNS config..."
# NetworkManager's shared-mode dnsmasq reads this; resolves every domain to the
# Pi so the phone's connectivity probe triggers the captive-portal popup.
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cat > /etc/NetworkManager/dnsmasq-shared.d/greenhouse-captive.conf <<EOF
address=/#/192.168.4.1
EOF
```

- [ ] **Step 2: Add the port-80 → 8080 redirect in `pi/scripts/ap_up.sh`**

In `ap_up.sh`, find the final two lines:
```bash
nmcli connection up greenhouse-ap
echo "[ap_up] broadcasting open network '${SSID}' at 192.168.4.1"
```
Replace with:
```bash
nmcli connection up greenhouse-ap

# Redirect captive-portal probes (HTTP/80) to the Flask portal on 8080 so the
# WiFi-setup page auto-pops when a phone joins. Idempotent.
iptables -t nat -C PREROUTING -i wlan0 -p tcp --dport 80 -j REDIRECT --to-port 8080 2>/dev/null \
  || iptables -t nat -A PREROUTING -i wlan0 -p tcp --dport 80 -j REDIRECT --to-port 8080

echo "[ap_up] broadcasting open network '${SSID}' at 192.168.4.1 (captive portal on)"
```

- [ ] **Step 3: Deploy and verify the config files + redirect rule install correctly**

```bash
scp pi/install.sh pi@greenhouse.local:/home/pi/greenhouse/install.sh
scp pi/scripts/ap_up.sh pi@greenhouse.local:/home/pi/greenhouse/scripts/ap_up.sh
ssh pi@greenhouse.local 'find /home/pi/greenhouse -type f \( -name "*.sh" \) -exec sed -i "s/\r$//" {} +
  sudo bash /home/pi/greenhouse/install.sh >/tmp/i.log 2>&1; echo install_exit=$?
  cat /etc/NetworkManager/dnsmasq-shared.d/greenhouse-captive.conf
  command -v iptables >/dev/null && echo "iptables: present" || echo "iptables: MISSING"'
```
Expected: `install_exit=0`, the line `address=/#/192.168.4.1`, and `iptables: present`.

- [ ] **Step 4: Manual hardware verification (auto-popup)**

This is a hardware test — run the AP detached with auto-revert (so SSH returns), then test from a phone:
```bash
ssh pi@greenhouse.local 'CLIENT=$(nmcli -t -f NAME,DEVICE connection show --active | awk -F: "\$2==\"wlan0\"{print \$1}")
  sudo systemd-run --collect --unit=aptest /bin/bash -c "rm -f /etc/greenhouse/.wifi_configured; /home/pi/greenhouse/scripts/ap_up.sh; sleep 180; nmcli connection down greenhouse-ap; touch /etc/greenhouse/.wifi_configured; nmcli connection up \"$CLIENT\""'
```
On the phone: join `Greenhouse-XXXX`. Expected: within a few seconds a **"Sign in to network"** notification / captive browser opens automatically showing the "🌿 Set up your Greenhouse" page — without manually typing the IP. (Pi auto-reverts after 180s; reconnect SSH.)

- [ ] **Step 5: Commit**

```bash
git add pi/install.sh pi/scripts/ap_up.sh
git commit -m "$(cat <<'EOF'
feat: captive-portal auto-popup on AP join

NetworkManager shared-mode dnsmasq resolves all domains to 192.168.4.1
(via dnsmasq-shared.d), and ap_up.sh redirects port 80 -> 8080, so a phone
joining Greenhouse-XXXX auto-opens the WiFi setup page instead of needing
the user to type the IP. Adds the iptables dependency.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Self-test coverage, full re-verify, runbook update

**Files:**
- Modify: `pi/scripts/selftest.sh` (assert the new hardening artifacts exist)
- Create: `RUNBOOK.md` (canonical build process reflecting all changes)

**Interfaces:**
- Consumes: all artifacts produced by Tasks 2–4.
- Produces: a green self-test and a documented build process.

- [ ] **Step 1: Add hardening checks to `pi/scripts/selftest.sh`**

In `selftest.sh`, find:
```bash
echo "== AP profile sanity =="
command -v nmcli >/dev/null && ok "nmcli present" || no "nmcli missing"
```
Insert immediately BEFORE it:
```bash
echo "== hardening artifacts =="
[ -f /etc/NetworkManager/dnsmasq-shared.d/greenhouse-captive.conf ] && ok "captive DNS config present" || no "captive DNS config missing"
command -v iptables >/dev/null && ok "iptables present" || no "iptables missing"
[ -f /etc/systemd/system/mosquitto.service.d/greenhouse.conf ] && ok "mosquitto ordering drop-in present" || no "mosquitto drop-in missing"
sudo -u mosquitto test -r /etc/mosquitto/certs/server.key && ok "per-unit server.key readable by broker" || no "server.key unreadable"
```

- [ ] **Step 2: Deploy and run the full self-test**

```bash
scp pi/scripts/selftest.sh pi@greenhouse.local:/home/pi/greenhouse/scripts/selftest.sh
ssh pi@greenhouse.local 'sed -i "s/\r$//" /home/pi/greenhouse/scripts/selftest.sh; sudo bash /home/pi/greenhouse/scripts/selftest.sh | tail -8'
```
Expected: `RESULT: <n> passed, 0 failed` (n now includes the 4 new checks).

- [ ] **Step 3: Create `RUNBOOK.md`**

```markdown
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
```

- [ ] **Step 4: Commit**

```bash
git add pi/scripts/selftest.sh RUNBOOK.md
git commit -m "$(cat <<'EOF'
docs: runbook + self-test checks for hardening artifacts

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Push everything**

```bash
git push
```

---

## Self-Review

**Spec coverage:**
- Finding #1 (default password) → Task 3 (per-unit password + admin key). ✓
- Finding #2 (unauthenticated /pair) → explicitly deferred with rationale (Out of scope). ✓
- Finding #3 (shared TLS key) → Task 2 (per-unit certs). ✓
- Committed password hash → Task 1 (delete `pi/factory/`). ✓
- Captive-portal auto-popup → Task 4. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full content or an exact find/replace against the current file. ✓

**Type/name consistency:** `gen_certs.sh` path `/home/pi/greenhouse/scripts/gen_certs.sh`; referenced in `install.sh` as `"$REPO/scripts/gen_certs.sh"` and in `first_boot.sh` as `"$(dirname "$0")/gen_certs.sh"` (both resolve to the same path). `INITIAL_PASSWORD.txt` written and removed at the same `/boot/firmware` (fallback `/boot`) path. Captive conf path identical in install/selftest. ✓

**Risk notes for the executor:**
- Always confirm key SSH works (Task 3 Step 2) before depending on the randomized password.
- The captive popup (Task 4 Step 4) is the only step requiring a phone; everything else is verifiable over SSH.
- `first_boot.sh` changes only take effect when `.provisioned` is absent — the verify steps delete it deliberately to force a re-run.
