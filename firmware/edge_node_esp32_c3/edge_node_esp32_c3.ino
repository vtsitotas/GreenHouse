#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <DHT.h>
#include <mesh_config.h>
#include <mesh_node.h>

// ── Pin definitions ───────────────────────────────────────────────────────────
#define SOIL_DATA_PIN  2   // ADC1_CH2
#define DHT_DATA_PIN   6   // GPIO6 — moved away from JTAG pins
#define SOIL_PWR_PIN   4
#define DHT_PWR_PIN    5

// ── Soil moisture calibration ─────────────────────────────────────────────────
// Read SOIL_DATA_PIN with sensor in dry air → set DRY_VAL
// Read SOIL_DATA_PIN with sensor submerged in water → set WET_VAL
#define SOIL_DRY_VAL  3163
#define SOIL_WET_VAL  1529

// ── Network (channel scan only — never connects) ──────────────────────────────
// WIFI_SSID: copy secrets.h.example to secrets.h in
// firmware/libraries/GreenhouseSecrets/ and fill in real values (gitignored
// -- see IMPROVEMENTS.md finding A1).
#include <secrets.h>

// ── Timing ────────────────────────────────────────────────────────────────────
#define SEND_INTERVAL_MS  5000   // must match MESH_EXPECTED_REPORT_INTERVAL_MS
#define SENSOR_WARMUP_MS  2000   // sensor power-up settle time

DHT dht(DHT_DATA_PIN, DHT22);

// Non-blocking sensor cycle: blocking delay()s would starve the beacon/timeout
// scheduling in loop(), so the old delay(2000)+delay(5000) cycle is now a
// two-phase state machine driven by millis().
enum SensorPhase { PHASE_IDLE, PHASE_WARMUP };
SensorPhase phase        = PHASE_IDLE;
uint32_t    phaseStartMs = 0;
uint32_t    lastCycleMs  = 0;
uint32_t    lastRescanMs = 0;

int32_t getWiFiChannel(const char* ssid) {
  int32_t n = WiFi.scanNetworks();
  for (int i = 0; i < n; i++) {
    if (strcmp(ssid, WiFi.SSID(i).c_str()) == 0) return WiFi.channel(i);
  }
  return 1;
}

float soilPercent(int raw) {
  float pct = 100.0f * (SOIL_DRY_VAL - raw) / (float)(SOIL_DRY_VAL - SOIL_WET_VAL);
  if (pct < 0)   pct = 0;
  if (pct > 100) pct = 100;
  return pct;
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
    // Some child picked us as its parent — relay its packet toward the bridge.
    meshRelayData(info->src_addr, data, len);
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);  // wait for USB CDC to connect on C3

  pinMode(SOIL_PWR_PIN, OUTPUT);
  pinMode(DHT_PWR_PIN,  OUTPUT);
  digitalWrite(SOIL_PWR_PIN, LOW);
  digitalWrite(DHT_PWR_PIN,  LOW);

  dht.begin();

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
  meshInit(0);  // channel 0 = follow current radio channel (survives re-scans)

  Serial.printf("[edge] MAC: %s\n", WiFi.macAddress().c_str());
  Serial.println("[edge] unrouted — listening for trusted beacons");
}

void loop() {
  uint32_t now = millis();

  meshBeaconTick(now);          // own beacon on the trickle schedule
  meshCheckParentTimeout(now);  // self-heal: drop a silent parent

  switch (phase) {
    case PHASE_IDLE:
      if (now - lastCycleMs >= SEND_INTERVAL_MS) {
        digitalWrite(SOIL_PWR_PIN, HIGH);
        digitalWrite(DHT_PWR_PIN,  HIGH);
        phaseStartMs = now;
        phase = PHASE_WARMUP;
      }
      break;

    case PHASE_WARMUP:
      if (now - phaseStartMs >= SENSOR_WARMUP_MS) {
        SensorPacket pkt;
        pkt.temperature   = dht.readTemperature();
        pkt.humidity      = dht.readHumidity();
        pkt.soil_moisture = soilPercent(analogRead(SOIL_DATA_PIN));  // read while powered

        digitalWrite(SOIL_PWR_PIN, LOW);
        digitalWrite(DHT_PWR_PIN,  LOW);
        lastCycleMs = now;
        phase = PHASE_IDLE;

        if (isnan(pkt.temperature) || isnan(pkt.humidity)) {
          Serial.println("[sensor] DHT read failed — check pull-up resistor on GPIO6");
        } else {
          Serial.printf("[sensor] T=%.1f H=%.1f Soil=%.0f%%\n",
                        pkt.temperature, pkt.humidity, pkt.soil_moisture);
          meshSendReading(&pkt);  // to parent, or buffered while unrouted
        }
      }
      break;
  }

  // Continuously unrouted for a minute → maybe the router changed channels.
  // Re-scan and retune (peers use channel 0, so no re-registration needed).
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

  delay(10);  // yield; keeps the loop responsive without busy-spinning
}
