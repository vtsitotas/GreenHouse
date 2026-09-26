import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
import drift_logger as dl


def test_live_mesh_message_becomes_a_csv_line():
    assert dl.format_line('greenhouse/nodes/206EF16C9DB0/mesh', False, 1700000000.123456) \
        == '206EF16C9DB0,1700000000.123456'


def test_retained_replay_is_ignored():
    assert dl.format_line('greenhouse/nodes/206EF16C9DB0/mesh', True, 1.0) is None


def test_other_topics_are_ignored():
    assert dl.format_line('greenhouse/nodes/206EF16C9DB0/status', False, 1.0) is None


def test_every_connect_resubscribes():
    # Subscribing once before loop_forever() silently lost the subscription on
    # the first reconnect (seen live after a Pi reboot: the logger kept
    # running and recorded nothing). The subscription must live in on_connect.
    subs = []
    fake = type('C', (), {'subscribe': lambda self, topic, qos=0: subs.append((topic, qos))})()
    dl.on_connect(fake, None, None, 0)
    dl.on_connect(fake, None, None, 0)
    assert subs == [(dl.TOPIC, 1), (dl.TOPIC, 1)]
