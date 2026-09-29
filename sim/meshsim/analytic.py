"""Closed-form results the simulator is checked against.

Every function here mirrors a specific firmware rule or a textbook formula,
so a discrete-event run can be compared with an exact expectation.
"""
import json
import math

# ── PHY: IEEE 802.11-2020 DSSS (Clause 15) + ESP-NOW vendor action frame ──────────
# ESP-IDF "ESP-NOW frame format": MAC header 24, category 1, OUI 3, random 4,
# vendor-specific element header 7 (id 1, len 1, OUI 3, type 1, version 1), FCS 4.
ESPNOW_OVERHEAD = {"mac_header": 24, "category_code": 1, "oui": 3, "random": 4,
                   "vendor_element_header": 7, "fcs": 4}
ESPNOW_OVERHEAD_BYTES = sum(ESPNOW_OVERHEAD.values())          # 43
PLCP_LONG_US = 192        # long preamble 144 + PLCP header 48; mandatory at 1 Mbps
ACK_FRAME_BYTES = 14      # 802.11 ACK control frame (FC 2, dur 2, RA 6, FCS 4)
SIFS_US, SLOT_US = 10, 20
DIFS_US = SIFS_US + 2 * SLOT_US                                   # 50
CW_MIN, CW_MAX = 31, 1023


def airtime_us(payload_bytes, rate_mbps=1.0, plcp_us=PLCP_LONG_US,
               overhead=ESPNOW_OVERHEAD_BYTES):
    """On-air time of one ESP-NOW frame."""
    return plcp_us + 8 * (overhead + payload_bytes) / rate_mbps


def ack_airtime_us(rate_mbps=1.0, plcp_us=PLCP_LONG_US):
    return plcp_us + 8 * ACK_FRAME_BYTES / rate_mbps


def unicast_exchange_us(payload_bytes, backoff_slots=0.0, slot_us=SLOT_US):
    """DIFS + backoff + DATA + SIFS + ACK (one successful attempt)."""
    return (DIFS_US + backoff_slots * slot_us + airtime_us(payload_bytes)
            + SIFS_US + ack_airtime_us())


def broadcast_us(payload_bytes, backoff_slots=0.0, slot_us=SLOT_US):
    """DIFS + backoff + DATA (broadcasts are never ACKed or retried)."""
    return DIFS_US + backoff_slots * slot_us + airtime_us(payload_bytes)


def mean_backoff_slots(cw=CW_MIN):
    """Uniform backoff in [0, CW] slots → mean CW/2."""
    return cw / 2


def csma_first_slot_collision(k, cw=16):
    """P(the earliest of k backoff draws is shared), uniform over cw slots.
    Same formula as docs/analysis/cart_sim.py B1."""
    p_unique = sum(k * (1 / cw) * ((cw - 1 - m) / cw) ** (k - 1) for m in range(cw))
    return 1 - p_unique


def aloha_success(G):
    """Pure ALOHA: P(no overlap) for offered load G (frames per frame-time)."""
    return math.exp(-2 * G)


def retry_success(p_fail, retries):
    """One frame, independent attempts: 1 initial + `retries` retransmissions."""
    return 1 - p_fail ** (retries + 1)


# ── Radio: DBPSK anchored at the datasheet sensitivity ──────────────────────
def ber_at_sensitivity(fer=0.08, psdu_bytes=1024):
    """802.11b defines sensitivity at FER 8 % with a 1024-octet PSDU."""
    return 1 - (1 - fer) ** (1 / (8 * psdu_bytes))


def dbpsk_ebn0_db_for_ber(ber):
    """DBPSK: BER = ½·exp(−Eb/N0) → Eb/N0 = ln(1/(2·BER))."""
    return 10 * math.log10(math.log(1 / (2 * ber)))


def dbpsk_ber(ebn0_db):
    return 0.5 * math.exp(-10 ** (ebn0_db / 10))


def frame_error_rate(rssi_dbm, payload_bytes, sensitivity_dbm=-98.4,
                     ebn0_at_sens_db=None, interference_margin_db=0.0):
    """PER of one ESP-NOW frame at a given received power, anchored so that the
    datasheet sensitivity reproduces the 802.11b definition exactly."""
    if ebn0_at_sens_db is None:
        ebn0_at_sens_db = dbpsk_ebn0_db_for_ber(ber_at_sensitivity())
    ebn0 = ebn0_at_sens_db + (rssi_dbm - sensitivity_dbm) - interference_margin_db
    bits = 8 * (ESPNOW_OVERHEAD_BYTES + payload_bytes)
    return 1 - (1 - dbpsk_ber(ebn0)) ** bits


def free_space_pl_db(d_m=1.0, freq_hz=2.412e9):
    return 20 * math.log10(4 * math.pi * d_m * freq_hz / 299_792_458)


# ── UART (bridge ↔ Pi) ───────────────────────────────────────────────────────
def uart_byte_time_s(baud, bits_per_byte=10):
    """8N1 = start + 8 data + stop = 10 bits per byte."""
    return bits_per_byte / baud


def bridge_frame_line_bytes(fmt, packet_len, println=True):
    """uartPrintf(fmt, hex) with %s = 2 hex chars per byte, + CRLF from println."""
    return len(fmt.replace("%s", "")) + 2 * packet_len + (2 if println else 0)


def pi_ack_line_bytes(seq, ok=True, ttl=None, mac="AABBCCDDEEFF"):
    """serial_bridge._send_command: compact json.dumps + '\\n'."""
    payload = {"type": "ack", "mac": mac, "seq": seq, "ok": ok}
    if ttl is not None:
        payload["ttl"] = ttl
    return len(json.dumps(payload, separators=(",", ":"))) + 1


# ── TTL rules, exactly as the firmware applies them ──────────────────────────
def data_ttl(rank, margin, max_ttl):
    """meshTxTtl(): rank + margin, capped at MESH_MAX_TTL."""
    return min(rank + margin, max_ttl)


def data_delivers(rank, margin, max_ttl):
    """meshRelayData(): each relay drops ttl > max or ttl == 0, else forwards
    ttl−1. The bridge (rank 0) never checks TTL."""
    t = data_ttl(rank, margin, max_ttl)
    for _relay_rank in range(rank - 1, 0, -1):
        if t > max_ttl or t == 0:
            return False
        t -= 1
    return True


def ack_ttl0(rank, ack_margin, ack_max, bridge_max):
    """Pi: min(ACK_TTL_MAX, rank + ACK_TTL_MARGIN); bridge clamps to [1, MESH_MAX_TTL]."""
    return max(1, min(min(ack_max, rank + ack_margin), bridge_max))


def ack_received_ttl(ttl0, at_rank):
    """meshHandleAck(): a node re-floods with ttl−1 only if it received ttl > 0,
    so a rank-k node hears the ACK with ttl0 − (k−1) — or not at all."""
    t = ttl0
    for _k in range(1, at_rank):
        if t <= 0:
            return None
        t -= 1
    return t


def ack_reaches(rank, ack_margin, ack_max, bridge_max):
    return ack_received_ttl(ack_ttl0(rank, ack_margin, ack_max, bridge_max), rank) is not None


def depth_ceiling(margin, max_ttl, ack_margin, ack_max, bridge_max, limit=254):
    """Deepest rank r such that every rank 1..r both delivers data and gets its ACK."""
    r = 0
    while r < limit and data_delivers(r + 1, margin, max_ttl) and \
            ack_reaches(r + 1, ack_margin, ack_max, bridge_max):
        r += 1
    return r


def flood_rebroadcasts(ranks, per_rank, margin, max_ttl, ack_margin, ack_max, bridge_max):
    """Loss-free count of ACK re-broadcasts per cycle when every node relays
    (non-sleepy gate) and every node reports once. Only delivered readings get
    an ACK; each node re-floods an ACK once (dedup) iff it received ttl > 0."""
    total = 0
    for r in range(1, ranks + 1):
        if not data_delivers(r, margin, max_ttl):
            continue
        ttl0 = ack_ttl0(r, ack_margin, ack_max, bridge_max)
        rebroadcasters = sum(per_rank for k in range(1, ranks + 1)
                             if (t := ack_received_ttl(ttl0, k)) is not None and t > 0)
        total += per_rank * rebroadcasters
    return total


# ── Energy ───────────────────────────────────────────────────────────────────
def mah_per_day(T_s, awake_s, i_active_ma, i_sleep_ma):
    """Two-state duty cycle; identical to docs/analysis/cart_sim.py mah_per_day()."""
    return (86400 / T_s) * (awake_s * i_active_ma + (T_s - awake_s) * i_sleep_ma) / 3600


def g_max_s(bias, T_s, factor=1.3):
    """CART v2 ceiling rule: G_max = 2·|b|·T·1.3 (spec 2026-09-23 §3.3)."""
    return 2 * abs(bias) * T_s * factor


# ── Queues ───────────────────────────────────────────────────────────────────
def md1k_blocking(rho, K, terms=400):
    """Blocking probability of an M/D/1/K queue (K = system capacity) via the
    embedded Markov chain at departures (Gross & Harris). Used to check the
    bridge ingress queue (WiFi RX buffers) against the simulation."""
    if rho <= 0:
        return 0.0
    # a_j = P(j Poisson(rho) arrivals during one deterministic service)
    a = [math.exp(-rho) * rho ** j / math.factorial(j) for j in range(K + 1)]
    pi = [1.0]
    for n in range(K - 1):  # π_{n+1} from the balance equations of the chain
        s = pi[n] - sum(pi[k] * a[n - k + 1] for k in range(1, n + 1)) - pi[0] * a[n]
        pi.append(s / a[0])
    pi0_departure = pi[0] / sum(pi)   # departure-epoch P(empty), states 0..K-1
    return 1 - 1 / (pi0_departure + rho)
