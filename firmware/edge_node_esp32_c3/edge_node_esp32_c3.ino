#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_sleep.h>
#include <DHT.h>
#include <mesh_config.h>
#include <mesh_node.h>

// ── Pin definitions ───────────────────────────────────────────────────────────
#define SOIL_DATA_PIN  2   // ADC1_CH2
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
// two-phase state machine driven by millis(). (Always-on / non-sleepy path
// only — the sleepy path below uses its own bounded wait loops.)
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

// -1 = pending (no send callback yet), 0 = last unicast failed, 1 = ok.
// Written from the ESP-NOW send callback (interrupt/task context), read from
// the wake cycle's poll loop — volatile keeps the compiler from caching it.
volatile int8_t g_lastTxStatus = -1;

void onDataSent(const wifi_tx_info_t* info, esp_now_send_status_t status) {
  bool ok = (status == ESP_NOW_SEND_SUCCESS);
  meshNotifyTxStatus(ok);   // shared by both roles: 3-consecutive-fail backstop
  g_lastTxStatus = ok ? 1 : 0;
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

// 8-sample average of analogReadMilliVolts() (factory ADC calibration,
// default 11 dB attenuation — correct for the divided range, no
// analogSetAttenuation() call needed), doubled for the 2×220k divider.
// < 2000 mV ⇒ pin floating / divider not fitted ⇒ "not measured".
uint16_t readBatteryMv() {
  uint32_t sum = 0;
  for (int i = 0; i < 8; i++) {
    sum += analogReadMilliVolts(BATT_ADC_PIN);
    delay(2);
  }
  uint16_t mv = (uint16_t)((sum / 8) * 2);
  return (mv < 2000) ? 0 : mv;
}

// The ONLY exit from the sleepy wake cycle. Persists RTC state LAST, arms the
// timer for whatever's left of the 15-minute interval (floored), and never
// returns. Sensor PWR pins are forced LOW here defensively so every call site
// doesn't have to remember to do it first.
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

// Send the wake reading and wait (bounded by both MESH_TX_CONFIRM_WAIT_MS and
// the overall wake deadline) for the ESP-NOW send callback to confirm it.
// g_lastTxStatus is reset to -1 immediately before meshSendReading() so the
// wake beacon's own (broadcast, always-success) send callback — which fires
// within milliseconds of a call ~2s earlier — can't be mistaken for this
// unicast's result.
// If meshSendReading() had no parent to send to, it already buffered the
// packet instead of handing it to the radio: no send callback will ever
// arrive for it, so check meshHasParent() BEFORE waiting rather than idling
// out the full window for nothing.
bool sendWithConfirm(const SensorPacket* payload, uint32_t deadline) {
  g_lastTxStatus = -1;
  meshSendReading(payload);

  if (!meshHasParent()) return false;  // buffered, unrouted — no callback coming

  uint32_t waitStart = millis();
  while (g_lastTxStatus == -1 &&
         millis() - waitStart < MESH_TX_CONFIRM_WAIT_MS &&
         millis() < deadline) {
    delay(5);
  }
  return (g_lastTxStatus == 1 && meshHasParent());
}

// Replaces the always-on loop() entirely for a node whose own TRUSTED_NODES[]
// entry says sleepy=true. Single pass, hard-bounded by MESH_WAKE_MAX_AWAKE_MS,
// never returns — the last thing it ever does is esp_deep_sleep_start().
void runSleepyCycle() {
  const uint32_t deadline = MESH_WAKE_MAX_AWAKE_MS;  // millis() budget from boot

  bool restored = meshRtcRestore();  // hint only — confirmed via TX/beacon below
  Serial.printf("[wake] rtc restore: %s\n", restored ? "parent hint" : "none (cold/invalid)");

  // Sensors on immediately — warm-up runs concurrently with radio bring-up.
  digitalWrite(SOIL_PWR_PIN, HIGH);
  digitalWrite(DHT_PWR_PIN,  HIGH);
  uint32_t warmupStart = millis();

  uint8_t ch = meshRtcSavedChannel();
  if (ch == 0) {
    Serial.println("[wake] no saved channel — full SSID scan");
    ch = (uint8_t)getWiFiChannel(WIFI_SSID);
  }
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  if (esp_now_init() != ESP_OK) {
    // CRITICAL: sleep, never a restart loop — a restart loop on a battery
    // node with a persistently-failing radio would drain the pack fast.
    Serial.println("[esp-now] init failed on wake — sleeping without sending");
    goToSleep(ch);
  }
  esp_now_register_send_cb(onDataSent);
  esp_now_register_recv_cb(onDataRecv);
  meshInit(0);  // channel 0 = follow current radio channel

  // Announces us (sleepy-flagged); trickle-resets awake neighbors so we hear
  // the parent's (or a new candidate's) beacon during the warm-up wait below.
  meshSendBeaconNow(meshMyRank, MESH_SLEEP_INTERVAL_MS);

  while (millis() - warmupStart < SENSOR_WARMUP_MS && millis() < deadline) delay(10);

  SensorPacket pkt;
  pkt.temperature   = dht.readTemperature();
  pkt.humidity      = dht.readHumidity();
  pkt.soil_moisture = soilPercent(analogRead(SOIL_DATA_PIN));  // read while powered

  digitalWrite(SOIL_PWR_PIN, LOW);
  digitalWrite(DHT_PWR_PIN,  LOW);

  if (isnan(pkt.temperature) || isnan(pkt.humidity)) {
    Serial.println("[sensor] DHT read failed — check pull-up resistor on GPIO6");
  } else {
    Serial.printf("[sensor] T=%.1f H=%.1f Soil=%.0f%%\n",
                  pkt.temperature, pkt.humidity, pkt.soil_moisture);
  }

  meshSetBatteryMv(readBatteryMv());

  bool delivered = sendWithConfirm(&pkt, deadline);
  Serial.printf("[wake] delivered=%d hasParent=%d\n", delivered, meshHasParent());

  if (!delivered && millis() < deadline) {
    // Orphan fallback: the remembered parent (if any) is unconfirmed —
    // rediscover, bounded by the discovery window and the overall deadline.
    if (meshHasParent()) meshDropParent("wake tx unconfirmed");
    meshRequeueLastReading();  // no-op if meshSendReading already buffered it

    uint32_t listenStart = millis();
    while (!meshHasParent() && millis() - listenStart < MESH_WAKE_DISCOVERY_MS &&
           millis() < deadline) {
      delay(10);
    }
    if (meshHasParent()) {
      meshFlushBuffer();  // resends same-seq packets; bridge de-dups any that arrived
    } else {
      Serial.println("[wake] still unrouted — reading stays buffered");
    }
  }

  goToSleep(ch);  // never returns
}

void setup() {
  Serial.begin(115200);
  // Skip the USB-CDC settle wait on a timer wake — nobody's watching a
  // serial monitor in the field, and it's 1.5 s of battery every 15 min.
  // Keep it on power-on/flash/brownout wakes for bench work.
  if (esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_TIMER) delay(1500);

  pinMode(SOIL_PWR_PIN, OUTPUT);
  pinMode(DHT_PWR_PIN,  OUTPUT);
  digitalWrite(SOIL_PWR_PIN, LOW);
  digitalWrite(DHT_PWR_PIN,  LOW);

  dht.begin();

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  // meshIsSelfSleepy() needs WiFi.mode() set (WiFi.macAddress requires the
  // driver up) — satisfied above. Sleepy boards never reach the always-on
  // scan/loop below; runSleepyCycle() ends in esp_deep_sleep_start().
  if (meshIsSelfSleepy()) runSleepyCycle();  // never returns

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

        // Battery telemetry: always-on nodes report too when a divider is
        // fitted (mains node on a shared board revision); an unfitted pin
        // floats near 0 and readBatteryMv() reports "not measured" (0).
        meshSetBatteryMv(readBatteryMv());

        if (isnan(pkt.temperature) || isnan(pkt.humidity)) {
          Serial.println("[sensor] DHT read failed — check pull-up resistor on GPIO6");
        } else {
          Serial.printf("[sensor] T=%.1f H=%.1f Soil=%.0f%%\n",
                        pkt.temperature, pkt.humidity, pkt.soil_moisture);
        }
        // Send even on a failed DHT read: NaN survives the wire fine
        // (IEEE-754, same encoding both ends) and the bridge skips
        // publishing just the NaN metric(s). Keeps lastSeenMs/nodeOnline
        // current so a bad DHT read doesn't falsely report the whole node
        // offline (IMPROVEMENTS.md finding B6).
        meshSendReading(&pkt);  // to parent, or buffered while unrouted
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
