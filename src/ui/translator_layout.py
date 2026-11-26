"""
How big the Translator window is, where it opens and how its two panes are arranged - the
decisions, not the widgets (TODO.md U7, V11).

Pure logic, no Tk: the window used to be 1400 x 850 whatever the screen - wider and taller than a
1366 x 768 laptop - and its widgets were sized in raw pixels. Now the size comes from the work area
and the remembered size, every number is scaled to the screen, and the panes sit side by side only
when the window is wide enough to give each a reading width.
"""
from typing import NamedTuple, Optional, Tuple

from src.constants import (
    TRANSLATOR_DEFAULT_SIZE as DEFAULT_SIZE,
    TRANSLATOR_MIN_SIZE as MIN_SIZE,
    TRANSLATOR_SIDE_BY_SIDE_MIN_WIDTH as SIDE_BY_SIDE_MIN_WIDTH,
    TRANSLATOR_WORK_SHARE as WORK_SHARE,
)
from src.ui.theme.scale import px

SIDE = 'side'
STACKED = 'stacked'

Area = Tuple[int, int, int, int]          # (left, top, right, bottom) of a work area, screen pixels


class WindowSize(NamedTuple):
    width: int
    height: int
    min_width: int
    min_height: int


def window_size(area: Area, saved: Optional[Tuple[Optional[int], Optional[int]]] = None,
                scale: float = 1.0, default: Tuple[int, int] = DEFAULT_SIZE,
                minimum: Tuple[int, int] = MIN_SIZE) -> WindowSize:
    """The window's size on `area`: the remembered size when there is one, else the default, both
    at least the minimum and at most `WORK_SHARE` of the work area each way (on a small screen
    the cap wins over the minimum - a window that does not fit helps nobody).

    `saved` is physical pixels as the user left it; the default and the minimum are at 100 % and
    are the Translator's unless another window (Settings) gives its own.
    """
    left, top, right, bottom = area
    cap_w, cap_h = int((right - left) * WORK_SHARE), int((bottom - top) * WORK_SHARE)
    min_w, min_h = min(px(minimum[0], scale), cap_w), min(px(minimum[1], scale), cap_h)
    saved_w, saved_h = saved or (None, None)
    width = saved_w if saved_w else px(default[0], scale)
    height = saved_h if saved_h else px(default[1], scale)
    return WindowSize(min(max(width, min_w), cap_w), min(max(height, min_h), cap_h), min_w, min_h)


def window_position(size: WindowSize, area: Area) -> Tuple[int, int]:
    """(x, y) that centres the window on the work area and keeps it entirely on it."""
    left, top, right, bottom = area
    x = left + (right - left - size.width) // 2
    y = top + (bottom - top - size.height) // 2
    return max(left, min(x, right - size.width)), max(top, min(y, bottom - size.height))


def layout_mode(width: int, scale: float = 1.0) -> str:
    """SIDE when `width` (pixels) gives two panes a reading width each, else STACKED."""
    return SIDE if width >= px(SIDE_BY_SIDE_MIN_WIDTH, scale) else STACKED


def resize_mode(width: int, current: Optional[str], scale: float = 1.0) -> str:
    """Keep a small hysteresis band so border jitter cannot repeatedly regrid panes."""
    threshold = px(SIDE_BY_SIDE_MIN_WIDTH, scale)
    margin = px(8, scale)
    if current == SIDE and width >= threshold - margin:
        return SIDE
    if current == STACKED and width < threshold + margin:
        return STACKED
    return layout_mode(width, scale)

