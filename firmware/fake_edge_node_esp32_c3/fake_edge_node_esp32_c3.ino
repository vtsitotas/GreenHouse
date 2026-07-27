// ── Fake Edge Node ────────────────────────────────────────────────────────────
// Generates realistic random-walk sensor data (temp, humidity, soil, light)
// to test the full pipeline: mesh → bridge → MQTT → Pi → app.
// No real sensors needed. Flash this, test, then go back to edge_node_esp32_c3.

#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <mesh_config.h>
#include <mesh_node.h>

// ── Network ───────────────────────────────────────────────────────────────────
#define WIFI_SSID "billredmi"

// ── Timing ────────────────────────────────────────────────────────────────────
#define SEND_INTERVAL_MS  5000

// ── Fake sensor state (smooth random walk) ────────────────────────────────────
float fakeTemp  = 25.0;
float fakeHumid = 65.0;
float fakeSoil  = 40.0;
float fakeLight = 60.0;

uint32_t lastCycleMs  = 0;
uint32_t lastRescanMs = 0;

float clampf(float v, float lo, float hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

float randomWalk(float current, float minVal, float maxVal, float maxStep) {
  float delta = (random(-100, 101) / 100.0f) * maxStep;
  return clampf(current + delta, minVal, maxVal);
}

int32_t getWiFiChannel(const char* ssid) {
  int32_t n = WiFi.scanNetworks();
  for (int i = 0; i < n; i++) {
    if (strcmp(ssid, WiFi.SSID(i).c_str()) == 0) return WiFi.channel(i);
  }
  return 1;
}

void onDataSent(const wifi_tx_info_t* info, esp_now_send_status_t status) {
  meshNotifyTxStatus(status == ESP_NOW_SEND_SUCCESS);
}

void onDataRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  uint32_t now = millis();
  int rssi = info->rx_ctrl ? info->rx_ctrl->rssi : -127;
  if (len == sizeof(MeshBeacon)) {
    MeshBeacon b;
    memcpy(&b, data, sizeof(b));
    if (b.magic == MESH_MAGIC) meshHandleBeacon(info->src_addr, &b, rssi, now);
  } else if (len == sizeof(MeshDataPacket)) {
    meshRelayData(info->src_addr, data, len);
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  Serial.println("--- FAKE SENSOR MODE ---");
  randomSeed(analogRead(0) ^ (uint32_t)ESP.getEfuseMac());

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  Serial.print("[wifi] scanning channel for " WIFI_SSID "...");
  int32_t ch = getWiFiChannel(WIFI_SSID);
  Serial.printf(" ch%d\n", ch);

  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  if (esp_now_init() != ESP_OK) {
    Serial.println("[esp-now] init failed, rebooting");
    ESP.restart();
  }
  esp_now_register_send_cb(onDataSent);
  esp_now_register_recv_cb(onDataRecv);
  meshInit(0);

  Serial.printf("[edge] MAC: %s\n", WiFi.macAddress().c_str());
  Serial.println("[edge] unrouted — listening for trusted beacons");
}

void loop() {
  uint32_t now = millis();

  meshBeaconTick(now);
  meshCheckParentTimeout(now);

  if (now - lastCycleMs >= SEND_INTERVAL_MS) {
    lastCycleMs = now;

    SensorPacket pkt;
    fakeTemp  = randomWalk(fakeTemp,  18.0, 38.0, 0.3);   // °C
    fakeHumid = randomWalk(fakeHumid, 40.0, 95.0, 1.0);   // %
    fakeSoil  = randomWalk(fakeSoil,  10.0, 80.0, 2.0);   // %
    fakeLight = randomWalk(fakeLight, 10.0, 100.0, 3.0);  // %

    pkt.temperature   = fakeTemp;
    pkt.humidity      = fakeHumid;
    pkt.soil_moisture = fakeSoil;
    pkt.light_lux     = fakeLight;

    Serial.printf("[sensor] T=%.1f H=%.1f Soil=%.0f%% Light=%.0f%% (FAKE)\n",
                  pkt.temperature, pkt.humidity,
                  pkt.soil_moisture, pkt.light_lux);
    meshSendReading(&pkt);
  }

  // Re-scan if unrouted too long
  if (!meshHasParent()) {
    if (now - lastRescanMs >= MESH_RESCAN_AFTER_MS) {
      lastRescanMs = now;
      Serial.println("[esp-now] unrouted too long — re-scanning WiFi channel");
      int32_t ch = getWiFiChannel(WIFI_SSID);
      esp_wifi_set_promiscuous(true);
      esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
      esp_wifi_set_promiscuous(false);
      Serial.printf("[esp-now] tuned to ch%d\n", ch);
    }
  } else {
    lastRescanMs = now;
  }

  delay(10);
}
