#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_sleep.h>
#include <DHT.h>
#include "mesh_config.h"
#include "mesh_node.h"

// ── Pin definitions ───────────────────────────────────────────────────────────
#define SOIL_DATA_PIN  1   // ADC1_CH1 — NOT GPIO2: that's an ESP32-C3 strapping
                            // pin and some boards carry a hardware pull-up on
                            // it, which pins the ADC near VCC regardless of
                            // sensor output (found via sensor_pin_test.ino).
#define DHT_DATA_PIN   6   // GPIO6 — moved away from JTAG pins
#define SOIL_PWR_PIN   4
#define DHT_PWR_PIN    5

// Battery divider: battery+ ── 220 kΩ ── ADC pin ── 220 kΩ ── GND. ~7.5 µA
// constant drain (accepted per spec: 14% of the 55 µA sleep floor, avoids a
// high-side switch part). Unfitted (mains-powered) boards read the pin
// floating near 0 V — readBatteryMv() clamps anything under 2000 mV to 0
// ("not measured"), which is also the correct reading for a real near-dead
// cell, so the bridge simply skips publishing in either case.
#define BATT_ADC_PIN  3   // GPIO3 / ADC1_CH3

// ── Soil moisture calibration ─────────────────────────────────────────────────
#define SOIL_DRY_VAL  3163
#define SOIL_WET_VAL  1529

// ── Timing ────────────────────────────────────────────────────────────────────
#define SEND_INTERVAL_MS  5000   // must match MESH_EXPECTED_REPORT_INTERVAL_MS
#define SENSOR_WARMUP_MS  2000   // sensor power-up settle time

DHT dht(DHT_DATA_PIN, DHT22);

enum SensorPhase { PHASE_IDLE, PHASE_WARMUP };
SensorPhase phase        = PHASE_IDLE;
uint32_t    phaseStartMs = 0;
uint32_t    lastCycleMs  = 0;
uint32_t    lastRescanMs = 0;
uint32_t    lastJoinMs   = 0;

int32_t getMeshChannel() {
  return MESH_FIXED_CHANNEL;
}

float soilPercent(int raw) {
  float pct = 100.0f * (SOIL_DRY_VAL - raw) / (float)(SOIL_DRY_VAL - SOIL_WET_VAL);
  if (pct < 0)   pct = 0;
  if (pct > 100) pct = 100;
  return pct;
}

volatile int8_t g_lastTxStatus = -1;
RTC_DATA_ATTR uint8_t g_unconfirmedWakes = 0;

void onDataSent(const wifi_tx_info_t* info, esp_now_send_status_t status) {
  bool ok = (status == ESP_NOW_SEND_SUCCESS);
  meshNotifyTxStatus(ok);
  g_lastTxStatus = ok ? 1 : 0;
}

void onDataRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  uint32_t now = millis();
  int rssi = info->rx_ctrl ? info->rx_ctrl->rssi : -127;

  // Check for a provisioning blob (33 bytes, not a beacon or data packet)
  if (len == MESH_PROVISION_LEN && !meshStoreIsProvisioned()) {
    if (meshHandleProvision(data, len)) {
      Serial.println("[edge] provisioned — will boot into normal mode");
      delay(500);
      ESP.restart();
    }
    return;
  }

  if (len == (int)sizeof(MeshBeacon)) {
    MeshBeacon b;
    memcpy(&b, data, sizeof(b));
    if (b.magic == MESH_MAGIC_V2) meshHandleBeacon(info->src_addr, &b, rssi, now);
  } else if (len == MESH_PACKET_LEN) {
    // Some child picked us as its parent — relay its packet toward the bridge.
    meshRelayData(info->src_addr, data, len);
  }
}

uint16_t readBatteryMv() {
  uint32_t sum = 0;
  for (int i = 0; i < 8; i++) {
    sum += analogReadMilliVolts(BATT_ADC_PIN);
    delay(2);
  }
  uint16_t mv = (uint16_t)((sum / 8) * 2);
  return (mv < 2000) ? 0 : mv;
}

void goToSleep(uint8_t channel) {
  digitalWrite(SOIL_PWR_PIN, LOW);
  digitalWrite(DHT_PWR_PIN,  LOW);

  meshRtcPersist(channel);

  uint32_t awake = millis();
  uint64_t sleepMs = (MESH_SLEEP_INTERVAL_MS > awake + MESH_MIN_SLEEP_MS)
                       ? (MESH_SLEEP_INTERVAL_MS - awake) : MESH_MIN_SLEEP_MS;
  Serial.printf("[sleep] awake %lums, sleeping %llums\n",
                (unsigned long)awake, (unsigned long long)sleepMs);
  Serial.flush();
  esp_sleep_enable_timer_wakeup(sleepMs * 1000ULL);
  esp_deep_sleep_start();
}

bool sendWithConfirm(const SensorReading* r, uint32_t deadline) {
  g_lastTxStatus = -1;
  meshSendReading(r);

  if (!meshHasParent()) return false;

  uint32_t waitStart = millis();
  while (g_lastTxStatus == -1 &&
         millis() - waitStart < MESH_TX_CONFIRM_WAIT_MS &&
         millis() < deadline) {
    delay(5);
  }
  return (g_lastTxStatus == 1 && meshHasParent());
}

void runSleepyCycle() {
  const uint32_t deadline = MESH_WAKE_MAX_AWAKE_MS;

  bool restored = meshRtcRestore();
  Serial.printf("[wake] rtc restore: %s\n", restored ? "parent hint" : "none (cold/invalid)");

  digitalWrite(SOIL_PWR_PIN, HIGH);
  digitalWrite(DHT_PWR_PIN,  HIGH);
  uint32_t warmupStart = millis();

  uint8_t ch = meshRtcSavedChannel();
  if (ch == 0 || g_unconfirmedWakes >= 2) {
    ch = (uint8_t)getMeshChannel();
  }
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  if (esp_now_init() != ESP_OK) {
    Serial.println("[esp-now] init failed on wake — sleeping without sending");
    goToSleep(ch);
  }
  esp_now_register_send_cb(onDataSent);
  esp_now_register_recv_cb(onDataRecv);
  meshInit(0);

  meshSendBeaconNow(meshMyRank, MESH_SLEEP_INTERVAL_MS);

  while (millis() - warmupStart < SENSOR_WARMUP_MS && millis() < deadline) delay(10);

  SensorReading r;
  r.temperature   = dht.readTemperature();
  r.humidity      = dht.readHumidity();
  r.soil_moisture = soilPercent(analogRead(SOIL_DATA_PIN));

  digitalWrite(SOIL_PWR_PIN, LOW);
  digitalWrite(DHT_PWR_PIN,  LOW);

  if (isnan(r.temperature) || isnan(r.humidity)) {
    Serial.println("[sensor] DHT read failed — check pull-up resistor on GPIO6");
  } else {
    Serial.printf("[sensor] T=%.1f H=%.1f Soil=%.0f%%\n",
                  r.temperature, r.humidity, r.soil_moisture);
  }

  meshSetBatteryMv(readBatteryMv());

  bool delivered = sendWithConfirm(&r, deadline);
  Serial.printf("[wake] delivered=%d hasParent=%d\n", delivered, meshHasParent());

  if (!delivered && millis() < deadline) {
    if (meshHasParent()) meshDropParent("wake tx unconfirmed");
    meshRequeueLastReading();

    uint32_t listenStart = millis();
    while (!meshHasParent() && millis() - listenStart < MESH_WAKE_DISCOVERY_MS &&
           millis() < deadline) {
      delay(10);
    }
    if (meshHasParent()) {
      g_lastTxStatus = -1;
      meshFlushBuffer();
      uint32_t confirmStart = millis();
      while (g_lastTxStatus == -1 && millis() - confirmStart < MESH_TX_CONFIRM_WAIT_MS &&
             millis() < deadline) {
        delay(5);
      }
    } else {
      Serial.println("[wake] still unrouted — reading stays buffered");
    }
  }

  bool confirmed = delivered || (meshHasParent() && g_lastTxStatus == 1);
  if (confirmed) g_unconfirmedWakes = 0;
  else if (g_unconfirmedWakes < 250) g_unconfirmedWakes++;

  goToSleep(ch);  // never returns
}

void setup() {
  Serial.begin(115200);
  if (esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_TIMER) delay(1500);

  pinMode(SOIL_PWR_PIN, OUTPUT);
  pinMode(DHT_PWR_PIN,  OUTPUT);
  digitalWrite(SOIL_PWR_PIN, LOW);
  digitalWrite(DHT_PWR_PIN,  LOW);

  dht.begin();

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  meshStoreBegin();

  // Cold boot only: a timer wake keeps its RTC seq, so bumping here would burn
  // flash every 15 minutes for nothing.
  if (esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_TIMER)
    meshStoreBumpBootCount();

  if (!meshStoreIsProvisioned()) {
    // Unenrolled: announce ourselves, never sleep (the owner is standing here
    // with the app open), never route, never read sensors.
    int32_t ch = getMeshChannel();
    esp_wifi_set_promiscuous(true);
    esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_promiscuous(false);
    if (esp_now_init() != ESP_OK) { Serial.println("[esp-now] init failed"); return; }
    esp_now_register_recv_cb(onDataRecv);
    meshInit((uint8_t)ch);
    WiFi.macAddress(meshSelfMac);
    Serial.printf("[edge] unenrolled — join beacons every %lums\n",
                  (unsigned long)MESH_JOIN_BEACON_INTERVAL_MS);
    return;
  }

  // meshIsSelfSleepy() uses NVS (meshStoreBegin already called)
  if (meshIsSelfSleepy()) runSleepyCycle();  // never returns

  int32_t ch = getMeshChannel();
  Serial.printf("[wifi] fixed mesh channel: ch%d\n", ch);

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
  Serial.println("[edge] provisioned — listening for beacons");
}

void loop() {
  uint32_t now = millis();

  // Not yet provisioned — send join beacons, don't do anything else.
  if (!meshStoreIsProvisioned()) {
    if (now - lastJoinMs >= MESH_JOIN_BEACON_INTERVAL_MS) {
      lastJoinMs = now;
      meshSendJoinBeacon();
    }
    return;
  }

  meshBeaconTick(now);
  meshCheckParentTimeout(now);

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
        SensorReading r;
        r.temperature   = dht.readTemperature();
        r.humidity      = dht.readHumidity();
        r.soil_moisture = soilPercent(analogRead(SOIL_DATA_PIN));

        digitalWrite(SOIL_PWR_PIN, LOW);
        digitalWrite(DHT_PWR_PIN,  LOW);
        lastCycleMs = now;
        phase = PHASE_IDLE;

        meshSetBatteryMv(readBatteryMv());

        if (isnan(r.temperature) || isnan(r.humidity)) {
          Serial.println("[sensor] DHT read failed — check pull-up resistor on GPIO6");
        } else {
          Serial.printf("[sensor] T=%.1f H=%.1f Soil=%.0f%%\n",
                        r.temperature, r.humidity, r.soil_moisture);
        }
        meshSendReading(&r);
      }
      break;
  }

  if (!meshHasParent()) {
    if (now - lastRescanMs >= MESH_RESCAN_AFTER_MS) {
      lastRescanMs = now;
      int32_t ch = getMeshChannel();
      esp_wifi_set_promiscuous(true);
      esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
      esp_wifi_set_promiscuous(false);
      Serial.printf("[esp-now] still unrouted, confirmed on ch%d\n", ch);
    }
  } else {
    lastRescanMs = now;
  }

  delay(10);
}
