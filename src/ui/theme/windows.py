"""
Windows-only window chrome that follows the theme (TODO.md V10, V15; spike S3).

`apply_title_bar()` makes a window's title bar match its palette: DWM dark mode (attribute 20) so
the caption buttons are drawn for the right background, and on Windows 11 the caption, text and
border colours (35, 36, 34) so the bar blends into the window. Every call is best effort - DWM
answers with an HRESULT and an old Windows simply does not know an attribute - and the DWM call is
an injectable seam for tests.
"""
import ctypes
import logging
import sys
from typing import Callable, Optional

from src.ui.theme import tokens
from src.ui.theme.contrast import hex_to_rgb

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3

DwmSetter = Callable[[int, int, int], int]       # (hwnd, attribute, value) -> HRESULT


def colorref(hex_colour: str) -> int:
    """'#RRGGBB' -> the 0x00BBGGRR DWM wants."""
    r, g, b = hex_to_rgb(hex_colour)
    return (b << 16) | (g << 8) | r


def dwm_set(hwnd: int, attribute: int, value: int) -> int:
    """DwmSetWindowAttribute with a 4-byte value; the HRESULT, or -1 when DWM is unavailable."""
    if sys.platform != 'win32':
        return -1
    try:
        from ctypes import wintypes
        setting = ctypes.c_int(value)
        return ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(attribute), ctypes.byref(setting),
            ctypes.sizeof(setting)) & 0xFFFFFFFF
    except Exception:
        return -1


def hwnd_of(window) -> int:
    """The Win32 handle of the frame Windows draws the title bar on."""
    return int(window.wm_frame(), 16)


def apply_title_bar(window, palette: tokens.Palette, setter: DwmSetter = dwm_set) -> bool:
    """Dark or light caption buttons, and (Windows 11) a caption in the window's own colour.
    Returns whether the dark-mode attribute was accepted - the one attribute every supported
    Windows 10 build knows."""
    try:
        hwnd = hwnd_of(window)
    except Exception:
        logging.debug("[Theme] no frame to style", exc_info=True)
        return False
    ok = setter(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if palette.mode == tokens.DARK_MODE else 0) == 0
    setter(hwnd, DWMWA_CAPTION_COLOR, colorref(palette.bg))
    setter(hwnd, DWMWA_TEXT_COLOR, colorref(palette.text))
    setter(hwnd, DWMWA_BORDER_COLOR, colorref(palette.border))
    return ok


def round_corners(window, small: bool = False, setter: DwmSetter = dwm_set) -> bool:
    """Ask DWM (Windows 11) to round the window's corners; a harmless no-op elsewhere."""
    try:
        return setter(hwnd_of(window), DWMWA_WINDOW_CORNER_PREFERENCE,
                      DWMWCP_ROUNDSMALL if small else DWMWCP_ROUND) == 0
    except Exception:
        return False


def style_floating(window, palette: tokens.Palette, setter: DwmSetter = dwm_set) -> bool:
    """A borderless popup on Windows 11: rounded corners, and the system's border (where it draws one)
    in the palette's hairline colour. Windows 10 does not know the attributes and ignores them, which
    leaves the square popup with its own 1 px ring; returns whether DWM accepted the rounding.

    Call it once the window is mapped (`<Map>`): the frame Windows draws is made then.
    """
    try:
        hwnd = hwnd_of(window)
    except Exception:
        return False
    rounded = setter(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND) == 0
    setter(hwnd, DWMWA_BORDER_COLOR, colorref(palette.border))
    return rounded


def set_default_icon(root, ico_path: Optional[str]) -> bool:
    """One call gives every Toplevel - existing and new - the application icon (spike S3:
    `iconbitmap(default=...)` replaces the window class's icon)."""
    if not ico_path:
        return False
    try:
        root.iconbitmap(default=ico_path)
        return True
    except Exception:
        logging.debug("[Theme] could not set the default window icon", exc_info=True)
        return False


def no_activate(window) -> bool:
    """Mark a Toplevel WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW so that showing it never takes the focus
    from the window the user is typing in (a tooltip, an update offer - Decision 43). The bit must
    be set once the window is mapped: set any earlier it is lost (Decision 98, formerly DEFERRED D20)."""
    try:
        import win32con
        import win32gui
        hwnd = win32gui.GetParent(window.winfo_id()) or window.winfo_id()
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                               style | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW)
        return True
    except (ImportError, OSError, tk_error()):
        return False


def tk_error():
    import tkinter
    return tkinter.TclError


def monitor_work_area(x, y):
    """The nearest monitor's usable rectangle, or None for the caller's fallback."""
    try:
        import win32api
        monitor = win32api.MonitorFromPoint((int(x), int(y)), 2)
        return tuple(win32api.GetMonitorInfo(monitor)['Work'])
    except Exception:
        return None
