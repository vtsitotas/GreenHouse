# pi/tests/test_hivemq_bridge.py
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import hivemq_bridge as hb


class FakeTarget:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))


def _forwarder():
    hb._last_seen.clear()
    target = FakeTarget()
    return hb._make_forwarder('local', {'client': target}), target


def _msg(topic, payload, retain=False):
    return SimpleNamespace(topic=topic, payload=payload, retain=retain)


def test_a_retained_delete_reaches_the_other_broker_as_retained():
    # A broker delivers a live retained publish to existing subscribers with
    # RETAIN=0 (MQTT 3.1.1 §3.3.1.3), so a deletion (empty payload) used to be
    # forwarded as a plain message and never cleared the other side's copy --
    # which then flowed back on the next bridge start and resurrected deleted
    # sensors and zones.
    fwd, target = _forwarder()
    fwd(None, None, _msg('greenhouse/sensor9DB0/air/temperature', b'', retain=False))
    assert target.published == [('greenhouse/sensor9DB0/air/temperature', b'', True)]


def test_a_normal_live_message_keeps_its_retain_flag():
    fwd, target = _forwarder()
    fwd(None, None, _msg('greenhouse/zone3/air/temperature', b'23.5', retain=False))
    assert target.published == [('greenhouse/zone3/air/temperature', b'23.5', False)]


def test_a_retained_snapshot_message_stays_retained():
    fwd, target = _forwarder()
    fwd(None, None, _msg('greenhouse/zone3/air/temperature', b'23.5', retain=True))
    assert target.published == [('greenhouse/zone3/air/temperature', b'23.5', True)]


def test_a_forwarded_delete_is_not_echoed_back():
    fwd, target = _forwarder()
    fwd(None, None, _msg('greenhouse/z/air/humidity', b'', retain=False))
    fwd(None, None, _msg('greenhouse/z/air/humidity', b'', retain=False))   # the echo
    assert len(target.published) == 1
