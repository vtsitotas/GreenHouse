# Multi-Site Greenhouse via LoRaWAN + Cellular Gateway — Design Spec

**Date:** 2026-09-14
**Status:** Designed via conversational brainstorm, **not yet written up as an implementation plan, not yet built**. One external unknown blocks committing to the static-IP path (see §8) — everything else is ready to plan from.

## Background

The project today is single-site: one Pi per greenhouse, reachable over the owner's home WiFi/internet, with a HiveMQ Cloud bridge for away-from-home access (see `deployment-model` / `tech-decisions` memory and `pi/scripts/hivemq_bridge.py`). Two related but distinct needs prompted this design:

1. **A site with no local internet at all** (not just "away from home" — the physical site itself has no ISP/broadband) still needs remote view/control.
2. **Multiple greenhouses, kilometers apart**, should be viewable/controllable from one app, ideally without paying for a separate cellular data plan at every single site.

These are not the same problem and do not need the same solution — decomposing them was the first step of this brainstorm.

## Goals

1. A remote greenhouse with no local internet can still report sensor data and receive control commands.
2. Multiple greenhouses (kilometers apart) are viewable from one app, **without** requiring a cellular subscription at every site.
3. Reuse existing project patterns wherever possible (UART bridge pattern, TLS cert-pinning pattern, MQTT bridge-script pattern) rather than inventing new architecture from scratch.
4. Stay within realistic regulatory (EU868 duty cycle) and hardware (LoRaWAN payload size) constraints — validated with real numbers, not assumptions.

## Non-goals

- **Redesigning the local ESP-NOW mesh.** Unchanged — see `docs/superpowers/specs/2026-09-09-unlimited-sensors-app-onboarding-design.md`. This design only adds a new hop *between* a site's existing Pi and the outside world.
- **Final hardware procurement.** Part numbers below (RAK811, RAK2287, SIM7600G-H) are concrete, currently-real, currently-available recommendations to make the plan buildable — not a locked bill of materials.
- **The Flutter app's multi-site UI.** Flagged as necessary (§9) but not designed here — separate brainstorm.
- **NB-IoT and Sigfox as the greenhouse↔gateway link.** Considered and rejected — see §Alternatives considered.

## Architecture

### Topology

```
┌─────────────────────────────────────────────────────────────┐
│  REMOTE GREENHOUSE (e.g. several km from the gateway)         │
│  ESP32 sensors ──(ESP-NOW mesh, unchanged)──► Pi Zero W       │
│                                          + LoRaWAN end-node    │
│                                          module + antenna      │
└──────────────────────┬──────────────────────────────────────┘
                        │  LoRaWAN radio, no SIM card at this site
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  CENTRAL GATEWAY (co-located with one greenhouse, or standalone) │
│  LoRaWAN concentrator + antenna (receives from every remote site) │
│  + ChirpStack (self-hosted LoRaWAN Network Server, same Pi)   │
│  + bridge script (same pattern as hivemq_bridge.py)           │
│  + SIM7600 4G/LTE module + its own antenna (internet uplink)  │
└──────────────────────┬──────────────────────────────────────┘
                        │  TLS MQTT (existing pattern)
                        ▼
              HiveMQ Cloud, or direct to a static IP (§8)
                        │
                        ▼
                  Flutter app (multi-site, §9)
```

Additional remote sites attach the same way — each needs only its own LoRaWAN end-node + antenna, **no SIM card, no internet subscription**.

### Why this decomposition (not "one WAN tech for everything")

The original ask named three WAN candidates (GSM, NB-IoT, Sigfox) plus LoRaWAN, as if picking one technology for the whole problem. They actually serve two different roles with opposite requirements:

- **The gateway's own uplink** needs continuous, moderate-bandwidth, low-latency, bidirectional connectivity (an always-open MQTT session, prompt control commands) — and is mains-powered, so power consumption is not a constraint. This is a job for **cellular data (GSM/LTE)**, not a low-power WAN technology.
- **Greenhouse↔gateway** needs long range (km), and — critically — the whole reason to avoid GSM/NB-IoT/Sigfox *here* is that Option B (one shared gateway) was chosen specifically to avoid a per-site subscription. Putting a cellular/NB-IoT/Sigfox radio at every remote site would silently re-introduce exactly that cost, defeating the purpose. **LoRaWAN** is the only one of the four that is a private, subscription-free, point-to-point-style link once the gateway is owned.

NB-IoT and Sigfox were also rejected here on technical grounds independent of cost: NB-IoT is built for infrequent tiny payloads with a device that sleeps most of the time, a poor fit for the gateway's continuous-session need, and has patchier rural coverage than LTE in practice; Sigfox's ~140 uplink messages/day (12 bytes each) and near-absent downlink make it unusable for anything beyond the sparsest telemetry, let alone control commands.

### Hardware — per remote greenhouse

| Part | Recommendation | Why |
|---|---|---|
| Everything that exists today (ESP32 sensors, ESP-NOW mesh, bridge ESP32, Pi Zero W) | Unchanged | No changes to the local mesh |
| LoRaWAN end-node module | **RAK811** or **RAK3172**, UART to the Pi | Self-contained: runs the LoRaWAN end-device protocol (join, encryption, timing, retries) on its own microcontroller. The Pi just sends it "transmit this" over UART/AT-commands — the same *shape* of integration as the existing ESP32 UART bridge, just a different peripheral. Deliberately kept off the Pi's own CPU: the Pi is already busy running the rest of the stack, and LoRaWAN timing is not something to share with a general-purpose OS doing other things. |
| Antenna | 868MHz-tuned, ~3 dBi omni whip | Matches the region (EU868); the *gateway's* antenna/height does most of the range work, not this end |

**Not needed at a remote site:** SIM card, cellular module, any internet connection of its own.

### Hardware — at the central gateway

| Part | Recommendation | Why |
|---|---|---|
| LoRaWAN concentrator | **RAK2287** (Semtech SX1302, mini-PCIe, Pi HAT), or an all-in-one kit (RAK7248/RAK7244) | Unlike the end-node module, this is deliberately a "dumb" radio front-end — it has no onboard protocol microcontroller. It must listen across *multiple channels/spreading-factors simultaneously* (it doesn't know in advance which remote site will transmit when, on what channel) — a fundamentally different, harder job than an end-node's "send one thing, listen briefly" cycle. Connects via **SPI** to the Pi (not UART — corrected mid-brainstorm; UART is specifically the end-node module's interface). |
| Antenna (LoRaWAN) | 868MHz-tuned, ~6 dBi omni, **mounted as high as physically possible** | Antenna height / Fresnel-zone clearance is the dominant factor in real-world range, more than raw dBi. 5–15 km rural line-of-sight is realistic with an elevated gateway antenna and an ordinary 3 dBi node antenna at the far end. |
| LoRaWAN Network Server software | **ChirpStack**, self-hosted, on the *same* Pi (official Raspberry Pi image runs both concentrator packet-forwarder and network server on one board) | Does the actual protocol intelligence the concentrator chip doesn't: decrypts/validates the LoRaWAN MAC layer, tracks per-device session state (frame counters, keys) for every remote site independently, exposes decoded application payloads over its own local MQTT integration. This is genuinely more total work than any single end-node does — it's just relocated to the full computer instead of packed into the radio chip, because it has to be (many independent device sessions, not one). |
| Bridge script (new) | Same shape as the existing `pi/scripts/hivemq_bridge.py` | Subscribes to ChirpStack's local MQTT, forwards decoded readings onward (to HiveMQ Cloud or a direct static-IP connection, §8) |
| Cellular module | **SIM7600G-H** (LTE Cat-4, ~50 Mbps up / 150 Mbps down, MQTT support built into module firmware) | Gives the gateway Pi its own internet connection. Presents to Linux as an ordinary network interface (like a USB WiFi dongle) — the MQTT client code doesn't need to know or care that it's cellular. |
| Antenna (cellular) | Small LTE stub/patch antenna (usually bundled with the HAT) | Separate frequency band (LTE) from LoRaWAN (868 MHz) — a completely different antenna, not a shared one. Cellular towers are typically much closer than the LoRaWAN link's km range, so a modest bundled antenna is normally sufficient. |
| SIM card | **M2M/IoT business SIM plan** (e.g. Cosmote M2M, Vodafone IoT) | Needed specifically if pursuing the static-IP-direct path (§8) — a consumer data SIM's "static IP" is frequently still carrier-NAT'd and unreachable from outside; this must be confirmed with the carrier as an explicit "public/routable" feature, not assumed. |

### Data flow, traced end to end

```
1. Sensor (remote greenhouse) → ESP-NOW → local Pi              [unchanged, exists today]
2. Local Pi → UART → RAK811 end-node module                      [module encodes the LoRaWAN frame]
3. RAK811 → antenna → air (km)                                   [antenna radiates the RF signal]
4. air → gateway antenna → RAK2287 concentrator                  [antenna receives; concentrator demodulates]
5. Concentrator → SPI → gateway Pi (packet-forwarder process)    [raw LoRaWAN frame handed to software]
6. packet-forwarder → localhost → ChirpStack Network Server      [MAC-layer decrypt/validate, per-device state]
7. ChirpStack → local MQTT → bridge script (new, on same Pi)     [decoded application payload]
8. bridge script → SIM7600 → cellular network → internet         [same MQTT client pattern as today]
9. → HiveMQ Cloud, or direct to gateway's static IP (§8) → app
```

### Security across the new hop

LoRaWAN has its own AES-128 encryption (NwkSKey for MAC-layer integrity, AppSKey for payload confidentiality) — conceptually parallel to this project's existing NetKey/AppKey split, just at a different layer. Recommendation: **layer, don't replace**:

- LoRaWAN's own session keys protect the remote-site → gateway hop (standard, built into ChirpStack, nothing to hand-build).
- The existing per-sensor AppKey/NetKey trust model (§ the unlimited-sensors design) stays **local to each site's own Pi** — the gateway/ChirpStack never needs to know anything about individual ESP32 sensor keys, only LoRaWAN-level session keys per *site*.
- The gateway re-wraps in TLS (existing pattern) before the cellular/cloud hop, same as today.

### Payload budget — validated against real EU868 numbers

The regulatory EU868 duty-cycle limit is **1% = 36 seconds of transmit time per hour**, per ETSI EN 300 220, on the commonly-used sub-band. This is a *hard regulatory* limit, separate from and more generous than the stricter "fair use" policies public community networks (e.g. The Things Network) additionally impose on top of it — **not applicable here**, since this design uses a private, self-hosted ChirpStack network, not a shared public one.

| Spreading Factor | Airtime (~20-byte payload) | Max payload (EU868) | Messages/hour at 1% duty cycle |
|---|---|---|---|
| SF7 (short range) | ~57 ms | 222 bytes | ~630/hour |
| SF12 (max range, km) | ~1.3–1.5 s | **51 bytes** | **~25/hour** |

Even in the worst case (maximum range, SF12), the duty-cycle budget supports a message roughly every 2.4 minutes — comfortably above the project's existing 5–15 minute reporting interval. **Duty cycle is not a binding constraint at this project's scale and reporting frequency, even at full range.** The real constraint is the 51-byte payload cap at long range.

### What to actually send

Given the 51-byte worst-case cap, don't relay the existing 61-byte encrypted mesh packet as-is (it wouldn't fit at SF10–12). Instead:

- **Compact binary encoding**, not the raw packet format: temperature/humidity/soil as `int16` scaled ×10 (2 bytes each, e.g. 24.5°C → 245) instead of 4-byte floats, `uint16` battery_mv, 1 flags byte — roughly **8–10 bytes total**, comfortably under 51 even with room to spare. (No need to include a site/zone identifier in the payload at all — LoRaWAN's DevEUI already tags every uplink with which device sent it.)
- **Send a local average, not every raw reading.** The remote Pi already collects readings from its own ESP-NOW mesh every few minutes; it should aggregate (mean, and optionally min/max) over the reporting window and send *one* summarized LoRaWAN message, not forward each individual local reading.
- **Alerts are separate, immediate, locally-triggered uplinks — not a downlink round-trip.** The remote Pi already has every raw reading locally and can run the same kind of rule evaluation the existing weather.py rules feature already does, entirely at the site. When a threshold trips, send a small alert message right away rather than waiting for the next scheduled report or relying on the gateway to notice.
- **Downlink (control commands)** is comparatively more constrained in LoRaWAN. Because the remote Pi is mains-powered (not a battery sensor), **Class C** (continuous receive) is viable and sidesteps LoRaWAN's classic slow-downlink weakness — that weakness exists specifically to conserve battery, which isn't a real constraint at a Pi.

## Alternatives considered

### GSM/NB-IoT/Sigfox for the greenhouse↔gateway link (instead of LoRaWAN)

Rejected. Each would require its own connectivity (SIM/data plan) at every remote site, directly undoing the reason Option B (one shared gateway) was chosen over Option A (independent per-site cellular) in the first place. NB-IoT also has patchier rural coverage than LTE in Greece in practice, and Sigfox's message-count/payload limits are unworkable for anything beyond the sparsest telemetry.

### NB-IoT or Sigfox for the gateway's own WAN uplink (instead of GSM/LTE)

Rejected. Both are designed for the opposite profile — infrequent, tiny, ultra-low-power payloads from a device that sleeps most of the time — not a continuously-open, moderate-bandwidth, low-latency MQTT session. The gateway is mains-powered, so the power-saving properties that make NB-IoT/Sigfox attractive elsewhere buy nothing here.

## Open risks / explicitly deferred

- **Static IP vs. cloud broker (§8 in the working conversation, not yet resolved in this doc):** direct static-IP connection would reuse the existing LAN TLS-pinning pattern (`cert_pinning.dart`) almost unchanged, and avoids a third-party dependency — but requires confirming with a specific carrier that their M2M/IoT SIM plan gives a genuinely public, inbound-reachable IP (not just "doesn't change" behind carrier NAT). This is an external unknown, not a technical decision this project controls alone. Until confirmed, default to the already-proven cloud-broker pattern; static IP can be added later without architectural rework.
- **Flutter app multi-site support:** not designed here. The app today pairs with exactly one greenhouse. Needs either (a) each site under its own topic prefix on a shared broker with the app distinguishing them, or (b) the app holding a list of independently-paired sites with a switcher UI. Separate brainstorm.
- **Exact antenna/module part numbers:** the ones named here are real, currently-available, and technically appropriate, but not a locked procurement list — verify current pricing/availability before purchasing.
- **Real-world range validation:** the 5–15 km rural line-of-sight figures are industry-typical, not measured for this project's actual terrain. Should be bench/field-validated once hardware is in hand, the same way the ESP-NOW mesh's range was field-tested rather than assumed.

## How this was produced

Reached via extended conversational brainstorm (2026-09-14) rather than the usual solo-then-present flow — the requirements, the two-problem decomposition, and several corrections (SPI not UART for the concentrator, "smart vs dumb" module framing, duty-cycle math) emerged through back-and-forth questioning. Recorded here in full so a future session doesn't have to reconstruct the reasoning from chat history.
