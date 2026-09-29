#pragma once
// ── GreenhouseMesh: binary UART framing between the bridge ESP32 and the Pi ───
// Replaces the hex-JSON line for the high-rate traffic (sensor frames up, acks
// down). meshsim: a 61-byte frame is a 150-byte hex line = 13.0 ms at 115200,
// but 67 bytes = 0.73 ms at 921600 — the gateway stops being the bottleneck
// and the rank-1 relays' flush window shrinks (docs/simulator/MODEL.md §7).
//
//   A5 5A | type (1) | len (1) | payload (len) | CRC-16/CCITT-FALSE (2, LE)
//   CRC over type, len, payload; poly 0x1021, init 0xFFFF ("123456789" → 0x29B1)
//
// Types:  bridge → Pi   0x01 FRAME  sealed mesh packet, verbatim (61 B)
//                       0x02 JSON   low-rate control/telemetry JSON text
//         Pi → bridge   0x81 ACK    mac[6] seq(u16 LE) ok(u8) ttl(u8)
//                       0x02 JSON   same command JSON the line protocol uses
// A JSON line starting with '{' and ending in '\n' is still accepted in both
// directions, so the fallback (hex JSON at 115200) needs no negotiation: the
// Pi detects the framing from what it receives. Pure C, host-testable
// (firmware/test/host/framing_tests.cpp; pi/shared/uart_framing.py mirrors it).
#include <stdint.h>
#include <stddef.h>

#define MESH_UART_SYNC0      0xA5
#define MESH_UART_SYNC1      0x5A
#define MESH_UART_T_FRAME    0x01
#define MESH_UART_T_JSON     0x02
#define MESH_UART_T_ACK      0x81
#define MESH_UART_ACK_LEN    10
#define MESH_UART_MAX_PAYLOAD 250
#define MESH_UART_OVERHEAD   6     // sync 2 + type 1 + len 1 + crc 2

static inline uint16_t meshUartCrc(uint16_t crc, const uint8_t* p, size_t n) {
  while (n--) {
    crc ^= (uint16_t)(*p++) << 8;
    for (int b = 0; b < 8; b++) crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}

// Writes one frame into out (size >= len + MESH_UART_OVERHEAD); returns its length, 0 if too long.
static inline size_t meshUartEncode(uint8_t type, const uint8_t* payload, size_t len, uint8_t* out) {
  if (len > MESH_UART_MAX_PAYLOAD) return 0;
  out[0] = MESH_UART_SYNC0; out[1] = MESH_UART_SYNC1; out[2] = type; out[3] = (uint8_t)len;
  for (size_t i = 0; i < len; i++) out[4 + i] = payload[i];
  uint16_t crc = meshUartCrc(0xFFFF, out + 2, len + 2);
  out[4 + len] = (uint8_t)(crc & 0xFF);
  out[5 + len] = (uint8_t)(crc >> 8);
  return len + MESH_UART_OVERHEAD;
}

// Byte-at-a-time decoder that also passes JSON lines through.
typedef enum { MESH_UART_NONE = 0, MESH_UART_GOT_FRAME = 1, MESH_UART_GOT_LINE = 2 } MeshUartEvent;

typedef struct {
  uint8_t  st;                                 // 0 idle, 1 sync1, 2 type, 3 len, 4 payload, 5 crc lo, 6 crc hi, 7 line
  uint8_t  type, len, crcLo;
  uint16_t pos;
  uint8_t  buf[MESH_UART_MAX_PAYLOAD + 1];     // payload, or the JSON line (NUL-terminated)
  uint16_t badCrc;                             // diagnostics
} MeshUartDecoder;

static inline void meshUartReset(MeshUartDecoder* d) { d->st = 0; d->pos = 0; }

// Feed one byte. On MESH_UART_GOT_FRAME: d->type, d->len, d->buf hold the frame.
// On MESH_UART_GOT_LINE: d->buf holds a NUL-terminated line (without '\n').
static inline MeshUartEvent meshUartFeed(MeshUartDecoder* d, uint8_t c) {
  switch (d->st) {
    case 0:
      if (c == MESH_UART_SYNC0) { d->st = 1; return MESH_UART_NONE; }
      if (c == '{') { d->st = 7; d->pos = 0; d->buf[d->pos++] = c; }
      return MESH_UART_NONE;                   // noise between messages
    case 1:
      if (c == MESH_UART_SYNC1) { d->st = 2; return MESH_UART_NONE; }
      d->st = 0;
      return meshUartFeed(d, c);               // re-examine: may be a new start
    case 2: d->type = c; d->st = 3; return MESH_UART_NONE;
    case 3:
      d->len = c; d->pos = 0;
      if (c > MESH_UART_MAX_PAYLOAD) { d->st = 0; return MESH_UART_NONE; }
      d->st = c ? 4 : 5;
      return MESH_UART_NONE;
    case 4:
      d->buf[d->pos++] = c;
      if (d->pos >= d->len) d->st = 5;
      return MESH_UART_NONE;
    case 5: d->crcLo = c; d->st = 6; return MESH_UART_NONE;
    case 6: {
      d->st = 0;
      uint8_t hdr[2] = { d->type, d->len };
      uint16_t crc = meshUartCrc(meshUartCrc(0xFFFF, hdr, 2), d->buf, d->len);
      if (crc == (uint16_t)(d->crcLo | ((uint16_t)c << 8))) return MESH_UART_GOT_FRAME;
      d->badCrc++;
      return MESH_UART_NONE;
    }
    case 7:
      if (c & 0x80) { d->st = 0; return meshUartFeed(d, c); }   // JSON is ASCII: not a line
      if (c == '\n' || c == '\r') {
        d->buf[d->pos] = 0; d->st = 0;
        return MESH_UART_GOT_LINE;
      }
      if (d->pos < MESH_UART_MAX_PAYLOAD) d->buf[d->pos++] = c;
      else d->st = 0;                          // overlong line: discard
      return MESH_UART_NONE;
  }
  d->st = 0;
  return MESH_UART_NONE;
}
