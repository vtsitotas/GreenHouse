# Application-Layer Mesh ACK — Design Spec

**Date:** 2026-09-16
**Status:** Designed, not yet implemented — implementation plan is the next step.
**Depends on:** the v2 mesh protocol (`firmware/libraries/GreenhouseMesh/mesh_node.h`,
`mesh_packet.h`, `mesh_crypto.h`) and Phase 1 deep sleep
(`docs/superpowers/specs/2026-07-26-mesh-deep-sleep-design.md`), both
implemented and field-verified. Complements, and is scoped independently of,
the mesh silent-failure visibility work already shipped
(`docs/superpowers/plans/2026-09-15-mesh-silent-failure-visibility.md`,
commits `4280668`/`dcd421e`).

## Background

Today a sleepy edge node only ever learns whether its ESP-NOW send to its
immediate parent got a link-layer ACK
(`onDataSent`/`esp_now_send_status_t`, `meshNotifyTxStatus()` in
`mesh_node.h`). That tells it nothing about what happened after: whether the
packet reached the bridge, whether the bridge forwarded it correctly over
UART, or whether the Pi could actually decrypt and accept it. A frame can be
delivered perfectly at the radio layer and still be silently rejected by the
Pi (wrong AppKey, replay, unenrolled MAC) — the sensor has no way to
distinguish "nobody heard me" from "somebody heard me but rejected what I
said." This exact ambiguity is what let the zone2/zone4 stale-AppKey bug
(2026-09-12 bench session) look, from the sensor's own logs, identical to a
perfectly healthy link.

The 2026-09-15 mesh-silent-failure-visibility work gave the *Pi* an audit
trail and a push alert for this — the owner now finds out. This spec gives
the *sensor itself* the matching half: a real end-to-end delivery
confirmation, so its own retry/buffer logic reacts to what actually happened
on the Pi, not just what happened one radio hop away.

Confirmed during brainstorming (2026-09-15/16):
- Scope is **sleepy (deep-sleep) leaf nodes only** — they're the case where
  a missed confirmation is expensive (a 15-minute-until-next-try mistake,
  not a "just keep listening" one).
- No reverse-path/unicast routing table. Every intermediate node already
  only tracks its own parent, never its children (`meshRelayData()` is
  fully stateless) — building a new downstream routing table just to save
  airtime this project's scale doesn't need was rejected in favor of a
  **broadcast-flood ACK**, reusing the TTL+dedup pattern the uplink data
  path already has.
- **Relationship to CART:** today only always-on (non-sleepy) nodes can be
  parents/relays at all (`MESH_FLAG_SLEEPY` is hard-rejected in
  `meshHandleBeacon()`) — this is a *Phase 1* limitation, not a permanent
  one. The target deployment (confirmed 2026-09-16, see project memory
  `project_deployment_target_all_nodes_relay`) has every field node running
  on battery+solar with deep sleep *and* relay capability; that requires
  CART (`docs/superpowers/specs/2026-08-17-mesh-phase2-synced-wake-design.md`),
  designed but not yet built. This spec's relay logic (§Flood-relay
  mechanics) is deliberately written the same way `meshRelayData()` already
  is — "if I'm not sleepy, I relay" — so that once CART lifts the sleepy
  parent restriction, the exact same ACK-relay code path naturally starts
  running during a relay-capable sleepy node's CART child-receptive window.
  Nothing in this design needs to be revisited when CART lands.

## Goals

1. A sleepy leaf node learns, before it goes back to sleep, whether the Pi
   actually **accepted** its reading, **explicitly rejected** it (bad
   AppKey/auth failure), or got no answer within the wake budget.
2. The mechanism works over however many hops the mesh currently has,
   without any new per-node routing state.
3. Fits inside the existing `MESH_WAKE_MAX_AWAKE_MS` (10 s) wake budget
   alongside the existing L2-confirm/retry dance — this is additive, not a
   redesign of `sendWithConfirm()`/`runSleepyCycle()`.
4. Forward-compatible with CART (see above) with zero rework.

## Non-goals

- **Unenrolled-MAC and replay-drop frames do not get an ACK.** An unenrolled
  node has no NetKey yet, so it couldn't verify a nettag'd ACK even if one
  were sent — sending one would be a no-op at best. A replay-drop is
  normally a duplicate of an already-acked delivery; acking (or NACKing) the
  duplicate risks confusing a sensor that's still legitimately waiting on
  its *first* attempt's ACK. Both remain exactly as shipped in the
  2026-09-15 visibility work (Pi-side log only).
- **Always-on (non-sleepy) edge nodes are out of scope for the *consuming*
  side.** They already have no confirm/retry mechanism today and adding one
  wasn't asked for. They **are** in scope for the *relaying* side (see
  §Flood-relay mechanics) exactly as they already relay data today.
- **No reverse-path/unicast ACK routing.** Rejected during brainstorming —
  see §Alternatives considered.
- **No new app/UI surface.** This is entirely a firmware + Pi/bridge
  protocol change.
- **No change to `MESH_FLAG_SLEEPY`'s parent-rejection rule.** That's
  CART's job, not this spec's.

## Architecture

### New wire format: `MeshAck`

A fourth broadcast packet type, alongside the existing `MeshBeacon` (27
bytes) and `MeshJoinBeacon` (8 bytes) — distinguished from all three other
message shapes purely by length, the same dispatch convention `onDataRecv`
already uses for `MESH_PROVISION_LEN`/`sizeof(MeshBeacon)`/`MESH_PACKET_LEN`.

```c
#define MESH_ACK_MARKER   0x41        // 'A' — distinguishes from MESH_JOIN_MARKER (0x4A)
#define MESH_ACK_OK       1           // 0 is deliberately unused, so an
#define MESH_ACK_REJECTED 2           // all-zero/uninitialized status is
                                       // never mistaken for a valid outcome
#define MESH_ACK_TTL      6           // flood hop budget; generous relative to
                                       // this fleet's field-observed 1-2 hops

typedef struct __attribute__((packed)) {
  uint8_t  magic;                  // byte 0:     MESH_MAGIC_V2
  uint8_t  marker;                 // byte 1:     MESH_ACK_MARKER
  uint8_t  target_mac[6];          // bytes 2-7:  which origin node this concerns
  uint16_t seq;                    // bytes 8-9:  which reading (origin's own seq)
  uint8_t  status;                 // byte 10:    MESH_ACK_OK or MESH_ACK_REJECTED
  uint8_t  ttl;                    // byte 11:    decremented per hop, flood
                                    //             stops at 0 — NOT covered by
                                    //             the tag (see below), same
                                    //             reason the data packet's ttl
                                    //             isn't: it mutates every hop
  uint8_t  tag[MESH_NETTAG_LEN];   // bytes 12-19: CMAC-AES(NetKey) over bytes
                                    //             0-10 only (magic..status) —
                                    //             mirrors MESH_AAD_LEN's
                                    //             "header minus its last byte"
                                    //             convention in mesh_packet.h
} MeshAck;                          // 20 bytes — unique length among all 4
                                     // message shapes (8 / 20 / 27 / 61)
```

The nettag is mandatory, not optional: without it, a forged `MeshAck` with
`status=MESH_ACK_OK` would let an attacker (or a bug) make a sensor believe
a reading was delivered when it never left the local mesh — silent,
undetectable data loss. Any node holding the NetKey can verify it exactly
like it already verifies beacon nettags today.

### Flood-relay mechanics

New function `meshHandleAck()` in `mesh_node.h`, parallel to the existing
`meshRelayData()`:

1. Verify the nettag. Drop silently on failure (same posture as a bad
   beacon nettag today — no log spam from strangers' garbage).
2. Check a **new, separate** small dedup cache keyed on
   `(target_mac, seq)` — deliberately not the existing
   `meshDedup`/`MESH_DEDUP_CACHE_SIZE` array used for uplink data. Sharing
   one array risks an uplink data packet and a downlink ack for the same
   `(mac, seq)` pair falsely deduping against each other. Size this new
   cache small (8 entries) — acks are far rarer than data packets and this
   fleet's scale doesn't need more.
3. If this `(target_mac, seq)` was already seen within the dedup window:
   drop, don't re-relay. This is what caps the flood — see the
   worked multi-path example from brainstorming (§Duplicate-arrival
   handling below).
4. If `target_mac == meshSelfMac`: this ack is mine. Record it —
   `meshPendingAckSeq`/`meshPendingAckStatus`/`meshPendingAckSeen` module
   state, checked by the wake-cycle poll loop (§Sensor-side timing). The
   write must be **idempotent** — a plain overwrite (`if (seq matches)
   { status = ack.status; seen = true; }`), never an increment or toggle —
   because the same genuine ack can legitimately arrive more than once (see
   below).
5. If I am **not** sleepy (`!meshIsSelfSleepy()`): decrement `ttl`; if the
   result is `> 0`, re-broadcast. A sleepy node never reaches this step for
   its own non-matching acks either, by construction — step 4 already
   handled the "it's mine" case, and a sleepy node has no children to serve
   today (Phase 1), so relaying further would only waste its wake budget.
   Once CART ships, this exact same step 5 is what a relay-capable sleepy
   node runs during its CART child-receptive window — no new code path
   needed then, just the `!meshIsSelfSleepy()` gate loosening the same way
   `meshHandleBeacon()`'s parent-rejection gate does.

`onDataRecv` (in each `.ino` sketch) gets one new length branch:
`len == sizeof(MeshAck)` → `meshHandleAck(info->src_addr, data, len)`,
alongside the existing three.

### Duplicate-arrival handling (worked example)

Confirmed during brainstorming — worth stating explicitly since it's the
exact kind of thing a fresh implementer could get subtly wrong. With two
always-on relays A and B both in range of the bridge, of each other, and of
a sleepy target node:

- Bridge broadcasts once. A and B **both** receive it directly (their first
  arrival) → both process and re-broadcast.
- A then also hears B's re-broadcast (a **second** arrival for A, same
  `(target_mac, seq)`) → A's dedup cache (step 2/3 above) recognizes it and
  drops it without a third re-broadcast. Symmetrically for B hearing A's
  re-broadcast. This is what bounds the flood — without it, A and B would
  keep re-triggering each other until TTL alone stopped it, burning
  needless airtime.
- The sleepy target hears **both** A's and B's re-broadcasts. It doesn't
  dedup at all (steps 2/3 only apply to relaying, and the target isn't
  relaying its own ack) — it just runs step 4 twice, harmlessly, because
  that step is idempotent.

Two independent mechanisms, two independent purposes: relay-level dedup
bounds flood growth; target-level idempotency makes redundant delivery to
the actual recipient harmless. Neither depends on the other.

### Pi / bridge changes

`pi/scripts/serial_bridge.py`'s `handle_frame()` gains two new calls, both
sending a new UART command to the bridge (new function `send_ack(ser, mac,
seq, ok)`, same shape as the existing `send_netkey()`):

- On successful decrypt (after the existing `_METRICS`/`_publish_mesh`
  publishing, so a slow MQTT publish never delays the ack): `send_ack(ser,
  mac, header.seq, ok=True)`.
- In the existing `except (MeshAuthError, MeshFormatError)` branch (already
  home to the 2026-09-15 `log_security_event('mesh_auth_failure', ...)`
  call): `send_ack(ser, mac, header.seq, ok=False)`. `mac` and
  `header.seq` are already in scope there — both come from the cleartext
  header, parsed successfully before decryption is even attempted.

`firmware/bridge_esp32/bridge_esp32.ino` gains a new incoming UART line
type (`{"type":"ack","mac":"...","seq":N,"ok":true|false}`, parsed the same
way the existing netkey/provisioning commands already are), which builds a
`MeshAck` (nettag computed with the bridge's own held NetKey, exactly like
its beacon) and broadcasts it — the bridge is always non-sleepy, so it's
just running step 5 of §Flood-relay mechanics like every other relay.

### Sensor-side timing

Extends `sendWithConfirm()`/`runSleepyCycle()` in
`edge_node_esp32_c3.ino` (and the WROOM variant, `edge_node_esp32.ino`) —
purely additive after the existing L2-confirm step, not a restructure:

```
existing:  send → wait ≤ MESH_TX_CONFIRM_WAIT_MS (500 ms) for L2 ack
new:       if L2-confirmed → wait ≤ MESH_APP_ACK_WAIT_MS (2000 ms, proposed)
                              for meshPendingAckSeen matching this seq
```

`MESH_APP_ACK_WAIT_MS = 2000` is proposed, not bench-measured — it only
needs to cover one UART round-trip (Pi decrypt is sub-millisecond;
`/dev/serial0` at 115200 baud moves the whole line in low single-digit ms)
plus this fleet's field-observed 1-2 mesh hops each way, each well under
`MESH_TX_CONFIRM_WAIT_MS`'s existing 500 ms budget. Even a pessimistic
3-hop-each-way round trip fits comfortably under 2 s. This still leaves
ample headroom inside `MESH_WAKE_MAX_AWAKE_MS` (10 s total, shared with
sensor warmup and the existing orphan-discovery retry path) — no existing
constant needs to change.

**Outcome handling**, replacing today's single `delivered` boolean with
three cases:

| Outcome | Meaning | Firmware behavior |
|---|---|---|
| `MESH_ACK_OK` received | Pi genuinely accepted the reading | Clear buffer, reset `g_unconfirmedWakes` to 0 (same as today's `delivered=true`) |
| `MESH_ACK_REJECTED` received | Pi explicitly rejected it (bad key/auth) | **Keep today's retry/buffer behavior unchanged** (retrying is still the only thing firmware can do without human intervention — re-flashing a bad key isn't something firmware can fix itself) but log it distinctly via `Serial.printf` (`"[mesh] Pi rejected reading — check AppKey"`), unlike a plain timeout. This is the diagnostic value: a bench engineer watching the serial console now sees *why* a node isn't getting through, instead of an ambiguous silence. |
| No ack within `MESH_APP_ACK_WAIT_MS` | L2-delivered but no app-level answer (bridge/Pi down, or the ack itself got lost) | Same as today's existing "unconfirmed" path — buffer, retry next cycle, count toward `g_unconfirmedWakes` |

Deliberately **not** building any different retry *strategy* for
`MESH_ACK_REJECTED` vs. timeout (e.g. backing off, or a distinct
LED/diagnostic signal) — logged distinctly is enough value for this
project's scope; a future bench-diagnostics feature could build on the new
`MESH_ACK_REJECTED` signal later without this spec needing to anticipate it.

## Testing

- **Firmware:** `arduino-cli compile` for all three sketches
  (`edge_node_esp32_c3`, `edge_node_esp32`, `bridge_esp32`), matching the
  existing project convention (no on-device unit test framework in this
  codebase — compile-verification plus bench testing is the established
  pattern, e.g. the 2026-09-12 fake-sensor-firmware sync).
- **Pi:** `pi/tests/test_serial_bridge_mesh.py` gains tests asserting
  `send_ack()` is called with the right `(mac, seq, ok)` on both the
  success and auth-failure paths of `handle_frame()`, following the same
  `monkeypatch` pattern already used for the 2026-09-15
  `log_security_event` tests.
- **Bench verification (not automatable):** a real sleepy node's serial
  console should show a distinct log line for each of the three outcomes
  in the table above, confirmed against a real bridge/Pi — this is the
  same kind of hands-on-hardware step every mesh protocol change in this
  project has needed (2026-08-16 relay test, 2026-09-12 enrollment
  bugfixes).

## Alternatives considered

### B: Unicast reverse-path ACK

Each relay remembers which neighbor a given `(mac, seq)` arrived from and
forwards the ack only to that specific neighbor, instead of flooding.
Rejected during brainstorming: it requires new per-node state on every
relay (a routing table with its own expiry/eviction logic — a new failure
mode this project doesn't have today), and the actual efficiency gain over
flooding is small at this project's real scale (a handful of nodes, 1-2
hops) since — as noted directly by the user during brainstorming — a
unicast reverse path still has to retrace the same physical route the data
came in on, so the two approaches aren't as different in practice as they
sound; the flood approach's "waste" is bounded by the same TTL and dedup
mechanism the uplink path already has, for a fraction of the implementation
risk.

## Open risks / explicitly deferred

- **`MESH_APP_ACK_WAIT_MS`'s proposed 2000 ms is not bench-measured.** If
  real hardware testing shows this is too tight (unlikely at this fleet's
  hop count, but not verified), it's a single constant to tune — no
  protocol change needed.
- **CART interaction is designed-compatible, not tested-compatible** —
  CART doesn't exist yet, so the "no rework needed" claim in §Background is
  an architectural argument, not something verified on hardware. Worth a
  one-line callout if/when CART's own implementation plan is written.
- **Ack-dedup cache sizing (8 entries) is a judgment call**, not derived
  from a formula the way the CART spec's constants are — this project's
  field-observed scale (a handful of nodes, low message rate) doesn't
  seem to need more, but if a future fleet grows meaningfully, this is the
  first constant to revisit.


## Revision 2026-09-24 — resend until acknowledged; link-only rescan counter

- The stale-channel rescan counter (\g_unconfirmedWakes\) counts link-layer
  failures only again. The app ACK previously fed it, so a Pi outage or a
  rejected AppKey made every node rescan channels every other wake.
- Every frame a sleepy node unicasts in a wake is tracked
  (\mesh_inflight.h\). Unanswered frames go back to the RTC buffer and are
  resent next wake. REJECTED frames are dropped.
- The Pi authenticates before its replay check and re-ACKs an authenticated
  duplicate (same boot_count + seq) without republishing. Authenticating
  first also stops a forged header from moving a node's replay window.
- Limitation: the Pi's replay window is in memory, so a resend that crosses
  a Pi restart is republished once.
Plan: \docs/superpowers/plans/2026-09-24-mesh-ack-retry-and-link-counter.md\.
