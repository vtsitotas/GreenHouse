# Unlimited Sensors + In-App Sensor Onboarding — Design Spec

**Date:** 2026-09-09
**Status:** Designed, approved, not yet implemented.
**Supersedes the analysis in:** `docs/SCALING_AND_EXPANSION_IDEAS.md` (2026-08-12). That
document reached the right core insight (end-to-end encryption with dumb relays) but placed
the trust store on the bridge, and then spent its §4δ cataloguing the failure modes that
choice creates. This spec puts the trust store on the Pi instead, which removes those
failure modes rather than mitigating them. See §Alternatives considered.
**Invalidates an assumption in:** `2026-08-17-mesh-phase2-synced-wake-design.md` §Non-goals,
which justifies its depth cap partly on "this fleet's ESP-NOW 7-peer limit (≤ 8
`TRUSTED_NODES` total) makes deep chains rare anyway". After this change that limit no
longer exists; the depth cap needs a fresh justification on its own merits.

## Background

Two user-visible limits, with three distinct causes, all verified in the code rather than
recalled.

**Only 8 devices can ever exist.** `meshInit()`
(`firmware/libraries/GreenhouseMesh/mesh_node.h:143`) registers every entry of
`TRUSTED_NODES[]` as an ESP-NOW *encrypted* peer at boot, because — as its own comment
explains — any node might dynamically become any other's parent at runtime. ESP-NOW's
default ceiling is 7 encrypted peers, so N−1 ≤ 7 → N ≤ 8. One bridge + seven sensors.

**Adding a sensor means reflashing the whole fleet.** `TRUSTED_NODES[]`
(`mesh_config.h:124`) is `static const` — part of the binary, not configuration. It cannot
be changed from the Pi, over SSH, or at runtime. And updating only the bridge is not enough,
for the same pairwise-peer reason.

**One key protects the entire network.** `MESH_PMK`/`MESH_LMK` (`mesh_config.h:101-106`) are
network-wide. `SECURITY.md` already records this: physically stealing one node exposes the
whole fleet's key.

The goal is that the owner buys a sensor, opens the app, scans the QR on its box, picks a
zone, and it works — with no limit on how many, no firmware toolchain, and no reflash of
anything already deployed.

**The prerequisite is now met.** `SCALING_AND_EXPANSION_IDEAS.md` §4α argued this work should
wait because no edge node had ever reliably delivered a reading, so a protocol change would
mean debugging two unknowns at once. That is resolved: `HANDOFF.md` records the mesh as
proven end to end, including real multi-hop relay, on 2026-08-16.

### The three facts this design rests on

Verified against the ESP-IDF ESP-NOW API reference for the ESP32-C3, not assumed:

1. **20 peers total, 7 encrypted by default** (configurable up to 17, but only by rebuilding
   the core's sdkconfig — not reachable from the Arduino IDE, which is this project's
   toolchain).
2. **Receiving from an unregistered peer works** for broadcast and *unencrypted* unicast.
   Registration is required only to *send*.
3. **250-byte payload limit** (ESP-NOW v1.0), against today's 33-byte data packet.

Fact 2 is the load-bearing one. A node only ever unicasts to a single destination — its
parent. If the radio layer is not doing the encryption, each node needs exactly two
registered peers, forever: broadcast, and its current parent. The 8-node ceiling is not a
property of the chip or the mesh; it is a property of using ESP-NOW's encrypted-peer
mechanism, which this design stops using.

## Goals

1. No architectural cap on sensor count. Every node registers ≤ 2 ESP-NOW peers regardless
   of fleet size.
2. Adding a sensor touches only the Pi's state. No firmware change, no reflash of any
   already-deployed node, no recompile, no SSH.
3. Per-device keys. Compromising one sensor exposes that sensor only — closing the
   `SECURITY.md` gap.
4. End-to-end confidentiality and authenticity from sensor to Pi. Relays forward ciphertext
   they cannot read and cannot forge.
5. Onboarding is: scan a QR, pick a zone, done. No MAC addresses typed, no technical
   vocabulary, no knowledge of what a mesh is.
6. Zone assignment and battery/mains role become runtime choices made in the app, not
   compile-time constants.

## Non-goals

**Range.** Battery nodes stay leaf-only (`MESH_FLAG_SLEEPY` is rejected before parent
selection in `meshHandleBeacon()`), so a distant sensor still needs a mains-powered node
between it and the hub. Unchanged by this work.

**Apple/Google Home interoperability.** Needs Thread/Matter, which needs an 802.15.4 radio —
a different chip. Out of scope.

**OTA firmware updates.** Independently valuable, independently risky, and not required by
either goal here. Explicitly deferred.

**Rotating a sensor's AppKey after commissioning.** Re-provisioning (remove in the app,
scan again) is the supported path. In-place rotation is not designed here.

**Backward compatibility with the current wire format.** Deliberately not attempted — see
§Migration.

## Architecture

### Key model

| Key | Size | Lives in | Known to |
|---|---|---|---|
| **AppKey** | 16 B | sensor NVS + the QR on its box | that sensor, and the Pi |
| **NetKey** | 16 B | Pi (authoritative); sensor NVS after provisioning; bridge **RAM only** | every provisioned node |

The AppKey is generated per sensor at provisioning time and never travels over the air. The
NetKey authenticates beacons and packet headers so relays can cheaply drop garbage and
strangers cannot inject routing state; it never protects sensor data.

The bridge receives the NetKey from the Pi over UART on every startup and holds it in RAM
only — never NVS. A bridge stolen while powered off yields nothing; powered on it yields the
NetKey, which cannot decrypt any sensor reading. The bridge never holds an AppKey at all.

**The Pi is the single source of truth.** There is no second copy of the trust store to
diverge from it.

### Wire formats

Both structures are versioned by the existing `magic` byte; the new format uses a distinct
value so an old node's packet is rejected on sight rather than misparsed.

**Beacon** (broadcast, cleartext, as today) gains an 8-byte truncated CMAC-AES tag over all
preceding fields, keyed with the NetKey. A node without the NetKey cannot produce a valid
beacon and therefore cannot be adopted as anyone's parent. Everything else about beaconing —
rank advertisement, trickle backoff, parent timeout, orphan beacons, self-heal — is
untouched.

**Data packet:**

```
[ header, cleartext ]   magic(1) origin_mac(6) seq(2) boot_count(4) flags(1) rank(1) ttl(1)   16 B
[ nettag ]              CMAC-AES(NetKey) over the header EXCLUDING ttl                          8 B
[ ciphertext ]          AES-GCM(AppKey): temp, humidity, soil, battery_mv, parent_mac, rssi    21 B
[ apptag ]              GCM authentication tag                                                  16 B
                                                                                        total ≈ 61 B
```

`ttl` is excluded from the nettag because it is mutated at every hop; including it would make
the tag unverifiable downstream. This is the same mutable-field exclusion IPsec AH makes.
The consequence is that `ttl` is attacker-modifiable, so every receiver drops any packet with
`ttl > MESH_MAX_TTL` before forwarding. Loop prevention does not depend on `ttl` anyway — the
strict-rank rule makes loops structurally impossible, and `ttl` is defence in depth.

Relays verify the nettag and forward. Only the Pi holds AppKeys, so only the Pi verifies the
GCM tag and reads the measurements.

### Nonce construction — the part most likely to be got wrong

`nonce = origin_mac(6) ‖ boot_count(4) ‖ seq(2)` = exactly the 12 bytes AES-GCM wants.

`SCALING_AND_EXPANSION_IDEAS.md` §4γ identified this correctly and it is worth restating in
full, because the failure is silent and catastrophic. `seq` lives in RTC memory: it survives
deep sleep but **not** power loss — and power loss is not hypothetical here, it has already
been observed on the bench, where deep-sleep current draw was low enough that powerbanks
switched themselves off. A `seq` that restarts at 0 under the same key reuses a nonce. In
AES-GCM, nonce reuse does not merely leak plaintext (already bad, since readings are highly
predictable); it leaks the authentication subkey, letting an attacker forge packets the Pi
accepts as genuine. In this system a forged "soil moisture 5%" makes the rules engine open
the irrigation valve.

`boot_count` is therefore stored in **NVS flash**, not RTC memory, and incremented **only on
a cold boot** — a timer wake continues the existing `seq` and writes nothing. A node waking
on the production 15-minute cycle writes to flash only when it actually loses power, so the
write count over a decade is in the thousands, well inside NVS wear-levelling limits. This is
measured during the bench phase, not assumed.

The Pi rejects any packet whose `(boot_count, seq)` pair it has already accepted from that
MAC, and rejects a `boot_count` lower than the highest seen. This makes replay detection
explicit rather than a side effect of the de-dup cache.

The existing hop-level de-dup cache (`MESH_DEDUP_CACHE_SIZE`, `MESH_DEDUP_WINDOW_MS`) stays
exactly as it is on relays and the bridge: it reads only `origin_mac` and `seq`, both still
cleartext, and its job is suppressing route-flap duplicates in flight, not security. The two
mechanisms are independent and both are kept — the Pi's check is authoritative and permanent,
the mesh's is a cheap in-flight optimisation with a 30-second window. Note that the compile-
time assertions tying that window to `MESH_SLEEP_INTERVAL_MS` (`mesh_config.h:84-89`) remain
load-bearing and must survive the refactor.

### How the peer limit dies

`meshInit()`'s registration loop is deleted. Instead:

- **Broadcast peer** — registered once at boot, unencrypted, as today.
- **Parent peer** — `esp_now_add_peer(parent, encrypt=false)` when a parent is adopted,
  `esp_now_del_peer()` when it is dropped. One at a time.

A relay does not register its children: it only ever *receives* from them, and fact 2 says
that works without registration. The bridge registers a peer transiently only to transmit a
provisioning blob, then removes it.

Two peers per node, whether the fleet has 3 sensors or 300.

### Provisioning flow

1. A sensor is flashed with its own AppKey and nothing else. With no NetKey in NVS it boots
   **unprovisioned**: it broadcasts a plain join beacon, does not route, does not relay, and
   sends no readings.
2. The bridge hears it, forwards it over UART; the Pi records it as *heard but not enrolled*.
   The app can surface this as "a sensor is nearby that hasn't been added yet".
3. The owner taps **Add sensor**, scans the QR (MAC + AppKey), and chooses a zone name and
   whether it runs on battery or mains.
4. The Pi writes the entry to its trust store, then seals the NetKey with AES-GCM under that
   sensor's AppKey and sends `{"type":"provision","mac":…,"blob":…}` down the UART.
5. The bridge adds the target as a transient unencrypted peer, transmits the opaque blob,
   removes the peer. It never learns what the blob contains.
6. The sensor decrypts with its AppKey, verifies the tag, writes the NetKey to NVS, and joins
   the mesh normally.
7. Its first reading arrives, the Pi decrypts it, publishes to MQTT, and the sensor appears
   on the dashboard.

The provisioning blob also carries the sleepy/mains role, so that role stops being a
compile-time property of `TRUSTED_NODES[]` and becomes the user's choice in step 3.

**Proximity requirement.** A join beacon carries no nettag, so relays will not forward it —
a joining sensor must be within direct radio range of the bridge. The app states this
plainly ("hold the sensor near the hub while you add it"); afterwards it can be placed
anywhere the mesh reaches. This is a genuine constraint, and also a security property: you
cannot enrol a sensor you are not physically near.

### Component changes

**Edge node** (`edge_node_esp32_c3.ino`, `edge_node_esp32.ino`, `mesh_node.h`)

`TRUSTED_NODES[]` disappears from edge nodes entirely — they no longer know any identity but
their own. Consequently:

- `meshParentIdx` (`mesh_node.h:62`), an index into `TRUSTED_NODES[]`, becomes
  `meshParentMac[6]`. The same substitution is needed in `MeshRtcState`
  (`mesh_node.h:414-426`), which persists `parentIdx` as an `int8_t` across deep sleep.
- `meshNeighborLastHeard[TRUSTED_NODE_COUNT]` (`mesh_node.h:73`) becomes a small fixed ring
  of recently-heard neighbours keyed by MAC.
- `meshTrustedIndex()` (`mesh_node.h:96`) and the relay trust check (`mesh_node.h:517`) are
  replaced by nettag verification.
- `meshIsSelfSleepy()` (`mesh_node.h:112`) reads the role from NVS instead of looking itself
  up in the trusted list.
- New: NVS storage for AppKey / NetKey / role / boot counter, the unprovisioned state
  machine, AES-GCM sealing of the body, CMAC over the header.

**Bridge** (`bridge_esp32.ino`) — ends up simpler than it is today. It loses the
`TRUSTED_NODES[]` lookup, the MAC→zone mapping, `esp_now_set_pmk()`, and encrypted peers. It
gains a UART command reader (the link is currently one-way: `serial_bridge.py` only calls
`ser.readline()`, and the sketch parses nothing inbound) handling exactly two commands,
`netkey` and `provision`. It forwards every well-formed data packet to the Pi verbatim.

**Pi** — new `shared/mesh_crypto.py` (seal/open, nonce construction, CMAC verification) and
`shared/nodes.py` (the trust store at `/etc/greenhouse/nodes.json`, mode 0600, owned by
`pi` — the ownership trap from the 2026-07-10 bench session applies). `serial_bridge.py`
decrypts inbound frames, maps MAC→zone from the store, pushes the NetKey to the bridge on
every serial connect, and relays provisioning commands. `portal.py` gains
`GET`/`POST`/`DELETE /api/nodes`, bearer-token gated exactly like `/api/history`.
`install.sh` generates the NetKey if absent. New `tools/provision_sensor.py` generates an
AppKey, emits the header the sketch compiles in, and writes the QR PNG.

**App** — an "Add sensor" entry point on the Devices screen, reusing the existing
`screens/pairing/qr_scan_screen.dart`; a form for zone name and battery/mains; a waiting
state until the first reading arrives; a "sensor detected nearby, not yet added" hint; and
removal from the same screen.

## Security analysis

**Improved.** Per-device keys close the `SECURITY.md` gap where one stolen node exposed the
fleet. Relays can no longer read the data they carry. AppKeys never touch the bridge, and the
NetKey never touches its flash. Adding a sensor no longer requires an operator with a
toolchain, so the fleet stops being reflashed as routine maintenance.

**Regressed, and mitigated.** Today an unauthorised transmitter is rejected by the radio
itself: without an encrypted-peer relationship its frames simply fail to decrypt, costing
nothing. With radio-layer encryption off, garbage reaches the CPU and is rejected in
software. The nettag makes that rejection cheap (one CMAC), and the nodes doing relay work
are mains-powered by design, so the battery-drain attack this would otherwise enable barely
applies to this topology.

**Residual, accepted, documented.** A replayed valid beacon is not detected — replaying the
bridge's own rank-0 beacon only points children at the real bridge, so the payoff is nil. An
attacker in radio range can still jam or flood; no key model addresses that. A stolen QR
sticker is a stolen key, so the trust store supports removing a sensor. And the Pi's SD card
becomes the single store for every AppKey — if it dies, every sensor must be re-scanned. That
is a real operational cost and belongs in the docs, not in a mitigation.

## Testing strategy

Firmware in this repo has **no tests at all**, and cannot be compiled or flashed from the
development sandbox. Shipping cryptography under those conditions is the largest risk in this
plan, larger than any individual design decision above.

**Host-side round-trip harness — this is the gate, not a nice-to-have.** The packet-format
and crypto code is written so it compiles as ordinary C++ on the development machine, with a
test that encrypts a packet with the firmware code and decrypts it with the Python
implementation, and vice versa. Struct-packing drift, a byte-order mistake, a mismatched AAD
range, or an off-by-one in the nonce would otherwise surface only as a sensor that silently
never reports — indistinguishable on the bench from a bad solder joint. Known-answer tests
against published AES-GCM and CMAC vectors pin both implementations to the standard rather
than to each other.

**Pi:** pytest for the trust store (including corrupt/missing file), crypto round-trip,
replay and `boot_count` regression rejection, the `/api/nodes` endpoints, and
`serial_bridge.py`'s decrypt path with an unknown MAC.

**App:** widget tests for the add-sensor flow, including the QR-scan-to-form path and the
waiting state.

**Bench:** the real acceptance test is adding a fourth sensor entirely from the phone, then a
fifth, with no cable attached to anything.

## Migration — one last flag day

The change that ends fleet reflashing requires one final fleet reflash, and it cannot be
staged: an old node rejects the new packet on its length check, and a new node rejects the old
`magic`. Old and new do not interoperate, by choice — a compatibility mode would mean running
both key models at once, which is how nonce-reuse bugs get in.

Order: update the Pi first (it must be able to mint keys and serve `/api/nodes`), then flash
the bridge, then flash each sensor with its own generated AppKey, then enrol each from the
app. Nothing reports during the window. On a bench with four boards this is one sitting.

Rollback is `git revert` plus reflashing the old images; the trust store is additive and can
be left in place.

## Failure modes worth designing for

- **Trust store missing or corrupt at boot.** The Pi refuses to publish readings it cannot
  authenticate and says so loudly, rather than silently dropping every sensor. Backing the
  file up is part of the install docs.
- **Provisioning blob never arrives** (sensor out of range, or powered off at the wrong
  moment). The sensor stays unprovisioned and keeps join-beaconing; the app shows the enrol
  as pending and offers a retry. No state is stranded.
- **A sensor is enrolled twice.** The store is keyed by MAC; a second enrol replaces the
  entry and re-sends the NetKey.
- **Clock-free replay window.** Covered by the `(boot_count, seq)` rule above rather than by
  timestamps, since no node has a real clock.

## Alternatives considered

**Raise the encrypted-peer ceiling** (`CONFIG_ESP_WIFI_ESPNOW_MAX_ENCRYPT_NUM`, 7 → 17).
Rejected: it needs a custom-built Arduino core, which is not this project's toolchain, and it
moves the ceiling to 18 rather than removing it. It also does nothing for onboarding or
per-device keys.

**Keep ESP-NOW encryption, register peers dynamically.** Genuinely cheap — a node would
register its parent and its current children, making the limit "7 direct children per node"
and allowing dozens of nodes through multi-hop. Rejected because the encryption is then
hop-by-hop: every relay decrypts and re-encrypts, so a relay sees its children's plaintext,
and ESP-NOW's per-peer LMK cannot express an end-to-end sensor→Pi relationship. It would not
deliver goals 3 or 4.

**Trust store on the bridge** (as `SCALING_AND_EXPANSION_IDEAS.md` proposed). Rejected: it
requires the bridge to hold every AppKey, creates a second source of truth that can diverge
from the Pi, introduces a "trust store lost or corrupted in flash = silent death" failure
mode, and needs a downstream UART channel anyway. Decrypting on the Pi removes all four
problems and makes the bridge simpler than it is today. The one thing the bridge genuinely
cannot delegate is beaconing, which is why it holds the NetKey — in RAM.

**Baking the NetKey in at flash time** instead of provisioning it over the air. Simpler: no
join exchange, no downstream UART, no proximity requirement. Rejected because it means every
sensor is manufactured for one specific network, which breaks the "buy another sensor and add
it" story this design exists to serve.

## Open questions for the implementation plan

1. **AES-GCM vs ChaCha20-Poly1305.** The C3 has hardware AES; whether mbedTLS's GCM path
   actually uses it, and what each costs in wake-time energy, is a bench measurement, not a
   paper decision. The design is agnostic — only the tag and nonce sizes are fixed.
2. **Truncating the GCM tag** from 16 to 8 bytes would save airtime and battery at a real
   cost in forgery resistance. Decide with measured numbers.
3. **Where the AppKey is written** — compiled into the sketch by
   `tools/provision_sensor.py`, or flashed separately into an NVS partition. The second is
   cleaner and lets one binary serve every sensor; it needs an `esptool` step in the
   provisioning tool.
