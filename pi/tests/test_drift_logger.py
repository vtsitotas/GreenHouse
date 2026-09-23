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
