// firmware/libraries/GreenhouseMesh/mesh_packet.h
#pragma once
// ── v2 mesh packet layout ─────────────────────────────────────────────────────
// Mirrors pi/shared/mesh_packet.py byte for byte; firmware/test/host proves it
// in CI. Deliberately free of Arduino/mbedTLS includes so it compiles on a host.
//
//   [ header 16 ][ nettag 8 ][ ciphertext 21 ][ apptag 16 ] = 61 bytes
//
// ttl is the last header byte and is rewritten at every hop, so both the GCM AAD
// and the nettag CMAC cover only the first 15 bytes.

#include <stdint.h>
#include <string.h>

#define MESH_MAGIC_V2     0x48
#define MESH_HEADER_LEN   16
#define MESH_NETTAG_LEN   8
#define MESH_BODY_LEN     21
#define MESH_APPTAG_LEN   16
#define MESH_PACKET_LEN   (MESH_HEADER_LEN + MESH_NETTAG_LEN + \
                           MESH_BODY_LEN + MESH_APPTAG_LEN)
#define MESH_AAD_LEN      (MESH_HEADER_LEN - 1)

// Explicit little-endian writers: never memcpy a struct onto the wire. Struct
// padding and host endianness are exactly how a layout silently diverges.
static inline void meshPutU16(uint8_t* p, uint16_t v) {
  p[0] = (uint8_t)(v & 0xFF); p[1] = (uint8_t)((v >> 8) & 0xFF);
}

static inline void meshPutU32(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)(v & 0xFF);         p[1] = (uint8_t)((v >> 8) & 0xFF);
  p[2] = (uint8_t)((v >> 16) & 0xFF); p[3] = (uint8_t)((v >> 24) & 0xFF);
}

static inline void meshPutF32(uint8_t* p, float v) {
  uint32_t bits; memcpy(&bits, &v, 4); meshPutU32(p, bits);
}

static inline void meshPackHeader(uint8_t* out, const uint8_t* originMac,
                                  uint16_t seq, uint32_t bootCount,
                                  uint8_t flags, uint8_t rank, uint8_t ttl) {
  out[0] = MESH_MAGIC_V2;
  memcpy(out + 1, originMac, 6);
  meshPutU16(out + 7, seq);
  meshPutU32(out + 9, bootCount);
  out[13] = flags;
  out[14] = rank;
  out[15] = ttl;
}

static inline void meshPackBody(uint8_t* out, float t, float h, float s,
                                uint16_t batteryMv, const uint8_t* parentMac,
                                int8_t rssi) {
  meshPutF32(out + 0, t);
  meshPutF32(out + 4, h);
  meshPutF32(out + 8, s);
  meshPutU16(out + 12, batteryMv);
  memcpy(out + 14, parentMac, 6);
  out[20] = (uint8_t)rssi;
}

static inline void meshBuildNonce(uint8_t* out12, const uint8_t* originMac,
                                  uint32_t bootCount, uint16_t seq) {
  memcpy(out12, originMac, 6);
  meshPutU32(out12 + 6, bootCount);
  meshPutU16(out12 + 10, seq);
}