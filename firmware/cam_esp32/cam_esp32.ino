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
#include <HTTPClient.h>
#include <esp_camera.h>
#include <SD_MMC.h>
#include <FS.h>

// ── WiFi (home router) ────────────────────────────────────────────────────────
#define WIFI_SSID     "TP-Link_14A6"
#define WIFI_PASSWORD "6940604664"

// ── Pi cam_bridge endpoint ────────────────────────────────────────────────
#define PI_HOST "greenhouse.local"
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

// ── /capture: single JPEG frame ────────────────────────────────────────────────
void handleCapture() {
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
  String eventId = server.pathArg(0);
  String path = eventPath(eventId);
  if (path == "" || !SD_MMC.exists(path)) {
    server.send(404, "text/plain", "not found");
    return;
  }
  SD_MMC.remove(path);
  server.send(200, "text/plain", "deleted");
}

// ── Periodic snapshot POST to the Pi (motion-detection intake) ────────────────
void sendSnapshotToPi() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return;

  HTTPClient http;
  String url = String("http://") + PI_HOST + ":" + PI_PORT + "/cam/frame";
  http.begin(url);
  http.addHeader("Content-Type", "image/jpeg");
  int code = http.POST(fb->buf, fb->len);

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
