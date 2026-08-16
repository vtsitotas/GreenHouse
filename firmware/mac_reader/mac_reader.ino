// ── MAC Address Reader ───────────────────────────────────────────────────────
// Standalone diagnostic: no ESP-NOW/mesh, no sensors, no GreenhouseMesh
// library dependency at all. Flash to any ESP32-C3 board and open Serial
// Monitor at 115200 — it prints the board's MAC address every 2s so you can
// identify which physical board is which before filling in TRUSTED_NODES[]
// in firmware/libraries/GreenhouseMesh/mesh_config.h. Reflash the real
// firmware (bridge_esp32.ino / edge_node_esp32_c3.ino / fake_edge_node) once
// you've noted the MAC down — this sketch does nothing else.

#include <Arduino.h>
#include <WiFi.h>

void setup() {
  Serial.begin(115200);
  delay(1500);  // USB-CDC settle on C3

  WiFi.mode(WIFI_STA);

  Serial.println();
  Serial.println("--- MAC ADDRESS READER ---");
}

void loop() {
  Serial.printf("MAC address: %s\n", WiFi.macAddress().c_str());
  delay(2000);
}
