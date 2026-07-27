#include <Arduino.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <WiFi.h>
#include <mesh_config.h>
#include <mesh_node.h>

// ── UART link to the Pi (replaces WiFi/MQTT/TLS — spec:
// docs/superpowers/specs/2026-07-20-uart-bridge-design.md) ────────────────────
// ESP32-C3 has only two hardware UART controllers: Serial (aliased to native
// USB-CDC on this board, used below purely for human-readable debug logging
// over the USB cable) and Serial1 (freely routable to any GPIO pair, used
// here for the actual Pi link). There is no Serial2 on this chip.
//
// HardwareSerial::begin() takes (baud, config, RX_PIN, TX_PIN) — RX BEFORE
// TX. Wiring (both sides 3.3V logic, no level shifter):
//   ESP32 GPIO4 (TX) ──────────► Pi physical pin 10 = GPIO15 (RXD)
//   ESP32 GPIO5 (RX) ◄────────── Pi physical pin  8 = GPIO14 (TXD)
//   ESP32 GND        ─────────── Pi GND (any GND pin)
// Avoid GPIO2/8/9 for this link — C3 SuperMini boot-strapping pins / onboard
// LED. Pi-side one-time OS step (raspi-config, disable login-shell-over-
// serial, keep hardware enabled) documented in INSTRUCTIONS.md.
#define UART_RX_PIN  5
#define UART_TX_PIN  4
#define UART_BAUD    115200

// ── Liveness: heartbeat over UART replaces the old MQTT LWT ───────────────────
// The bridge used to BE the MQTT client, so a dead bridge meant a dropped TCP
// connection and the broker fired the LWT automatically. Now the Pi-side
// serial_bridge.py is the only MQTT client — a UART link has no "connection"
// state of its own (spec §Fault Handling: "Bridge keeps trying Serial.print()
// regardless"), so bridge liveness needs an explicit signal instead. This
// heartbeat line, sent on the same cadence as the rank-0 anchor beacon, is
// that signal; serial_bridge.py flips the bridge's own retained /status
// offline if MESH_OFFLINE_AFTER heartbeats are missed (same multiplier
// pattern already used for every edge node — see checkOfflineNodes() below).

char bridgeMac[13];  // own 12-hex MAC, stamped into every self-referential line

void sendLine(const char* json) {
  Serial1.println(json);
  Serial.printf("  → %s\n", json);  // USB debug echo, bench visibility only
}

void sendHeartbeat() {
  char json[48];
  snprintf(json, sizeof(json), "{\"type\":\"heartbeat\",\"mac\":\"%s\"}", bridgeMac);
  sendLine(json);
}

// zone/group/metric are always internal C-string literals or TRUSTED_NODES[]
// entries in this codebase, never externally-influenced — no escaping needed,
// matching the trust assumptions the old mqtt-publishing code already made.
void sendReading(const char* zone, const char* group, const char* metric, float value) {
  char json[128];
  snprintf(json, sizeof(json),
           "{\"type\":\"reading\",\"zone\":\"%s\",\"group\":\"%s\",\"metric\":\"%s\",\"value\":%.1f}",
           zone, group, metric, value);
  sendLine(json);
}

void sendStatus(const char* mac, const char* status) {
  char json[64];
  snprintf(json, sizeof(json), "{\"type\":\"status\",\"mac\":\"%s\",\"status\":\"%s\"}", mac, status);
  sendLine(json);
}

void sendBattery(const char* mac, float pct) {
  // 51 content bytes worst case (12-char mac + "100.0") + NUL — sized with
  // margin, not tight against the exact worst case.
  char json[64];
  snprintf(json, sizeof(json), "{\"type\":\"battery\",\"mac\":\"%s\",\"pct\":%.1f}", mac, pct);
  sendLine(json);
}

// LiFePO4 discharge curve is very flat -- piecewise-linear interpolation
// over a handful of measured points (spec §Wire format changes) is plenty
// accurate; a chain of if/else would encode the same table far less
// legibly, so use a real table + loop instead. Descending by mv on purpose
// (table walk below relies on it). Clamped outside the table's ends.
static float batteryPctFromMv(uint16_t mv) {
  static const struct { uint16_t mv; float pct; } kTable[] = {
    { 3400, 100.0f }, { 3350, 90.0f }, { 3320, 80.0f }, { 3300, 70.0f },
    { 3280,  60.0f }, { 3260, 50.0f }, { 3250, 40.0f }, { 3220, 30.0f },
    { 3200,  20.0f }, { 3000, 10.0f }, { 2800,  0.0f },
  };
  const int n = sizeof(kTable) / sizeof(kTable[0]);
  if (mv >= kTable[0].mv)     return kTable[0].pct;
  if (mv <= kTable[n - 1].mv) return kTable[n - 1].pct;
  for (int i = 0; i < n - 1; i++) {
    if (mv <= kTable[i].mv && mv >= kTable[i + 1].mv) {
      float frac = (float)(mv - kTable[i + 1].mv) / (float)(kTable[i].mv - kTable[i + 1].mv);
      return kTable[i + 1].pct + frac * (kTable[i].pct - kTable[i + 1].pct);
    }
  }
  return 0.0f;  // unreachable — table covers [2800, 3400] with no gaps
}

// Mesh topology line (spec §Telemetry) — origin's own view of its uplink:
// parent MAC + RSSI it measured for that parent, its rank, sleepy flag, raw
// battery mv, and its zone. mac == null-parent sentinel means "this is the
// bridge's own root record" (rank 0, no parent) — used once at boot below.
void sendMesh(const char* mac, const uint8_t* parentMacBytes, int rank,
              int8_t parentRssi, bool sleepy, uint16_t batteryMv, const char* zone) {
  static const uint8_t kZeroMac[6] = { 0, 0, 0, 0, 0, 0 };
  bool hasParent = parentMacBytes != nullptr && !meshMacEqual(parentMacBytes, kZeroMac);
  char parentMac[13];
  if (hasParent) meshFormatMac(parentMacBytes, parentMac);

  char json[192];
  int off = 0;
  off += snprintf(json + off, sizeof(json) - off, "{\"type\":\"mesh\",\"mac\":\"%s\",\"parent\":", mac);
  if (hasParent) {
    off += snprintf(json + off, sizeof(json) - off, "\"%s\",", parentMac);
  } else {
    off += snprintf(json + off, sizeof(json) - off, "null,");
  }
  off += snprintf(json + off, sizeof(json) - off, "\"rank\":%d,", rank);
  if (parentRssi == -128) {
    off += snprintf(json + off, sizeof(json) - off, "\"rssi\":null,");
  } else {
    off += snprintf(json + off, sizeof(json) - off, "\"rssi\":%d,", parentRssi);
  }
  off += snprintf(json + off, sizeof(json) - off, "\"sleepy\":%s,", sleepy ? "true" : "false");
  if (batteryMv == 0) {
    off += snprintf(json + off, sizeof(json) - off, "\"battery_mv\":null,");
  } else {
    off += snprintf(json + off, sizeof(json) - off, "\"battery_mv\":%u,", batteryMv);
  }
  if (zone != nullptr) {
    snprintf(json + off, sizeof(json) - off, "\"zone\":\"%s\"}", zone);
  } else {
    snprintf(json + off, sizeof(json) - off, "\"zone\":null}");
  }
  sendLine(json);
}

// ── Node liveness (spec: Bridge Changes — offline detection) ──────────────────
uint32_t lastSeenMs[TRUSTED_NODE_COUNT];
bool     nodeOnline[TRUSTED_NODE_COUNT];
uint32_t lastBeaconMs       = 0;
uint32_t lastOfflineCheckMs = 0;

// ── ESP-NOW receive callback ──────────────────────────────────────────────────
void onDataRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  if (len == sizeof(MeshBeacon)) return;  // neighbor beacons: bridge doesn't route
  if (len != sizeof(MeshDataPacket)) {
    Serial.printf("[esp-now] bad packet size %d\n", len);
    return;
  }

  MeshDataPacket pkt;
  memcpy(&pkt, data, sizeof(pkt));
  if (pkt.magic != MESH_MAGIC) { Serial.println("[esp-now] bad magic, dropped"); return; }
  if (meshDedupSeen(pkt.origin_mac, pkt.seq)) return;  // route-flap duplicate

  // Zone lookup by ORIGIN, not the immediate sender — with relaying, the
  // ESP-NOW src_addr may be an intermediate hop, not the node that measured.
  char mac[13];
  meshFormatMac(pkt.origin_mac, mac);
  int idx = meshTrustedIndex(pkt.origin_mac);
  if (idx < 0 || TRUSTED_NODES[idx].zone == nullptr) {
    Serial.printf("[esp-now] unknown origin %s — add to TRUSTED_NODES[]\n", mac);
    return;
  }
  const char* zone = TRUSTED_NODES[idx].zone;
  // Liveness tracking is data-only, not beacon-based -- but edge nodes now
  // send a reading even on a failed DHT read (NaN payload) instead of
  // skipping the send entirely, so this stays current across a run of
  // consecutive DHT failures too, not just successful reads
  // (IMPROVEMENTS.md finding B6).
  lastSeenMs[idx] = millis();
  nodeOnline[idx] = true;

  Serial.printf("[esp-now] %s (zone=%s rank=%d ttl=%d battery_mv=%u sleepy=%d) "
                "T=%.1f H=%.1f Soil=%.1f\n",
                mac, zone, pkt.origin_rank, pkt.ttl, pkt.battery_mv,
                (pkt.flags & MESH_FLAG_SLEEPY) ? 1 : 0,
                pkt.payload.temperature, pkt.payload.humidity,
                pkt.payload.soil_moisture);

  // No "is the link up" gate here — unlike the old MQTT path, UART has no
  // connection state to check. sendLine() always fires; if serial_bridge.py
  // isn't there to read it, the OS just drops it (spec §Fault Handling),
  // same as any UART write with nothing listening on the other end.

  // NaN (failed DHT read on the origin) skips sending that one metric rather
  // than propagating a NaN into a field the Pi parses as a float -- the rest
  // of the packet (and node liveness above) is still valid and sent normally.
  if (!isnan(pkt.payload.temperature)) {
    sendReading(zone, "air", "temperature", pkt.payload.temperature);
  }
  if (!isnan(pkt.payload.humidity)) {
    sendReading(zone, "air", "humidity", pkt.payload.humidity);
  }
  sendReading(zone, "soil", "moisture", pkt.payload.soil_moisture);

  sendStatus(mac, "online");

  // Battery % (bridge does the mv→% mapping — spec: "single tunable place").
  // Sent only when the origin actually has a divider fitted; an unmeasured
  // mains node sends battery_mv=0 and gets no battery line at all (not a
  // zero reading, just absent — matches the simulator contract).
  if (pkt.battery_mv > 0) {
    sendBattery(mac, batteryPctFromMv(pkt.battery_mv));
  }

  sendMesh(mac, pkt.parent_mac, pkt.origin_rank, pkt.parent_rssi,
           (pkt.flags & MESH_FLAG_SLEEPY) != 0, pkt.battery_mv, zone);
}

// Publish "offline" once a node misses MESH_OFFLINE_AFTER expected reports —
// distinguishes "node is dead" from "data arriving via a longer path" (which
// still refreshes lastSeenMs through the relay chain).
void checkOfflineNodes(uint32_t now) {
  if (now - lastOfflineCheckMs < 1000) return;
  lastOfflineCheckMs = now;
  for (int i = 0; i < TRUSTED_NODE_COUNT; i++) {
    if (TRUSTED_NODES[i].zone == nullptr) continue;  // skip the bridge entry
    if (!nodeOnline[i]) continue;                    // already reported offline
    // Per-role expected cadence (spec §Bridge-side liveness): a sleepy leaf
    // only reports once per MESH_SLEEP_INTERVAL_MS, so judging it by the
    // always-on 5 s window would flag it offline between every wake.
    uint32_t expectedMs = TRUSTED_NODES[i].sleepy ? MESH_SLEEP_INTERVAL_MS
                                                   : MESH_EXPECTED_REPORT_INTERVAL_MS;
    if (now - lastSeenMs[i] > (uint32_t)MESH_OFFLINE_AFTER * expectedMs) {
      nodeOnline[i] = false;
      char mac[13];
      meshFormatMac(TRUSTED_NODES[i].mac, mac);
      sendStatus(mac, "offline");
      Serial.printf("[bridge] node %s (%s) → offline\n", mac, TRUSTED_NODES[i].zone);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);  // wait for USB CDC to connect on C3

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  // Own 12-hex MAC, used for the heartbeat line and the bridge's own root
  // /mesh record — WiFi.macAddress() is valid as soon as the driver is up,
  // no actual WiFi connection ever made (no router in this deployment mode).
  {
    uint8_t ownMac[6];
    WiFi.macAddress(ownMac);
    meshFormatMac(ownMac, bridgeMac);
  }
  Serial.printf("\n[bridge] MAC: %s\n", bridgeMac);

  // No router to scan for a channel to agree on — every node in a UART-wired
  // fleet locks to the same fixed constant instead (mesh_config.h). Same
  // promiscuous-mode-toggle pattern the edge sketches use to set the channel
  // outside of an actual AP connection.
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(MESH_FIXED_CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);
  Serial.printf("[wifi] fixed mesh channel: ch%d\n", MESH_FIXED_CHANNEL);

  Serial1.begin(UART_BAUD, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);
  Serial.printf("[uart] Serial1 up: rx=%d tx=%d baud=%d\n", UART_RX_PIN, UART_TX_PIN, UART_BAUD);

  // ESP-NOW + mesh
  if (esp_now_init() != ESP_OK) {
    Serial.println("[esp-now] init failed");
    return;
  }
  esp_now_register_recv_cb(onDataRecv);
  meshInit(MESH_FIXED_CHANNEL);

  // Baseline liveness at boot: a node that never reports goes offline after
  // the missed-report threshold instead of staying "unknown" forever.
  uint32_t now = millis();
  for (int i = 0; i < TRUSTED_NODE_COUNT; i++) {
    lastSeenMs[i] = now;
    nodeOnline[i] = true;
  }

  // Own root /mesh record (parent null, rank 0) and own "online" — sent once
  // at boot; serial_bridge.py republishes both retained on the Pi side, and
  // will re-flip online on the next heartbeat after any offline period, so
  // this boot-time send doesn't need to repeat periodically.
  sendMesh(bridgeMac, nullptr, 0, -128, false, 0, nullptr);
  sendStatus(bridgeMac, "online");

  Serial.println("[bridge] ready — beaconing at rank 0");
}

void loop() {
  uint32_t now = millis();

  // Rank-0 anchor beacon: fixed short interval, no trickle — mains-powered,
  // so there is no cost pressure (spec: Architecture). Heartbeat piggybacks
  // the same cadence — it's the UART-link equivalent of the old MQTT LWT.
  if (now - lastBeaconMs >= MESH_BRIDGE_BEACON_INTERVAL_MS) {
    lastBeaconMs = now;
    meshSendBeaconNow(0, MESH_BRIDGE_BEACON_INTERVAL_MS);
    sendHeartbeat();
  }

  checkOfflineNodes(now);
}
