"""Read every firmware / Pi-bridge / toolchain constant straight from source.

The simulator never copies a firmware constant by hand. Each value here is
parsed out of the file that defines it and carries the file:line it came
from, so the parameter catalogue cannot drift from the code. A JSON snapshot
(firmware_snapshot.json) lets the engine run where the repo is absent
(Pyodide in the browser); sim/tests asserts the snapshot still matches the
live sources, so a firmware change without a re-snapshot fails the tests.

    python -m meshsim snapshot     # re-parse sources, rewrite the snapshot
"""
import ast
import json
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = Path(__file__).with_name("firmware_snapshot.json")

# Parse order matters: later files reference earlier defines
# (e.g. MESH_INFLIGHT_MAX = MESH_DATA_BUFFER_SIZE + 1).
C_SOURCES = [
    "firmware/libraries/GreenhouseMesh/mesh_config.h",
    "firmware/libraries/GreenhouseMesh/mesh_packet.h",
    "firmware/libraries/GreenhouseMesh/mesh_crypto.h",
    "firmware/libraries/GreenhouseMesh/mesh_inflight.h",
    "firmware/libraries/GreenhouseMesh/mesh_node.h",
    "firmware/libraries/GreenhouseMesh/mesh_sched.h",
    "firmware/libraries/GreenhouseMesh/mesh_cart.h",
    "firmware/libraries/GreenhouseMesh/mesh_uart.h",
    "firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino",
    "firmware/bridge_esp32/bridge_esp32.ino",
]
PY_SOURCES = ["pi/scripts/serial_bridge.py"]
# Designed-but-unbuilt constants (CART Part C) live as #define blocks in the plan.
PLANNED_SOURCES = ["docs/superpowers/plans/2026-09-23-cart-phase2-synced-wake.md"]
NODE_H = "firmware/libraries/GreenhouseMesh/mesh_node.h"
CART_H = "firmware/libraries/GreenhouseMesh/mesh_cart.h"
SCHED_H = "firmware/libraries/GreenhouseMesh/mesh_sched.h"
EDGE = "firmware/edge_node_esp32_c3/edge_node_esp32_c3.ino"
BRIDGE = "firmware/bridge_esp32/bridge_esp32.ino"

# Toolchain files live outside the repo (arduino-cli / Arduino IDE data dir).
ARDUINO15 = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Arduino15"
HWSERIAL_GLOB = "packages/esp32/hardware/esp32/*/cores/esp32/HardwareSerial.cpp"
SDKCONFIG_GLOB = "packages/esp32/tools/esp32c3-libs/*/sdkconfig"
SDKCONFIG_KEYS = (
    "CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM",
    "CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM",
    "CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM",
    "CONFIG_ESP_WIFI_MGMT_SBUF_NUM",
    "CONFIG_ESP_PHY_MAX_WIFI_TX_POWER",
    "CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ",
    "CONFIG_BOOTLOADER_RESERVE_RTC_SIZE",
    "CONFIG_RTC_CLK_SRC_INT_RC",
    "CONFIG_FREERTOS_HZ",
)

TYPE_SIZE = {"bool": 1, "char": 1, "uint8_t": 1, "int8_t": 1, "uint16_t": 2,
             "int16_t": 2, "uint32_t": 4, "int32_t": 4, "float": 4,
             "uint64_t": 8, "int64_t": 8, "double": 8}
_INT_SUFFIX = re.compile(r"\b(0[xX][0-9A-Fa-f]+|\d+)(?:ULL|UL|LL|U|L)\b")
_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
           ast.Mult: lambda a, b: a * b, ast.FloorDiv: lambda a, b: a // b,
           ast.Mod: lambda a, b: a % b, ast.LShift: lambda a, b: a << b,
           ast.RShift: lambda a, b: a >> b, ast.BitOr: lambda a, b: a | b,
           ast.BitAnd: lambda a, b: a & b}
_CMPOPS = {ast.Gt: lambda a, b: int(a > b), ast.Lt: lambda a, b: int(a < b),
           ast.GtE: lambda a, b: int(a >= b), ast.LtE: lambda a, b: int(a <= b),
           ast.Eq: lambda a, b: int(a == b), ast.NotEq: lambda a, b: int(a != b)}


def _src(rel, line):
    return f"{rel}:{line}"


def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def safe_eval(expr, env):
    """C integer arithmetic over literals and already-known defines only
    (plus a single `cond ? a : b`, rewritten to Python's conditional)."""
    expr = _INT_SUFFIX.sub(r"\1", expr).strip()
    m = re.fullmatch(r"\(?\s*([^?]+?)\s*\?\s*([^:]+?)\s*:\s*([^)]+?)\s*\)?", expr)
    if m:
        expr = f"({m.group(2)}) if ({m.group(1)}) else ({m.group(3)})"
    tree = ast.parse(expr, mode="eval")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.Name) and n.id in env and isinstance(env[n.id], (int, float)):
            return env[n.id]
        if isinstance(n, ast.IfExp):
            return ev(n.body) if ev(n.test) else ev(n.orelse)
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and type(n.ops[0]) in _CMPOPS:
            return _CMPOPS[type(n.ops[0])](ev(n.left), ev(n.comparators[0]))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            a, b = ev(n.left), ev(n.right)
            return a // b if isinstance(a, int) and isinstance(b, int) else a / b
        if isinstance(n, ast.BinOp) and type(n.op) in _BINOPS:
            return _BINOPS[type(n.op)](ev(n.left), ev(n.right))
        raise ValueError(expr)
    return ev(tree)


def _strip_comments(text):
    """Drop C comments but keep every newline, so line numbers still match."""
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _logical_lines(text):
    """(first line number, line) with backslash continuations joined."""
    buf, start = "", None
    for i, line in enumerate(text.splitlines(), 1):
        if start is None:
            start = i
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        yield start, buf + line
        buf, start = "", None


def parse_defines(rel, text, env, out):
    for i, line in _logical_lines(text):
        m = re.match(r"\s*#define\s+(\w+)\s+(.+?)\s*$", line)
        if not m:
            continue
        name, body = m.group(1), m.group(2)
        comment = ""
        if "//" in body and not body.startswith('"'):
            body, comment = (s.strip() for s in body.split("//", 1))
        try:
            value = ast.literal_eval(body) if body.startswith('"') else safe_eval(body, env)
        except (ValueError, SyntaxError, ZeroDivisionError):
            continue
        env[name] = value
        out[name] = {"value": value, "src": _src(rel, i), "comment": comment}


def parse_structs(rel, text, env, structs):
    """sizeof() of every typedef struct, honouring __attribute__((packed))
    and natural C alignment otherwise (RISC-V ILP32, same as the C3's GCC)."""
    clean = _strip_comments(text)
    pat = re.compile(r"typedef\s+struct\s*(__attribute__\s*\(\(\s*packed\s*\)\))?"
                     r"\s*\{(.*?)\}\s*(\w+)\s*;", re.S)
    for m in pat.finditer(clean):
        packed, body, name = bool(m.group(1)), m.group(2), m.group(3)
        offset, max_align, ok = 0, 1, True
        for field in filter(None, (f.strip() for f in body.split(";"))):
            # "type name[dim]" or "type a, b[dim], c" (several declarators)
            fm = re.match(r"(\w+)\s+(.+)$", field, re.S)
            ftype = fm.group(1) if fm else None
            if ftype in TYPE_SIZE:
                size = align = TYPE_SIZE[ftype]
            elif ftype in structs:
                size, align = structs[ftype]["size"], structs[ftype]["align"]
            else:
                ok = False
                break
            align = 1 if packed else align
            for decl in (d.strip() for d in fm.group(2).split(",")):
                dm = re.match(r"(\w+)((?:\s*\[[^\]]+\])*)$", decl)
                if not dm:
                    ok = False
                    break
                count = 1
                for d in re.findall(r"\[([^\]]+)\]", dm.group(2)):
                    count *= safe_eval(d, env)
                offset = -(-offset // align) * align + size * count
            if not ok:
                break
            max_align = max(max_align, align)
        if ok:
            structs[name] = {"size": -(-offset // max_align) * max_align, "align": max_align,
                             "packed": packed, "src": _src(rel, _line_of(clean, m.end()))}


def _find(rel, text, pattern, cast=int):
    m = re.search(pattern, text, flags=re.S)
    if not m:
        raise ValueError(f"{rel}: pattern not found: {pattern}")
    return {"value": cast(m.group(1)), "src": _src(rel, _line_of(text, m.start(1)))}


def parse_literals(texts):
    """Constants the firmware writes as literals, not #defines."""
    node, edge, bridge = texts[NODE_H], texts[EDGE], texts[BRIDGE]
    lit = {
        "TX_FAIL_DROP_COUNT": _find(NODE_H, node, r"\+\+meshTxFailCount >= (\d+)"),
        "UNCONFIRMED_WAKES_RESCAN": _find(EDGE, edge, r"g_unconfirmedWakes >= (\d+)"),
        "UNCONFIRMED_WAKES_CAP": _find(EDGE, edge, r"g_unconfirmedWakes < (\d+)"),
        "COLD_BOOT_USB_WAIT_MS": _find(EDGE, edge, r"ESP_SLEEP_WAKEUP_TIMER\) delay\((\d+)\)"),
        "BATT_ADC_SAMPLES": _find(EDGE, edge, r"readBatteryMv\(\) \{.*?i < (\d+); i\+\+"),
        "BATT_ADC_SAMPLE_DELAY_MS": _find(EDGE, edge, r"analogReadMilliVolts\(BATT_ADC_PIN\);\s*delay\((\d+)\)"),
        "G_MAX_FACTOR_X10": _find(SCHED_H, texts[SCHED_H], r"biasPpm \* (\d+) / 10"),
        "G_MAX_WANDER_Z": _find(SCHED_H, texts[SCHED_H], r"wander = \(uint64_t\)(\d+) \* cycleMs"),
        "BRIDGE_FRAME_FORMAT": _find(BRIDGE, bridge,
                                     r'uartPrintf\("(\{\\"type\\":\\"frame\\".*?)", hex\)',
                                     cast=lambda s: s.replace('\\"', '"')),
        "BRIDGE_UART_WRITE": _find(BRIDGE, bridge, r"Serial1\.(println)\(buf\)", cast=str),
    }
    rtc = []
    for rel in (NODE_H, CART_H, EDGE):
        if rel not in texts:
            continue
        for m in re.finditer(r"RTC_DATA_ATTR\s+(?:static\s+)?(\w+)\s+(\w+)((?:\s*\[[^\]]+\])*)",
                             texts[rel]):
            rtc.append({"type": m.group(1), "name": m.group(2),
                        "dims": re.findall(r"\[([^\]]+)\]", m.group(3)),
                        "src": _src(rel, _line_of(texts[rel], m.start()))})
    lit["RTC_VARS"] = {"value": rtc, "src": "RTC_DATA_ATTR declarations"}
    return lit


def parse_python(rel, text):
    out = {}
    for node in ast.parse(text).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                continue
            if isinstance(value, (int, float, tuple)) and not isinstance(value, bool):
                out[node.targets[0].id] = {"value": value, "src": _src(rel, node.lineno)}
    return out


def _latest(glob):
    hits = sorted(ARDUINO15.glob(glob))
    return hits[-1] if hits else None


def parse_toolchain():
    """sdkconfig of the installed arduino-esp32 core + HardwareSerial defaults."""
    out = {}
    sdk = _latest(SDKCONFIG_GLOB)
    if sdk:
        label = "Arduino15/" + sdk.relative_to(ARDUINO15).as_posix()
        for i, line in enumerate(sdk.read_text(encoding="utf-8").splitlines(), 1):
            k, _, v = line.partition("=")
            if k in SDKCONFIG_KEYS:
                val = True if v == "y" else (int(v, 0) if re.fullmatch(r"0[xX][0-9a-fA-F]+|\d+", v) else v)
                out[k] = {"value": val, "src": _src(label, i)}
    hs = _latest(HWSERIAL_GLOB)
    if hs:
        label = "Arduino15/" + hs.relative_to(ARDUINO15).as_posix()
        text = hs.read_text(encoding="utf-8")
        out["HWSERIAL_TX_BUFFER_DEFAULT"] = _find(label, text, r"_txBufferSize\((\d+)\)")
        out["HWSERIAL_RX_BUFFER_DEFAULT"] = _find(label, text, r"_rxBufferSize\((\d+)\)")
    return out


def parse_live():
    env, defines, structs, texts = {}, {}, {}, {}
    for rel in C_SOURCES:
        texts[rel] = (REPO / rel).read_text(encoding="utf-8")
        parse_defines(rel, texts[rel], env, defines)
        parse_structs(rel, texts[rel], env, structs)
    python = {}
    for rel in PY_SOURCES:
        python.update(parse_python(rel, (REPO / rel).read_text(encoding="utf-8")))
    planned, planned_env = {}, dict(env)
    for rel in PLANNED_SOURCES:
        parse_defines(rel, (REPO / rel).read_text(encoding="utf-8"), planned_env, planned)
    data = {"defines": defines, "structs": structs, "literals": parse_literals(texts),
            "python": python, "toolchain": parse_toolchain(), "planned": planned}
    return json.loads(json.dumps(data))  # tuples -> lists, same shape as the snapshot


def write_snapshot(data=None):
    data = data or parse_live()
    SNAPSHOT.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return data


def load():
    """Live sources when the repo is present, otherwise the snapshot."""
    if (REPO / C_SOURCES[0]).exists():
        data = parse_live()
        if not data["toolchain"] and SNAPSHOT.exists():  # no Arduino15 on this machine
            data["toolchain"] = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["toolchain"]
        return data
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))
