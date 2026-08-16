#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_sleep.h>
#include <mesh_config.h>
#include <mesh_node.h>

// ── FAKE SENSOR FIRMWARE ─────────────────────────────────────────────────────
// Same mesh core, same sleepy wake cycle, same timing constants as
// edge_node_esp32_c3.ino — only the DHT22/soil-ADC/battery-divider reads are
// swapped for a per-node random walk. For bench-testing the mesh (routing,
// relay, self-heal, deep sleep, MQTT delivery) on a board whose sensors
// aren't wired up yet (docs/DEVICES.md "Pending": DHT22 pull-up on GPIO6 not
// soldered). Flash to a MAC already listed in TRUSTED_NODES[] (mesh_config.h)
// — a fake image on an untrusted MAC is invisible to the mesh, same as the
// real firmware would be. Reflash edge_node_esp32_c3.ino once real sensors
// are wired; keep the two files' mesh logic in sync if you change one.

// ── Pin definitions ───────────────────────────────────────────────────────────
// Sensor power pins only — kept and toggled on the same schedule as the real
// firmware so the wake-cycle timing (awake duration, radio windows) matches
// production even though nothing is wired to them here.
#define SOIL_PWR_PIN   4
#define DHT_PWR_PIN    5

// ── Timing ────────────────────────────────────────────────────────────────────
#define SEND_INTERVAL_MS  5000   // must match MESH_EXPECTED_REPORT_INTERVAL_MS
#define SENSOR_WARMUP_MS  2000   // kept for wake-cycle timing parity with the
                                  // real sensor power-up settle time

// Non-blocking sensor cycle (always-on / non-sleepy path only) — see
// edge_node_esp32_c3.ino for why this is a state machine rather than delay()s.
enum SensorPhase { PHASE_IDLE, PHASE_WARMUP };
SensorPhase phase        = PHASE_IDLE;
uint32_t    phaseStartMs = 0;
uint32_t    lastCycleMs  = 0;
uint32_t    lastRescanMs = 0;

int32_t getMeshChannel() {
  return MESH_FIXED_CHANNEL;
}

// ── Fake sensor generation ──────────────────────────────────────────────────
// Smooth bounded random walk, seeded per-node from its own MAC so multiple
// fake nodes don't report identical values. Temperature/humidity/soil are
// RTC-persisted so the walk actually continues across deep-sleep wakes
// instead of resetting to the same baseline every cycle — same
// survives-deep-sleep trick meshRtcState uses in mesh_node.h, just for fake
// state instead of routing state.
#define FAKE_RTC_MAGIC 0x46414B45UL  // 'FAKE'

RTC_DATA_ATTR uint32_t g_fakeMagic  = 0;
RTC_DATA_ATTR float    g_fakeTemp   = 0;
RTC_DATA_ATTR float    g_fakeHumid  = 0;
RTC_DATA_ATTR float    g_fakeSoil   = 0;
RTC_DATA_ATTR uint16_t g_fakeBattMv = 0;

float clampf(float v, float lo, float hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

float randomWalk(float current, float minVal, float maxVal, float maxStep) {
  float delta = (random(-100, 101) / 100.0f) * maxStep;
  return clampf(current + delta, minVal, maxVal);
}

// Spreads each node's starting point across its plausible range so, e.g.,
// zone1 and zone2 read visibly different from the very first wake instead of
// only diverging once the random walk has had time to drift apart.
float macOffset(float range) {
  uint8_t mac[6];
  WiFi.macAddress(mac);
  return ((mac[5] / 255.0f) - 0.5f) * range;
}

// Seeds RTC-persisted fake state once per power-on/flash (magic check — same
// pattern as meshRtcState); every later deep-sleep timer wake finds the
// magic already set and just continues the walk from last wake.
void ensureFakeSeeded() {
  if (g_fakeMagic == FAKE_RTC_MAGIC) return;
  g_fakeMagic  = FAKE_RTC_MAGIC;
  g_fakeTemp   = 25.0f + macOffset(8.0f);    // ~21-29 C spread
  g_fakeHumid  = 65.0f + macOffset(20.0f);   // ~55-75 % spread
  g_fakeSoil   = 40.0f + macOffset(20.0f);   // ~30-50 % spread
  g_fakeBattMv = (uint16_t)(4100 + macOffset(200.0f));
}

void readFakeSensors(SensorPacket* pkt) {
  g_fakeTemp  = randomWalk(g_fakeTemp,  18.0f, 38.0f, 0.3f);
  g_fakeHumid = randomWalk(g_fakeHumid, 40.0f, 95.0f, 1.0f);
  g_fakeSoil  = randomWalk(g_fakeSoil,  10.0f, 80.0f, 2.0f);
  pkt->temperature   = g_fakeTemp;
  pkt->humidity      = g_fakeHumid;
  pkt->soil_moisture = g_fakeSoil;
}

// One-way drift (discharge) rather than a random walk both ways — a real
// battery trends down between charges, it doesn't wander back up.
uint16_t readFakeBatteryMv() {
  uint16_t step = (uint16_t)random(0, 4);
  if (g_fakeBattMv > 3300 + step) g_fakeBattMv -= step;
  return g_fakeBattMv;
}

// -1 = pending (no send callback yet), 0 = last unicast failed, 1 = ok.
volatile int8_t g_lastTxStatus = -1;

// Consecutive sleepy wakes that ended without a single confirmed delivery —
// see edge_node_esp32_c3.ino for the full self-heal rationale.
RTC_DATA_ATTR uint8_t g_unconfirmedWakes = 0;

void onDataSent(const wifi_tx_info_t* info, esp_now_send_status_t status) {
  bool ok = (status == ESP_NOW_SEND_SUCCESS);
  meshNotifyTxStatus(ok);
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
    meshRelayData(info->src_addr, data, len);
  }
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
  const uint32_t deadline = MESH_WAKE_MAX_AWAKE_MS;

  bool restored = meshRtcRestore();
  Serial.printf("[wake] rtc restore: %s\n", restored ? "parent hint" : "none (cold/invalid)");

  digitalWrite(SOIL_PWR_PIN, HIGH);
  digitalWrite(DHT_PWR_PIN,  HIGH);
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
    Serial.println("[esp-now] init failed on wake — sleeping without sending");
    goToSleep(ch);
  }
  esp_now_register_send_cb(onDataSent);
  esp_now_register_recv_cb(onDataRecv);
  meshInit(0);  // channel 0 = follow current radio channel

  meshSendBeaconNow(meshMyRank, MESH_SLEEP_INTERVAL_MS);

  while (millis() - warmupStart < SENSOR_WARMUP_MS && millis() < deadline) delay(10);

  SensorPacket pkt;
  readFakeSensors(&pkt);

  digitalWrite(SOIL_PWR_PIN, LOW);
  digitalWrite(DHT_PWR_PIN,  LOW);

  Serial.printf("[sensor] T=%.1f H=%.1f Soil=%.0f%% (FAKE)\n",
                pkt.temperature, pkt.humidity, pkt.soil_moisture);

  meshSetBatteryMv(readFakeBatteryMv());

  bool delivered = sendWithConfirm(&pkt, deadline);
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

  randomSeed(micros() ^ (uint32_t)ESP.getEfuseMac());

  pinMode(SOIL_PWR_PIN, OUTPUT);
  pinMode(DHT_PWR_PIN,  OUTPUT);
  digitalWrite(SOIL_PWR_PIN, LOW);
  digitalWrite(DHT_PWR_PIN,  LOW);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  ensureFakeSeeded();  // needs WiFi.mode() up for WiFi.macAddress()

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

  Serial.printf("[edge] MAC: %s (FAKE SENSOR MODE)\n", WiFi.macAddress().c_str());
  Serial.println("[edge] unrouted — listening for trusted beacons");
}

void loop() {
  uint32_t now = millis();

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
        SensorPacket pkt;
        readFakeSensors(&pkt);

        digitalWrite(SOIL_PWR_PIN, LOW);
        digitalWrite(DHT_PWR_PIN,  LOW);
        lastCycleMs = now;
        phase = PHASE_IDLE;

        meshSetBatteryMv(readFakeBatteryMv());

        Serial.printf("[sensor] T=%.1f H=%.1f Soil=%.0f%% (FAKE)\n",
                      pkt.temperature, pkt.humidity, pkt.soil_moisture);
        meshSendReading(&pkt);
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
