#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <mesh_config.h>
#include <mesh_node.h>
// WIFI_SSID, WIFI_PASSWORD, MQTT_USER, MQTT_PASS: copy secrets.h.example to
// secrets.h in firmware/libraries/GreenhouseSecrets/ and fill in real values
// (gitignored -- see IMPROVEMENTS.md finding A1 for why this isn't a #define here).
#include <secrets.h>

// ── Pi MQTT broker ────────────────────────────────────────────────────────────
// mDNS resolution from WiFiClientSecure isn't reliable on this core either
// (same issue as cam_esp32's HTTPClient) -- use the Pi's IP directly.
#define MQTT_HOST     "10.19.153.202"
#define MQTT_PORT     8883

// ── WiFi watchdog (see IMPROVEMENTS.md finding B4) ─────────────────────────────
// The bridge is the mesh's rank-0 anchor, so a silently-stuck WiFi is worse
// here than on any edge node -- don't just trust the Arduino core's implicit
// auto-reconnect, check explicitly.
#define WIFI_CHECK_INTERVAL_MS     5000UL
#define WIFI_RECONNECT_TIMEOUT_MS  30000UL  // still down this long after a
                                            // reconnect attempt -> restart

// (Zone mapping now lives in mesh_config.h's TRUSTED_NODES[] — shared with the
// edge nodes. SensorPacket comes from mesh_node.h.)

// ── MQTT client ───────────────────────────────────────────────────────────────
WiFiClientSecure net;
PubSubClient     mqtt(net);

// Bridge's own identity, computed once in setup() right after WiFi.mode() —
// WiFi.macAddress() is usable as soon as the driver is up, well before
// meshInit() (which only runs later and fills meshSelfMac). Used for the
// bridge's own /status + /mesh records and its MQTT LWT (spec §Telemetry:
// "the bridge publishes its own /mesh record ... registers an MQTT LWT").
char bridgeMac[13];
char bridgeStatusTopic[64];

// ── Node liveness (spec: Bridge Changes — offline detection) ──────────────────
uint32_t lastSeenMs[TRUSTED_NODE_COUNT];
bool     nodeOnline[TRUSTED_NODE_COUNT];
uint32_t lastBeaconMs       = 0;
uint32_t lastOfflineCheckMs = 0;
uint32_t lastMqttAttemptMs  = 0;
uint32_t lastWifiCheckMs        = 0;
uint32_t wifiDisconnectedSinceMs = 0;  // 0 = currently connected

// Explicit WiFi liveness check + recovery, run periodically from loop().
void checkWifi(uint32_t now) {
  if (now - lastWifiCheckMs < WIFI_CHECK_INTERVAL_MS) return;
  lastWifiCheckMs = now;

  if (WiFi.status() == WL_CONNECTED) {
    wifiDisconnectedSinceMs = 0;
    return;
  }
  if (wifiDisconnectedSinceMs == 0) {
    wifiDisconnectedSinceMs = now;
  }
  if (now - wifiDisconnectedSinceMs >= WIFI_RECONNECT_TIMEOUT_MS) {
    Serial.println("[wifi] still down after reconnect timeout, restarting");
    ESP.restart();
    return;
  }
  // Retried every WIFI_CHECK_INTERVAL_MS until either reconnected or the
  // timeout above fires -- a single reconnect() call isn't guaranteed to
  // succeed, so don't rely on just one attempt.
  Serial.println("[wifi] disconnected, reconnecting...");
  WiFi.reconnect();
}

void mqttPublish(const char* topic, const char* payload, bool retain) {
  if (!mqtt.connected()) return;
  mqtt.publish(topic, payload, retain);
  Serial.printf("  → %s  %s%s\n", topic, payload, retain ? "  (retained)" : "");
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

// Published once right after every successful MQTT (re)connect, from both
// reconnectMQTT() and reconnectMQTTNonBlocking() — the bridge is the mesh's
// rank-0 anchor and never sends a data packet through onDataRecv() for
// itself, so this is the only place its own /status + /mesh get (re)stated.
// Paired with the LWT registered at connect() time (spec §Telemetry: "the
// bridge publishes its own /mesh record ... and registers an MQTT LWT").
void publishOwnRecords() {
  mqttPublish(bridgeStatusTopic, "online", true);
  char topic[64];
  snprintf(topic, sizeof(topic), "greenhouse/nodes/%s/mesh", bridgeMac);
  mqttPublish(topic,
              "{\"parent\":null,\"rank\":0,\"rssi\":null,\"sleepy\":false,"
              "\"battery_mv\":null,\"zone\":null}",
              true);
}

// One-time blocking connect, used only in setup() before the mesh starts
// beaconing. loop() never calls this — it uses reconnectMQTTNonBlocking()
// below instead, so a broker outage after boot doesn't stop beaconing.
void reconnectMQTT() {
  while (!mqtt.connected()) {
    Serial.print("[mqtt] connecting... ");
    String id = "gh-bridge-";
    id += String((uint32_t)ESP.getEfuseMac(), HEX);
    if (mqtt.connect(id.c_str(), MQTT_USER, MQTT_PASS,
                      bridgeStatusTopic, 1, true, "offline")) {
      Serial.println("OK");
      publishOwnRecords();
    } else {
      Serial.printf("failed rc=%d, retry in 5s\n", mqtt.state());
      delay(5000);
    }
  }
}

// Non-blocking reconnect attempt for loop() — unlike reconnectMQTT() (used
// once in setup()), this never blocks, so beaconing and offline-checking
// keep running every iteration even during a broker outage.
void reconnectMQTTNonBlocking(uint32_t now) {
  if (mqtt.connected()) return;
  if (now - lastMqttAttemptMs < 5000) return;
  lastMqttAttemptMs = now;
  Serial.print("[mqtt] connecting... ");
  String id = "gh-bridge-";
  id += String((uint32_t)ESP.getEfuseMac(), HEX);
  if (mqtt.connect(id.c_str(), MQTT_USER, MQTT_PASS,
                    bridgeStatusTopic, 1, true, "offline")) {
    Serial.println("OK");
    publishOwnRecords();
  } else {
    Serial.printf("failed rc=%d, retry in 5s\n", mqtt.state());
  }
}

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

  // Zone lookup by ORIGIN, not the immediate sender — with relaying, the
  // ESP-NOW src_addr may be an intermediate hop, not the node that measured.
  char mac[13];
  meshFormatMac(pkt.origin_mac, mac);

  // Logged, not silent: a node that loses its RTC state across sleep restarts
  // seq at 0 every wake, so every packet after its first looks like a route-flap
  // duplicate. Dropping that silently is indistinguishable from a dead link.
  if (meshDedupSeen(pkt.origin_mac, pkt.seq)) {
    Serial.printf("[esp-now] %s seq=%u dropped as duplicate\n", mac, pkt.seq);
    return;
  }

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

  if (!mqtt.connected()) { Serial.println("  MQTT not ready, packet dropped"); return; }

  char topic[64], payload[16];

  // retain=true: zone cards must survive a broker restart (HANDOFF.md backlog).
  // NaN (failed DHT read on the origin) skips publishing that one metric
  // rather than sending the literal string "nan" to a topic the app parses
  // as a float -- the rest of the packet (and node liveness above) is still
  // valid and published normally.
  if (!isnan(pkt.payload.temperature)) {
    snprintf(topic, sizeof(topic), "greenhouse/%s/air/temperature", zone);
    snprintf(payload, sizeof(payload), "%.1f", pkt.payload.temperature);
    mqttPublish(topic, payload, true);
  }

  if (!isnan(pkt.payload.humidity)) {
    snprintf(topic, sizeof(topic), "greenhouse/%s/air/humidity", zone);
    snprintf(payload, sizeof(payload), "%.1f", pkt.payload.humidity);
    mqttPublish(topic, payload, true);
  }

  snprintf(topic, sizeof(topic), "greenhouse/%s/soil/moisture", zone);
  snprintf(payload, sizeof(payload), "%.1f", pkt.payload.soil_moisture);
  mqttPublish(topic, payload, true);

  snprintf(topic, sizeof(topic), "greenhouse/nodes/%s/status", mac);
  mqttPublish(topic, "online", true);

  // Battery % (bridge does the mv→% mapping — spec: "single tunable place").
  // Published only when the origin actually has a divider fitted; an
  // unmeasured mains node sends battery_mv=0 and gets no /battery topic at
  // all (not a zero reading, just absent — matches the simulator contract).
  if (pkt.battery_mv > 0) {
    snprintf(topic, sizeof(topic), "greenhouse/nodes/%s/battery", mac);
    snprintf(payload, sizeof(payload), "%.1f", batteryPctFromMv(pkt.battery_mv));
    mqttPublish(topic, payload, true);
  }

  // Mesh topology JSON (spec §Telemetry) — origin's own view of its uplink:
  // parent MAC + RSSI it measured for that parent, its rank, sleepy flag,
  // raw battery mv, and its zone. Built incrementally rather than one
  // mega-format-string so the null/quoted-string branches stay readable.
  {
    static const uint8_t kZeroMac[6] = { 0, 0, 0, 0, 0, 0 };
    bool hasParent = !meshMacEqual(pkt.parent_mac, kZeroMac);
    char parentMac[13];
    if (hasParent) meshFormatMac(pkt.parent_mac, parentMac);

    char meshPayload[192];
    int off = 0;
    off += snprintf(meshPayload + off, sizeof(meshPayload) - off, "{\"parent\":");
    if (hasParent) {
      off += snprintf(meshPayload + off, sizeof(meshPayload) - off, "\"%s\",", parentMac);
    } else {
      off += snprintf(meshPayload + off, sizeof(meshPayload) - off, "null,");
    }
    off += snprintf(meshPayload + off, sizeof(meshPayload) - off,
                     "\"rank\":%d,", pkt.origin_rank);
    if (pkt.parent_rssi == -128) {
      off += snprintf(meshPayload + off, sizeof(meshPayload) - off, "\"rssi\":null,");
    } else {
      off += snprintf(meshPayload + off, sizeof(meshPayload) - off,
                       "\"rssi\":%d,", pkt.parent_rssi);
    }
    off += snprintf(meshPayload + off, sizeof(meshPayload) - off,
                     "\"sleepy\":%s,", (pkt.flags & MESH_FLAG_SLEEPY) ? "true" : "false");
    if (pkt.battery_mv == 0) {
      off += snprintf(meshPayload + off, sizeof(meshPayload) - off, "\"battery_mv\":null,");
    } else {
      off += snprintf(meshPayload + off, sizeof(meshPayload) - off,
                       "\"battery_mv\":%u,", pkt.battery_mv);
    }
    snprintf(meshPayload + off, sizeof(meshPayload) - off, "\"zone\":\"%s\"}", zone);

    snprintf(topic, sizeof(topic), "greenhouse/nodes/%s/mesh", mac);
    mqttPublish(topic, meshPayload, true);
  }
}

// Publish "offline" once a node misses MESH_OFFLINE_AFTER expected reports —
// distinguishes "node is dead" from "data arriving via a longer path" (which
// still refreshes lastSeenMs through the relay chain).
void checkOfflineNodes(uint32_t now) {
  if (!mqtt.connected()) return;  // don't flip nodeOnline[] until we can actually publish the transition
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
      char mac[13], topic[64];
      meshFormatMac(TRUSTED_NODES[i].mac, mac);
      snprintf(topic, sizeof(topic), "greenhouse/nodes/%s/status", mac);
      mqttPublish(topic, "offline", true);
      Serial.printf("[bridge] node %s (%s) → offline\n", mac, TRUSTED_NODES[i].zone);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);  // wait for USB CDC to connect on C3

  // Print own MAC so it can be checked against TRUSTED_NODES[]
  WiFi.mode(WIFI_STA);

  // Own 12-hex MAC + LWT status topic, computed right here rather than via
  // meshSelfMac (mesh_node.h only fills that later, inside meshInit(),
  // which itself runs after MQTT connects below) — WiFi.macAddress() is
  // already valid as soon as the STA driver is up.
  {
    uint8_t ownMac[6];
    WiFi.macAddress(ownMac);
    meshFormatMac(ownMac, bridgeMac);
  }
  snprintf(bridgeStatusTopic, sizeof(bridgeStatusTopic),
           "greenhouse/nodes/%s/status", bridgeMac);

  Serial.printf("\n[bridge] MAC: %s\n", WiFi.macAddress().c_str());

  // Connect WiFi
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("[wifi] connecting");
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.printf("\n[wifi] connected, IP=%s\n", WiFi.localIP().toString().c_str());

  // MQTT — skip cert verification (self-signed, local network)
  net.setInsecure();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(512);
  reconnectMQTT();

  // ESP-NOW + mesh
  if (esp_now_init() != ESP_OK) {
    Serial.println("[esp-now] init failed");
    return;
  }
  esp_now_register_recv_cb(onDataRecv);
  meshInit(0);  // PMK + encrypted peers for every trusted node; channel 0 =
                // follow the STA's current channel (the router's)

  // Baseline liveness at boot: a node that never reports goes offline after
  // the missed-report threshold instead of staying "unknown" forever.
  uint32_t now = millis();
  for (int i = 0; i < TRUSTED_NODE_COUNT; i++) {
    lastSeenMs[i] = now;
    nodeOnline[i] = true;
  }

  Serial.println("[bridge] ready — beaconing at rank 0");
}

void loop() {
  uint32_t now = millis();

  checkWifi(now);

  if (!mqtt.connected()) {
    reconnectMQTTNonBlocking(now);
  } else {
    mqtt.loop();
  }

  // Rank-0 anchor beacon: fixed short interval, no trickle — mains-powered,
  // so there is no cost pressure (spec: Architecture).
  if (now - lastBeaconMs >= MESH_BRIDGE_BEACON_INTERVAL_MS) {
    lastBeaconMs = now;
    meshSendBeaconNow(0, MESH_BRIDGE_BEACON_INTERVAL_MS);
  }

  checkOfflineNodes(now);
}
