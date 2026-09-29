// firmware/test/host/sht4x_tests.cpp -- host tests for the SHT4x parse/CRC (edge_node_esp32_c3/sht4x.h)
#include <cmath>
#include <cstdio>
#include "../../edge_node_esp32_c3/sht4x.h"

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)

static void frame(uint16_t st, uint16_t srh, uint8_t out[6]) {
  out[0] = st >> 8; out[1] = st & 0xFF; out[2] = sht4xCrc8(out, 2);
  out[3] = srh >> 8; out[4] = srh & 0xFF; out[5] = sht4xCrc8(out + 3, 2);
}

int main() {
  const uint8_t beef[2] = {0xBE, 0xEF};
  CHECK(sht4xCrc8(beef, 2) == 0x92);                       // datasheet example

  uint8_t b[6];
  float t = 0, rh = 0;
  frame(0x6666, 0x8000, b);                                // 0x6666 = 0.4·65535 → −45 + 70 = 25 °C
  CHECK(sht4xParse(b, &t, &rh));
  CHECK(std::fabs(t - 25.0f) < 0.01f);
  CHECK(std::fabs(rh - (-6.0f + 125.0f * 32768.0f / 65535.0f)) < 0.01f);   // ≈ 56.5 %RH

  frame(0x0000, 0x0000, b);                                // −45 °C, RH clamped at 0
  CHECK(sht4xParse(b, &t, &rh) && std::fabs(t + 45.0f) < 0.01f && rh == 0.0f);
  frame(0xFFFF, 0xFFFF, b);                                // 130 °C, RH clamped at 100
  CHECK(sht4xParse(b, &t, &rh) && std::fabs(t - 130.0f) < 0.01f && rh == 100.0f);

  frame(0x6666, 0x8000, b);
  b[4] ^= 0x01;                                            // corrupt RH
  t = rh = -1.0f;
  CHECK(!sht4xParse(b, &t, &rh) && t == -1.0f && rh == -1.0f);

  if (failures) { printf("%d FAILED\n", failures); return 1; }
  printf("ALL PASS\n");
  return 0;
}
