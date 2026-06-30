#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>

// ── WiFi (home router) ────────────────────────────────────────────────────────
#define WIFI_SSID     "TP-Link_14A6"
#define WIFI_PASSWORD "6940604664"   // ← fill in

// ── Pi MQTT broker ────────────────────────────────────────────────────────────
#define MQTT_HOST     "greenhouse.local"
#define MQTT_PORT     8883
#define MQTT_USER     "app"
#define MQTT_PASS     "tCCprsQSqwT072X6WRTr"

// ── Zone map: MAC → zone name ─────────────────────────────────────────────────
// Add one entry per edge node.  MAC must be uppercase, no colons.
struct ZoneEntry { const char* mac; const char* zone; };
static const ZoneEntry ZONES[] = {
  { "206EF16CA1B0", "zone1" },  // ESP32-C3 edge node
  { "88F155314564", "zone2" },  // ESP32 WROOM-32 edge node
};
static const int ZONE_COUNT = sizeof(ZONES) / sizeof(ZONES[0]);

// ── Shared data struct (must match edge node exactly) ────────────────────────
typedef struct {
  float temperature;
  float humidity;
  float soil_moisture;  // 0–100 %
} SensorPacket;

// ── MQTT client ───────────────────────────────────────────────────────────────
WiFiClientSecure net;
PubSubClient     mqtt(net);

const char* zoneForMac(const char* mac) {
  for (int i = 0; i < ZONE_COUNT; i++) {
    if (strcasecmp(ZONES[i].mac, mac) == 0) return ZONES[i].zone;
  }
  return nullptr;
}

void mqttPublish(const char* topic, const char* payload) {
  if (!mqtt.connected()) return;
  mqtt.publish(topic, payload);
  Serial.printf("  → %s  %s\n", topic, payload);
}

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

// ── ESP-NOW receive callback ──────────────────────────────────────────────────
void onDataRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  if (len != sizeof(SensorPacket)) {
    Serial.printf("[esp-now] bad packet size %d\n", len);
    return;
  }

  // Format MAC string
  char mac[13];
  snprintf(mac, sizeof(mac), "%02X%02X%02X%02X%02X%02X",
           info->src_addr[0], info->src_addr[1], info->src_addr[2],
           info->src_addr[3], info->src_addr[4], info->src_addr[5]);

  const char* zone = zoneForMac(mac);
  if (!zone) {
    Serial.printf("[esp-now] unknown node %s — add to ZONES[]\n", mac);
    return;
  }

  SensorPacket pkt;
  memcpy(&pkt, data, sizeof(pkt));

  Serial.printf("[esp-now] %s (zone=%s) T=%.1f H=%.1f Soil=%d\n",
                mac, zone, pkt.temperature, pkt.humidity, pkt.soil_moisture);

  if (!mqtt.connected()) { Serial.println("  MQTT not ready, packet dropped"); return; }

  char topic[64], payload[16];

  snprintf(topic, sizeof(topic), "greenhouse/%s/air/temperature", zone);
  snprintf(payload, sizeof(payload), "%.1f", pkt.temperature);
  mqttPublish(topic, payload);

  snprintf(topic, sizeof(topic), "greenhouse/%s/air/humidity", zone);
  snprintf(payload, sizeof(payload), "%.1f", pkt.humidity);
  mqttPublish(topic, payload);

  snprintf(topic, sizeof(topic), "greenhouse/%s/soil/moisture", zone);
  snprintf(payload, sizeof(payload), "%.1f", pkt.soil_moisture);
  mqttPublish(topic, payload);

  snprintf(topic, sizeof(topic), "greenhouse/nodes/%s/status", mac);
  mqttPublish(topic, "online");
}

void setup() {
  Serial.begin(115200);
  delay(1500);  // wait for USB CDC to connect on C3

  // Print own MAC so you can paste it into edge node firmware
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

  // ESP-NOW
  if (esp_now_init() != ESP_OK) {
    Serial.println("[esp-now] init failed");
    return;
  }
  esp_now_register_recv_cb(onDataRecv);
  Serial.println("[bridge] ready");
}

void loop() {
  if (!mqtt.connected()) reconnectMQTT();
  mqtt.loop();
}
