#include <Arduino.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <WiFi.h>
#include "mesh_config.h"
#include "mesh_node.h"

// ── UART link to the Pi ────────────────────────────────────────────────────────
// HardwareSerial::begin() takes (baud, config, RX_PIN, TX_PIN) — RX BEFORE TX.
// Wiring (both sides 3.3V logic, no level shifter):
//   ESP32 GPIO4 (TX) ──────────► Pi physical pin 10 = GPIO15 (RXD)
//   ESP32 GPIO5 (RX) ◄────────── Pi physical pin  8 = GPIO14 (TXD)
#define UART_RX_PIN  5
#define UART_TX_PIN  4
#define UART_BAUD    115200

// ── Bridge key store (RAM only, never NVS) ────────────────────────────────────
// NetKey: received from the Pi on every serial connect; never written to flash.
// A bridge stolen while powered off yields nothing useful.
// The bridge is DELIBERATELY KEYLESS on disk — it cannot decrypt sensor data,
// only forward sealed frames and relay provisioning blobs.
static uint8_t bridgeNetKey[16];
static bool    bridgeHasNetKey = false;

// ── UART command parser ────────────────────────────────────────────────────────
static char  uartLine[256];
static int   uartLineLen = 0;

char bridgeMac[13];

void uartPrintf(const char* fmt, ...) {
  char buf[256];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  Serial1.println(buf);
  Serial.printf("  → %s\n", buf);  // USB debug echo
}

void sendHeartbeat() {
  char json[48];
  snprintf(json, sizeof(json), "{\"type\":\"heartbeat\",\"mac\":\"%s\"}", bridgeMac);
  uartPrintf("%s", json);
}

// ── Command parser ─────────────────────────────────────────────────────────────
static bool hexToBytes(const char* hex, uint8_t* out, int outLen) {
  for (int i = 0; i < outLen; i++) {
    unsigned v;
    if (sscanf(hex + i * 2, "%2x", &v) != 1) return false;
    out[i] = (uint8_t)v;
  }
  return true;
}

static void handleUartLine(const char* line) {
  // {"type":"netkey","key":"<32 hex>"}
  const char* key = strstr(line, "\"netkey\"");
  if (key) {
    const char* k = strstr(line, "\"key\":\"");
    if (k && hexToBytes(k + 7, bridgeNetKey, 16)) {
      bridgeHasNetKey = true;
      // Also push the key into mesh_node so beacon-sending works
      memcpy(meshNetKey, bridgeNetKey, 16);
      meshKeysLoaded = true;
      Serial.println("[bridge] netkey installed (RAM only)");
    }
    return;
  }
  // {"type":"provision","mac":"<12 hex>","blob":"<66 hex>"}
  if (strstr(line, "\"provision\"")) {
    const char* m = strstr(line, "\"mac\":\"");
    const char* b = strstr(line, "\"blob\":\"");
    uint8_t mac[6], blob[MESH_PROVISION_LEN];
    if (!m || !b || !hexToBytes(m + 7, mac, 6) ||
        !hexToBytes(b + 8, blob, MESH_PROVISION_LEN)) return;
    // Transient peer: added only to transmit, removed immediately. This is the
    // only time the bridge registers anything but broadcast.
    esp_now_peer_info_t p = {};
    memcpy(p.peer_addr, mac, 6);
    p.channel = MESH_FIXED_CHANNEL;
    p.encrypt = false;
    esp_now_add_peer(&p);
    esp_now_send(mac, blob, MESH_PROVISION_LEN);
    esp_now_del_peer(mac);
    Serial.println("[bridge] provision blob transmitted");
  }
}

static void pumpUart() {
  while (Serial1.available()) {
    char c = (char)Serial1.read();
    if (c == '\n') { uartLine[uartLineLen] = 0; handleUartLine(uartLine); uartLineLen = 0; }
    else if (uartLineLen < (int)sizeof(uartLine) - 1) uartLine[uartLineLen++] = c;
    else uartLineLen = 0;   // overlong line, discard
  }
}

// ── ESP-NOW receive callback ──────────────────────────────────────────────────
void onDataRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  // Handle join beacons from unenrolled sensors
  if (len == (int)sizeof(MeshJoinBeacon) && data[1] == MESH_JOIN_MARKER) {
    char mac[13]; meshFormatMac(data + 2, mac);
    uartPrintf("{\"type\":\"unenrolled\",\"mac\":\"%s\"}", mac);
    return;
  }

  // Sealed sensor data
  if (len != MESH_PACKET_LEN) { Serial.printf("[esp-now] bad size %d\n", len); return; }
  if (data[0] != MESH_MAGIC_V2) return;

  // Cheap nettag check — drop garbage without involving the Pi
  if (!bridgeHasNetKey) { Serial.println("[esp-now] no netkey yet, frame dropped"); return; }
  if (!meshVerifyNettag(data, bridgeNetKey)) {
    Serial.println("[esp-now] nettag failed, frame dropped");
    return;
  }

  // De-dup by (origin_mac, seq)
  const uint8_t* originMac = data + 1;
  uint16_t seq = (uint16_t)(data[7] | ((uint16_t)data[8] << 8));
  if (meshDedupSeen(originMac, seq)) {
    Serial.println("[esp-now] duplicate, dropped");
    return;
  }

  // Forward the sealed frame verbatim — the Pi decrypts and publishes
  char hex[MESH_PACKET_LEN * 2 + 1];
  for (int i = 0; i < MESH_PACKET_LEN; i++) sprintf(hex + i * 2, "%02x", data[i]);
  uartPrintf("{\"type\":\"frame\",\"data\":\"%s\"}", hex);
}

// ── liveness tracking ─────────────────────────────────────────────────────────
uint32_t lastBeaconMs       = 0;

void setup() {
  Serial.begin(115200);
  delay(1500);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  {
    uint8_t ownMac[6];
    WiFi.macAddress(ownMac);
    meshFormatMac(ownMac, bridgeMac);
  }
  Serial.printf("\n[bridge] MAC: %s\n", bridgeMac);

  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(MESH_FIXED_CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);
  Serial.printf("[wifi] fixed mesh channel: ch%d\n", MESH_FIXED_CHANNEL);

  Serial1.begin(UART_BAUD, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);
  Serial.printf("[uart] Serial1 up: rx=%d tx=%d baud=%d\n", UART_RX_PIN, UART_TX_PIN, UART_BAUD);

  if (esp_now_init() != ESP_OK) {
    Serial.println("[esp-now] init failed");
    return;
  }
  esp_now_register_recv_cb(onDataRecv);
  meshInit(MESH_FIXED_CHANNEL);

  // Own status at boot (Pi's serial_bridge.py publishes retained)
  uartPrintf("{\"type\":\"mesh\",\"mac\":\"%s\",\"parent\":null,\"rank\":0,"
             "\"rssi\":null,\"sleepy\":false,\"battery_mv\":null,\"zone\":null}",
             bridgeMac);
  uartPrintf("{\"type\":\"status\",\"mac\":\"%s\",\"status\":\"online\"}", bridgeMac);

  Serial.println("[bridge] ready — waiting for netkey from Pi before beaconing");
}

void loop() {
  pumpUart();

  uint32_t now = millis();

  // Rank-0 anchor beacon + heartbeat, only once the Pi has sent us the NetKey.
  // An unsigned beacon (bridgeHasNetKey == false) would be rejected by every
  // provisioned node anyway.
  if (bridgeHasNetKey &&
      now - lastBeaconMs >= MESH_BRIDGE_BEACON_INTERVAL_MS) {
    lastBeaconMs = now;
    meshSendBeaconNow(0, MESH_BRIDGE_BEACON_INTERVAL_MS);
    sendHeartbeat();
  }
}
