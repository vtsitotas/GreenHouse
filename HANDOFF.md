# Greenhouse IoT — Session Handoff

**Last updated:** 2026-08-11 (history false-alert fix + mesh map bugfixes +
git-history credential scrub — three TL;DRs below, same day). Previous
session 2026-08-10 (WiFi provisioning + UART bridge bring-up, full bench
deploy). Camera remains **parked** since 2026-08-02 (see that TL;DR below);
the 2026-07-28 security-hardening pass is merged and live.

> ⚠️ **Every commit hash changed on 2026-08-11.** History was rewritten to
> remove leaked credentials. Any clone made before then must be **re-cloned**,
> not pulled — pulling would merge the literals back in. Pre-scrub hashes
> quoted in the older session notes below no longer resolve.

**Status:** UART bridge to the Pi is now **working** — the 2026-07-27 "no
traffic on `/dev/serial0`" note below is resolved (was a diagnostic artifact,
not the link). WiFi first-time setup (captive portal) had four stacked bugs
and is now fixed end to end. The bench Pi is fully deployed off current
`main`, `selftest.sh` reports 45/45. Still open: no edge node has actually
delivered a sensor *reading* yet (bridge heartbeats fine, mesh path unproven);
see the 2026-08-10 TL;DR for the full list.

---

## TL;DR of this session (2026-08-11 — history false-alert fix, mesh map bugfixes, overview doc)

Follow-up work later the same day as the credential scrub below, triggered by
the owner actually using the app and noticing (1) a "security alert" pushed
to their own phone every time they opened weather history, and (2) the mesh
map "feels buggy" — nodes shown offline with a last-seen time that reads as
"just now".

**History screen was crying wolf at its own owner.** A phone paired before
2026-07-28 has no `api_token` stored locally (the field didn't exist yet at
pairing time). Every history-screen open sent `/api/history` with no auth,
`portal.py` correctly 401'd, and `history_auth_failure` is one of
`security_log.py`'s alertable kinds — so the owner's own stale pairing
triggered real "Greenhouse security alert" pushes indistinguishable from an
actual intrusion attempt. Fixed client-side: `historyPointsProvider` now
checks `config.apiToken.isEmpty` and throws a dedicated
`HistoryTokenMissingException` *before* ever hitting the network, so the
doomed request (and the false alert it caused) never happens; the history
screen shows a "Re-pair" button instead of a raw 401. Owner still needs to
re-pair once to get a real token.

**Mesh map: the actual bug behind "shows offline but last-seen says now".**
`greenhouse_repository.dart`'s `/status` merge wrote `lastSeen: event.lastSeen`
unconditionally, and `event.lastSeen` is stamped `DateTime.now()` at parse
time regardless of what the payload says — an offline event (which carries no
timestamp of its own; the bridge/serial_bridge just publish the bare string
the instant they detect staleness) was overwriting a node's last-seen with
the exact moment it was found to be gone. Fixed: lastSeen now only advances on
a transition *to* online. Consequence fix: `NodeListTile` and the mesh map's
detail sheet now qualify last-seen with a date once it isn't today
(`app/lib/utils/last_seen_format.dart`) — a bare `HH:mm` misreads as "recent"
once nodes can legitimately show hours- or days-old timestamps. Known,
documented-not-fixed residual gap: a *retained* MQTT redelivery (e.g. on app
reconnect) still advances lastSeen, since the wire protocol carries no real
timestamp to fall back on either — fixing that needs the MQTT retain flag
threaded through `mqtt_connection.dart`, out of scope for this pass.

**Mesh map: "how do I know if a node is bridged" now has an answer on
screen.** `meshRank` already *is* hop-count-from-bridge in this mesh (one
bridge, rank assigned by hop distance) but nothing surfaced that — added an
explicit "Direct"/"N hops" label on each card and a "Connection" row in the
detail sheet ("Direct to bridge" / "Relayed — 2 hops (via node1)" / "This is
the bridge" / "Unknown — no mesh data yet"). Also added a link-quality color
legend and a node-count summary bar (`N nodes · N online · N offline`) —
both were flagged as "missing structure", and the map had zero explanation
of what its own link colors meant.

**New doc:** `docs/SYSTEM_OVERVIEW_SIMPLE.md` — a friendlier, prose-style
walkthrough combining the full system layout and the security work, meant as
the first thing to read before `ARCHITECTURE.md`/`docs/technical/`.

`flutter test`: 200 passed (was 184; ~15 new tests for the token-missing
path, the lastSeen-on-offline fix, the last-seen date formatter, and the
Direct/hop-count labels). `flutter analyze`: clean.

Full detail and rationale for both fixes: `IMPROVEMENTS.md` Β8-Β10.

---

## TL;DR of this session (2026-08-11 — git-history credential scrub)

Ran the scrub that `SECURITY.md` §1 Step 3 had been describing as optional
since 2026-07-28. `git filter-repo --replace-text` rewrote all 223 commits.

**What came out:** two WiFi passwords (home router + phone hotspot), two SSIDs,
the 20-char MQTT password, and an early throwaway `#define MQTT_PASS "123"`.
Verified: the five distinctive literals now return **0 hits** across every
commit, against 2057 in the pre-scrub mirror kept as a backup.

**The `"123"` one needed a `regex:` rule, not a literal one.** As a literal
replacement it would have matched the substring `123` in 15 unrelated files —
`pubspec.lock` version numbers, `123456` test PINs, `portal.py`. Anchoring it
to `#define MQTT_PASS\s+"123"` scrubs the credential and leaves the rest alone.

**Docs that had gone stale the moment the hashes changed**, now corrected:
`SECURITY.md` §1 (was telling you to `git show c0383b3`, which no longer
resolves), `check_leaked_secrets.py`'s leak descriptions, `rotate_secrets.sh`'s
header comment, and `10-security.md` §8.7 (still claimed the secrets were in
history).

**`check_leaked_secrets.py` gained two hashes** — the hotspot WiFi password and
the `"123"` MQTT password were leaked too but were never on its list, so a unit
still using either passed the healthcheck clean. Now it fails, as it should.

**Rotation is still not done, and the scrub did not do it.** Anyone who cloned
before today still holds working credentials; the router and HiveMQ passwords
in particular live outside anything this repo can reach. `SECURITY.md` §1
Steps 1–2 remain open.

`pytest pi/tests`: 182 passed.

---

## TL;DR of the previous session (2026-08-10 — WiFi provisioning + UART bridge fixed, full bench deploy)

Bench session on a real Pi Zero W + ESP32-C3 bridge + phone hotspot. Commits
`3ce691c`..`4f190ab` on `main`. Full details/rationale are in those commit
messages and `TODO.md` — this is the map of what changed and why it matters.

**C3 soil sensor was reading dead air.** `SOIL_DATA_PIN` was GPIO2, an
ESP32-C3 strapping pin some boards carry a hardware pull-up on — `analogRead()`
sat at 4095 permanently, indistinguishable from a cold solder joint. Moved to
GPIO1; readings now track moisture correctly. **Every already-wired C3 node
needs the AOUT wire moved + a reflash.** Added `firmware/sensor_pin_test/`, a
standalone bench sketch for checking joints without the mesh stack in the way.

**WiFi first-time setup was unusable — four stacked bugs, each masking the
next:** `/etc/greenhouse` was root-owned while the portal runs as `pi` (500 on
submit); `_reboot_soon()`'s `bash -c "sleep 3 && sudo reboot"` silently never
reached `sudo`; the typed SSID was written and committed with zero check it
was even visible (NetworkManager matches byte-for-byte — `billyredmi` typed
for a hotspot actually named `REDACTED_WIFI_SSID` cost most of a session before this
was fixed); and the watchdog reverted to AP mode recording nothing about why.
Now: `/connect` rejects an unreachable SSID and suggests the closest visible
name; the watchdog writes `wifi_fail.log` to the boot partition before
reverting, pairing the configured SSID byte-exact against what's actually
visible.

**UART bridge to the Pi: resolved, not actually broken.** The 2026-07-27
"zero bytes on `/dev/serial0`" turned out to be a diagnostic artifact — `head
-c N` buffers and gets killed by `timeout` before flushing, reading as a dead
link. An instrumented pyserial read shows the bridge heartbeating reliably
(11 lines/20s @ 115200). `dtoverlay=disable-bt` is **not** needed here despite
being the usual fix for this symptom — `enable_uart=1` already pins core_freq
on this Zero W.

**Not yet proven: any edge node delivering a reading.** The UART carries
`heartbeat` lines only; zero `reading` lines seen; no mesh rows reached the
recorder. `zone1`/`zone2` values visible in MQTT during this session were
*retained* messages from an earlier session, not live data — easy to mistake
for a working feed since retained topics read back instantly on subscribe.

**`serial_bridge.py` stale-status bug:** liveness tracking only started once a
heartbeat had been seen, so restarting the service while the ESP32 was
unpowered left every retained `online` standing forever. Now sweeps once at
startup and retracts stale claims if no heartbeat arrives within the timeout
window. Found two paho-mqtt ordering bugs writing the fix (callbacks must be
registered before `connect()`; `SUBSCRIBE` must be issued from `on_connect`,
not inline) — both made the sweep silently never run in the service even
though identical code worked driven by hand.

**Full `deploy.ps1` run against the bench unit**, which surfaced one more real
bug: `gen_certs.sh`'s mosquitto-cert idempotency guard sat in front of the
portal HTTPS keypair copy, so any unit whose mosquitto cert predated that copy
step could never get a portal keypair from any later install. Split into two
independent checks. `selftest.sh`: **45 passed, 0 failed** (was 29/37 before
this session). `selftest.sh` also gained a UART-bridge section — it never
checked `greenhouse-serial-bridge` before, only the unrelated
`greenhouse-hivemq-bridge`, so a dead sensor link could pass clean.

**App rebuilt and reinstalled from a second dev machine wiped local pairing
data** (full uninstall+reinstall, not an in-place update) — see the ⚠️ note
under Quick Start below for why and what to expect. Re-paired manually since
mDNS discovery doesn't work on a phone-hotspot topology (Android's
`MulticastLock` covers the client radio, not the SoftAP interface a hotspot's
own clients arrive on).

**Deliberately NOT done this session:** rotating the `app` MQTT password,
still `123` on this unit — `install.sh` only backfills *missing* `device.json`
fields, never regenerates existing ones, and rotating would break the
just-re-paired app. Use `rotate_secrets.sh` when that's wanted.

---

## TL;DR of this session (2026-08-02 — camera parked, dead weight removed)

The camera never worked well enough on real hardware to keep carrying, so it
was **parked, not deleted**: every camera file was `git mv`'d under
`parked/camera/` (history intact, `git log --follow` works), and everything
that referenced it was unwired.

- **App:** no Camera tab or `/camera` route; `cam/*` MQTT topic routing,
  the repository's cam streams / `fetchEventPhoto` / live-frame reassembly,
  `CamStatusRaw`/`CamEventChunkRaw`/`CamLiveFrameChunkRaw`, the
  `motion_alert` notification switch and the `'motion'` alert title are all
  gone from `app/lib`. Dependency `flutter_mjpeg` dropped.
- **Pi:** `install.sh` no longer installs `python3-pil`, provisions
  `cam_token.txt`, or copies/enables/restarts `greenhouse-cam-bridge` — and
  it now actively disables+removes that unit on units that already have it,
  so a redeploy leaves nothing running. `weather.py` no longer carries
  `motion_alert`.
- **CI:** `Pillow` dropped from the pip install line (only the parked
  motion tests needed it).
- **Docs:** technical docs, `DEVICES.md`, `ARCHITECTURE.md`, `TODO.md` and
  `IMPROVEMENTS.md` now mark the camera as parked rather than live. The
  design specs/plans under `docs/superpowers/` were left alone — they're
  history.

Net: −359 lines from the live tree, two dependencies gone, one fewer
always-on service on the Pi. Restore instructions (including the exact
shared-file edits to undo) are in `parked/camera/README.md`.

**Pi tests:** 115 passed. The 2 `test_push.py` failures seen locally are the
known `firebase-admin`-not-installed-in-sandbox case, not a regression — CI
installs it. Flutter isn't available in that sandbox, so `flutter analyze`
/ `flutter test` were verified by CI, not locally.

---

## TL;DR of this session (2026-07-28 — security hardening pass)

**Merge note (2026-08-07):** this pass predates the camera parking above.
Everything below that touches the camera — the `POST /cam/frame` gate, frame
signing, frame encryption, `/stream` token auth, the `CAM_TOKEN` provisioning
— was real work and its files moved intact under `parked/camera/`, but none
of it runs today: the cam bridge is not installed and `install.sh` removes it.
Treat those items as fixes banked against a future restore. The non-camera
hardening (portal HTTPS + auth, per-IP lockout, systemd sandboxing, ACL fix,
SSH hardening, secret rotation, leak detection) is live.

Full security review of every attack surface, then fixes. **One critical
vulnerability found and closed**, plus four smaller real issues. 30 new tests
(179 pytest / 187 Flutter, all green; `flutter analyze` clean).

**Critical — `POST /cam/frame` was completely unauthenticated.** `cam_bridge.py`
listens on `0.0.0.0:8090` and accepted a snapshot from anyone on the LAN. The
chain: `_update_heartbeat()` sets `_camera_ip` from the *sender's* address, so
one POST made an attacker "the camera"; every subsequent Pi fetch
(`/capture`, `/event/<id>`) then went to that attacker **carrying
`?token=CAM_TOKEN`** — handing over the token that authorizes
`DELETE /event/<id>`, i.e. the ability to wipe stored motion evidence off the
real camera's SD card. It also let the attacker feed arbitrary images to the
app as live/event frames and trigger unlimited motion alerts / push
notifications / DB rows. Fixed with a token gate (`X-Cam-Token` header, or
`?token=` for bench debugging), `hmac.compare_digest`, fail-closed when
unprovisioned, plus a body-size cap. 7 tests, **verified to fail** with the
gate removed.

**Also fixed:**
- `/api/history*` served the unit's entire sensor history to anything on the
  LAN/hotspot. Now bearer-token gated via a new per-unit `api_token`.
  `install.sh` backfills it onto already-provisioned units — without that,
  `first_boot.sh`'s `.provisioned` sentinel would have left existing Pis
  permanently 401ing.
- Camera `/stream` was the last open endpoint (`IMPROVEMENTS.md §Α5`'s
  remainder). Built the full cross-stack chain: portal payload →
  `ConnectionConfig.camToken` → `streamUrl()` → firmware `checkCamToken()`.
- PIN comparison used `!=`, which short-circuits and leaks the correct prefix
  through response timing. Now `hmac.compare_digest`.
- Mosquitto ACL allowed the `bridge` user 4 topics but the firmware publishes
  6 — `/battery` and `/mesh` were being **silently denied** since the
  2026-07-26 telemetry work. Security-config bug and a functional one.

**Hardening:** full systemd sandbox set across all services (two documented
exceptions: serial-bridge keeps `/dev` access, portal keeps setuid-sudo
compatibility); portal gained CSP with a per-request nonce, security headers,
a 64KB body cap, and no longer echoes exception text to clients; `selftest.sh`
gained security regression checks that assert the gates return 401.

**Second wave (same session) — the gaps the first pass had only documented:**
- **TLS on the LAN links.** The portal now serves **HTTPS on 8443** using the
  same per-unit cert as the broker, and the app pins the same fingerprint it
  already pins for MQTT (new shared `app/lib/utils/cert_pinning.dart`; the
  MQTT path was refactored onto it so the two can't drift). Port 80 stays
  plaintext *only* for the captive portal, which can't work over TLS. Verified
  end-to-end against a real TLS server: authenticated 200, unauthenticated 401,
  enforcement flag 403s plaintext while leaving the captive portal reachable.
  An opt-in `/etc/greenhouse/require_https` closes the downgrade path once
  every paired phone is updated (not default — it would lock out old builds).
- **Global pairing lockout was itself a DoS.** 5 wrong PINs from *anyone*
  latched a global flag until service restart, so any device on the network
  could permanently block the owner from pairing. Now per-IP with automatic
  expiry, a bounded state table, a much higher global backstop, and
  `X-Forwarded-For` deliberately not trusted.
- **`CAM_TOKEN` default was a value published in this repo.** Now generated
  randomly per unit in `first_boot.sh` (so clones differ), auto-replaced on
  units still holding the placeholder, and `selftest.sh` fails if it sees it.
- **Shared fleet admin SSH key** is no longer baked into shipped clones
  (`prep_image.sh` strips it; `KEEP_ADMIN_KEY=1` retains it deliberately), and
  SSH password auth is disabled — guarded, so a unit with no authorized key
  keeps password login rather than locking the owner out, with `sshd -t`
  validation and rollback.
- Rate limiting on the camera frame intake, checked before the token compare.

**Third wave — closing what the second wave had called structural:**
- **First-contact MITM is now closed for the QR path.** Pairing pins the
  certificate whenever a fingerprint is known out of band (QR scan or manual
  entry), rejecting a mismatched cert *before* the PIN is sent, with an
  explicit security warning in the UI. It never silently downgrades to
  plaintext once a fingerprint is known. Only the mDNS-without-QR path still
  trusts on first use — and a 6-digit PIN provably cannot fix that (any
  proof-of-knowledge is offline brute-forceable from one captured exchange),
  so the pairing screen now recommends QR as the secure path.
- **`CAM_TOKEN` no longer crosses the wire.** Frame POSTs are HMAC-signed —
  `HMAC-SHA256(token, timestamp + sha256(body))` — so a passive sniffer sees
  only a signature bound to one body and one moment. Replays are rejected. I
  transcribed the firmware's signing independently and verified the Pi accepts
  it, and that tampered bodies, wrong tokens, stale timestamps, malformed
  timestamps, and replays are all refused. `no_unsigned_cam` drops the legacy
  bearer path once every camera is reflashed.
- **Credential rotation is now one command.** `pi/scripts/rotate_secrets.sh`
  rotates every Pi-owned secret (both MQTT passwords, API token, PIN, cam
  token, and the TLS keypair) and prints exactly what must be re-paired and
  re-flashed. New `SECURITY.md` carries the full checklist including the
  external steps (router, HiveMQ) and the git-history scrub procedure.
- Confirmed the leak is real and specific rather than assumed: commit
  `c0383b3` carries a live WiFi password and MQTT password. `SECURITY.md`
  names them so the rotation can be verified as complete.

**Fourth wave — the last three code-fixable items:**
- **Camera frames are now encrypted**, closing the final plaintext data path.
  AES-256-GCM, key = `HMAC(CAM_TOKEN, "greenhouse-cam-frame-key")`, wire format
  `nonce||ciphertext||tag`. No new dependency: `cryptography` already arrives
  with `firebase-admin`, and the import is guarded so a unit without it rejects
  encrypted frames rather than crashing. I transcribed the firmware's
  `encryptFrame()` byte-for-byte and confirmed the Pi decrypts it, that the key
  derivations match, and that the plaintext JPEG never appears in the wire
  bytes. `require_encrypted_cam` refuses plaintext once cameras are reflashed.
- **Safety code** (`ABCD-EF12`, derived from the pinned fingerprint) is printed
  by `selftest.sh` and shown in the app under Settings. This is what makes an
  active MITM on the mDNS pairing path *detectable* — its substituted
  certificate yields a different code. Both derivations are cross-checked by a
  test that pins the exact expected value, so they can't drift apart.
- **`check_leaked_secrets.py`** fails the healthcheck while any credential
  published in git history (verified: `c0383b3`) or any public repo placeholder
  is still live — so "did I actually rotate?" is answered by the system, not by
  memory. 8 tests.

**Explicitly still open** — see `SECURITY.md §3`:
1. **Rotating the router WiFi and HiveMQ Cloud passwords** — everything the Pi
   owns is now one command (`rotate_secrets.sh`); these two live in systems
   this repo cannot reach.
2. **Active MITM on a first mDNS pairing is detectable, not prevented** —
   compare the safety code, or pair via QR (which *is* prevented). Genuinely
   unfixable with a 6-digit PIN without a PAKE.
3. **Shared ESP-NOW keys** — per-node keys need provisioning the project
   doesn't have, and per-pair derivation from a shared PMK buys nothing.
   Deliberately not attempted blind: changing mesh crypto with no compiler or
   hardware risks silently breaking fleet mesh join, the exact failure mode
   this repo's docs call hardest to diagnose remotely.
4. **Single-tenant HiveMQ and unencrypted SD card** — the first needs
   per-customer cloud infrastructure; the second has no good answer on headless
   Pi Zero W hardware with no TPM.

Everything in `firmware/` still carries the standing caveat that nothing here
has been compiled or flashed from this sandbox — the camera's new signing code
in particular needs a bench run.

---

## Next step

> **Superseded by 2026-08-10 (see that TL;DR above):** item 1 below is
> resolved — the bridge was never broken, "zero bytes" was a diagnostic
> artifact of `head -c N` getting killed by `timeout` before flushing. Item 2
> is moot — the camera was parked on 2026-08-02, after this list was written.
> Current next step is proving an edge node delivers a real `reading` over the
> now-working UART link, per that TL;DR's "not yet proven" note.

**Immediate — from the later 2026-07-27 session:**

1. ~~**Bridge UART: confirm reflash status.** Wiring + power are confirmed
   correct (see that session's TL;DR), but `/dev/serial0` shows zero bytes.
   Check whether `bridge_esp32.ino` (current UART version) has actually been
   flashed onto the board yet — if it's still running the old WiFi/MQTT
   firmware, that fully explains zero UART output. If reflashing doesn't fix
   it, `firmware/bridge_esp32_wifi_fallback/` is ready to flash instead (WiFi
   + MQTT direct to the Pi, no GPIO wiring/raspi-config step needed) — a real
   `bridge` MQTT account already exists on the Pi for it.~~
2. ~~**ESP32-CAM: flash and bench-test.** Firmware now compiles (missing
   `secrets.h` and a missing `UriBraces` include were both real blockers,
   now fixed) — this hasn't been flashed/tested yet. Watch the serial
   monitor at 115200 baud for camera/SD init, WiFi connect (`REDACTED_WIFI_SSID`),
   and the snapshot-POST cadence reaching `cam_bridge.py`.~~
3. **Mesh relay + deep-sleep Phase 1** — per
   `docs/superpowers/plans/2026-07-26-mesh-deep-sleep.md` Task 6 and the
   original relay plan's Task 5. Untouched this session.
4. Once all of the above are confirmed working end-to-end, this repo will
   finally have its firmware fully field-validated, not just code-reviewed.

**Also worth doing:** the hardware bring-up edits stashed at the top of this
session's TL;DR (`git stash list`) have real learned values (device MACs,
GPIO pin fixes, soil calibration) that were never reapplied against the
newer UART-based firmware — worth a deliberate pass rather than leaving
them stashed indefinitely.

**After that**, other candidates (see `TODO.md` for the full picture):

1. **Phase 2 mesh deep sleep** (synchronized wake windows so relay-capable
   nodes can sleep too) — deliberately deferred until Phase 1's bench run
   produces real per-board RTC-drift measurements; see the deep-sleep
   spec's §Phase 2.
2. **Direct-to-Pi pairing + PIN auth** (`docs/superpowers/specs/2026-07-17-direct-pi-pairing-design.md`)
   — approved in conversation, no implementation plan written yet.
3. Work through `IMPROVEMENTS.md`'s remaining open findings — Β3 (LAN
   streaming blocks motion detection) is directly relevant to the cam bench
   work above; Α1's rotation step (WiFi/HiveMQ credentials) is still a live
   real exposure whenever convenient, and is now smaller in scope than
   before — the bridge no longer has WiFi/MQTT credentials to rotate at
   all after this session's change (only the cam's WiFi credentials and
   the Pi's own HiveMQ credentials remain).

Ask the user which before picking one — none was prioritized explicitly
beyond the immediate cam debugging already underway.

---

## TL;DR of this session (2026-07-27, later — login bugs + bench bring-up)

Started as "pull latest, deploy the Pi, rebuild the app" and became two real
bug hunts plus hardware bring-up, after the user (rightly) pushed back hard
on "check your input" guesses that turned out wrong — see
[[feedback_verify_dont_blame_user]] in memory for the process lesson.

**Repo sync:** local `main` was 51 commits behind `origin/main` with
uncommitted hardware bring-up edits (real MACs, WiFi creds, GPIO pin fixes,
soil calibration) that conflicted with the newer UART-bridge rewrite.
Rebased the one genuine local fix (cam-bridge restart-on-redeploy) onto the
pulled history and stashed the rest rather than guess-merging hardware
values into now-restructured files — that stash (`stash@{0}` as of this
writing) still has those edits if anyone wants to reapply them deliberately.

**Bug 1 — permanent black screen on reinstall:** `PairingService.loadConfig()`
let a `BadPaddingException` (stale Keystore-encrypted blob after a reinstall)
escape into go_router's redirect, leaving the router with no route to
resolve. Fixed with try/catch + `resetOnError: true`; 5 new tests.

**Bug 2 — the real one, took most of the session:** every single TLS/MQTT
login attempt failed with a generic "Could not connect", regardless of how
correct the address/password/fingerprint were. Traced (after wrongly
suggesting typos/autocorrect multiple times — the thing the user was angry
about) to an unsound internal cast in `mqtt_client` 10.11.11:
`onBadCertificate as bool Function(Object)?` throws a `TypeError` for any
real `X509Certificate`-typed callback, before a single cert is inspected.
Broken since TLS pinning was added (PR #12); silently swallowed by
`_tryConnect`'s catch. Fixed by typing the app's callback parameter as
`Object` instead (contravariance still satisfies the field's declared
type; runtime type now matches what the internal cast expects). Verified
end-to-end against the real Pi: `CONNECTED SUCCESSFULLY` /
`connectionAccepted`. Also fixed underneath: the Pi's self-signed chain
triggers `onBadCertificate` twice (CA, then leaf — the leaf has no SAN, so
connecting by IP is a separate hostname-mismatch failure), so
`tls_fingerprint` is now a comma-separated list of both, generated by
`first_boot.sh`.

**mDNS ("Find my greenhouse"):** added a real `WifiManager.MulticastLock`
via a new Android platform channel (`MainActivity.kt` +
`multicast_lock.dart`), confirmed via logcat that it acquires/releases
correctly — and discovery *still* doesn't find the Pi. Reason: the bench
network is the phone's own WiFi hotspot, not a router, and `MulticastLock`
governs the WiFi **client** radio's multicast filter, not the SoftAP
interface a hotspot's own clients arrive on. The fix is correct and will
help on a shared router; it doesn't and can't fix this specific topology.
Manual IP entry is the reliable path while on this hotspot.

**Hardware bring-up:**
- **Bridge (UART):** wiring + power confirmed correct against
  `INSTRUCTIONS.md` Part 6 (GPIO4→pin10/RXD, GPIO5←pin8/TXD, GND→pin6,
  5V→pin2/4, board's LED lit) — but `/dev/serial0` shows zero bytes across
  repeated samples. Not yet confirmed whether the board has actually been
  reflashed with the current `bridge_esp32.ino` since the UART rewrite;
  that's the leading suspect and the next thing to check.
- **Temporary WiFi fallback added:** `firmware/bridge_esp32_wifi_fallback/`
  — the last pre-UART bridge firmware, restored from git history and
  updated to the current network/credentials, so the user can get sensor
  data flowing again without the UART link while that gets sorted. Created
  a real `bridge` MQTT account on the Pi for it (didn't exist —
  `device.json` predated that field).
- **Camera:** never connected all session (`greenhouse/cam/status` stuck at
  `online: false`) — root cause was `secrets.h` never existing, so the
  current firmware couldn't even compile. Created it (gitignored,
  per-device), added the missing Arduino-library junction (same pattern as
  `GreenhouseMesh`), set a real `CAM_TOKEN` on the Pi (was still the
  install-time placeholder), and fixed two real compile bugs found while
  actually trying to flash it: missing `#include <uri/UriBraces.h>>`, and
  `PI_HOST` switched from `greenhouse.local` to a direct IP (`HTTPClient`
  doesn't resolve mDNS reliably on this core — same class of bug as the
  mqtt_client fix above). Compiles now; not yet flashed/bench-tested.

**Also added:** `docs/DEVICES.md` (real device MAC registry + Arduino IDE
setup notes, committed — was sitting untracked from an earlier local
session), `firmware/fake_edge_node_esp32_c3/` (random-walk fake sensor node
for bench-testing the pipeline without real sensors), `install_to_pi.py`
(cross-platform paramiko/scp alternative to `deploy.ps1`).

**Deployed:** Pi redeployed via `deploy.ps1` after the repo sync (28/28 real
checks, 2 transient restart-timing failures — normal). App rebuilt and
installed multiple times across the session; last install has all fixes
above. Two confirmed-stale remote branches deleted after verifying their
content was fully superseded on `main`.

---

## Older sessions

Session TL;DRs from 2026-07-08 through 2026-07-27 (UART-wired bridge
implementation, mesh deep sleep + field visualization, the ESP32-CAM's
first design pass, the technical-docs/CI session, FCM push + alert rules,
and earlier) were moved to `docs/archive/HANDOFF_ARCHIVE.md` on 2026-08-07
to keep this file focused on current state. That archive is historical —
`TODO.md` and `IMPROVEMENTS.md` are the maintained, code-verified backlog.

---

## Quick Start (next session)

```bash
# SSH to the master Pi — passwordless (admin key authorized)
ssh pi@greenhouse.local
# DHCP IP varies by session; OS password is per-unit random (see
# /boot/firmware/INITIAL_PASSWORD.txt) — use the SSH key, not a password.

# Verify the Pi
ssh pi@greenhouse.local "sudo bash /home/pi/greenhouse/scripts/selftest.sh"
# Expect 45/45 (as of 2026-08-10, real bench unit). If "portal not responding"
# or "history endpoint NOT protected (got 000)" shows up, it's a startup race
# — the portal takes ~6-8s to rebind port 80 on a Zero W after any restart.
# Rerun a few seconds later before treating either as a real failure.

# Reopen the pairing window (it auto-expires after 600s uptime)
ssh pi@greenhouse.local "sudo systemctl restart greenhouse-portal"

# Redeploy the Pi side after code changes — from repo root, NOT manual scp:
.\deploy.ps1                          # defaults to greenhouse.local
.\deploy.ps1 -PiHost 192.168.1.54     # or target a specific IP

# No real sensors attached? greenhouse-simulator.service is installed
# (disabled by default) — enable it on demo/no-real-sensor units instead of
# the old ad-hoc systemd-run one-liner (IMPROVEMENTS.md Δ4):
ssh pi@greenhouse.local "sudo systemctl enable --now greenhouse-simulator"

# Build + install the app (phone via USB)
export PATH="$PATH:/c/Users/billy/flutter/bin"   # Git Bash
cd app
flutter pub get
flutter build apk --release
flutter install -d <device-id>   # `flutter devices` to list; approve the
                                  # install prompt ON THE PHONE when it appears
```

> ⚠️ **`flutter install` may silently WIPE the phone's local app data**
> (pairing state, secure storage, cached prefs) even though the release build
> type signs with the same `debug` signing config as debug builds
> (`android/app/build.gradle:39`). This happens whenever the installed APK's
> *actual* debug keystore doesn't match this machine's `~/.android/debug.keystore`
> — e.g. the previous install came from a different dev machine. Android then
> refuses an in-place update (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`) and Flutter
> silently falls back to uninstall-then-install. Tell: `flutter install` prints
> "Uninstalling old version..." instead of updating in place, and
> `adb shell dumpsys package <pkg> | grep firstInstallTime` matches
> `lastUpdateTime` exactly (bench-confirmed 2026-08-10). No known way to avoid
> this short of sharing one debug keystore across every dev machine — just
> expect a full re-pair after installing from a machine that hasn't built this
> app before.

---

## Architecture (current)

```
┌─────────── FIELD / GREENHOUSE ───────────┐
│  ESP-NOW sensor nodes, dynamic multi-hop  │  (mesh relay + deep-sleep
│    mesh relay (+ Phase 1 deep sleep for   │   phase 1 + UART bridge
│    battery nodes) → ESP32-C3 bridge       │   firmware all done, not
└──────────────────┬─────────────────────────┘   yet field-tested on real
                    │ UART GPIO, 3.3V, no router     HW — see this session's
                    │ (replaces WiFi/MQTT/TLS,        specs + docs/MESH_
                    │  2026-07-27)                    RELAY_EXPLAINED.md)
┌───────────────────▼───────────────────────────────┐
│  Raspberry Pi Zero W                              │
│  ├─ greenhouse-serial-bridge — UART→loopback MQTT  │
│  ├─ Mosquitto — local TLS 8883 + HiveMQ Cloud bridge│
│  ├─ greenhouse-recorder — SQLite history (minute   │
│  │    buckets → hourly rollup, 90d/2yr retention)  │
│  ├─ greenhouse-weather — Open-Meteo + automation   │
│  │    rules + forecast publish                     │
│  └─ greenhouse-portal — Flask :80, WiFi setup +    │
│       /pair + /api/history[/series]                │
└───────────────────┬───────────────────────────────┘
                    │ LAN (port 8883/80) or HiveMQ Cloud bridge (remote)
┌───────────────────▼───────────────────────────────┐
│  Flutter app (Android; iOS untested)              │
│  Dashboard, Control, Devices (+ Mesh Map screen,   │
│  2026-07-26), Weather+Rules, History, Settings     │
└─────────────────────────────────────────────────────┘
```

Remote access is **HiveMQ Cloud**, not Tailscale (dropped that plan entirely). No InfluxDB/Node-RED/Grafana — replaced by the lighter local SQLite recorder + in-app automation rules + push notifications, since the Pi Zero W can't comfortably run heavier services.

---

## Key files

| File | Role |
|---|---|
| `pi/install.sh` | Master installer (idempotent): packages, TLS, Mosquitto, all 6 systemd services |
| `pi/scripts/recorder.py` | Sensor history recorder — MQTT ingest → SQLite minute buckets → hourly rollup |
| `pi/scripts/weather.py` | Open-Meteo polling, automation rules engine, forecast publish |
| `pi/portal/portal.py` | Flask :80 — WiFi setup, `/pair`, `/api/history`, `/api/history/series` |
| `pi/tools/simulator.py` | Fake sensor data generator — use when no real edge nodes are attached |
| `pi/scripts/hivemq_bridge.py` | HiveMQ Cloud bridge (paho-mqtt) — replaces Mosquitto's native bridge, which never worked (see below) |
| `firmware/libraries/GreenhouseMesh/` | Shared mesh-relay library (`mesh_config.h` keys/trusted-nodes/tuning, `mesh_node.h` routing/relay logic) — 2026-07-09 session, see `docs/MESH_RELAY_EXPLAINED.md` |
| `deploy.ps1` | One command: scp + install + selftest on any Pi — **use this, not manual scp** |
| `app/lib/screens/history/history_screen.dart` | The chart screen (fl_chart) + custom date-range chip (2026-07-09) |
| `app/lib/utils/history_prediction.dart` | Trend-extrapolation + forecast-overlay prediction logic |
| `pi/shared/history_query.py` | Shared `query_points()` used by both `portal.py` (HTTP) and `recorder.py` (MQTT) — 2026-07-09, closes an old duplication gap |
| `pi/shared/push.py` | FCM push helper — `send_push()`, reads registered device tokens from a retained MQTT topic — 2026-07-10 |
| `app/lib/services/fcm_token_service.dart` | Registers/refreshes the device's FCM token over MQTT (retained) — 2026-07-10 |
| `app/lib/screens/weather/rule_form_dialog.dart` | The customizable rule builder dialog (any zone/metric/operator/threshold/duration/action/notify) — 2026-07-10 |
| `app/lib/models/weather_rule.dart` | Rule model — zone+metric split, optional action, optional duration, per-rule notify flag — rewritten 2026-07-10 |
| `docs/technical/00-INDEX.md` | Entry point to the 15-file OSI-level Greek technical deep-dive (protocol/hardware/security/db detail) — 2026-07-20 |
| `TODO.md` | Consolidated, code-verified list of designed-but-unbuilt and built-but-hardware-unvalidated work — 2026-07-20 |
| `IMPROVEMENTS.md` | Code-verified list of things that work but could be better (security/correctness/performance/process), each with `file:line` — 2026-07-20 |
| `.github/workflows/ci.yml` | pytest + flutter analyze/test on every PR — 2026-07-20, previously nothing ran automated |
| `docs/superpowers/specs/2026-07-26-mesh-deep-sleep-design.md` + plan | Deep-sleep Phase 1 (leaf sleep) design + 6-task implementation plan (Tasks 1-5 done) — this session |
| `docs/superpowers/specs/2026-07-26-mesh-field-visualization-design.md` + plan | Mesh Map screen design + 6-task implementation plan (fully done) — this session |
| `firmware/libraries/GreenhouseMesh/mesh_node.h` | Wire format v2 (sleepy flag, battery/parent telemetry), RTC-persistent state helpers — this session |
| `app/lib/screens/devices/mesh_map_screen.dart` + `mesh_map/` | The live Mesh Map screen: layout engine, link painter, node card, pinned-position store — this session |
| `pi/tools/simulator.py` | Also now publishes `/mesh` topology JSON (fake shifting topology) — this session |
| `docs/superpowers/specs/2026-07-20-uart-bridge-design.md` + plan | UART-wired bridge design + 5-task plan, now approved+implemented (2026-07-27) |
| `firmware/bridge_esp32/bridge_esp32.ino` | Rewritten: UART (`Serial1`) instead of WiFi/MQTT/TLS, heartbeat liveness, battery/mesh telemetry over the wire — 2026-07-27 |
| `pi/scripts/serial_bridge.py` | New — reads the bridge's UART JSON stream, republishes to loopback Mosquitto — 2026-07-27 |
| `pi/systemd/greenhouse-serial-bridge.service` | New systemd unit for the above — 2026-07-27 |

---

## Scope status

The project was originally scoped as 6 slices (`docs/superpowers/specs/2026-06-25-greenhouse-app-connectivity-design.md` §2). Status against that scope, plus everything found since:

**Slice status:**
- 1 App + Connectivity — ✅ done
- 2 Field Firmware (ESP-NOW mesh, WROOM bridges) — firmware done, including 2026-07-09's dynamic multi-hop relay upgrade, 2026-07-26's Phase 1 deep-sleep + telemetry upgrade, and 2026-07-27's UART-wired bridge (full replacement — the old WiFi/MQTT/TLS bridge code no longer exists in the tree, only in git history); **still not field-validated on real sensor hardware** (simulator only — none of the relay/deep-sleep/UART-bridge code has ever been compiled/flashed, no toolchain in the dev sandbox); BLE pairing was planned but superseded by the working mDNS/QR discovery instead
- 3 Storage + History — ✅ done, reimplemented as a local SQLite recorder (not InfluxDB) + this session's chart feature
- 4 Automation + Alerts — ✅ done, and this session made it fully customizable: in-app rule builder (any zone/metric/operator/threshold/duration/action, Weather screen → Rules tab → "Add rule") instead of six hardcoded thresholds, plus a real fix for rule edits never having reached the Pi (see this session's TL;DR above)
- 5 Cloud Relay (multi-customer accounts, device registry, FCM push) — **partially done this session**: FCM push notifications now work (app closed/backgrounded still gets real alerts) via `pi/shared/push.py` + a retained-token registry topic; multi-customer accounts/device registry still **not started** — current remote access is still single-tenant HiveMQ Cloud
- 6 Field Hardening (solar/18650, IP65 enclosures, cellular fallback) — hardware mods **not started**, but the firmware duty-cycle side is now coded: 2026-07-26's deep-sleep Phase 1 (leaf sleep for battery nodes), see `docs/EDGE_NODE_POWER_OPTIMIZATION.md` and `docs/superpowers/plans/2026-07-26-mesh-deep-sleep.md`. Never compiled/flashed yet.

**Fixed this session (2026-07-08, later):** Mosquitto's native `connection` bridge to HiveMQ Cloud had never actually worked — 0 successful handshakes across 9 days of logs, a real Mosquitto bridge-code incompatibility with this HiveMQ cluster (not a quota/account issue). Any prior appearance of "remote access working" was the app displaying a stale retained value, not live data. Replaced with `greenhouse-hivemq-bridge.service` (small paho-mqtt forwarder script) — verified live two-way delivery, stable connection, automated round-trip check added to `selftest.sh` (now 26/26).

**Also fixed this session:** history charts now work remotely too. They previously called the Pi's HTTP `/api/history` directly, which only exists on the LAN (HiveMQ bridges MQTT, not HTTP) — so charts failed with "could not load" as soon as remote MQTT access started actually working and got tested. Added an MQTT request/response path (`greenhouse/history/request` → `greenhouse/history/response/<id>`, answered by `greenhouse-recorder`); the app now picks HTTP or MQTT based on whether it's connected local or remote. Verified end-to-end against the real HiveMQ cluster (409 real points returned).

**Detailed backlog: see `TODO.md` and `IMPROVEMENTS.md` instead of this
section.** This used to be a checklist duplicating both files and had
drifted out of sync with them (e.g. still claiming `/pair` was fully
unauthenticated after PIN-gated `/pair/confirm` had already shipped, and
still describing the ESP-NOW channel-follows-router-SSID dependency as an
open future risk after the UART-bridge work had already replaced it with a
fixed channel) — two backlogs disagreeing with each other is worse than
one. `TODO.md` covers designed-but-unbuilt and built-but-hardware-
unvalidated work; `IMPROVEMENTS.md` covers things that work but could be
better (security/correctness/performance), each with real `file:line`
references, kept current each session and cross-checked against the actual
tree rather than trusted checkbox state.

---

## Pi details (master unit)

| Item | Value |
|---|---|
| Hostname / mDNS | `greenhouse` / `greenhouse.local` |
| User / password | `pi` / per-unit random (`/boot/firmware/INITIAL_PASSWORD.txt`); use the SSH key |
| SSH key | `C:\Users\billy\.ssh\id_ed25519` (passwordless; baked into every image as admin key) |
| MQTT | 8883 TCP-TLS local (user `app`, per-unit password in `/etc/greenhouse/device.json`); HiveMQ Cloud bridge for remote |
| Pairing window | 600s after `greenhouse-portal` starts; restart the service to reopen (~20s to rebind port 80) |
| Recorder DB | `/var/lib/greenhouse/greenhouse.db` (SQLite, WAL mode) |
