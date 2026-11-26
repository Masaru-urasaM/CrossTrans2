"""Switch a CI runner's display to a usable size before the desktop-dependent tests run (DEFERRED D30, Decision 83).

windows-latest starts with a 1024x768 virtual display. A Toplevel asked for 1700x700 gets 1028x700 there, so the
tests that assume an ordinary desktop (popup height cap, wide settings window, text-layout windows 820 pixels high)
fail. The runner's display driver lists modes up to 1920x1080; this switches to the largest 32-bit mode that fits
inside the request.

    python tools/ci_set_display.py [--width 1920] [--height 1080]

Windows only. It changes the live display mode of the machine it runs on: use it on a CI runner, not on a PC.
"""
import argparse
import ctypes
from ctypes import wintypes as W
import sys

DM_BITSPERPEL, DM_PELSWIDTH, DM_PELSHEIGHT = 0x40000, 0x80000, 0x100000
ENUM_CURRENT_SETTINGS = -1
DISP_CHANGE_SUCCESSFUL = 0


class DEVMODEW(ctypes.Structure):
    """The display half of the Win32 DEVMODEW record (220 bytes)."""
    _fields_ = [('dmDeviceName', W.WCHAR * 32), ('dmSpecVersion', W.WORD), ('dmDriverVersion', W.WORD),
                ('dmSize', W.WORD), ('dmDriverExtra', W.WORD), ('dmFields', W.DWORD),
                ('dmPositionX', W.LONG), ('dmPositionY', W.LONG), ('dmDisplayOrientation', W.DWORD),
                ('dmDisplayFixedOutput', W.DWORD), ('dmColor', W.SHORT), ('dmDuplex', W.SHORT),
                ('dmYResolution', W.SHORT), ('dmTTOption', W.SHORT), ('dmCollate', W.SHORT),
                ('dmFormName', W.WCHAR * 32), ('dmLogPixels', W.WORD), ('dmBitsPerPel', W.DWORD),
                ('dmPelsWidth', W.DWORD), ('dmPelsHeight', W.DWORD), ('dmDisplayFlags', W.DWORD),
                ('dmDisplayFrequency', W.DWORD), ('dmICMMethod', W.DWORD), ('dmICMIntent', W.DWORD),
                ('dmMediaType', W.DWORD), ('dmDitherType', W.DWORD), ('dmReserved1', W.DWORD),
                ('dmReserved2', W.DWORD), ('dmPanningWidth', W.DWORD), ('dmPanningHeight', W.DWORD)]


def choose_mode(modes, width, height):
    """The largest (width, height) among the 32-bit modes that fit inside the request; None when none does."""
    fitting = [(w, h) for w, h, bits in modes if bits == 32 and w <= width and h <= height]
    return max(fitting, key=lambda mode: (mode[0] * mode[1], mode)) if fitting else None


def _devmode():
    mode = DEVMODEW()
    mode.dmSize = ctypes.sizeof(DEVMODEW)
    return mode


def list_modes():
    """Every (width, height, bits per pixel) the primary display driver offers."""
    modes, index = set(), 0
    while True:
        mode = _devmode()
        if not ctypes.windll.user32.EnumDisplaySettingsW(None, index, ctypes.byref(mode)):
            return sorted(modes)
        modes.add((mode.dmPelsWidth, mode.dmPelsHeight, mode.dmBitsPerPel))
        index += 1


def switch_to(width, height):
    """Change the live mode (not saved to the registry); returns the Win32 result, 0 meaning success."""
    mode = _devmode()
    ctypes.windll.user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(mode))
    mode.dmPelsWidth, mode.dmPelsHeight, mode.dmBitsPerPel = width, height, 32
    mode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_BITSPERPEL
    return ctypes.windll.user32.ChangeDisplaySettingsW(ctypes.byref(mode), 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--width', type=int, default=1920)
    parser.add_argument('--height', type=int, default=1080)
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        print('ci_set_display: Windows only')
        return 1
    modes = list_modes()
    print(f'Display modes offered: {modes}')
    chosen = choose_mode(modes, args.width, args.height)
    if chosen is None:
        print(f'FAIL: no 32-bit mode fits {args.width}x{args.height}')
        return 1
    result = switch_to(*chosen)
    if result != DISP_CHANGE_SUCCESSFUL:
        print(f'FAIL: ChangeDisplaySettings to {chosen[0]}x{chosen[1]} returned {result}')
        return 1
    user32 = ctypes.windll.user32
    print(f'Display is now {user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
