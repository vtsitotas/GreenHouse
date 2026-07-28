# SECURITY — posture, rotation, and what is genuinely still open

Operational companion to `docs/technical/10-security.md` (which explains the
mechanisms). This file is the **checklist**: what to run, what to change by
hand, and what the system does *not* protect against.

---

## 1. Known live exposure: credentials in git history

**Verified, not hypothetical.** Commit `c0383b3` ("feat: edge node + bridge
firmware for multi-zone sensor mesh") contains a real home WiFi password and a
real MQTT password as plaintext `#define`s in
`firmware/bridge_esp32/bridge_esp32.ino`. See them for yourself:

```bash
git show c0383b3:firmware/bridge_esp32/bridge_esp32.ino | grep -E 'WIFI_PASSWORD|MQTT_PASS'
```

They are deliberately **not** reproduced in this file. Everything tracked in
this repo avoids restating them — `check_leaked_secrets.py` matches on SHA-256
hashes instead — so that the scrub in Step 3 below actually sticks rather than
being undone by the next commit.

Later commits removed these from the working tree and moved them into a
gitignored `secrets.h` — that fixed the *structure* but **did not invalidate
the credentials**. Anyone with a clone of this repository, including anyone
who ever had access to it, still holds a working WiFi password and MQTT
password. Deleting a secret from `HEAD` is not rotation.

There is also a stash (`1ce749f`, "hardware bring-up: real MACs, wifi creds…")
carrying real values.

### Is this unit still affected?

Don't guess — check:

```bash
python3 pi/scripts/check_leaked_secrets.py
```

It compares every live credential against the known-published values and exits
non-zero if any is still in use. `selftest.sh` runs it on every deploy, so a
unit that was never rotated fails its healthcheck instead of looking fine.

### Fixing it

**Step 1 — rotate everything the Pi owns (automated):**

```bash
sudo bash /home/pi/greenhouse/scripts/rotate_secrets.sh
```

Regenerates the MQTT `app` and `bridge` passwords, the history API token, the
pairing PIN, the camera token, and the TLS keypair (so a leaked private key
can't be used to impersonate the unit). Prints the new PIN and camera token,
and tells you exactly what must be re-paired and re-flashed.

**Step 2 — rotate what lives outside the Pi (manual, unavoidable):**

| What | Where | Then |
|---|---|---|
| Home WiFi password | Router admin page | Re-flash the camera's `secrets.h` with the new one |
| HiveMQ Cloud password | HiveMQ Cloud console | Update `/etc/greenhouse/hivemq.json`, restart `greenhouse-hivemq-bridge` |

**Step 3 — decide about the history itself.** Rotation makes the leaked values
worthless, which is the security-relevant part. Scrubbing history is optional
and destructive; only do it if the repo will be made public:

```bash
# Rewrites every commit — everyone else must re-clone. Back up first.
pip install git-filter-repo

# Build the replacement list locally from history. Keep this file UNTRACKED:
# committing it would put the literals straight back into the repo.
git show c0383b3:firmware/bridge_esp32/bridge_esp32.ino \
  | grep -oP '(?<=#define WIFI_PASSWORD ")[^"]+|(?<=#define MQTT_PASS     ")[^"]+' \
  | sed 's/$/==>REDACTED/' > /tmp/leaked.txt

git filter-repo --replace-text /tmp/leaked.txt
rm /tmp/leaked.txt
git push --force --all      # coordinate with anyone else using the repo
```

After scrubbing, `check_leaked_secrets.py` keeps working — it stores hashes,
not the values, so it can still recognise an un-rotated credential on a unit
even once the literals are gone from history.

Also drop the stash carrying real values: `git stash drop stash@{0}` (check
`git stash list` first — confirm it's the hardware bring-up one).

---

## 2. Post-hardening checklist

Run after any deploy. `selftest.sh` checks most of this automatically.

- [ ] `sudo bash pi/scripts/selftest.sh` passes with no `[FAIL]`
- [ ] `/api/history*` returns **401** without a token (selftest asserts this)
- [ ] `POST /cam/frame` returns **401** unauthenticated (selftest asserts this)
- [ ] `cam_token.txt` is not the repo's public placeholder (selftest asserts this)
- [ ] SSH password auth disabled (selftest asserts this)
- [ ] Portal HTTPS on 8443 responds (selftest asserts this)
- [ ] `bash firmware/verify_crypto.sh` passes — compiles the camera's signing
      and AES-GCM helpers against the real ESP32 mbedtls headers with the
      actual target compiler. Catches wrong/missing includes and mis-typed
      mbedtls calls without needing the board. Does **not** replace a bench
      run: it proves the crypto API usage is correct, not that the sketch runs.

### Optional: host firewall (default-deny inbound)

Every exposed service is authenticated, but a firewall means a port that gets
opened *later* — a debug listener, a new dependency — isn't reachable unless
it was deliberately allowed.

```bash
sudo bash pi/scripts/firewall.sh --dry-run   # see the rules, change nothing
sudo bash pi/scripts/firewall.sh --apply     # apply, verify, auto-rollback on failure
sudo bash pi/scripts/firewall.sh --flush     # undo
```

Not enabled automatically — turning on a firewall unattended on a remote unit
is a decision, not a default. SSH is allowed unconditionally and established
connections are accepted before the policy flips, so the session applying the
rules survives; `--apply` verifies both and rolls back if either check fails.
Rules are non-persistent until you run `netfilter-persistent save`, so a
power-cycle clears them if something goes wrong.

`pi/tests/test_firewall_rules.py` asserts the allowlist matches the ports this
project actually serves — it fails if a service's port goes missing, or if a
port is opened without justification (including the anonymous MQTT listener on
1883, which must never leave loopback).

### Three opt-in hardening switches

Both are **off by default on purpose** — turning them on before the
corresponding hardware/app is updated locks you out of your own system.

| Switch | Turn on when | Effect |
|---|---|---|
| `sudo touch /etc/greenhouse/require_https` | every paired phone runs a build that speaks HTTPS | Plaintext `/pair/confirm` and `/api/history*` return 403; closes the downgrade path |
| `sudo touch /etc/greenhouse/no_unsigned_cam` | every camera is re-flashed with signing firmware | Frame POSTs must be HMAC-signed; the bearer-token-in-the-clear path is refused |
| `sudo touch /etc/greenhouse/require_encrypted_cam` | every camera is re-flashed with encrypting firmware | Plaintext frames are refused; only AES-256-GCM payloads accepted |

Restart the affected service after creating any of them.

### Is anyone attacking it?

Prevention is only half the job — until now the unit had no way to tell you it
was under attack. Security-relevant events (failed PINs, lockouts, camera auth
failures, history-API probing) are appended to
`/var/log/greenhouse-security.log` as structured JSON, and the serious ones
push a notification to your phone over the existing FCM pipeline.

```bash
sudo bash pi/scripts/selftest.sh     # prints a per-kind summary
tail -f /var/log/greenhouse-security.log
```

Notifications are rate-limited to one per event kind per 15 minutes. That's
deliberate: without it the alert channel becomes a weapon — anyone able to
trigger a failed auth could flood your phone until you mute greenhouse alerts,
and the alert you'd then miss is the real one. Every event is still logged;
only the pushes collapse.

A single wrong PIN logs but doesn't notify — that's a typo. A lockout does.

### Verify the greenhouse you paired with is the real one

Pairing pins the certificate whenever the fingerprint is known in advance (QR
scan, or manual entry). Pairing over **mDNS discovery** has nothing to verify
against on the first try, so confirm it afterwards — once, by eye:

```bash
sudo bash pi/scripts/selftest.sh   # prints  SAFETY CODE : ABCD-EF12
```

Compare with **Settings → Safety code** in the app. They must match. If they
don't, something impersonated your greenhouse during pairing: disconnect,
rotate secrets, and re-pair using the QR code.

---

## 3. What this system does and does not defend against

### Defended

- **Anyone on the LAN/hotspot reading your data or controlling the greenhouse.**
  Every HTTP endpoint is authenticated and fails closed when unprovisioned.
- **Passive eavesdropping on app↔Pi traffic.** MQTT and the portal both run TLS
  with per-unit certificate pinning.
- **A stolen camera token from sniffing.** Frame POSTs are HMAC-signed; the
  token itself never crosses the wire, and signatures are timestamp-bound and
  single-use.
- **Watching the greenhouse by sniffing camera frames.** Frames are encrypted
  with AES-256-GCM under a key derived from the shared token, so the JPEGs are
  unreadable on the wire and tampering fails authentication on decrypt.
- **Silently forgetting to rotate leaked credentials.**
  `check_leaked_secrets.py` fails the healthcheck while any known-published
  value is still live.
- **PIN brute force**, online and offline-by-timing: per-IP lockout with
  automatic expiry, constant-time comparison.
- **A compromised/spoofed bridge** sending actuator commands: the MQTT ACL
  restricts it to publish-only on its own telemetry topics.
- **Service compromise escalating to the whole Pi:** every service runs as
  `pi`, unprivileged, with a full systemd sandbox and no capabilities.
- **One stolen laptop unlocking the whole fleet:** shipped clones no longer
  carry the shared admin SSH key.

### Not defended — be explicit about these

1. **Active MITM during the *first* mDNS pairing — detectable and opt-in, not
   prevented.** Pairing via QR or manual fingerprint entry *is* prevented (the
   certificate is pinned and a mismatch is refused before the PIN is sent).
   Pairing over mDNS with no prior fingerprint has nothing to verify against at
   that moment. A 6-digit PIN cannot fix it — any proof-of-knowledge it carried
   would be brute-forceable offline from one captured exchange, and a PAKE
   (which would fix it) has no usable Dart implementation.

   Two things reduce it from a silent weakness to an informed, checkable one:
   - the app now **refuses to pair unverified without an explicit
     acknowledgement**, naming QR as the secure alternative (same pattern as
     SSH accepting an unknown host key);
   - the **safety code** (§2) lets you confirm afterwards — a MITM's
     substituted certificate produces a different code.
2. **Physical capture of a mesh node.** ESP-NOW uses one network-wide PMK/LMK
   compiled into firmware. Extracting it from any node exposes mesh traffic.
   Per-node keys need a provisioning mechanism this project doesn't have, and
   deriving per-pair keys from the shared PMK wouldn't help — whoever extracts
   the PMK can derive them all. An explicit non-goal in the mesh design spec.
   *(Not attempted rather than not considered: changing mesh crypto blind, with
   no compiler or hardware in this environment, risks silently breaking the
   fleet's ability to join the mesh — the failure mode the repo's own docs flag
   as hardest to diagnose remotely.)*
3. **Multi-tenant isolation.** One shared HiveMQ Cloud account serves the whole
   fleet. There is no per-customer separation or device registry. Fine for a
   single-owner deployment; not a product.
4. **A malicious actor with physical access to the Pi.** The SD card is not
   encrypted; `device.json` and the TLS keys are readable by anyone holding the
   card. Full-disk encryption on a headless Pi Zero W needs either a passphrase
   at every boot (defeats unattended operation) or a key stored on the same
   card (defeats the purpose). No TPM/secure element on this hardware.
5. **`sudo nmcli` for the `pi` user.** Required by the portal's WiFi setup, but
   broad: compromise of any service running as `pi` inherits it. Mitigated by
   the sandboxing and by no endpoint being unauthenticated, not eliminated.
6. **Rotating the router and HiveMQ passwords.** Automated for everything the
   Pi owns; these two live in systems this repo cannot reach (§1, step 2).

---

## 4. Reporting

This is a thesis project, not a supported product. If you find something,
open an issue on the repository.
