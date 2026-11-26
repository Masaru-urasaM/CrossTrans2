"""Choosing the display mode a CI runner is switched to (tools/ci_set_display.py).

Only the pure choice is tested: switching the mode itself would resize the display of whoever runs the suite.
"""
import ctypes
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))

import ci_set_display as display

RUNNER_MODES = [(640, 480, 32), (1024, 768, 32), (1280, 1024, 32), (1600, 1200, 32), (1920, 1080, 32)]


def test_the_requested_size_is_used_when_the_driver_offers_it():
    assert display.choose_mode(RUNNER_MODES, 1920, 1080) == (1920, 1080)


def test_the_largest_mode_inside_the_request_is_used_otherwise():
    # 1600x1200 is taller than the request, so 1280x1024 is the largest that fits
    modes = [m for m in RUNNER_MODES if m[:2] != (1920, 1080)]
    assert display.choose_mode(modes, 1920, 1080) == (1280, 1024)


def test_modes_that_are_not_32_bit_are_ignored():
    assert display.choose_mode([(1920, 1080, 16), (1024, 768, 32)], 1920, 1080) == (1024, 768)


def test_nothing_is_chosen_when_no_mode_fits():
    assert display.choose_mode([(1920, 1080, 32)], 1024, 768) is None
    assert display.choose_mode([], 1920, 1080) is None


def test_the_mode_record_has_the_win32_size():
    assert ctypes.sizeof(display.DEVMODEW) == 220


def test_a_non_windows_platform_is_refused_without_touching_anything(monkeypatch, capsys):
    monkeypatch.setattr(display, 'sys', SimpleNamespace(platform='linux'))
    assert display.main([]) == 1
    assert 'Windows only' in capsys.readouterr().out
