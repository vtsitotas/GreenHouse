#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_sleep.h>
#include <DHT.h>
#include <mesh_config.h>
#include <mesh_node.h>

// ── Pin definitions (ESP32 WROOM-32) ─────────────────────────────────────────
#define DHT_DATA_PIN   4   // GPIO4
#define SOIL_DATA_PIN  34  // GPIO34 — ADC1, input-only
#define DHT_PWR_PIN    26  // GPIO26
#define SOIL_PWR_PIN   27  // GPIO27

// Battery divider: battery+ ── 220 kΩ ── ADC pin ── 220 kΩ ── GND. ~7.5 µA
// constant drain (accepted per spec: 14% of the 55 µA sleep floor, avoids a
// high-side switch part). Unfitted (mains-powered) boards read the pin
// floating near 0 V — readBatteryMv() clamps anything under 2000 mV to 0
// ("not measured"), which is also the correct reading for a real near-dead
// cell, so the bridge simply skips publishing in either case.
#define BATT_ADC_PIN  35  // GPIO35 / ADC1_CH7, input-only

// ── Soil moisture calibration ─────────────────────────────────────────────────
// Read SOIL_DATA_PIN with sensor in dry air → set DRY_VAL
// Read SOIL_DATA_PIN with sensor submerged in water → set WET_VAL
#define SOIL_DRY_VAL  3163
#define SOIL_WET_VAL  1529

// ── Timing ────────────────────────────────────────────────────────────────────
#define SEND_INTERVAL_MS  5000   // must match MESH_EXPECTED_REPORT_INTERVAL_MS
#define SENSOR_WARMUP_MS  2000   // sensor power-up settle time

DHT dht(DHT_DATA_PIN, DHT22);

// Non-blocking sensor cycle — see the C3 variant for rationale. (Always-on /
// non-sleepy path only — the sleepy path below uses its own bounded wait
// loops.)
enum SensorPhase { PHASE_IDLE, PHASE_WARMUP };
SensorPhase phase        = PHASE_IDLE;
uint32_t    phaseStartMs = 0;
uint32_t    lastCycleMs  = 0;
uint32_t    lastRescanMs = 0;

// Deployments with a router used to scan for its SSID purely to pick a
// channel to agree on. A UART-wired fleet (no router at all) has nothing to
// scan for, so every node just locks to the fixed constant instead — see
// MESH_FIXED_CHANNEL's comment in mesh_config.h. Kept as its own function
// (rather than inlining the constant at each call site) so every call site
// below is otherwise byte-for-byte unchanged — same self-heal scaffolding,
// same signature, only the channel-acquisition primitive underneath swapped.
int32_t getMeshChannel() {
  return MESH_FIXED_CHANNEL;
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

// Consecutive sleepy wakes that ended without a single confirmed delivery.
// Survives deep sleep so a router channel change while asleep can't strand
// the node forever: after 2 silent wakes (~30 min) the next wake ignores the
// cached channel and pays for a full SSID scan — the only way back onto the
// mesh's new channel. Reset to 0 by any confirmed delivery.
RTC_DATA_ATTR uint8_t g_unconfirmedWakes = 0;

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
  digitalWrite(DHT_PWR_PIN,  LOW);
  digitalWrite(SOIL_PWR_PIN, LOW);

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
// (This WROOM board ships always-on in TRUSTED_NODES[] today, but the sketch
// carries the full sleepy path regardless — role is config, not code.)
void runSleepyCycle() {
  const uint32_t deadline = MESH_WAKE_MAX_AWAKE_MS;  // millis() budget from boot

  bool restored = meshRtcRestore();  // hint only — confirmed via TX/beacon below
  Serial.printf("[wake] rtc restore: %s\n", restored ? "parent hint" : "none (cold/invalid)");

  // Sensors on immediately — warm-up runs concurrently with radio bring-up.
  digitalWrite(DHT_PWR_PIN,  HIGH);
  digitalWrite(SOIL_PWR_PIN, HIGH);
  uint32_t warmupStart = millis();

  uint8_t ch = meshRtcSavedChannel();
  if (ch == 0 || g_unconfirmedWakes >= 2) {
    Serial.printf("[wake] channel lookup (%s)\n",
                  ch == 0 ? "no saved channel" : "2+ silent wakes — re-confirming");
    ch = (uint8_t)getMeshChannel();
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

  digitalWrite(DHT_PWR_PIN,  LOW);
  digitalWrite(SOIL_PWR_PIN, LOW);

  if (isnan(pkt.temperature) || isnan(pkt.humidity)) {
    Serial.println("[sensor] DHT read failed — check pull-up resistor on GPIO4");
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
      g_lastTxStatus = -1;  // flush result must not be judged by a stale status
      meshFlushBuffer();    // resends same-seq packets; bridge de-dups any that arrived
      uint32_t confirmStart = millis();
      while (g_lastTxStatus == -1 && millis() - confirmStart < MESH_TX_CONFIRM_WAIT_MS &&
             millis() < deadline) {
        delay(5);
      }
    } else {
      Serial.println("[wake] still unrouted — reading stays buffered");
    }
  }

  // Channel self-heal bookkeeping: a confirmed delivery proves the cached
  // channel is still right; anything else counts toward forcing a rescan.
  bool confirmed = delivered || (meshHasParent() && g_lastTxStatus == 1);
  if (confirmed) g_unconfirmedWakes = 0;
  else if (g_unconfirmedWakes < 250) g_unconfirmedWakes++;

  goToSleep(ch);  // never returns
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

  // meshIsSelfSleepy() needs WiFi.mode() set (WiFi.macAddress requires the
  // driver up) — satisfied above. This board's TRUSTED_NODES[] entry is
  // always-on today, so this branch is dormant here, but it's the same
  // firmware image as the C3 — role is config, not code.
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
        digitalWrite(DHT_PWR_PIN,  HIGH);
        digitalWrite(SOIL_PWR_PIN, HIGH);
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

        digitalWrite(DHT_PWR_PIN,  LOW);
        digitalWrite(SOIL_PWR_PIN, LOW);
        lastCycleMs = now;
        phase = PHASE_IDLE;

        // Battery telemetry: always-on nodes report too when a divider is
        // fitted (mains node on a shared board revision); an unfitted pin
        // floats near 0 and readBatteryMv() reports "not measured" (0).
        meshSetBatteryMv(readBatteryMv());

        if (isnan(pkt.temperature) || isnan(pkt.humidity)) {
          Serial.println("[sensor] DHT read failed — check pull-up resistor on GPIO4");
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

  // No router to change channels on a fixed-channel fleet, so this can no
  // longer be a real recovery action — re-applying the same constant is a
  // harmless no-op, kept only so a continuously-unrouted node still logs
  // something periodically for bench visibility, without adding a new
  // "why is this node stuck" code path just for that.
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

  delay(10);  // yield; keeps the loop responsive without busy-spinning
}
