#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Compile-verify the camera firmware's crypto helpers against the real ESP32
# mbedtls, using the toolchain the Arduino IDE already installed.
# ═══════════════════════════════════════════════════════════════════════════
# Nothing in firmware/ can be flashed or run from a dev machine, but the
# security-critical helpers in cam_esp32.ino (request signing + AES-GCM frame
# encryption) CAN be compiled against the actual headers with the actual target
# compiler. That catches the failure mode this project has already hit once —
# a wrong/missing mbedtls include or a mis-typed API call — without needing the
# board.
#
#     bash firmware/verify_crypto.sh
#
# What this does NOT prove: that the full sketch links, fits, or behaves
# correctly on real hardware. Those still need a bench run. What it DOES prove:
# every mbedtls call in the crypto path has the right signature and headers.
#
# Compiled as C++ (not C) because that is what Arduino does with a .ino —
# the sketch uses nullptr and other C++-only spellings.
#
# The matching *logic* check (that the firmware's output is what the Pi
# expects) lives in pi/tests/test_cam_bridge.py, which reimplements the same
# derivations and asserts cam_bridge accepts them.
# ═══════════════════════════════════════════════════════════════════════════
set -e

ARDUINO15="${ARDUINO15:-$HOME/AppData/Local/Arduino15}"
[ -d "$ARDUINO15" ] || ARDUINO15="$HOME/.arduino15"
PKG="$ARDUINO15/packages/esp32/tools"

GCC=$(find "$PKG" -name "xtensa-esp32-elf-g++*" -type f 2>/dev/null | head -1)
LIBS=$(find "$PKG" -maxdepth 2 -type d -name "esp32-libs" 2>/dev/null | head -1)
LIBS=$(find "$LIBS" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | head -1)

if [ -z "$GCC" ] || [ -z "$LIBS" ]; then
  echo "SKIP: ESP32 toolchain not found under $PKG"
  echo "      (install the esp32 board package in the Arduino IDE first)"
  exit 0
fi

echo "==> toolchain: $GCC"
echo "==> esp32 libs: $LIBS"

SRC=$(dirname "$0")/cam_esp32/cam_esp32.ino
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Extract the crypto helpers verbatim from the sketch, so this can never drift
# from what actually ships: everything between the signing banner and the
# snapshot function.
{
  echo '#include <stdio.h>'
  echo '#include <string.h>'
  echo '#include <stdlib.h>'
  echo '#include <stdint.h>'
  echo '#include <mbedtls/md.h>'
  echo '#include <mbedtls/gcm.h>'
  echo 'static const char *CAM_TOKEN = "compile-check-token";'
  echo 'static uint32_t esp_random(void) { return 4; }  /* stub: not under test */'
  sed -n '/^static void toHex/,/^\/\/ ── Periodic snapshot POST/p' "$SRC" \
    | sed '$d'
  echo 'int main(void){ uint8_t k[32]; frameKey(k); size_t n; uint8_t*b=encryptFrame((const uint8_t*)"x",1,&n); free(b); return 0; }'
} > "$WORK/crypto.cpp"

echo "==> extracted $(wc -l < "$WORK/crypto.cpp") lines of crypto code"

"$GCC" -c "$WORK/crypto.cpp" -o "$WORK/crypto.o" \
  -I"$LIBS/qio_qspi/include" \
  -I"$LIBS/include/mbedtls/mbedtls/include" \
  -I"$LIBS/include/mbedtls/port/include" \
  -I"$LIBS/include/mbedtls/mbedtls/library" \
  -I"$LIBS/include/config" \
  -I"$LIBS/include/newlib/platform_include" \
  -I"$LIBS/include/esp_common/include" \
  -I"$LIBS/include/esp_system/include" \
  -I"$LIBS/include/esp_hw_support/include" \
  -I"$LIBS/include/soc/include" \
  -I"$LIBS/include/hal/include" \
  -I"$LIBS/include/freertos/config/include" \
  -Wall -Wno-unused-function

echo ""
echo "PASS: firmware crypto compiles against real ESP32 mbedtls headers."
echo "      (Runtime behaviour on the board still needs a bench run.)"
