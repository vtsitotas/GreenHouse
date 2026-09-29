#pragma once
// ── SHT4x (SHT40) — the pure part of the driver, host-testable ────────────────
// Sensirion SHT4x datasheet v6.4: I²C address 0x44; command 0xFD = measure T+RH
// with high repeatability, result after ≤ 8.3 ms as 6 bytes
//   T_msb T_lsb CRC  RH_msb RH_lsb CRC
// CRC-8: poly 0x31, init 0xFF, no reflection (datasheet example: 0xBEEF → 0x92).
//   T  [°C]  = −45 + 175 · S_T  / 65535
//   RH [%RH] =  −6 + 125 · S_RH / 65535, clamped to 0..100
// The Wire transaction lives in the sketch; this file has no Arduino dependency
// (firmware/test/host/sht4x_tests.cpp).
#include <stdint.h>

#define SHT4X_ADDR          0x44
#define SHT4X_CMD_MEASURE_HP 0xFD
#define SHT4X_MEASURE_MS    9      // ≤ 8.3 ms high-repeatability conversion, rounded up
#define SHT4X_POWERUP_MS    1      // ≤ 1 ms from VDD to ready

static inline uint8_t sht4xCrc8(const uint8_t* p, int n) {
  uint8_t crc = 0xFF;
  while (n--) {
    crc ^= *p++;
    for (int b = 0; b < 8; b++) crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
  }
  return crc;
}

// Parse the 6-byte answer. Returns false (and leaves t/rh untouched) on a CRC error.
static inline bool sht4xParse(const uint8_t b[6], float* t, float* rh) {
  if (sht4xCrc8(b, 2) != b[2] || sht4xCrc8(b + 3, 2) != b[5]) return false;
  uint16_t st = (uint16_t)((b[0] << 8) | b[1]);
  uint16_t srh = (uint16_t)((b[3] << 8) | b[4]);
  float h = -6.0f + 125.0f * (float)srh / 65535.0f;
  if (h < 0.0f) h = 0.0f;
  if (h > 100.0f) h = 100.0f;
  *t = -45.0f + 175.0f * (float)st / 65535.0f;
  *rh = h;
  return true;
}
