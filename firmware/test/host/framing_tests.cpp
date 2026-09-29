// firmware/test/host/framing_tests.cpp -- host tests for mesh_uart.h (bridge <-> Pi binary framing).
// The byte vectors are shared with pi/tests/test_uart_framing.py, so both ends agree bit-for-bit.
#include <cstdio>
#include <cstring>
#include "../../libraries/GreenhouseMesh/mesh_uart.h"

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)

static const uint8_t ACK_VEC[] = {   // encode_ack('AABBCCDDEEFF', 0x1234, ok, ttl 7)
  0xa5, 0x5a, 0x81, 0x0a, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff, 0x34, 0x12, 0x01, 0x07, 0x98, 0x41};
static const uint8_t JSON_VEC[] = {  // encode(JSON, '{"type":"hello"}')
  0xa5, 0x5a, 0x02, 0x10, '{', '"', 't', 'y', 'p', 'e', '"', ':', '"', 'h', 'e', 'l', 'l', 'o', '"', '}',
  0x52, 0xe2};

static int feedAll(MeshUartDecoder* d, const uint8_t* p, size_t n, MeshUartEvent* last) {
  int events = 0;
  for (size_t i = 0; i < n; i++) {
    MeshUartEvent e = meshUartFeed(d, p[i]);
    if (e != MESH_UART_NONE) { events++; *last = e; }
  }
  return events;
}

int main() {
  CHECK(meshUartCrc(0xFFFF, (const uint8_t*)"123456789", 9) == 0x29B1);   // CRC-16/CCITT-FALSE check value

  // encode matches the Python vectors
  uint8_t out[300];
  uint8_t ackPayload[MESH_UART_ACK_LEN] = {0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff, 0x34, 0x12, 0x01, 0x07};
  CHECK(meshUartEncode(MESH_UART_T_ACK, ackPayload, sizeof(ackPayload), out) == sizeof(ACK_VEC));
  CHECK(memcmp(out, ACK_VEC, sizeof(ACK_VEC)) == 0);
  uint8_t frame[61];
  for (int i = 0; i < 61; i++) frame[i] = (uint8_t)i;
  size_t n = meshUartEncode(MESH_UART_T_FRAME, frame, sizeof(frame), out);
  CHECK(n == 67 && out[65] == 0xff && out[66] == 0x48);
  CHECK(meshUartEncode(MESH_UART_T_JSON, frame, MESH_UART_MAX_PAYLOAD + 1, out) == 0);

  // decode: binary frame
  MeshUartDecoder d; meshUartReset(&d); d.badCrc = 0;
  MeshUartEvent last = MESH_UART_NONE;
  CHECK(feedAll(&d, ACK_VEC, sizeof(ACK_VEC), &last) == 1 && last == MESH_UART_GOT_FRAME);
  CHECK(d.type == MESH_UART_T_ACK && d.len == 10 && memcmp(d.buf, ackPayload, 10) == 0);

  // decode: JSON line (fallback protocol), then a binary frame right after it
  const char* line = "{\"type\":\"netkey\",\"key\":\"00\"}\n";
  CHECK(feedAll(&d, (const uint8_t*)line, strlen(line), &last) == 1 && last == MESH_UART_GOT_LINE);
  CHECK(strcmp((const char*)d.buf, "{\"type\":\"netkey\",\"key\":\"00\"}") == 0);
  CHECK(feedAll(&d, JSON_VEC, sizeof(JSON_VEC), &last) == 1 && last == MESH_UART_GOT_FRAME);
  CHECK(d.type == MESH_UART_T_JSON && d.len == 16 && memcmp(d.buf, "{\"type\":\"hello\"}", 16) == 0);

  // corrupted CRC is rejected and counted; the next good frame still decodes
  uint8_t bad[sizeof(ACK_VEC)];
  memcpy(bad, ACK_VEC, sizeof(bad)); bad[8] ^= 0x01;
  CHECK(feedAll(&d, bad, sizeof(bad), &last) == 0 && d.badCrc == 1);
  CHECK(feedAll(&d, ACK_VEC, sizeof(ACK_VEC), &last) == 1 && last == MESH_UART_GOT_FRAME);

  // noise and a truncated line before a frame: resync on the sync bytes
  const uint8_t noise[] = {0x00, 0x13, 0xA5, 0x00, '{', 'x', 'y'};
  feedAll(&d, noise, sizeof(noise), &last);
  CHECK(feedAll(&d, ACK_VEC, sizeof(ACK_VEC), &last) == 1 && last == MESH_UART_GOT_FRAME);
  CHECK(d.type == MESH_UART_T_ACK);

  if (failures) { printf("%d FAILED\n", failures); return 1; }
  printf("ALL PASS\n");
  return 0;
}
