#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <DHT.h>

// ── Pin definitions (ESP32 WROOM-32) ─────────────────────────────────────────
#define DHT_DATA_PIN   4   // GPIO4
#define SOIL_DATA_PIN  34  // GPIO34 — ADC1, input-only
#define DHT_PWR_PIN    26  // GPIO26
#define SOIL_PWR_PIN   27  // GPIO27

// ── Soil moisture calibration ─────────────────────────────────────────────────
// Read SOIL_DATA_PIN with sensor in dry air → set DRY_VAL
// Read SOIL_DATA_PIN with sensor submerged in water → set WET_VAL
#define SOIL_DRY_VAL  3163
#define SOIL_WET_VAL  1529

// ── Bridge MAC address ────────────────────────────────────────────────────────
uint8_t bridgeMac[] = { 0x20, 0x6E, 0xF1, 0x6C, 0x6B, 0x50 };

// ── Network (channel scan only — never connects) ──────────────────────────────
#define WIFI_SSID "TP-Link_14A6"

// ── Send interval ─────────────────────────────────────────────────────────────
#define SEND_INTERVAL_MS 5000

// ── Shared struct (must match bridge exactly) ─────────────────────────────────
typedef struct {
  float temperature;
  float humidity;
  float soil_moisture;  // 0–100 %
} SensorPacket;

DHT dht(DHT_DATA_PIN, DHT22);
esp_now_peer_info_t peer;
int failCount = 0;

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
  if (status == ESP_NOW_SEND_SUCCESS) {
    Serial.println("[esp-now] send OK");
    failCount = 0;
  } else {
    Serial.println("[esp-now] send FAIL");
    failCount++;
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(DHT_PWR_PIN,  OUTPUT);
  pinMode(SOIL_PWR_PIN, OUTPUT);
  digitalWrite(DHT_PWR_PIN,  LOW);
  digitalWrite(SOIL_PWR_PIN, LOW);

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

  memcpy(peer.peer_addr, bridgeMac, 6);
  peer.channel = ch;
  peer.encrypt  = false;
  esp_now_add_peer(&peer);

  Serial.printf("[edge] MAC: %s\n", WiFi.macAddress().c_str());
  Serial.println("[edge] ready");
}

void loop() {
  digitalWrite(DHT_PWR_PIN,  HIGH);
  digitalWrite(SOIL_PWR_PIN, HIGH);
  delay(2000);

  SensorPacket pkt;
  pkt.temperature   = dht.readTemperature();
  pkt.humidity      = dht.readHumidity();
  pkt.soil_moisture = soilPercent(analogRead(SOIL_DATA_PIN));

  digitalWrite(DHT_PWR_PIN,  LOW);
  digitalWrite(SOIL_PWR_PIN, LOW);

  if (isnan(pkt.temperature) || isnan(pkt.humidity)) {
    Serial.println("[sensor] DHT read failed — check pull-up resistor on GPIO4");
  } else {
    pkt.soil_moisture = soilPercent(analogRead(SOIL_DATA_PIN));
    Serial.printf("[sensor] T=%.1f H=%.1f Soil=%.0f%%\n",
                  pkt.temperature, pkt.humidity, pkt.soil_moisture);
    esp_now_send(bridgeMac, (uint8_t*)&pkt, sizeof(pkt));
  }

  if (failCount >= 3) {
    Serial.println("[esp-now] re-scanning channel...");
    int32_t ch = getWiFiChannel(WIFI_SSID);
    esp_wifi_set_promiscuous(true);
    esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_promiscuous(false);
    peer.channel = ch;
    esp_now_mod_peer(&peer);
    failCount = 0;
  }

  delay(SEND_INTERVAL_MS);
}
