"""
Tests for src/ui/translator_layout.py - the Translator window's size, place and arrangement as
numbers in and numbers out (TODO.md U7, V11). No Tk.
"""
import pytest

from src.constants import (TRANSLATOR_DEFAULT_SIZE, TRANSLATOR_MIN_SIZE, TRANSLATOR_SIDE_BY_SIDE_MIN_WIDTH,
                           TRANSLATOR_WORK_SHARE)
from src.ui import translator_layout as T

FULL_HD = (0, 0, 1920, 1040)           # 1080p with a 40 px taskbar
LAPTOP = (0, 0, 1366, 728)             # 1366 x 768 less the taskbar
SECOND = (1920, 0, 3840, 1080)         # a monitor to the right, no taskbar


@pytest.mark.parametrize('scale', [1.0, 1.5, 2.0])
def test_resize_breakpoint_does_not_oscillate_for_small_pointer_jitter(scale):
    threshold = round(TRANSLATOR_SIDE_BY_SIDE_MIN_WIDTH * scale)
    for mode in (T.SIDE, T.STACKED):
        current = mode
        for delta in (-2, 1, -1, 2, 0) * 5:
            current = T.resize_mode(threshold + delta, current, scale)
            assert current == mode
    assert T.resize_mode(threshold - round(20 * scale), T.SIDE, scale) == T.STACKED
    assert T.resize_mode(threshold + round(20 * scale), T.STACKED, scale) == T.SIDE


class TestWindowSize:
    def test_the_default_is_smaller_than_the_old_fixed_1400_by_850(self):
        size = T.window_size(FULL_HD)
        assert (size.width, size.height) == TRANSLATOR_DEFAULT_SIZE
        assert size.width < 1400 and size.height < 850

    def test_it_fits_a_small_laptop_which_the_old_size_did_not(self):
        size = T.window_size(LAPTOP)
        assert size.width <= int(1366 * TRANSLATOR_WORK_SHARE)
        assert size.height <= int(728 * TRANSLATOR_WORK_SHARE)

    def test_a_remembered_size_wins_over_the_default(self):
        size = T.window_size(FULL_HD, saved=(1200, 700))
        assert (size.width, size.height) == (1200, 700)

    def test_a_remembered_size_too_large_for_this_screen_is_cut_to_it(self):
        size = T.window_size(LAPTOP, saved=(1800, 1000))
        assert size.width == int(1366 * TRANSLATOR_WORK_SHARE)
        assert size.height == int(728 * TRANSLATOR_WORK_SHARE)

    def test_a_remembered_size_too_small_is_raised_to_the_minimum(self):
        size = T.window_size(FULL_HD, saved=(100, 100))
        assert (size.width, size.height) == TRANSLATOR_MIN_SIZE

    def test_half_a_saved_size_uses_the_default_for_the_rest(self):
        size = T.window_size(FULL_HD, saved=(None, 700))
        assert (size.width, size.height) == (TRANSLATOR_DEFAULT_SIZE[0], 700)
        size = T.window_size(FULL_HD, saved=(1100, None))
        assert (size.width, size.height) == (1100, TRANSLATOR_DEFAULT_SIZE[1])

    def test_nothing_saved_means_the_default(self):
        assert T.window_size(FULL_HD, saved=None) == T.window_size(FULL_HD, saved=(None, None))

    def test_the_default_grows_with_the_screens_scale(self):
        big = T.window_size((0, 0, 3840, 2080), scale=2.0)
        assert (big.width, big.height) == (2 * TRANSLATOR_DEFAULT_SIZE[0], 2 * TRANSLATOR_DEFAULT_SIZE[1])
        assert big.min_width == 2 * TRANSLATOR_MIN_SIZE[0]

    def test_the_minimum_gives_way_to_a_screen_too_small_for_it(self):
        size = T.window_size((0, 0, 500, 400))
        assert size.min_width <= 450 and size.min_height <= 360
        assert size.width <= 450 and size.height <= 360

    def test_the_minimum_never_exceeds_the_size(self):
        for area in (FULL_HD, LAPTOP, (0, 0, 800, 600)):
            size = T.window_size(area)
            assert size.min_width <= size.width and size.min_height <= size.height


class TestAnotherWindowsSizes:
    """Settings sizes itself with the same rule and its own default and minimum (TODO.md U8)."""
    DEFAULT, MINIMUM = (960, 640), (680, 480)

    def test_its_own_default_is_used(self):
        size = T.window_size(FULL_HD, None, 1.0, default=self.DEFAULT, minimum=self.MINIMUM)
        assert (size.width, size.height) == self.DEFAULT

    def test_its_own_minimum_is_used(self):
        size = T.window_size(FULL_HD, (100, 100), 1.0, default=self.DEFAULT, minimum=self.MINIMUM)
        assert (size.width, size.height) == self.MINIMUM
        assert (size.min_width, size.min_height) == self.MINIMUM

    def test_the_scale_applies_to_both(self):
        size = T.window_size((0, 0, 3840, 2080), None, 2.0, default=self.DEFAULT, minimum=self.MINIMUM)
        assert (size.width, size.height) == (1920, 1280) and size.min_width == 1360

    def test_the_work_area_cap_still_wins(self):
        size = T.window_size((0, 0, 700, 500), None, 1.0, default=self.DEFAULT, minimum=self.MINIMUM)
        assert size.width == 630 and size.height == 450

    def test_without_them_the_translators_sizes_are_used(self):
        assert T.window_size(FULL_HD) == T.window_size(FULL_HD, None, 1.0, default=TRANSLATOR_DEFAULT_SIZE,
                                                       minimum=TRANSLATOR_MIN_SIZE)


class TestWindowPosition:
    def test_centred_on_the_work_area(self):
        size = T.WindowSize(1000, 600, 640, 560)
        assert T.window_position(size, FULL_HD) == (460, 220)

    def test_centred_on_a_monitor_that_does_not_start_at_zero(self):
        size = T.WindowSize(1000, 600, 640, 560)
        x, y = T.window_position(size, SECOND)
        assert x == 1920 + (1920 - 1000) // 2 and y == (1080 - 600) // 2

    def test_never_off_the_work_area(self):
        size = T.WindowSize(1300, 700, 640, 560)
        x, y = T.window_position(size, LAPTOP)
        assert x >= 0 and y >= 0
        assert x + size.width <= 1366 and y + size.height <= 728

    def test_a_window_larger_than_the_area_sits_at_its_corner(self):
        size = T.WindowSize(2000, 1200, 640, 560)
        assert T.window_position(size, LAPTOP) == (0, 0)


class TestLayoutMode:
    def test_wide_windows_put_the_panes_side_by_side(self):
        assert T.layout_mode(TRANSLATOR_SIDE_BY_SIDE_MIN_WIDTH) == T.SIDE
        assert T.layout_mode(1400) == T.SIDE

    def test_narrow_ones_stack_them(self):
        assert T.layout_mode(TRANSLATOR_SIDE_BY_SIDE_MIN_WIDTH - 1) == T.STACKED
        assert T.layout_mode(640) == T.STACKED

    def test_the_threshold_is_in_pixels_of_this_screen(self):
        assert T.layout_mode(900, scale=1.5) == T.STACKED
        assert T.layout_mode(1350, scale=1.5) == T.SIDE

    def test_the_default_window_is_wide_enough_for_two_panes_at_every_scale(self):
        for scale in (1.0, 1.25, 1.5, 2.0):
            area = (0, 0, int(3840 * scale / 2), int(2080 * scale / 2))
            size = T.window_size(area, scale=scale)
            assert T.layout_mode(size.width, scale) == T.SIDE

    @pytest.mark.parametrize('mode', [T.SIDE, T.STACKED])
    def test_the_modes_are_distinct_plain_strings(self, mode):
        assert isinstance(mode, str) and T.SIDE != T.STACKED
