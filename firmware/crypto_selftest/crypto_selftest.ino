// firmware/crypto_selftest/crypto_selftest.ino
// Prints one sealed packet built from fixed inputs, for pi/tests/test_firmware_vectors.py.
// Not part of any deployment — a bench tool, flashed on demand.
#include "mesh_crypto.h"

void setup() {
  Serial.begin(115200);
  delay(2000);

  uint8_t appKey[16], netKey[16];
  for (int i = 0; i < 16; i++) { appKey[i] = i; netKey[i] = 16 + i; }
  const uint8_t mac[6]    = { 0x20, 0x6E, 0xF1, 0x6C, 0x9D, 0xB0 };
  const uint8_t parent[6] = { 0x20, 0x6E, 0xF1, 0x6C, 0xBE, 0x80 };

  uint8_t body[MESH_BODY_LEN];
  meshPackBody(body, 21.5f, 60.0f, 42.0f, 3700, parent, -67);

  uint8_t packet[MESH_PACKET_LEN];
  if (!meshSeal(packet, appKey, netKey, mac, 7, 3, 0, 1, 4, body)) {
    Serial.println("packet=FAILED");
    return;
  }
  Serial.print("packet=");
  for (int i = 0; i < MESH_PACKET_LEN; i++) Serial.printf("%02x", packet[i]);
  Serial.println();
}

void loop() {}
