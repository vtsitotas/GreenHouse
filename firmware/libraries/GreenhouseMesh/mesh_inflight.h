// firmware/libraries/GreenhouseMesh/mesh_inflight.h
#pragma once
// ── Frames awaiting the Pi's app-level verdict ─────────────────────────────────
// Every sealed frame a sleepy node unicasts during one wake (buffer flush +
// the new reading) is recorded here. meshHandleAck() marks entries answered;
// at the end of the wake anything still unanswered goes back into the
// RTC-persisted buffer and is resent next wake (the Pi re-acks an exact
// duplicate without republishing it). REJECTED entries are dropped: resending
// cannot fix a wrong AppKey. Deliberately free of Arduino/ESP-NOW includes so
// firmware/test/host can unit-test it with plain g++.

#include <stdint.h>
#include <string.h>
#include "mesh_packet.h"
#include "mesh_config.h"

#define MESH_ACK_OK        1      // 0 is deliberately unused: an all-zero or
#define MESH_ACK_REJECTED  2      // uninitialized status is never mistaken
                                  // for a valid outcome

#define MESH_INFLIGHT_PENDING 0
#define MESH_INFLIGHT_MAX     (MESH_DATA_BUFFER_SIZE + 1)  // full flush + the new reading

typedef struct {
  uint16_t seq;
  uint8_t  status;                  // MESH_INFLIGHT_PENDING, MESH_ACK_OK or MESH_ACK_REJECTED
  uint8_t  pkt[MESH_PACKET_LEN];    // exact bytes sent, so a resend is byte-identical
} MeshInFlightEntry;

typedef struct {
  MeshInFlightEntry e[MESH_INFLIGHT_MAX];
  int count;
} MeshInFlight;

typedef void (*MeshRequeueFn)(const uint8_t* pkt);

// seq lives little-endian at header bytes 7..8 (mesh_packet.h meshPackHeader).
static inline uint16_t meshPacketSeq(const uint8_t* pkt) {
  return (uint16_t)(pkt[7] | ((uint16_t)pkt[8] << 8));
}

static inline void meshInFlightClear(MeshInFlight* f) { f->count = 0; }

// false = table full; the caller must keep the frame in its own buffer.
static inline bool meshInFlightAdd(MeshInFlight* f, const uint8_t* pkt) {
  if (f->count >= MESH_INFLIGHT_MAX) return false;
  MeshInFlightEntry* x = &f->e[f->count++];
  x->seq    = meshPacketSeq(pkt);
  x->status = MESH_INFLIGHT_PENDING;
  memcpy(x->pkt, pkt, MESH_PACKET_LEN);
  return true;
}

// Idempotent: the same ack can arrive via two relays. Returns whether seq
// matched a frame in flight (a late ack from an earlier wake matches nothing).
static inline bool meshInFlightAnswer(MeshInFlight* f, uint16_t seq, uint8_t status) {
  for (int i = 0; i < f->count; i++) {
    if (f->e[i].seq == seq) { f->e[i].status = status; return true; }
  }
  return false;
}

static inline int meshInFlightPending(const MeshInFlight* f) {
  int n = 0;
  for (int i = 0; i < f->count; i++) if (f->e[i].status == MESH_INFLIGHT_PENDING) n++;
  return n;
}

// Hands every unanswered frame to requeue() in send order, reports how many
// were accepted / rejected, empties the table. Returns the number requeued.
static inline int meshInFlightDrain(MeshInFlight* f, MeshRequeueFn requeue,
                                    int* okOut, int* rejectedOut) {
  int requeued = 0, ok = 0, rejected = 0;
  for (int i = 0; i < f->count; i++) {
    if (f->e[i].status == MESH_ACK_OK)            ok++;
    else if (f->e[i].status == MESH_ACK_REJECTED) rejected++;
    else { requeue(f->e[i].pkt); requeued++; }
  }
  f->count = 0;
  if (okOut)       *okOut = ok;
  if (rejectedOut) *rejectedOut = rejected;
  return requeued;
}
