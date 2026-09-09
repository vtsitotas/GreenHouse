// firmware/libraries/GreenhouseMesh/mesh_crypto.h
#pragma once
// AES-GCM (body, AppKey) + AES-CMAC (header, NetKey) over mbedTLS, which ships
// with the ESP32 Arduino core and uses the C3's hardware AES.
#include <mbedtls/cipher.h>
#include <mbedtls/gcm.h>
#include <string.h>

#include "mesh_packet.h"

#define MESH_PROVISION_AAD     "greenhouse-provision-v1"
#define MESH_PROVISION_AAD_LEN 23
#define MESH_PROVISION_LEN     33   // 16-byte NetKey + 1 flags byte + 16-byte tag

static bool meshCmacTruncated(const uint8_t* key, const uint8_t* in, size_t len,
                              uint8_t* out8) {
  const mbedtls_cipher_info_t* info =
      mbedtls_cipher_info_from_type(MBEDTLS_CIPHER_AES_128_ECB);
  if (!info) return false;
  uint8_t full[16];
  if (mbedtls_cipher_cmac(info, key, 128, in, len, full) != 0) return false;
  memcpy(out8, full, MESH_NETTAG_LEN);
  return true;
}

static bool meshSeal(uint8_t* packet, const uint8_t* appKey, const uint8_t* netKey,
                     const uint8_t* originMac, uint16_t seq, uint32_t bootCount,
                     uint8_t flags, uint8_t rank, uint8_t ttl, const uint8_t* body) {
  meshPackHeader(packet, originMac, seq, bootCount, flags, rank, ttl);
  if (!meshCmacTruncated(netKey, packet, MESH_AAD_LEN, packet + MESH_HEADER_LEN))
    return false;

  uint8_t nonce[12];
  meshBuildNonce(nonce, originMac, bootCount, seq);

  uint8_t* ct  = packet + MESH_HEADER_LEN + MESH_NETTAG_LEN;
  uint8_t* tag = ct + MESH_BODY_LEN;

  mbedtls_gcm_context gcm;
  mbedtls_gcm_init(&gcm);
  bool ok = mbedtls_gcm_setkey(&gcm, MBEDTLS_CIPHER_ID_AES, appKey, 128) == 0 &&
            mbedtls_gcm_crypt_and_tag(&gcm, MBEDTLS_GCM_ENCRYPT, MESH_BODY_LEN,
                                      nonce, sizeof(nonce),
                                      packet, MESH_AAD_LEN,
                                      body, ct, MESH_APPTAG_LEN, tag) == 0;
  mbedtls_gcm_free(&gcm);
  return ok;
}

static bool meshVerifyNettag(const uint8_t* packet, const uint8_t* netKey) {
  uint8_t expected[MESH_NETTAG_LEN];
  if (!meshCmacTruncated(netKey, packet, MESH_AAD_LEN, expected)) return false;
  uint8_t diff = 0;
  for (int i = 0; i < MESH_NETTAG_LEN; i++)
    diff |= expected[i] ^ packet[MESH_HEADER_LEN + i];
  return diff == 0;
}

static bool meshOpenProvision(const uint8_t* appKey, const uint8_t* mac,
                              const uint8_t* blob, int blobLen,
                              uint8_t* outNetKey16, bool* outSleepy) {
  if (blobLen != MESH_PROVISION_LEN) return false;
  uint8_t nonce[12];
  memcpy(nonce, mac, 6);
  memset(nonce + 6, 0, 6);          // distinct nonce domain from readings

  uint8_t plain[17];
  mbedtls_gcm_context gcm;
  mbedtls_gcm_init(&gcm);
  bool ok = mbedtls_gcm_setkey(&gcm, MBEDTLS_CIPHER_ID_AES, appKey, 128) == 0 &&
            mbedtls_gcm_auth_decrypt(&gcm, 17, nonce, sizeof(nonce),
                                     (const uint8_t*)MESH_PROVISION_AAD,
                                     MESH_PROVISION_AAD_LEN,
                                     blob + 17, MESH_APPTAG_LEN,
                                     blob, plain) == 0;
  mbedtls_gcm_free(&gcm);
  if (!ok) return false;
  memcpy(outNetKey16, plain, 16);
  *outSleepy = plain[16] == 1;
  return true;
}
