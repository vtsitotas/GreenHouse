// firmware/test/host/inflight_test.cpp
// Host-side unit test for mesh_inflight.h. Exit code 0 + "ALL PASS" = green.
#include <cstdio>
#include <cstring>
#include "../../libraries/GreenhouseMesh/mesh_inflight.h"

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)

static uint8_t requeued[MESH_INFLIGHT_MAX][MESH_PACKET_LEN];
static int requeuedCount = 0;
static void captureRequeue(const uint8_t* pkt) {
  memcpy(requeued[requeuedCount++], pkt, MESH_PACKET_LEN);
}

static void makePkt(uint8_t* pkt, uint16_t seq) {
  memset(pkt, 0xAB, MESH_PACKET_LEN);
  pkt[7] = (uint8_t)(seq & 0xFF);          // seq is little-endian at bytes 7..8
  pkt[8] = (uint8_t)(seq >> 8);
}

int main() {
  MeshInFlight f;
  meshInFlightClear(&f);
  uint8_t p[MESH_PACKET_LEN];

  makePkt(p, 0x1234);
  CHECK(meshPacketSeq(p) == 0x1234);

  // Empty table: nothing pending, drain requeues nothing.
  CHECK(meshInFlightPending(&f) == 0);

  // Three frames in flight.
  for (uint16_t s = 10; s < 13; s++) { makePkt(p, s); CHECK(meshInFlightAdd(&f, p)); }
  CHECK(meshInFlightPending(&f) == 3);

  // ACK for an unknown seq changes nothing.
  CHECK(!meshInFlightAnswer(&f, 99, MESH_ACK_OK));
  CHECK(meshInFlightPending(&f) == 3);

  // OK for 10, REJECTED for 11; a second identical ACK is harmless.
  CHECK(meshInFlightAnswer(&f, 10, MESH_ACK_OK));
  CHECK(meshInFlightAnswer(&f, 10, MESH_ACK_OK));
  CHECK(meshInFlightAnswer(&f, 11, MESH_ACK_REJECTED));
  CHECK(meshInFlightPending(&f) == 1);

  // Drain: only the unanswered one (12) is requeued; counts reported; table empty.
  int ok = -1, rej = -1;
  requeuedCount = 0;
  int n = meshInFlightDrain(&f, captureRequeue, &ok, &rej);
  CHECK(n == 1);
  CHECK(requeuedCount == 1);
  CHECK(meshPacketSeq(requeued[0]) == 12);
  CHECK(ok == 1);
  CHECK(rej == 1);
  CHECK(meshInFlightPending(&f) == 0);
  CHECK(f.count == 0);

  // Drain keeps send order and accepts NULL out-params.
  for (uint16_t s = 20; s < 23; s++) { makePkt(p, s); meshInFlightAdd(&f, p); }
  requeuedCount = 0;
  CHECK(meshInFlightDrain(&f, captureRequeue, NULL, NULL) == 3);
  CHECK(meshPacketSeq(requeued[0]) == 20);
  CHECK(meshPacketSeq(requeued[2]) == 22);

  // Full table refuses more and keeps what it has.
  meshInFlightClear(&f);
  for (int i = 0; i < MESH_INFLIGHT_MAX; i++) { makePkt(p, (uint16_t)(100 + i)); CHECK(meshInFlightAdd(&f, p)); }
  makePkt(p, 999);
  CHECK(!meshInFlightAdd(&f, p));
  CHECK(meshInFlightPending(&f) == MESH_INFLIGHT_MAX);

  if (failures == 0) printf("ALL PASS\n");
  return failures == 0 ? 0 : 1;
}
