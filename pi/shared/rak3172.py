"""RAK3172 (RUI3 firmware) AT-command driver.

The module runs the whole LoRaWAN stack itself (join, keys, duty cycle,
retransmissions); the Pi only issues AT commands over USB-serial. Async
+EVT lines can arrive interleaved with command replies -- they are queued,
never lost, and handed out by poll_events().
"""
import time

_TERMINAL_OK = 'OK'


class RakError(RuntimeError):
    pass


def parse_event(line: str):
    line = line.strip()
    if not line.startswith('+EVT:'):
        return None
    body = line[5:]
    if body == 'JOINED':
        return {'type': 'joined'}
    if body.startswith('JOIN_FAILED'):
        return {'type': 'join_failed'}
    if body == 'TX_DONE' or body == 'SEND_CONFIRMED_OK':
        return {'type': 'tx_done'}
    if body == 'SEND_CONFIRMED_FAILED':
        return {'type': 'tx_failed'}
    if body.startswith('RX_'):
        parts = body.split(':')
        # RX_C:<rssi>:<snr>:UNICAST:<port>:<hex>
        if len(parts) >= 6:
            return {'type': 'rx', 'window': parts[0][3:], 'rssi': int(parts[1]),
                    'snr': int(parts[2]), 'port': int(parts[4]),
                    'payload': bytes.fromhex(parts[5])}
    return {'type': 'other', 'raw': body}


class Rak3172:
    def __init__(self, ser, clock=time.monotonic):
        self.ser = ser
        self.clock = clock
        self.events = []

    def _readline(self):
        raw = self.ser.readline()
        return raw.decode('ascii', 'replace').strip() if raw else ''

    def command(self, cmd: str, timeout: float = 3.0):
        self.ser.write((cmd + '\r\n').encode())
        deadline = self.clock() + timeout
        lines = []
        while self.clock() < deadline:
            line = self._readline()
            if not line:
                continue
            evt = parse_event(line)
            if evt is not None:
                self.events.append(evt)
            elif line == _TERMINAL_OK:
                return lines
            elif line.startswith('AT_'):
                raise RakError(f'{cmd} -> {line}')
            else:
                lines.append(line)
        raise RakError(f'{cmd} -> timeout')

    def configure_otaa(self, dev_eui: str, app_eui: str, app_key: str) -> None:
        for cmd in ('AT+NWM=1', 'AT+BAND=4', 'AT+NJM=1', 'AT+CLASS=C', 'AT+ADR=1',
                    f'AT+DEVEUI={dev_eui}', f'AT+APPEUI={app_eui}', f'AT+APPKEY={app_key}'):
            self.command(cmd)

    def _wait_event(self, types, timeout):
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            for i, evt in enumerate(self.events):
                if evt['type'] in types:
                    return self.events.pop(i)
            line = self._readline()
            if line:
                evt = parse_event(line)
                if evt is not None:
                    self.events.append(evt)
        return None

    def join(self, timeout: float = 120.0) -> bool:
        self.command('AT+JOIN=1:0:10:8')
        evt = self._wait_event(('joined', 'join_failed'), timeout)
        return evt is not None and evt['type'] == 'joined'

    def send(self, port: int, payload: bytes, timeout: float = 15.0) -> bool:
        self.command(f'AT+SEND={port}:{payload.hex().upper()}')
        evt = self._wait_event(('tx_done', 'tx_failed'), timeout)
        return evt is not None and evt['type'] == 'tx_done'

    def poll_events(self):
        while True:
            line = self._readline()
            if not line:
                break
            evt = parse_event(line)
            if evt is not None:
                self.events.append(evt)
        out, self.events = self.events, []
        return out
