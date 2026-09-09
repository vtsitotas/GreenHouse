// firmware/libraries/GreenhouseMesh/mesh_store.h
#pragma once
// ── Persistent per-node identity ──────────────────────────────────────────────
// AppKey  — this node's own key, written once at provisioning-tool time.
// NetKey  — learned over the air during enrolment; its presence IS the
//           "provisioned" flag.
// boot    — monotonic cold-boot counter feeding the AES-GCM nonce. seq lives in
//           RTC memory and survives sleep but NOT power loss; without this
//           counter a power cut would reuse a nonce, which in GCM leaks the
//           authentication subkey and lets an attacker forge readings. Bumped on
//           cold boot ONLY, so a node on the 15-minute cycle writes flash only
//           when it actually loses power.

#include <Preferences.h>
#include <stdint.h>
#include <string.h>

static Preferences meshPrefs;

static void meshStoreBegin() { meshPrefs.begin("ghmesh", false); }

static bool meshStoreAppKey(uint8_t* out16) {
  return meshPrefs.getBytes("appkey", out16, 16) == 16;
}

static void meshStoreSetAppKey(const uint8_t* key16) {
  meshPrefs.putBytes("appkey", key16, 16);
}

static bool meshStoreNetKey(uint8_t* out16) {
  return meshPrefs.getBytes("netkey", out16, 16) == 16;
}

static void meshStoreSetNetKey(const uint8_t* key16) {
  meshPrefs.putBytes("netkey", key16, 16);
}

static bool meshStoreIsProvisioned() {
  uint8_t tmp[16];
  return meshStoreNetKey(tmp);
}

static bool meshStoreSleepy()          { return meshPrefs.getBool("sleepy", false); }
static void meshStoreSetSleepy(bool v) { meshPrefs.putBool("sleepy", v); }
static uint32_t meshStoreBootCount()   { return meshPrefs.getUInt("boot", 0); }

static uint32_t meshStoreBumpBootCount() {
  uint32_t next = meshStoreBootCount() + 1;
  meshPrefs.putUInt("boot", next);
  return next;
}

// Factory reset: forget the network but keep our own identity, so the node can
// simply be re-enrolled from the app without a reflash.
static void meshStoreClearProvisioning() {
  meshPrefs.remove("netkey");
  meshPrefs.remove("sleepy");
}
