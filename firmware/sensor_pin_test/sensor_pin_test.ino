// ── Sensor Pin/Solder Bench Test ─────────────────────────────────────────────
// Standalone diagnostic: no WiFi/ESP-NOW/mesh. Powers the DHT22 + soil sensor
// and streams raw + converted readings over Serial so you can probe/wiggle
// joints on the bench and watch for dropouts or stuck values.
//
// ESP32-C3 only. Flash, open Serial Monitor at 115200.

#include <Arduino.h>
#include <WiFi.h>
#include <DHT.h>

#define SOIL_DATA_PIN  1   // ADC1_CH1 — NOT GPIO2: that's an ESP32-C3 strapping
                            // pin with a board pull-up that pins the ADC near
                            // VCC regardless of sensor output (confirmed on bench).
#define DHT_DATA_PIN   6
#define SOIL_PWR_PIN   4
#define DHT_PWR_PIN    5

// Same calibration as the production firmware — absolute accuracy doesn't
// matter here, only whether the raw value moves/responds at all.
#define SOIL_DRY_VAL  3163
#define SOIL_WET_VAL  1529

#define DHT_READ_INTERVAL_MS   2500   // DHT22 min sample spacing is ~2s
#define ADC_READ_INTERVAL_MS   500

DHT dht(DHT_DATA_PIN, DHT22);

uint32_t lastDhtMs = 0;
uint32_t lastAdcMs = 0;
uint32_t dhtFailStreak = 0;

float soilPercent(int raw) {
  float pct = 100.0f * (SOIL_DRY_VAL - raw) / (float)(SOIL_DRY_VAL - SOIL_WET_VAL);
  if (pct < 0)   pct = 0;
  if (pct > 100) pct = 100;
  return pct;
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  WiFi.mode(WIFI_STA);

  pinMode(SOIL_PWR_PIN, OUTPUT);
  pinMode(DHT_PWR_PIN,  OUTPUT);
  digitalWrite(SOIL_PWR_PIN, HIGH);  // left on for the whole test — probe joints live
  digitalWrite(DHT_PWR_PIN,  HIGH);

  dht.begin();

  Serial.println("--- SENSOR PIN TEST ---");
  Serial.println("board: ESP32-C3");
  Serial.printf("MAC address: %s\n", WiFi.macAddress().c_str());
  Serial.printf("soil: data=GPIO%d pwr=GPIO%d | dht: data=GPIO%d pwr=GPIO%d\n",
                SOIL_DATA_PIN, SOIL_PWR_PIN, DHT_DATA_PIN, DHT_PWR_PIN);
  Serial.println("Wiggle/probe connections while readings stream — dropouts or");
  Serial.println("stuck values point at the joint on that pin.");
}

void loop() {
  uint32_t now = millis();

  if (now - lastAdcMs >= ADC_READ_INTERVAL_MS) {
    lastAdcMs = now;
    int soilRaw  = analogRead(SOIL_DATA_PIN);
    Serial.printf("[adc] soil raw=%d (%.0f%%)%s\n",
                  soilRaw, soilPercent(soilRaw),
                  (soilRaw <= 5 || soilRaw >= 4090) ? "  <-- stuck at rail, check joint" : "");
  }

  if (now - lastDhtMs >= DHT_READ_INTERVAL_MS) {
    lastDhtMs = now;
    float t = dht.readTemperature();
    float h = dht.readHumidity();
    if (isnan(t) || isnan(h)) {
      dhtFailStreak++;
      Serial.printf("[dht] READ FAILED (streak=%lu) — check data/pwr/gnd joints on GPIO%d/GPIO%d\n",
                    (unsigned long)dhtFailStreak, DHT_DATA_PIN, DHT_PWR_PIN);
    } else {
      dhtFailStreak = 0;
      Serial.printf("[dht] T=%.1fC H=%.1f%%\n", t, h);
    }
  }
}
