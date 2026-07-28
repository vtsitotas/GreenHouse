// firmware/cam_esp32/cam_esp32.ino
// ═══════════════════════════════════════════════════════════════════════════
// Greenhouse IoT — ESP32-CAM (AI-Thinker)
// Serves a LAN MJPEG stream + single-frame capture (for the Pi's live relay
// and the app's direct LAN view), POSTs periodic snapshots to the Pi for
// motion detection, and stores Pi-flagged motion-event frames on its own SD
// card (served/deleted via a tiny HTTP API the Pi calls on demand).
// See docs/superpowers/specs/2026-07-10-esp32-cam-integration-design.md.
// ═══════════════════════════════════════════════════════════════════════════
#include <WiFi.h>
#include <WebServer.h>
#include <uri/UriBraces.h>
#include <HTTPClient.h>
#include <esp_camera.h>
#include <SD_MMC.h>
#include <FS.h>
#include <mbedtls/md.h>
#include <mbedtls/gcm.h>
#include <esp_random.h>
#include <time.h>

// ── WiFi + HTTP API token (home router / secrets.h) ────────────────────────────
// WIFI_SSID, WIFI_PASSWORD, CAM_TOKEN: copy secrets.h.example to secrets.h in
// firmware/libraries/GreenhouseSecrets/ and fill in real values (gitignored
// -- see IMPROVEMENTS.md findings A1/A5).
#include <secrets.h>

// ── Pi cam_bridge endpoint ────────────────────────────────────────────────
// Plain HTTPClient can't resolve .local mDNS names reliably on this core --
// use the Pi's current LAN IP directly. Update this if the Pi's IP changes.
#define PI_HOST "10.19.153.202"
#define PI_PORT 8090

// ── AI-Thinker ESP32-CAM pin map (standard, from Espressif's camera examples) ─
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

WebServer server(80);

const uint32_t SNAPSHOT_INTERVAL_MS = 3000;
uint32_t lastSnapshotMs = 0;

// ── Camera init ────────────────────────────────────────────────────────────────
bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;   // 640x480 — used for /stream and /capture
  config.jpeg_quality = 12;
  config.fb_count = 2;

  return esp_camera_init(&config) == ESP_OK;
}

// Gates every HTTP endpoint on this camera against anyone on the LAN -- see
// IMPROVEMENTS.md finding A5. /stream is now included: the app learns
// CAM_TOKEN through the PIN-gated /pair/confirm response (portal.py's
// _pairing_payload -> ConnectionConfig.camToken) and appends it as ?token=,
// so the last unauthenticated way to watch the greenhouse is closed.
bool checkCamToken() {
  if (server.arg("token") != CAM_TOKEN) {
    server.send(401, "text/plain", "unauthorized");
    return false;
  }
  return true;
}

// ── /capture: single JPEG frame ────────────────────────────────────────────────
void handleCapture() {
  if (!checkCamToken()) return;
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) { server.send(503, "text/plain", "capture failed"); return; }
  server.sendHeader("Content-Type", "image/jpeg");
  server.setContentLength(fb->len);
  server.send(200, "image/jpeg", "");
  server.client().write(fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

// ── /stream: continuous MJPEG (multipart/x-mixed-replace) ─────────────────────
void handleStream() {
  if (!checkCamToken()) return;
  WiFiClient client = server.client();
  String boundary = "greenhousecamframe";
  client.printf(
    "HTTP/1.1 200 OK\r\n"
    "Content-Type: multipart/x-mixed-replace; boundary=%s\r\n\r\n",
    boundary.c_str());
  while (client.connected()) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) break;
    client.printf("--%s\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                  boundary.c_str(), fb->len);
    client.write(fb->buf, fb->len);
    client.print("\r\n");
    esp_camera_fb_return(fb);
    if (!client.connected()) break;
    delay(50);  // ~20fps cap, matches the design's "genuinely smooth LAN view"
  }
}

// ── /event/<id>: serve or delete a saved motion-event JPEG ─────────────────────
String eventPath(const String &eventId) {
  // Sanitize to the same charset cam_bridge.py generates ("evt" + digits) —
  // reject anything else rather than trusting a path segment as a filename.
  for (size_t i = 0; i < eventId.length(); i++) {
    char c = eventId[i];
    if (!isalnum(c)) return "";
  }
  return "/" + eventId + ".jpg";
}

void handleEventGet() {
  if (!checkCamToken()) return;
  String eventId = server.pathArg(0);
  String path = eventPath(eventId);
  if (path == "" || !SD_MMC.exists(path)) {
    server.send(404, "text/plain", "not found");
    return;
  }
  File f = SD_MMC.open(path, FILE_READ);
  server.streamFile(f, "image/jpeg");
  f.close();
}

void handleEventDelete() {
  if (!checkCamToken()) return;
  String eventId = server.pathArg(0);
  String path = eventPath(eventId);
  if (path == "" || !SD_MMC.exists(path)) {
    server.send(404, "text/plain", "not found");
    return;
  }
  SD_MMC.remove(path);
  server.send(200, "text/plain", "deleted");
}

// ── Request signing (keeps CAM_TOKEN off the wire) ────────────────────────────
// The link to the Pi is plain HTTP -- this chip has no practical way to
// validate the Pi's per-unit self-signed cert without provisioning it into
// firmware. Sending CAM_TOKEN as a header therefore exposed it to any passive
// sniffer on the LAN, and that token also authorizes DELETE /event/<id> here,
// i.e. wiping stored motion evidence.
//
// Instead, prove knowledge of the token without transmitting it:
//   signature = HMAC-SHA256(CAM_TOKEN, "<unix_ts>.<sha256_hex(body)>")
// The Pi recomputes it. A sniffer sees only a signature bound to one timestamp
// and one exact body, so it can neither recover the token nor replay the call.
static void toHex(const uint8_t *buf, size_t len, char *out) {
  static const char *hex = "0123456789abcdef";
  for (size_t i = 0; i < len; i++) {
    out[i * 2]     = hex[(buf[i] >> 4) & 0xF];
    out[i * 2 + 1] = hex[buf[i] & 0xF];
  }
  out[len * 2] = '\0';
}

static void sha256Hex(const uint8_t *data, size_t len, char *out65) {
  uint8_t digest[32];
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 0);
  mbedtls_md_starts(&ctx);
  mbedtls_md_update(&ctx, data, len);
  mbedtls_md_finish(&ctx, digest);
  mbedtls_md_free(&ctx);
  toHex(digest, sizeof(digest), out65);
}

static void hmacSha256Hex(const char *key, const char *msg, char *out65) {
  uint8_t digest[32];
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
  mbedtls_md_hmac_starts(&ctx, (const uint8_t *)key, strlen(key));
  mbedtls_md_hmac_update(&ctx, (const uint8_t *)msg, strlen(msg));
  mbedtls_md_hmac_finish(&ctx, digest);
  mbedtls_md_free(&ctx);
  toHex(digest, sizeof(digest), out65);
}

// ── Frame encryption (AES-256-GCM) ────────────────────────────────────────────
// Signing (above) keeps CAM_TOKEN off the wire; this keeps the IMAGES off it
// too. Without it a passive sniffer on the LAN can watch the greenhouse, since
// this link has no TLS. Key is derived from the same shared token both ends
// already have, so no new provisioning:
//     key = HMAC-SHA256(CAM_TOKEN, "greenhouse-cam-frame-key")
// Wire format: nonce(12) || ciphertext || tag(16) -- matches _decrypt_frame()
// in pi/scripts/cam_bridge.py. The ESP32 has a hardware AES engine, so this is
// cheap even on a VGA frame.
#define GCM_NONCE_LEN 12
#define GCM_TAG_LEN   16

static void frameKey(uint8_t out32[32]) {
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
  mbedtls_md_hmac_starts(&ctx, (const uint8_t *)CAM_TOKEN, strlen(CAM_TOKEN));
  mbedtls_md_hmac_update(&ctx, (const uint8_t *)"greenhouse-cam-frame-key", 24);
  mbedtls_md_hmac_finish(&ctx, out32);
  mbedtls_md_free(&ctx);
}

// Returns a heap buffer the caller must free(), or nullptr on failure.
// outLen = GCM_NONCE_LEN + inLen + GCM_TAG_LEN.
static uint8_t *encryptFrame(const uint8_t *in, size_t inLen, size_t *outLen) {
  uint8_t key[32];
  frameKey(key);

  *outLen = GCM_NONCE_LEN + inLen + GCM_TAG_LEN;
  uint8_t *out = (uint8_t *)malloc(*outLen);
  if (!out) return nullptr;

  // Fresh random nonce per frame -- GCM is catastrophically broken by nonce
  // reuse under a fixed key, so this must never become a counter that resets
  // on reboot. esp_random() is hardware-backed once WiFi/BT is up.
  for (size_t i = 0; i < GCM_NONCE_LEN; i += 4) {
    uint32_t r = esp_random();
    memcpy(out + i, &r, (GCM_NONCE_LEN - i >= 4) ? 4 : (GCM_NONCE_LEN - i));
  }

  mbedtls_gcm_context gcm;
  mbedtls_gcm_init(&gcm);
  int rc = mbedtls_gcm_setkey(&gcm, MBEDTLS_CIPHER_ID_AES, key, 256);
  if (rc == 0) {
    rc = mbedtls_gcm_crypt_and_tag(&gcm, MBEDTLS_GCM_ENCRYPT, inLen,
                                   out, GCM_NONCE_LEN,
                                   nullptr, 0,               // no AAD
                                   in, out + GCM_NONCE_LEN,  // ciphertext
                                   GCM_TAG_LEN,
                                   out + GCM_NONCE_LEN + inLen);  // tag
  }
  mbedtls_gcm_free(&gcm);
  if (rc != 0) { free(out); return nullptr; }
  return out;
}

// ── Periodic snapshot POST to the Pi (motion-detection intake) ────────────────
void sendSnapshotToPi() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return;

  HTTPClient http;
  String url = String("http://") + PI_HOST + ":" + PI_PORT + "/cam/frame";
  http.begin(url);
  http.addHeader("Content-Type", "image/jpeg");
  // Authenticates this camera TO the Pi. Without it the Pi's intake endpoint
  // accepted a snapshot from anyone on the LAN, which let an attacker
  // impersonate the camera (the Pi treats the POSTing address as the camera's
  // IP) and get CAM_TOKEN sent to them on the next /capture or /event fetch.
  //
  // Signed rather than sent in the clear -- see the signing helpers above.
  // Needs a real wall-clock time, which arrives via SNTP in setup(); if that
  // hasn't synced yet the Pi rejects the stale timestamp, so retry silently
  // rather than falling back to leaking the token.
  time_t nowSec = time(nullptr);
  if (nowSec < 1700000000) {   // clock not yet synced (well before 2023)
    Serial.println("[cam] clock not synced yet — skipping this snapshot");
    http.end();
    esp_camera_fb_return(fb);
    return;
  }
  // Encrypt first, then sign the ENCRYPTED bytes -- that is what actually
  // travels, and it lets the Pi verify authenticity before spending any work
  // on decryption. Falls back to sending plaintext if allocation fails, which
  // the Pi still accepts unless /etc/greenhouse/require_encrypted_cam is set.
  size_t sendLen = fb->len;
  const uint8_t *sendBuf = fb->buf;
  uint8_t *encrypted = encryptFrame(fb->buf, fb->len, &sendLen);
  if (encrypted) {
    sendBuf = encrypted;
    http.addHeader("X-Cam-Encrypted", "aes-256-gcm");
  } else {
    sendLen = fb->len;
    Serial.println("[cam] WARN: frame encryption failed — sending plaintext");
  }

  char bodyHash[65], signature[65], message[96], tsBuf[24];
  snprintf(tsBuf, sizeof(tsBuf), "%lu", (unsigned long)nowSec);
  sha256Hex(sendBuf, sendLen, bodyHash);
  snprintf(message, sizeof(message), "%s.%s", tsBuf, bodyHash);
  hmacSha256Hex(CAM_TOKEN, message, signature);
  http.addHeader("X-Cam-Timestamp", tsBuf);
  http.addHeader("X-Cam-Signature", signature);
  int code = http.POST((uint8_t *)sendBuf, sendLen);
  if (encrypted) free(encrypted);

  if (code == 200) {
    String resp = http.getString();
    if (resp.startsWith("save:")) {
      String eventId = resp.substring(5);
      String path = eventPath(eventId);
      if (path != "") {
        File f = SD_MMC.open(path, FILE_WRITE);
        if (f) {
          f.write(fb->buf, fb->len);
          f.close();
          Serial.printf("[cam] Saved motion event %s (%u bytes)\n", eventId.c_str(), fb->len);
        } else {
          Serial.printf("[cam] WARN: could not open %s for writing\n", path.c_str());
        }
      }
    }
  } else {
    Serial.printf("[cam] WARN: snapshot POST failed, code=%d\n", code);
  }
  http.end();
  esp_camera_fb_return(fb);
}

void setup() {
  Serial.begin(115200);

  if (!initCamera()) {
    Serial.println("[cam] FATAL: camera init failed");
    return;
  }

  if (!SD_MMC.begin("/sdcard", true)) {  // true = 1-bit mode (AI-Thinker shares
                                          // camera pins with 4-bit SD mode)
    Serial.println("[cam] WARN: SD card init failed — motion events won't persist");
  }

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("[cam] Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[cam] Connected, IP=%s\n", WiFi.localIP().toString().c_str());

  // Wall-clock time for request signing (the timestamp binds each signature to
  // a moment, which is what makes captured requests non-replayable). The Pi
  // tolerates SIGNATURE_WINDOW_SECONDS of skew, so rough accuracy is enough.
  // Non-blocking: sendSnapshotToPi() skips a cycle while the clock is unset
  // rather than sending something the Pi would reject.
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  Serial.println("[cam] SNTP requested (signing needs a real clock)");

  server.on("/capture", HTTP_GET, handleCapture);
  server.on("/stream", HTTP_GET, handleStream);
  server.on(UriBraces("/event/{}"), HTTP_GET, handleEventGet);
  server.on(UriBraces("/event/{}"), HTTP_DELETE, handleEventDelete);
  server.begin();
  Serial.println("[cam] HTTP server started");
}

void loop() {
  server.handleClient();

  uint32_t now = millis();
  if (now - lastSnapshotMs >= SNAPSHOT_INTERVAL_MS) {
    lastSnapshotMs = now;
    sendSnapshotToPi();
  }
}
