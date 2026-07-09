#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <mesh_config.h>
#include <mesh_node.h>

// ── WiFi (home router) ────────────────────────────────────────────────────────
#define WIFI_SSID     "TP-Link_14A6"
#define WIFI_PASSWORD "6940604664"   // ← fill in

// ── Pi MQTT broker ────────────────────────────────────────────────────────────
#define MQTT_HOST     "greenhouse.local"
#define MQTT_PORT     8883
#define MQTT_USER     "app"
#define MQTT_PASS     "tCCprsQSqwT072X6WRTr"

// (Zone mapping now lives in mesh_config.h's TRUSTED_NODES[] — shared with the
// edge nodes. SensorPacket comes from mesh_node.h.)

// ── MQTT client ───────────────────────────────────────────────────────────────
WiFiClientSecure net;
PubSubClient     mqtt(net);

// ── Node liveness (spec: Bridge Changes — offline detection) ──────────────────
uint32_t lastSeenMs[TRUSTED_NODE_COUNT];
bool     nodeOnline[TRUSTED_NODE_COUNT];
uint32_t lastBeaconMs       = 0;
uint32_t lastOfflineCheckMs = 0;
uint32_t lastMqttAttemptMs  = 0;

void mqttPublish(const char* topic, const char* payload, bool retain) {
  if (!mqtt.connected()) return;
  mqtt.publish(topic, payload, retain);
  Serial.printf("  → %s  %s%s\n", topic, payload, retain ? "  (retained)" : "");
}

// One-time blocking connect, used only in setup() before the mesh starts
// beaconing. loop() never calls this — it uses reconnectMQTTNonBlocking()
// below instead, so a broker outage after boot doesn't stop beaconing.
void reconnectMQTT() {
  while (!mqtt.connected()) {
    Serial.print("[mqtt] connecting... ");
    String id = "gh-bridge-";
    id += String((uint32_t)ESP.getEfuseMac(), HEX);
    if (mqtt.connect(id.c_str(), MQTT_USER, MQTT_PASS)) {
      Serial.println("OK");
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
  if (mqtt.connect(id.c_str(), MQTT_USER, MQTT_PASS)) {
    Serial.println("OK");
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
  lastSeenMs[idx] = millis();
  nodeOnline[idx] = true;

  Serial.printf("[esp-now] %s (zone=%s rank=%d ttl=%d) T=%.1f H=%.1f Soil=%.1f\n",
                mac, zone, pkt.origin_rank, pkt.ttl,
                pkt.payload.temperature, pkt.payload.humidity,
                pkt.payload.soil_moisture);

  if (!mqtt.connected()) { Serial.println("  MQTT not ready, packet dropped"); return; }

  char topic[64], payload[16];

  // retain=true: zone cards must survive a broker restart (HANDOFF.md backlog).
  snprintf(topic, sizeof(topic), "greenhouse/%s/air/temperature", zone);
  snprintf(payload, sizeof(payload), "%.1f", pkt.payload.temperature);
  mqttPublish(topic, payload, true);

  snprintf(topic, sizeof(topic), "greenhouse/%s/air/humidity", zone);
  snprintf(payload, sizeof(payload), "%.1f", pkt.payload.humidity);
  mqttPublish(topic, payload, true);

  snprintf(topic, sizeof(topic), "greenhouse/%s/soil/moisture", zone);
  snprintf(payload, sizeof(payload), "%.1f", pkt.payload.soil_moisture);
  mqttPublish(topic, payload, true);

  snprintf(topic, sizeof(topic), "greenhouse/nodes/%s/status", mac);
  mqttPublish(topic, "online", true);
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
    if (now - lastSeenMs[i] >
        (uint32_t)MESH_OFFLINE_AFTER * MESH_EXPECTED_REPORT_INTERVAL_MS) {
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
