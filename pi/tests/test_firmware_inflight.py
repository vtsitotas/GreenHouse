# pi/tests/test_firmware_inflight.py
"""Runs the firmware's in-flight ACK table unit test (plain C++, no Arduino).

Skips when g++ is unavailable, same policy as test_firmware_layout.py.
"""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOST_DIR = os.path.normpath(os.path.join(HERE, '..', '..', 'firmware', 'test', 'host'))


def _make_cmd():
    """Return 'make' or 'mingw32-make', whichever is on PATH."""
    for name in ('make', 'mingw32-make'):
        if shutil.which(name):
            return name
    return None


def test_inflight_table_host_unit_test_passes():
    try:
        rc = subprocess.call(['g++', '--version'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        rc = 1
    if rc != 0:
        pytest.skip('g++ not available')
    make = _make_cmd()
    if make is None:
        pytest.skip('make / mingw32-make not available')
    subprocess.check_call([make, '-s', 'inflight_test'], cwd=HOST_DIR)
    result = subprocess.run([os.path.join(HOST_DIR, 'inflight_test')],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ALL PASS' in result.stdout
