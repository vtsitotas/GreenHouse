// firmware/test/host/layout_vectors.cpp
// Prints the firmware's own byte layout so pytest can diff it against the Pi's.
#include <cstdio>
#include "../../libraries/GreenhouseMesh/mesh_packet.h"

static void printHex(const char* label, const uint8_t* buf, int len) {
  printf("%s=", label);
  for (int i = 0; i < len; i++) printf("%02x", buf[i]);
  printf("\n");
}

int main() {
  const uint8_t mac[6]    = { 0x20, 0x6E, 0xF1, 0x6C, 0x9D, 0xB0 };
  const uint8_t parent[6] = { 0x20, 0x6E, 0xF1, 0x6C, 0xBE, 0x80 };

  uint8_t header[MESH_HEADER_LEN];
  meshPackHeader(header, mac, 0x1234, 0x0A0B0C0D, 0x01, 2, 4);
  printHex("header", header, MESH_HEADER_LEN);

  uint8_t body[MESH_BODY_LEN];
  meshPackBody(body, 21.5f, 60.25f, 42.0f, 3700, parent, -67);
  printHex("body", body, MESH_BODY_LEN);

  uint8_t nonce[12];
  meshBuildNonce(nonce, mac, 0x0A0B0C0D, 0x1234);
  printHex("nonce", nonce, 12);

  printf("header_len=%d\n", MESH_HEADER_LEN);
  printf("body_len=%d\n",   MESH_BODY_LEN);
  printf("packet_len=%d\n", MESH_PACKET_LEN);
  printf("aad_len=%d\n",    MESH_AAD_LEN);
  printf("magic=%d\n",      MESH_MAGIC_V2);
  return 0;
}