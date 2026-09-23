# pi/tests/test_lora_payload.py
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import lora_payload as lp


def test_zone_number_parses_the_project_convention():
    assert lp.zone_number('zone3') == 3
    assert lp.zone_number('Tomatoes') is None


def test_summary_round_trip_with_one_decimal_precision():
    zs = [lp.ZoneSummary(2, 24.46, 61.04, 42.0), lp.ZoneSummary(3, -3.2, None, 10.55)]
    (msg,) = lp.encode_summaries(zs)
    out = lp.decode_summary(msg)
    assert out == [lp.ZoneSummary(2, 24.5, 61.0, 42.0), lp.ZoneSummary(3, -3.2, None, 10.6)]


def test_summaries_are_chunked_to_the_sf12_cap():
    zs = [lp.ZoneSummary(i, 20.0, 50.0, 30.0) for i in range(1, 16)]
    msgs = lp.encode_summaries(zs)
    assert [len(m) for m in msgs] == [51, 51, 9]
    assert sum(len(lp.decode_summary(m)) for m in msgs) == 15


def test_alert_round_trip_and_length_cap():
    data = lp.encode_alert('warning', 'soil-dry-zone3')
    assert lp.decode_alert(data) == {'severity': 'warning', 'rule_id': 'soil-dry-zone3'}
    assert len(lp.encode_alert('critical', 'x' * 100)) <= lp.MAX_PAYLOAD


def test_command_round_trip():
    assert lp.decode_command(lp.encode_command('pump1', True)) == ('pump1', True)
    assert lp.decode_command(lp.encode_command('fan', False)) == ('fan', False)


def test_unknown_version_is_rejected():
    with pytest.raises(ValueError):
        lp.decode_summary(b'\x02\x00')
