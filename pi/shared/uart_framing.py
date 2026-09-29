"""Binary UART framing between the bridge ESP32 and the Pi.

Mirror of firmware/libraries/GreenhouseMesh/mesh_uart.h (same bytes, same CRC):

    A5 5A | type | len | payload | CRC-16/CCITT-FALSE (LE) over type, len, payload

Types: 0x01 FRAME (sealed mesh packet, bridge → Pi), 0x02 JSON (either way),
0x81 ACK (Pi → bridge: mac[6] seq u16 LE ok u8 ttl u8). A JSON text line that
starts with '{' and ends in '\\n' is accepted alongside, so an old hex-JSON
bridge keeps working and the framing never needs negotiating.
"""
import struct

SYNC = b'\xA5\x5A'
T_FRAME = 0x01
T_JSON = 0x02
T_ACK = 0x81
MAX_PAYLOAD = 250
OVERHEAD = 6


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no xorout."""
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def encode(msg_type: int, payload: bytes) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f'payload {len(payload)} B > {MAX_PAYLOAD}')
    body = bytes([msg_type, len(payload)]) + payload
    return SYNC + body + struct.pack('<H', crc16(body))


def encode_ack(mac: str, seq: int, ok: bool, ttl: int) -> bytes:
    payload = bytes.fromhex(mac) + struct.pack('<HBB', seq & 0xFFFF, 1 if ok else 0, ttl & 0xFF)
    return encode(T_ACK, payload)


class Decoder:
    """Incremental decoder. feed() returns a list of events:
    ('frame', type, payload) for a CRC-valid binary frame,
    ('line', bytes) for a JSON text line (without the newline)."""

    def __init__(self):
        self.buf = bytearray()
        self.bad_crc = 0

    def feed(self, data: bytes) -> list:
        self.buf += data
        out = []
        while self.buf:
            b0 = self.buf[0]
            if b0 == SYNC[0]:
                if len(self.buf) < 2:
                    break
                if self.buf[1] != SYNC[1]:
                    del self.buf[0]
                    continue
                if len(self.buf) < 4:
                    break
                n = self.buf[3]
                if n > MAX_PAYLOAD:
                    del self.buf[0]
                    continue
                total = n + OVERHEAD
                if len(self.buf) < total:
                    break
                body = bytes(self.buf[2:4 + n])
                (crc,) = struct.unpack('<H', self.buf[4 + n:total])
                if crc16(body) == crc:
                    out.append(('frame', body[0], body[2:]))
                    del self.buf[:total]
                else:
                    self.bad_crc += 1
                    del self.buf[0]          # resync on the next sync byte
            elif b0 == ord('{'):
                end, bad = -1, -1
                for i, c in enumerate(self.buf):
                    if c in (0x0A, 0x0D):
                        end = i
                        break
                    if c >= 0x80:            # JSON is ASCII: this is not a line
                        bad = i
                        break
                if bad >= 0:
                    del self.buf[:bad]
                    continue
                if end < 0:
                    if len(self.buf) > MAX_PAYLOAD * 4:   # runaway line: drop it
                        self.buf.clear()
                    break
                out.append(('line', bytes(self.buf[:end])))
                del self.buf[:end + 1]
            else:
                del self.buf[0]              # noise / CR-LF between messages
        return out
