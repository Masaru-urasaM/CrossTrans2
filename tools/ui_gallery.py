"""
Render every CrossTrans surface and state, at each scale, and measure it (TODO.md U0).

    python tools/ui_gallery.py --label before
    python tools/ui_gallery.py --label after --scales 100,150 --surfaces translator,dialogs
    python tools/ui_gallery.py --label quick --scales 100 --no-shots

Writes logs/ui_gallery/<label>/s<scale>/<surface>.<state>.png, logs/ui_gallery/<label>/
s<scale>.json per scale and logs/ui_gallery/<label>.json for the whole run, and prints one
line per capture plus a summary of the findings by kind. Each scale runs in its own process:
`tk scaling` decides every font a window builds, so a process measures one scale.

The same safety model as tools/popup_gallery.py (Decision 32): before any window exists, the
Tk thread moves to a desktop no monitor shows (tests/quiet_session.py) and the tool refuses to
run if it cannot; APPDATA points at a scratch folder, so the user's settings are neither read
nor written; the network, the clipboard and the keyboard are refused - and, so a run does not
even try, the two background fetches the app starts (remote config, update check) are
replaced by no-ops, and the modal calls a dialog may make (`wait_window`, `grab_set`) do
nothing. A run that reached for anything it should not, left a window on the user's desktop,
or could not build a surface exits 1.

"Findings" are what a person sees as wrong, read from the live widgets: controls squeezed
out or clipped (`clipped_control`), a window larger than the work area (`outside_work_area`),
no icon of its own (`no_window_icon`), a light title bar on a dark window (`light_title_bar`),
text under 4.5:1 (`low_contrast`), fonts under 12 px (`small_font`), a two-row list
(`tiny_list`), a text box too short for two lines (`tiny_text_box`), content that only a
hidden scroll bar could reach (`scroll_without_scrollbar`),
emoji and symbols in text (`symbol_in_text`), toasts that look alike (`toasts_alike`). The
before-run must report the defects TODO.md section 5 recorded (`KNOWN_DEFECTS`): a gallery
that cannot see a known bug is not a test.
"""
import argparse
import _tkinter
import contextlib
import ctypes
import ctypes.wintypes as wt
import json
import os
import platform
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def report_path(path, start):
    """Keep reports usable when output and checkout live on different Windows drives."""
    try:
        return os.path.relpath(path, start)
    except ValueError:
        return os.path.abspath(path)

TESTS = os.path.join(REPO, 'tests')
OUT_ROOT = os.path.join(REPO, 'logs', 'ui_gallery')
ALL_SCALES = (100, 125, 150, 175, 200)
# A small laptop's work area at 100 %: 1366x768 less the taskbar. TODO.md V11.
SMALL_LAPTOP = ('laptop-1366x768', 1366, 728)
MIN_TEXT_CONTRAST = 4.5                    # WCAG 2.2 SC 1.4.3
MIN_FONT_PX = 12                           # Fluent's smallest regular text
# The arrow of a language pair ("Japanese -> English", U+2192) is typography in the text font, not
# an icon in a second system (V6 was about those): left out of the symbols.
SYMBOLS = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2191\u2193-\u21FF\u25A0-\u25FF]")

# What the "before" run must report (TODO.md section 5). id -> (what, a predicate on a finding row)
# The rows are dicts: surface, state, scale and the finding's kind / widget / detail.
KNOWN_DEFECTS = {
    'V11-api-dialog-clipped': ("the API error dialog cuts its buttons",
                                 lambda f: f['surface'] == 'dialog.api_error'
                                 and f['kind'] == 'clipped_control'),
    'V19-two-row-language-list': ("the Translator's language list shows two rows",
                                  lambda f: f['surface'] == 'translator'
                                  and f['kind'] == 'tiny_list'),
    'V11-translator-over-small-laptop': ("the Translator is larger than a 1366x768 screen's "
                                         "work area",
                                         lambda f: f['surface'] == 'translator'
                                         and f['kind'] == 'outside_work_area'
                                         and SMALL_LAPTOP[0] in f['detail']),
    'V11-translation-box-squeezed-at-150': ("the Translator's translation box shrinks to a line "
                                            "at 150 %",
                                            lambda f: f['surface'] == 'translator'
                                            and f['kind'] == 'tiny_text_box'
                                            and f['scale'] >= 150),
    'V9-feather-icon': ("a window without the app's icon",
                        lambda f: f['kind'] == 'no_window_icon'),
    'V29-translate-button-contrast': ("the Translate button's text is under 4.5:1",
                                      lambda f: f['surface'] == 'translator'
                                      and f['kind'] == 'low_contrast'
                                      and 'Translate' in f['widget']),
    'V10-light-title-bar': ("a dialog with a light title bar in a dark app",
                            lambda f: f['kind'] == 'light_title_bar'),
    'V11-hotkeys-scroll-without-bar': ("the Hotkeys page scrolls with no visible scroll bar",
                                       lambda f: f['surface'] == 'settings'
                                       and f['kind'] == 'scroll_without_scrollbar'),
    'V3-toasts-alike': ("success, warning and error toasts look the same",
                        lambda f: f['kind'] == 'toasts_alike'),
}

FIXED_HISTORY_TIME = time.mktime((2026, 1, 2, 10, 15, 0, 0, 0, -1))   # 2026-01-02 10:15 local
# Defects the engine switch (Decision 45) removes by itself: ttkbootstrap 1.x overwrote the toasts'
# colours, 2.x does not (V3); the 2.x theme built from our tokens has a Translate button that
# reads (V29); U5 gave every window the app's icon (V9). These must stay UNSEEN - seeing one
# again is a regression.
ENGINE_FIXED = ('V3-toasts-alike', 'V29-translate-button-contrast', 'V9-feather-icon',
                'V11-api-dialog-clipped', 'V10-light-title-bar')
# A defect that only shows from some scale on cannot be expected in a run that stops short of it.
NEEDS_SCALE = {'V11-translation-box-squeezed-at-150': 150}
JP_SRC = "私は毎日日本語を勉強しています。東京大学で言語学を学んでいます。"
EN_TR = "I study Japanese every day. I am studying linguistics at the University of Tokyo."
ERROR_TR = "Error: API key invalid or quota exceeded (401)"


# --------------------------------------------------------------------------- #
# Pure helpers (no Tk, no Windows)
# --------------------------------------------------------------------------- #
def parse_scales(text: str) -> List[int]:
    """'100,150' -> [100, 150]; every value must be one of ALL_SCALES."""
    scales = [int(part) for part in text.split(',') if part.strip()]
    bad = [s for s in scales if s not in ALL_SCALES]
    if bad or not scales:
        raise SystemExit(f"ui_gallery: scales must be from {ALL_SCALES}, got {text!r}")
    return scales


def scaling_for(base_scaling: float, scale_percent: int) -> float:
    """Tk's `scaling` (pixels per point) for a Windows scale factor, from the 100 % value."""
    return base_scaling * scale_percent / 100.0


def hex_to_rgb(value: str) -> Tuple[int, int, int]:
    value = value.lstrip('#')
    if len(value) == 3:
        value = ''.join(c * 2 for c in value)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def relative_luminance(rgb: Tuple[int, int, int]) -> float:
    """WCAG 2.x relative luminance."""
    def channel(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def font_px(size: int, pixels_per_point: float) -> float:
    """A Tk font size in pixels: negative sizes already are, positive ones are points."""
    return float(-size) if size < 0 else size * pixels_per_point


def rect_inside(inner: Tuple[int, int, int, int], outer: Tuple[int, int, int, int]) -> bool:
    """(left, top, right, bottom) inside (left, top, right, bottom)."""
    return (inner[0] >= outer[0] and inner[1] >= outer[1]
            and inner[2] <= outer[2] and inner[3] <= outer[3])


def fits_work_area(width: int, height: int, work_w: int, work_h: int) -> bool:
    return width <= work_w and height <= work_h


def slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_') or 'x'


@dataclass
class Finding:
    kind: str
    widget: str
    detail: str


def match_known_defects(rows: Iterable[dict]) -> Dict[str, List[str]]:
    """For each known defect, the 'surface.state@scale' captures that show it ([] = unseen)."""
    rows = list(rows)
    found = {}
    for key, (_, predicate) in KNOWN_DEFECTS.items():
        found[key] = sorted({f"{r['surface']}.{r['state']}@{r['scale']}"
                             for r in rows if predicate(r)})
    return found


def summarise(findings: Iterable[dict]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f['kind']] = counts.get(f['kind'], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


# --------------------------------------------------------------------------- #
# Windows (ctypes)
# --------------------------------------------------------------------------- #
def work_area() -> Tuple[int, int, int, int]:
    """The primary monitor's work area (left, top, right, bottom)."""
    rect = wt.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)   # SPI_GETWORKAREA
    return rect.left, rect.top, rect.right, rect.bottom


def window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    rect = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def tk_feather_icon() -> Optional[int]:
    """The handle of Tk's own default window icon (the feather): the icon resource Tk's DLL
    carries, which is also the class icon of every Tk toplevel nobody gave another one."""
    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.LoadIconW.restype = ctypes.c_void_p
        user32.LoadIconW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        module = kernel32.GetModuleHandleW('tk86t.dll')
        return user32.LoadIconW(module, 'tk') if module else None
    except Exception:
        return None


def has_window_icon(hwnd: int) -> bool:
    """True when the window shows an icon other than Tk's feather: one set on the window
    itself (WM_SETICON, `iconbitmap(path)`) or one set for all toplevels (the window class's,
    `iconbitmap(default=...)` / `iconphoto(True, ...)`). Measured, U1 spike S3."""
    user32 = ctypes.windll.user32
    if user32.SendMessageW(hwnd, 0x007F, 0, 0) or user32.SendMessageW(hwnd, 0x007F, 1, 0):
        return True                                            # ICON_SMALL / ICON_BIG
    user32.GetClassLongPtrW.restype = ctypes.c_void_p
    class_icon = user32.GetClassLongPtrW(hwnd, -14)            # GCLP_HICON
    return bool(class_icon) and class_icon != tk_feather_icon()


def dark_title_bar(hwnd: int) -> Optional[bool]:
    """DWMWA_USE_IMMERSIVE_DARK_MODE (20), or None when DWM would not say."""
    value = ctypes.c_int(0)
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wt.HWND(hwnd), 20, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        return None
    return bool(value.value) if hr == 0 else None


def capture(hwnd: int, path: str, *, flags: int = 2) -> bool:
    """Capture a native window and release every selected GDI object on all paths."""
    try:
        from PIL import Image
    except ImportError:
        return False
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    signatures = (
        (user32.GetWindowDC, [wt.HWND], wt.HDC),
        (user32.ReleaseDC, [wt.HWND, wt.HDC], ctypes.c_int),
        (user32.PrintWindow, [wt.HWND, wt.HDC, wt.UINT], wt.BOOL),
        (gdi32.CreateCompatibleDC, [wt.HDC], wt.HDC),
        (gdi32.CreateCompatibleBitmap, [wt.HDC, ctypes.c_int, ctypes.c_int], wt.HBITMAP),
        (gdi32.SelectObject, [wt.HDC, wt.HGDIOBJ], wt.HGDIOBJ),
        (gdi32.DeleteObject, [wt.HGDIOBJ], wt.BOOL),
        (gdi32.DeleteDC, [wt.HDC], wt.BOOL),
        (gdi32.GetDIBits, [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                           ctypes.c_void_p, ctypes.c_void_p, wt.UINT], ctypes.c_int),
    )
    for function, arguments, result in signatures:
        function.argtypes, function.restype = arguments, result
    left, top, right, bottom = window_rect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return False
    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = bitmap = previous = None
    try:
        if not window_dc:
            return False
        memory_dc = gdi32.CreateCompatibleDC(window_dc)
        bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
        if not memory_dc or not bitmap:
            return False
        previous = gdi32.SelectObject(memory_dc, bitmap)
        if not previous:
            return False
        printed = user32.PrintWindow(hwnd, memory_dc, flags)
        # GetDIBits and DeleteObject require the bitmap to be deselected.
        gdi32.SelectObject(memory_dc, previous)
        previous = None
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
                        ('biPlanes', wt.WORD), ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                        ('biSizeImage', wt.DWORD), ('biXPelsPerMeter', wt.LONG),
                        ('biYPelsPerMeter', wt.LONG), ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD)]
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32,
                                  0, 0, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        copied = gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(header), 0)
        if not printed or copied != height:
            return False
        with Image.frombuffer('RGB', (width, height), pixels, 'raw', 'BGRX', 0, 1) as output:
            output.save(path)
        return True
    finally:
        if previous and memory_dc:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if window_dc:
            user32.ReleaseDC(hwnd, window_dc)


def is_blank(path: str, inset: int = 14) -> bool:
    """Is the image's interior one solid pure-black colour? PrintWindow run before a window's
    first paint returns the title bar and black where the content will be."""
    from PIL import Image
    with Image.open(path) as image:
        width, height = image.size
        if width <= 2 * inset or height <= 2 * inset + 30:
            return False
        interior = image.convert('RGB').crop((inset, 30 + inset, width - inset, height - inset))
        return interior.getextrema() == ((0, 0), (0, 0), (0, 0))


def capture_painted(window, path: str, ctx, attempts: int = 4) -> bool:
    """capture(), repeated while a framed window has not painted yet (a race seen once at 200 %:
    a dialog came back black)."""
    ok = False
    window.update_idletasks()
    # PrintWindow can copy cached child paint after a resize on a private desktop.
    redraw = ctypes.windll.user32.RedrawWindow
    redraw.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    redraw.restype = wt.BOOL
    redraw(wt.HWND(int(window.wm_frame(), 16)), None, None, 0x585)
    ctypes.windll.gdi32.GdiFlush()
    ctx.pump(0.15)
    for _ in range(attempts):
        ok = capture(int(window.wm_frame(), 16), path)
        if not os.path.exists(path):
            break
        retry = is_blank(path)
        if not retry:
            from PIL import Image
            from tkinter import ttk
            with Image.open(path) as painted:
                retry = bool(low_contrast(window, ttk.Style(window), painted.convert('RGB'),
                                          window_rect(int(window.wm_frame(), 16))[:2]))
        if not retry:
            break
        ctx.pump(0.3)
        redraw(wt.HWND(int(window.wm_frame(), 16)), None, None, 0x585)
    return ok


# --------------------------------------------------------------------------- #
# Reading widgets
# --------------------------------------------------------------------------- #
TEXT_CLASSES = ('Button', 'TButton', 'Label', 'TLabel', 'Checkbutton', 'TCheckbutton',
                'Radiobutton', 'TRadiobutton', 'Menubutton', 'TMenubutton')
SQUEEZE_CLASSES = TEXT_CLASSES + ('Entry', 'TEntry', 'TCombobox')


def descendants(widget, into_text: bool = False) -> Iterator:
    """Every widget under `widget`, depth first. Text widgets' children are embedded windows
    (ruby frames) - content, not layout - so they are skipped unless `into_text`."""
    for child in widget.winfo_children():
        if child.winfo_toplevel() is not widget.winfo_toplevel():
            continue
        yield child
        if into_text or child.winfo_class() != 'Text':
            yield from descendants(child, into_text)


def toplevels_under(widget) -> Iterator:
    """Every Toplevel below `widget` (a dialog may hang off another window, not the root)."""
    import tkinter as tk
    for child in widget.winfo_children():
        if isinstance(child, tk.Toplevel):
            yield child
        yield from toplevels_under(child)


def widget_name(widget) -> str:
    text = None
    try:
        text = widget.cget('text')
    except Exception:
        pass
    label = f" '{str(text)[:30]}'" if text else ''
    return f"{widget.winfo_class()}{label} ({widget.winfo_name()})"


def in_canvas(widget) -> bool:
    """Is the widget inside a Canvas? Scrolled content (Settings pages) runs past the window
    by design - the fold is `scroll_without_scrollbar`'s business, not clipping."""
    parent = widget.master
    while parent is not None:
        if parent.winfo_toplevel() is parent:
            break
        if parent.winfo_class() == 'Canvas':
            return True
        parent = parent.master
    return False


def canvas_clips(widget) -> bool:
    """Is the widget inside a Canvas but not wholly inside its viewport? A scrolled row, half
    shown at the area's edge, is clipped by design - there is nothing to judge in it."""
    parent = widget.master
    while parent is not None:
        if parent.winfo_toplevel() is parent:
            break
        if parent.winfo_class() == 'Canvas':
            left, top = parent.winfo_rootx(), parent.winfo_rooty()
            right, bottom = left + parent.winfo_width(), top + parent.winfo_height()
            return not (widget.winfo_rootx() >= left and widget.winfo_rooty() >= top
                        and widget.winfo_rootx() + widget.winfo_width() <= right
                        and widget.winfo_rooty() + widget.winfo_height() <= bottom)
        parent = parent.master
    return False


def ancestors_mapped(widget) -> bool:
    """Is every ancestor up to the toplevel mapped? (A control on an unselected tab is not
    squeezed, it is merely hidden.)"""
    parent = widget.master
    while parent is not None:
        if parent.winfo_toplevel() is parent:
            return True
        if not parent.winfo_ismapped():
            return False
        parent = parent.master
    return True


def viewport_overflow(window) -> List[Finding]:
    """A scroll viewport must fit its host even when its content is scrollable."""
    found: List[Finding] = []
    for widget in descendants(window):
        host = widget.master
        if (widget.winfo_toplevel() is not window or widget.winfo_class() != 'Canvas'
                or getattr(host, 'canvas', None) is not widget
                or not widget.winfo_ismapped() or not ancestors_mapped(widget)):
            continue
        left, top = host.winfo_rootx(), host.winfo_rooty()
        right, bottom = left + host.winfo_width(), top + host.winfo_height()
        x, y = widget.winfo_rootx(), widget.winfo_rooty()
        if (x < left - 1 or y < top - 1 or x + widget.winfo_width() > right + 1
                or y + widget.winfo_height() > bottom + 1):
            found.append(Finding('viewport_overflow', widget_name(widget),
                f"viewport {widget.winfo_width()}x{widget.winfo_height()} exceeds "
                f"host {host.winfo_width()}x{host.winfo_height()}"))
    return found


def clipped_controls(window) -> List[Finding]:
    """Controls squeezed out of their window or narrower/shorter than they asked to be, and
    controls reaching past the window's client area."""
    found: List[Finding] = []
    client = (window.winfo_rootx(), window.winfo_rooty(),
              window.winfo_rootx() + window.winfo_width(),
              window.winfo_rooty() + window.winfo_height())
    for widget in descendants(window):
        if widget.winfo_toplevel() is not window:
            continue
        if widget.winfo_class() not in SQUEEZE_CLASSES:
            continue
        if widget.winfo_manager() not in ('pack', 'grid', 'place'):
            continue
        if not ancestors_mapped(widget):
            continue
        viewport = widget.master
        while viewport is not None and viewport.winfo_class() != 'Canvas':
            if viewport is window:
                viewport = None
                break
            viewport = viewport.master
        if viewport is not None:
            x, y = widget.winfo_rootx(), widget.winfo_rooty()
            left, top = viewport.winfo_rootx(), viewport.winfo_rooty()
            right, bottom = left + viewport.winfo_width(), top + viewport.winfo_height()
            left, top = max(left, client[0]), max(top, client[1])
            right, bottom = min(right, client[2]), min(bottom, client[3])
            if y < bottom and y + widget.winfo_height() > top and (x < left - 1 or x + widget.winfo_width() > right + 1):
                found.append(Finding('clipped_control', widget_name(widget),
                                     'horizontal content exceeds a vertical-only scroll viewport'))
                continue
        if canvas_clips(widget):
            continue
        if not widget.winfo_ismapped() and in_canvas(widget):
            continue
        if not widget.winfo_ismapped():
            found.append(Finding('clipped_control', widget_name(widget),
                                 'not drawn: the window gave it no room'))
            continue
        short_w = widget.winfo_reqwidth() - widget.winfo_width()
        short_h = widget.winfo_reqheight() - widget.winfo_height()
        if short_w > 1 or short_h > 1:
            found.append(Finding(
                'clipped_control', widget_name(widget),
                f"drawn {widget.winfo_width()}x{widget.winfo_height()} "
                f"of the {widget.winfo_reqwidth()}x{widget.winfo_reqheight()} it asked for"))
            continue
        box = (widget.winfo_rootx(), widget.winfo_rooty(),
               widget.winfo_rootx() + widget.winfo_width(),
               widget.winfo_rooty() + widget.winfo_height())
        if not in_canvas(widget) and not rect_inside(box, client):
            found.append(Finding('clipped_control', widget_name(widget),
                                 f"reaches {box} outside the window's client area {client}"))
    return found


def scroll_without_scrollbar(window) -> List[Finding]:
    """A canvas whose scroll region is larger than what it shows, with no scroll bar drawn
    beside it: the content below the fold is reachable only by the wheel."""
    found: List[Finding] = []
    for widget in descendants(window):
        if widget.winfo_class() != 'Canvas' or not widget.winfo_ismapped():
            continue
        try:
            region = [float(v) for v in str(widget.cget('scrollregion')).split()]
        except (ValueError, Exception):
            continue
        if len(region) != 4:
            continue
        hidden_h = (region[3] - region[1]) - widget.winfo_height()
        hidden_w = (region[2] - region[0]) - widget.winfo_width()
        if hidden_h <= 2 and hidden_w <= 2:
            continue
        siblings = descendants(widget.master) if widget.master is not None else ()
        if not any(w.winfo_class() in ('Scrollbar', 'TScrollbar') and w.winfo_ismapped()
                   for w in siblings):
            found.append(Finding('scroll_without_scrollbar', widget_name(widget),
                                 f"{max(0, round(hidden_h))} px below and "
                                 f"{max(0, round(hidden_w))} px beside the fold, no scroll bar"))
    return found


def tiny_lists(window) -> List[Finding]:
    """A listbox of many items shown through a window of two or three rows."""
    found: List[Finding] = []
    for widget in descendants(window):
        if widget.winfo_class() != 'Listbox' or not widget.winfo_ismapped():
            continue
        try:
            rows, items = int(widget.cget('height')), widget.size()
        except Exception:
            continue
        if rows <= 3 and items >= 10:
            found.append(Finding('tiny_list', widget_name(widget),
                                 f"{rows} of {items} items visible"))
    return found


def tiny_text_boxes(window, root, ignore=()) -> List[Finding]:
    """A text box too short to show two lines of its own font: the window gave it what was
    left over (TODO.md V11: the Translator's translation box at 150 %). `ignore` names boxes
    that are one line by design (the Reading pane's placeholder)."""
    from tkinter import font as tkfont
    found: List[Finding] = []
    for widget in descendants(window):
        if (widget.winfo_class() != 'Text' or not widget.winfo_ismapped()
                or widget in ignore):
            continue
        try:
            line = tkfont.Font(root=root, font=str(widget.cget('font'))).metrics('linespace')
            pad = 2 * (int(widget.cget('pady')) + int(widget.cget('borderwidth'))
                       + int(widget.cget('highlightthickness')))
        except Exception:
            continue
        if getattr(widget, 'fits_to_content', False):
            try:
                result = widget.count('1.0', 'end', 'update', 'ypixels')
                needed = int(result[0] if isinstance(result, tuple) else result or 0) + pad
                if widget.winfo_height() + 1 >= needed:
                    continue
            except Exception:
                pass
        if widget.winfo_height() < 2 * line + pad:
            found.append(Finding('tiny_text_box', widget_name(widget),
                                 f"{widget.winfo_height()} px high, a line is {line} px"))
    return found


def _rgb(widget, colour: str) -> Optional[Tuple[int, int, int]]:
    try:
        r, g, b = widget.winfo_rgb(colour)
    except Exception:
        return None
    return r // 257, g // 257, b // 257


def _ttk_option(style, widget, option: str) -> str:
    """A ttk widget's colour or font: its own option, else its style's."""
    try:
        own = str(widget.cget(option))
    except Exception:
        own = ''
    if own:
        return own
    name = ''
    try:
        name = str(widget.cget('style'))
    except Exception:
        pass
    return str(style.lookup(name or widget.winfo_class(), option, state=widget.state()) or '')


def widget_colours(widget, style) -> Optional[Tuple[Tuple[int, int, int], Tuple[int, int, int]]]:
    """(text colour, background) of a text-carrying widget, as the live widget holds them -
    after ttkbootstrap has had its say. None when either cannot be read."""
    cls = widget.winfo_class()
    if cls.startswith('T'):
        fg = _ttk_option(style, widget, 'foreground')
        bg = _ttk_option(style, widget, 'background')
        node = widget
        while not bg and node.master is not None:          # a ttk label takes its parent's
            node = node.master
            try:
                bg = str(node.cget('bg')) if not node.winfo_class().startswith('T') \
                    else _ttk_option(style, node, 'background')
            except Exception:
                bg = ''
    else:
        try:
            fg, bg = str(widget.cget('fg')), str(widget.cget('bg'))
        except Exception:
            return None
    rgb_fg, rgb_bg = (_rgb(widget, fg) if fg else None), (_rgb(widget, bg) if bg else None)
    return (rgb_fg, rgb_bg) if rgb_fg and rgb_bg else None


def pixel_background(image, widget, origin) -> Optional[Tuple[int, int, int]]:
    """The colour actually painted behind a widget's text: the most common colour in its rectangle
    on the capture (text is a minority of the pixels; a rounded image-element button has no
    `background` option that says what it looks like, the picture does). A check or switch is read
    to the right of its indicator. `origin` is the window's outer top-left on screen."""
    if image is None:
        return None
    left = widget.winfo_rootx() - origin[0]
    top = widget.winfo_rooty() - origin[1]
    width, height = widget.winfo_width(), widget.winfo_height()
    if widget.winfo_class() in ('TCheckbutton', 'Checkbutton', 'TRadiobutton', 'Radiobutton'):
        left, width = left + width * 2 // 5, width - width * 2 // 5
    box = (max(0, left), max(0, top), min(image.width, left + width), min(image.height, top + height))
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    regions = [box]
    for a, b, c, d in overlay_rectangles(widget):
        a, b, c, d = a - origin[0], b - origin[1], c - origin[0], d - origin[1]
        visible = []
        for l, t, r, bottom in regions:
            il, it, ir, ib = max(l, a), max(t, b), min(r, c), min(bottom, d)
            if il >= ir or it >= ib:
                visible.append((l, t, r, bottom))
                continue
            visible.extend(rect for rect in ((l, t, r, it), (l, ib, r, bottom),
                                            (l, it, il, ib), (ir, it, r, ib))
                           if rect[0] < rect[2] and rect[1] < rect[3])
        regions = visible
    colours = {}
    for region in regions:
        for count, colour in image.crop(region).getcolors(maxcolors=1 << 16) or ():
            rgb = tuple(colour[:3])
            colours[rgb] = colours.get(rgb, 0) + count
    return max(colours, key=colours.get) if colours else None


def is_disabled(widget) -> bool:
    try:
        if str(widget.cget('state')) == 'disabled':
            return True
    except Exception:
        pass
    try:
        return bool(widget.instate(['disabled']))
    except Exception:
        return False


def overlay_rectangles(widget):
    """Screen bounds of sibling panels painted over this widget's branch."""
    rectangles = []
    node = widget
    while node.master is not None:
        parent = node.master
        for sibling in parent.winfo_children():
            if sibling is node or not getattr(sibling, 'occludes_underlay', False) or not sibling.winfo_ismapped():
                continue
            left, top = sibling.winfo_rootx(), sibling.winfo_rooty()
            rectangles.append((left, top, left + sibling.winfo_width(), top + sibling.winfo_height()))
        node = parent
    return rectangles


def covered_by_overlay(widget, partial=False) -> bool:
    """A fully occluded underlay has no painted foreground in this capture."""
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    right, bottom = x + widget.winfo_width(), y + widget.winfo_height()
    for left, top, r, b in overlay_rectangles(widget):
        inside = left <= x and top <= y and right <= r and bottom <= b
        overlaps = x < r and left < right and y < b and top < bottom
        if inside or partial and overlaps:
            return True
    return False


def low_contrast(window, style, image=None, origin=(0, 0)) -> List[Finding]:
    """Text-carrying widgets whose text is under 4.5:1 against its background - the background
    sampled from `image` (the capture) when there is one, else read from the widget or its style.
    A disabled widget is exempt (WCAG), as is a widget with no text."""
    found: List[Finding] = []
    for widget in descendants(window):
        if (widget.winfo_class() not in TEXT_CLASSES or not widget.winfo_ismapped()
                or not ancestors_mapped(widget)):
            continue
        if canvas_clips(widget) or covered_by_overlay(widget):
            continue
        try:
            text = str(widget.cget('text')).strip()
        except Exception:
            continue
        if not text or is_disabled(widget):
            continue
        colours = widget_colours(widget, style)
        if colours is None:
            continue
        painted = pixel_background(image, widget, origin)
        ratio = contrast_ratio(colours[0], painted if painted else colours[1])
        if ratio < MIN_TEXT_CONTRAST:
            found.append(Finding('low_contrast', widget_name(widget), f"{ratio:.2f}:1"))
    return found


def small_fonts(window, scale_percent: int, root) -> List[Finding]:
    """Text set smaller than 12 px at 100 % (at 150 % the same point size is 18 px, so the
    size is compared at the scale-independent value)."""
    from tkinter import font as tkfont
    found: List[Finding] = []
    per_point = root.winfo_fpixels('1p')
    for widget in descendants(window):
        if widget.winfo_class() not in TEXT_CLASSES or not widget.winfo_ismapped():
            continue
        try:
            if not str(widget.cget('text')).strip():
                continue
            spec = str(widget.cget('font')) or 'TkDefaultFont'
            size = tkfont.Font(root=root, font=spec).actual('size')
        except Exception:
            continue
        dip = font_px(size, per_point) * 100.0 / scale_percent
        if dip < MIN_FONT_PX - 0.5:
            found.append(Finding('small_font', widget_name(widget), f"{dip:.1f} px at 100 %"))
    return found


def symbols_in_text(window) -> List[Finding]:
    found: List[Finding] = []
    for widget in descendants(window):
        try:
            text = str(widget.cget('text'))
        except Exception:
            continue
        if SYMBOLS.search(text):
            found.append(Finding('symbol_in_text', widget_name(widget), text[:40]))
    return found


# --------------------------------------------------------------------------- #
# Measuring one window
# --------------------------------------------------------------------------- #
def window_metrics(window, root, scale_percent: int, style, ignore_tiny=(),
                   dark_expected: bool = True, image=None) -> dict:
    """Everything the gallery records about one window (all findings included)."""
    hwnd = int(window.wm_frame(), 16)
    left, top, right, bottom = window_rect(hwnd)
    width, height = right - left, bottom - top
    area = work_area()
    area_w, area_h = area[2] - area[0], area[3] - area[1]
    framed = not window.overrideredirect()
    findings: List[Finding] = []
    # Only a framed window is sized by its own code; a popup, a toast and the screenshot
    # overlay place themselves (and the overlay is meant to cover every monitor).
    if framed and not fits_work_area(width, height, area_w, area_h):
        findings.append(Finding('outside_work_area', f"{window.winfo_class()} {width}x{height}",
                                f"the work area is {area_w}x{area_h}"))
    # A window that sizes itself from the work area (the Translator, the Expanded view) is judged
    # by its rule - `translator_layout` has the small-laptop test - not by this screen's pixels.
    if (framed and not getattr(window, 'sizes_from_work_area', False)
            and not fits_work_area(width, height, SMALL_LAPTOP[1], SMALL_LAPTOP[2])):
        findings.append(Finding('outside_work_area', f"{window.winfo_class()} {width}x{height}",
                                f"{SMALL_LAPTOP[0]} work area is "
                                f"{SMALL_LAPTOP[1]}x{SMALL_LAPTOP[2]}"))
    icon = dark = None
    if framed:
        icon, dark = has_window_icon(hwnd), dark_title_bar(hwnd)
        if not icon:
            findings.append(Finding('no_window_icon', str(window.title()),
                                    "Tk's feather is shown in the title bar"))
        if dark is False and dark_expected:
            findings.append(Finding('light_title_bar', str(window.title()),
                                    "DWM immersive dark mode is off"))
    findings += viewport_overflow(window)
    findings += clipped_controls(window)
    findings += scroll_without_scrollbar(window)
    findings += tiny_lists(window)
    if framed:
        # A popup measures its boxes to their content (Decision 34): one line is the right
        # height for one line. Only a window sized by its own code can squeeze a box (V11).
        findings += tiny_text_boxes(window, root, ignore_tiny)
    findings += low_contrast(window, style, image, (left, top))
    findings += small_fonts(window, scale_percent, root)
    findings += symbols_in_text(window)
    return {'outer': [left, top, width, height],
            'client': [window.winfo_width(), window.winfo_height()],
            'framed': framed, 'title': str(window.title()) if framed else None,
            'has_icon': icon, 'dark_title_bar': dark,
            'findings': [f.__dict__ for f in findings]}


# --------------------------------------------------------------------------- #
# Surfaces
# --------------------------------------------------------------------------- #
class Context:
    """What a surface builder is handed: the live app and a few helpers."""

    def __init__(self, app, root, scale_percent: int):
        self.app, self.root, self.scale = app, root, scale_percent
        self.extra: List[dict] = []              # findings a builder adds by hand
        self.ignore_tiny: set = set()            # text boxes that are one line by design
        manager = getattr(app, 'theme_manager', None)
        self.dark = bool(manager.is_dark) if manager is not None else True

    def pump(self, seconds: float = 0.35) -> None:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            # update() drains until the queue is empty; a running progress animation
            # can keep that queue nonempty forever. Match mainloop's one-event dispatch.
            if not self.root.tk.dooneevent(_tkinter.DONT_WAIT):
                time.sleep(0.005)

    def settle_settings(self) -> None:
        """Wait for mapping and stable geometry, rather than capture an intermediate native layout."""
        settings = getattr(self.app, 'settings_window', None)
        if settings is None or settings.current not in settings.pages:
            return
        page = settings.pages[settings.current]
        area = settings._areas.get(settings.current)
        state = settings.current
        results = getattr(settings, '_results', None)
        searching = results is not None and bool(results.winfo_manager())
        if searching:
            area, state = results, 'search-results'
        previous, stable = None, 0
        deadline = time.perf_counter() + 3
        while time.perf_counter() < deadline:
            self.pump(0.05)
            widgets = []
            if searching:
                pending = list(area.content.winfo_children())
                while pending:
                    widget = pending.pop()
                    if widget.winfo_manager():
                        widgets.append(widget)
                        pending.extend(widget.winfo_children())
            else:
                for row in page.rows.values():
                    if not row.visible:
                        continue
                    widgets.extend(widget for widget in (row, row.title_label, row.description_label)
                                   if widget is not None)
                    if row.control.winfo_children():
                        widgets.append(row.control)
            if area is not None:
                widgets.extend((area.canvas, area.content))
            geometry = tuple((widget.winfo_width(), widget.winfo_reqwidth(), widget.winfo_height(),
                              bool(widget.winfo_ismapped())) for widget in widgets)
            ready = all(item[3] for item in geometry)
            if area is not None:
                ready = ready and area._layout_job is None and area._bar_job is None
                left, top = area.winfo_rootx(), area.winfo_rooty()
                right, bottom = left + area.winfo_width(), top + area.winfo_height()
                viewports = [area.canvas]
                scrollbar = getattr(area, 'scrollbar', None)
                if scrollbar is not None and scrollbar.winfo_ismapped():
                    viewports.append(scrollbar)
                ready = ready and all(left - 1 <= widget.winfo_rootx()
                    and widget.winfo_rootx() + widget.winfo_width() <= right + 1
                    and top - 1 <= widget.winfo_rooty()
                    and widget.winfo_rooty() + widget.winfo_height() <= bottom + 1 for widget in viewports)
                geometry += ((area.canvas.winfo_width(), area.content.winfo_width(),
                              area.content.winfo_height(), area.canvas.winfo_height()),)
            stable = stable + 1 if ready and geometry == previous else 0
            if stable >= 2:
                return
            previous = geometry
        self.add_finding('settings_layout', state, 'unsettled_layout', state,
                         'mapping or geometry did not stabilize within three seconds')

    def new_windows(self, before: set) -> list:
        return [w for w in toplevels_under(self.root) if w not in before and w.winfo_exists()]

    def toplevels(self) -> set:
        return set(toplevels_under(self.root))

    def add_finding(self, surface: str, state: str, kind: str, widget: str, detail: str) -> None:
        self.extra.append({'surface': surface, 'state': state, 'scale': self.scale,
                           'kind': kind, 'widget': widget, 'detail': detail})


Shots = Iterator[Tuple[str, object]]          # (state, window) pairs a builder yields


def surface_translator(ctx: Context) -> Shots:
    app = ctx.app

    def show(original, translated, language):
        app.show_popup(original, translated, language, force_new=True)
        app._refresh_reading_pane()  # Capture settled readings; debounce has separate regressions.

    show("", "", "English")
    yield 'empty', app.popup
    show(JP_SRC, EN_TR, "English")
    yield 'japanese', app.popup
    show("Good morning! How are you today?", "おはようございます！今日はお元気ですか？", "Japanese")
    yield 'latin', app.popup
    app._start_translate_animation()
    ctx.pump(0.2)
    yield 'loading', app.popup
    app._update_translation_with_original(ERROR_TR)
    yield 'error', app.popup

    # The language picker open, over the Translator
    show(JP_SRC, EN_TR, "English")
    picker = app._view.picker
    picker.open()
    ctx.pump(0.3)
    yield 'picker', picker.window
    picker.close()

    # More options open, a tone chosen
    from src.core import tone_prompts
    app._view.show_options(True)
    app._view.custom_prompt.set(tone_prompts.toggle('', 'formal'))
    app._view._sync_tones()
    yield 'options', app.popup
    app._view.show_options(False)
    yield 'options-closed', app.popup

    # An explicit, typed source and its full searchable language selector.
    app._view.source_chip.set('Japanese')
    app._source_selection_changed('Japanese')
    app._refresh_reading_pane()
    ctx.pump(0.3)
    yield 'inline-readings-explicit-source', app.popup
    app._view.source_chip.open()
    app._view.source_chip.query.set('French')
    ctx.pump(0.3)
    yield 'source-language-picker', app._view.source_chip.window
    app._view.source_chip.close()

    # Attachments: the strip, with an image and a document in it
    import tempfile
    from PIL import Image
    scratch = tempfile.mkdtemp(prefix='crosstrans-gallery-')
    image_path, text_path = os.path.join(scratch, 'menu.png'), os.path.join(scratch, 'notes.txt')
    Image.new('RGB', (64, 48), (90, 140, 200)).save(image_path)
    with open(text_path, 'w', encoding='utf-8') as handle:
        handle.write('Meeting notes')
    app.config.set('vision_enabled', True)
    app.config.set('file_processing_enabled', True)
    app.config.has_any_vision_capable = lambda: True
    app.config.has_any_file_capable = lambda: True
    show(JP_SRC, EN_TR, "English")
    app.attachment_area.add_file(image_path)
    app.attachment_area.add_file(text_path)
    yield 'attachments', app.popup

    # A narrow window stacks the panes
    show(JP_SRC, EN_TR, "English")
    app.popup.geometry('700x640')
    ctx.pump(0.3)
    yield 'stacked', app.popup


GALLERY_KEYS = [
    {'model_name': 'gemini-2.5-flash', 'api_key': 'AIza-gallery-key-0001', 'provider': 'Google',
     'vision_capable': True, 'file_capable': True},
    {'model_name': '', 'api_key': 'gsk_gallery-key-0002', 'provider': 'Groq',
     'vision_capable': False, 'file_capable': False},
    {'model_name': '', 'api_key': '', 'provider': 'Auto'},
]


def surface_settings(ctx: Context) -> Shots:
    """Every page of the Settings window, then the states a page can be in (TODO.md U8)."""
    from src.core import settings_index
    app = ctx.app
    app.show_settings()
    ctx.pump(0.6)
    sw = app.settings_window
    for page in settings_index.PAGES:
        sw.open_page(page.key)
        ctx.pump(0.6)
        if page.key == 'dictionary':                    # the page asks the pack manager on a worker thread
            waited = 0
            while not sw.pages['dictionary'].scanned and waited < 600:
                ctx.pump(0.1)
                waited += 1
        yield page.key, app.popup
        if page.key == 'translation':
            app.config.set_linked_reading_enabled(False)
            sw.pages['translation'].refresh()
            yield 'translation-linked-off', app.popup
            app.config.set_linked_reading_enabled(True)
            sw.pages['translation'].refresh()
        if page.key == 'shortcuts':
            editor = next(iter(sw.pages['shortcuts'].editors.values()))
            dialog = editor.confirm_clear()
            if dialog is not None:
                yield 'shortcuts-confirm-clear', dialog.window
                dialog.cancel()
        if page.key == 'about':
            from src.utils.github_updates import TOKEN_REQUIRED
            sw.update_status_label.configure(text='Error: ' + TOKEN_REQUIRED)
            sw.reveal('about', sw.update_status_label)
            ctx.pump(0.2)
            yield 'about-update-access', app.popup
            sw.update_status_label.configure(text='')
            sw.reveal('about', sw.pages['about'].update_token)
            ctx.pump(0.2)
            yield 'about-private-token', app.popup
            sw.reveal('about', sw.pages['about'].buttons['diagnostics'])
            ctx.pump(0.2)
            yield 'about-diagnostics', app.popup
    sw.open_page('general')
    sw.search.insert(0, 'key')
    sw._run_search()
    ctx.pump(0.4)
    yield 'search-results', app.popup
    sw.search.clear()
    sw._run_search()
    # AI providers: keys with what their tests found, the model list, the delete confirmation
    app.config.set_api_keys(GALLERY_KEYS)
    sw.rebuild_page('providers')
    sw.open_page('providers')
    ctx.pump(0.6)
    yield 'providers-keys', app.popup
    page = sw.pages['providers']
    cards = page.cards
    now = time.time()
    page.usage.apply(dict(id=cards[0].key_id, kind='quota', at=now - 60, seconds=0))
    page.usage.apply(dict(id=cards[1].key_id, kind='success', at=now, seconds=1.8))
    page.refresh_usage(now)
    sw.reveal('providers', cards[1])
    ctx.pump(0.4)
    yield 'providers-last-used', app.popup
    sw.reveal('providers', cards[1].field)
    ctx.pump(0.3)
    yield 'providers-last-used-fields', app.popup
    page.refresh_usage(now + 86400)
    ctx.pump(0.15)
    yield 'providers-expired-status', app.popup
    app.config.set('key_usage', {})
    page.refresh_usage()
    sw.reveal('providers', cards[0])
    cards[0].set_status('Works with gemini-2.5-flash. It can read images and files.', 'success')
    page.usage.apply(dict(id=cards[0].key_id, kind='test_success', at=now, seconds=0))
    page.refresh_usage(now)
    cards[1].set_status('None of the 2 models answered. The provider did not accept the key. '
                        'Check that it was copied whole and is for this provider.', 'danger')
    cards[0].set_revealed(True)
    ctx.pump(0.4)
    yield 'providers-tested', app.popup
    cards[0].set_revealed(False)
    for state, query in (('providers-models', ''), ('providers-model-search', 'flash')):
        before = ctx.toplevels()
        cards[0].model.open()
        if query:
            cards[0].model.search.set(query)
        ctx.pump(0.4)
        for window in ctx.new_windows(before):
            yield state, window
        cards[0].model.close()
    before = ctx.toplevels()
    sw.pages['providers'].confirm_delete_all()
    ctx.pump(0.6)
    for window in ctx.new_windows(before):
        yield 'providers-delete-all', window
        window.destroy()
    # Eight fixture rows and a real drag preview/commit, without touching actual credentials.
    app.config.set_api_keys([dict(api_key=f'fixture-row-{index}', provider='Google',
                                model_name='fixture-model') for index in range(8)])
    sw.rebuild_page('providers')
    sw.open_page('providers')
    page = sw.pages['providers']
    ctx.pump(0.4)
    yield 'providers-eight-rows', app.popup
    page._begin_drag(page.cards[-1])
    page._drag_target = 0
    page._show_drop_marker()
    sw.reveal('providers', page._drop_marker)
    page._show_drag_ghost(app.popup.winfo_rootx() + 60, app.popup.winfo_rooty() + 160)
    ctx.pump(0.3)
    yield 'providers-drag-preview', app.popup
    yield 'providers-drag-card', page._drag_ghost
    page._finish_drag(True)
    sw.reveal('providers', page.cards[0])
    ctx.pump(0.3)
    yield 'providers-reordered', app.popup

    # Dictionary while a pack is being installed, and its questions (nothing is installed for real)
    sw.open_page('dictionary')
    page = sw.pages['dictionary']
    page._show_progress('Installing Japanese (1 of 3)...')
    page.progress_detail.configure(text='Installing unidic-lite')
    page.show_notice('success', 'German is installed.')
    ctx.pump(0.4)
    yield 'dictionary-working', app.popup
    page._hide_progress()
    page.clear_notice()
    before = ctx.toplevels()
    page.confirm_install_all()
    ctx.pump(0.6)
    for window in ctx.new_windows(before):
        yield 'dictionary-install-all', window
        window.destroy()


def surface_history(ctx: Context) -> Shots:
    app = ctx.app
    manager = app.translation_service.history_manager
    app.show_popup("", "", "English", force_new=True)        # History hangs off the Translator
    dialog = app._view.workspace.select_home('history')
    ctx.pump(0.8)
    yield 'empty', app.popup
    for source, translated, language in [("Hello world", "こんにちは世界", "Japanese"),
                                         (JP_SRC, EN_TR, "English"),
                                         ("Good morning", "Bonjour", "French")]:
        manager.add_entry(source, translated, language)
    history = app.config.get('history', [])
    for entry in history:                                     # the clock must not show in a diff
        entry['timestamp'] = FIXED_HISTORY_TIME
    app.config.set('history', history)
    dialog = app._view.workspace.select_home('history')
    ctx.pump(0.8)
    yield 'filled', app.popup
    dialog.select_item(history[0])
    yield 'detail', app.popup
    dialog.show_list()

    row = dialog.scrollable_frame.winfo_children()[0]
    dialog._show_actions(row)
    yield 'hover-actions', app.popup
    dialog.search_entry.set('world')
    ctx.pump(0.3)
    yield 'search', app.popup
    dialog.search_entry.clear()
    dialog.set_pair(dialog.pairs[0])
    ctx.pump(0.3)
    yield 'language-filter', app.popup
    dialog.search_entry.set('no matching translation')
    ctx.pump(0.3)
    yield 'no-results', app.popup
    dialog._clear_filters()
    question = dialog._clear_all()
    ctx.pump(0.3)
    yield 'clear-confirmation', question.window
    question.cancel()


def surface_dictionary(ctx: Context) -> Shots:
    from unittest.mock import patch
    from src.ui.dictionary_mode import WordButtonFrame
    from src.core.nlp_manager import nlp_manager
    app = ctx.app
    flow = app.dictionary_popup
    # A deterministic word split, independent of optional language-pack installations.
    with patch.object(WordButtonFrame, '_tokenize_text',
                      lambda self, text: ['今日', 'は', '日本語', 'を', '勉強', 'します', '。']):
        window = flow._open_with_language(JP_SRC, 'Japanese', trusted=True, target_language='English')
    frame, boxes = flow.word_frame, flow.custom_boxes
    ctx.ignore_tiny.add(boxes._boxes[0].text_widget)
    ctx.pump(0.4)
    yield 'picker', window
    frame._toggle_word(0)
    frame._toggle_word(4)
    frame._notify_selection_change()
    boxes.add_word_to_box('日本語')
    boxes._boxes[0].text_widget.insert('end', 'を学ぶ')
    ctx.pump(0.4)
    yield 'phrases', window
    for i in range(4):
        boxes._on_add_clicked(i)
        boxes._boxes[-1].text_widget.insert('end', 'A custom phrase ' + str(i + 2))
        ctx.ignore_tiny.add(boxes._boxes[-1].text_widget)
    ctx.pump(0.3)
    yield 'five-boxes', window
    frame.start_lookup_animation()
    yield 'loading', window
    window.destroy()

    text = ("## [勉強]\n1. **Translation**: study\n2. **Source Language**: Japanese\n"
            "3. **Definition**: the act of studying; learning\n4. **Word Type**: noun, suru-verb\n"
            "5. **Pronunciation**: べんきょう\n6. **Synonyms** (if any): 学習 → learning, 習う → learn\n"
            "7. **Antonyms** (if any): None\n8. **Examples**:\n"
            "   - 毎日日本語を勉強しています。 → I study Japanese every day.")

    def result(state, value, annotate=True):
        with patch.object(app.quick_translate_manager, '_ruby_enabled', lambda: annotate):
            window = app.quick_translate_manager.show_dictionary_result(
                value, 'English', None, ['勉強'], source_lang='Japanese')
        ctx.pump(0.4)
        for box in window.dictionary_view.boxes:
            ctx.ignore_tiny.add(box)                  # individual card values may be a single line
        return state, window

    for state, value, annotate in [('result', text, True), ('result-plain', text, False),
                                  ('multiple-entries', text + '\n---\n' + text.replace('勉強', '会議'), True),
                                  ('unstructured', 'The model returned an explanation without fields. It stays readable here.', False)]:
        state, window = result(state, value, annotate)
        yield state, window
        window.destroy()

    with patch.object(nlp_manager, 'get_installed_languages',
                      lambda: ['English', 'Japanese', 'Chinese (Simplified)']):
        for state, build in [
                ('no-pack', lambda: flow._show_nlp_not_installed_dialog()),
                ('kanji-language', lambda: flow._show_language_selection_dialog('勉強', kanji_only=True)),
                ('missing-pack', lambda: flow._show_language_selection_dialog('한글', 'Korean', True)),
                ('unknown-language', lambda: flow._show_language_selection_dialog('word'))]:
            dialog = build()
            ctx.pump(0.3)
            yield state, dialog.window
            dialog.close()


def _dialog(ctx: Context, key: str, build: Callable[[], object]) -> Shots:
    before = ctx.toplevels()
    build()
    ctx.pump(0.8)
    for index, window in enumerate(ctx.new_windows(before)):
        yield 'dialog' if index == 0 else f'dialog{index}', window


def surface_dialog_api_error(ctx: Context) -> Shots:
    from src.ui import dialogs
    return _dialog(ctx, 'api_error', lambda: dialogs.APIErrorDialog(
        ctx.root, "No working API key found.", lambda: None, lambda tab: None))






def surface_dialog_update_failed(ctx: Context) -> Shots:
    from src.ui import dialogs
    return _dialog(ctx, 'update_failed', lambda: dialogs.UpdateFailedDialog(
        ctx.root, "The new version could not replace the running program.",
        r"C:\temp\CrossTrans_new.exe", r"C:\temp\CrossTrans.exe"))


def surface_dialog_password(ctx: Context) -> Shots:
    from src.core import auth
    return _dialog(ctx, 'password', lambda: auth.PasswordDialog(ctx.root, "Enter Password"))


def surface_dialog_diagnostics(ctx: Context) -> Shots:
    from src.ui.diagnostics_dialog import DiagnosticsDialog
    dlg = DiagnosticsDialog(ctx.root, config=ctx.app.config, shortcut_status={"English": "ok"})
    ctx.pump(0.4)
    yield "dialog", dlg.window
    dlg.view.close()


def surface_home_dictionary(ctx: Context) -> Shots:
    app = ctx.app
    app.show_main_window()
    page = app._view.workspace.select_home('dictionary')
    page.input.insert('1.0', JP_SRC)
    ctx.pump(0.3)
    yield 'input', app.popup
    page.receive_result('## [日本語]\n1. **Translation**: Japanese\n2. **Source Language**: Japanese\n3. **Definition**: The Japanese language.\n4. **Word Type**: noun\n5. **Pronunciation**: にほんご\n6. **Synonyms**: None\n7. **Antonyms**: None\n8. **Examples**: 日本語を学ぶ → Learn Japanese.', 'English', ['日本語'], 'Japanese', JP_SRC)
    ctx.pump(0.3)
    yield 'result', app.popup
    page.show_input()


def surface_home_quick_tools(ctx: Context) -> Shots:
    from unittest.mock import patch
    from src.core.nlp_manager import nlp_manager
    from src.ui.dictionary_mode import WordButtonFrame
    app = ctx.app
    app.show_popup(JP_SRC, EN_TR, 'English', force_new=True)
    app.config.set_linked_reading_enabled(True)
    manager = app.translation_service.history_manager
    manager.add_entry(JP_SRC, EN_TR, 'English')
    entry = manager.get_history()[0]
    entry['timestamp'] = FIXED_HISTORY_TIME
    def resize(dimensions):
        left, top, right, bottom = work_area()
        outer = window_rect(int(app.popup.wm_frame(), 16))
        chrome_w = max(0, outer[2] - outer[0] - app.popup.winfo_width())
        chrome_h = max(0, outer[3] - outer[1] - app.popup.winfo_height())
        minimum = tuple(map(int, app.popup.minsize()))
        width = max(minimum[0], min(round(dimensions[0] * ctx.scale / 100), right - left - chrome_w - 16))
        height = max(minimum[1], min(round(dimensions[1] * ctx.scale / 100), bottom - top - chrome_h - 16))
        app.popup.geometry(f'{width}x{height}{left:+d}{top:+d}')
    for name, dimensions in (('normal', (1120, 760)), ('narrow', (800, 620)), ('wide', (1460, 900))):
        resize(dimensions)
        app._open_history()
        ctx.pump(0.35)
        yield 'history-' + name, app.popup
        app._home_quick_panel.close()
    full = app._view.workspace.select_home('history')
    full.select_item(entry)
    ctx.pump(0.3)
    yield 'history-linked-detail', app.popup
    app._view.workspace.select_home('translate')
    app._view.source_chip.set('Japanese')
    with patch.object(nlp_manager, 'is_installed', lambda language: True), patch.object(
            WordButtonFrame, '_tokenize_text', lambda self, text: ['今日', 'は', '日本語', 'を', '勉強', 'します', '。']):
        page = app._open_home_dictionary()
    ctx.ignore_tiny.add(page.custom_boxes._boxes[0].text_widget)
    page.custom_boxes.add_word_to_box('日本語')
    for name, dimensions in (('normal', (1120, 760)), ('narrow', (800, 620)), ('wide', (1460, 900))):
        resize(dimensions)
        ctx.pump(0.35)
        yield 'dictionary-' + name, app.popup
    resize((800, 620))
    ctx.pump(0.2)
    with patch.object(page.config, 'get_api_keys', return_value=[]):
        page.lookup_words(['日本語'], 'English', 'Japanese', context=page.text())
    ctx.pump(0.3)
    yield 'dictionary-no-key', app.popup
    original = app.theme_manager.mode
    from src.ui.theme.tokens import MODES
    for mode in MODES:
        app.theme_manager.set_mode(mode)
        ctx.pump(0.2)
        yield 'dictionary-live-' + mode, app.popup
    app.theme_manager.set_mode(original)
    app._home_quick_panel.close()
    for name, raw in (('copy', 'No text received after three Ctrl+C attempts.'),
                      ('images', 'Error: HTTP 400: image input not supported'),
                      ('rate-limit', 'Error: HTTP 429: rate_limit_exceeded')):
        app.quick_translate_manager.show(raw, 'English', original='')
        ctx.pump(0.25)
        yield 'error-' + name, app.quick_translate_manager.popup
        app.quick_translate_manager.dismiss()


def surface_welcome(ctx: Context) -> Shots:
    welcome = ctx.app.onboarding
    welcome.show()
    for step in range(5):
        ctx.pump(0.3)
        yield f'step-{step + 1}', welcome.window
        if step < 4:
            welcome.next()
    welcome.skip()


def surface_hello_waiting(ctx: Context) -> Shots:
    from src.core.auth import WaitingDialog
    waiting = WaitingDialog(ctx.root)
    ctx.pump(0.3)
    yield 'waiting', waiting.dialog
    waiting.close()


def surface_update_progress(ctx: Context) -> Shots:
    import threading
    app = ctx.app
    app.show_settings()
    sw = app.settings_window
    sw.open_page('about')
    stop = threading.Event()

    class Download:
        def download(self, progress):
            progress(57)
            stop.wait(10)
            return {'success': False, 'cancelled': True}

    sw.updater = Download()
    sw._start_update_download('2.0.1')
    ctx.pump(0.3)
    try:
        yield 'downloading', sw.progress_win
        sw._cancel_download()
        ctx.pump(0.1)
        yield 'cancelling', sw.progress_win
    finally:
        sw._cancel_download()
        stop.set()
        ctx.pump(0.3)


def surface_update_notice(ctx: Context) -> Shots:
    from src.ui.update_notice import UpdateNotice
    notice = UpdateNotice(ctx.root, "2.0.1", lambda action: None)
    ctx.pump(0.6)
    yield 'offer', notice.window


def surface_toasts(ctx: Context) -> Shots:
    app = ctx.app
    backgrounds = {}
    for kind, message in [('success', "Copied to clipboard!"), ('warning', "No content to translate"),
                          ('error', "Translation failed"), ('info', "Settings saved")]:
        toast = getattr(app.toast, f'show_{kind}')(message)
        ctx.pump(0.5)
        backgrounds[kind] = str(toast.bar.cget('style'))      # the info bar's kind and plane
        yield kind, toast
        toast.destroy()
    toast = app.toast.show_warning_with_action("No vision-capable API found.",
                                               "Open API Key Settings", lambda: None)
    ctx.pump(0.5)
    yield 'warning_action', toast
    toast.destroy()
    if len({backgrounds['success'], backgrounds['warning'], backgrounds['error']}) < 3:
        ctx.add_finding('toasts', 'all', 'toasts_alike', 'success / warning / error',
                        f"backgrounds {backgrounds}")


def surface_expanded(ctx: Context) -> Shots:
    before = ctx.toplevels()
    ctx.app.expanded_window.show(EN_TR * 3, "English", source_language=None)
    ctx.pump(0.8)
    for window in ctx.new_windows(before):
        yield 'view', window


def surface_screenshot_overlay(ctx: Context) -> Shots:
    """The region selector on a synthetic screen: ImageGrab is replaced, the drag is faked."""
    import types
    from PIL import Image, ImageDraw, ImageGrab
    from src.core import screenshot
    width, height = ctx.root.winfo_screenwidth(), ctx.root.winfo_screenheight()
    fake = Image.new('RGB', (width, height), '#d8dde6')
    ImageDraw.Draw(fake).rectangle((80, 80, width - 80, height - 120), fill='#ffffff')
    original, ImageGrab.grab = ImageGrab.grab, lambda *a, **k: fake
    original_bounds = screenshot.get_virtual_screen_bounds
    screenshot.get_virtual_screen_bounds = lambda: (0, 0, width, height)
    try:
        capture_tool = screenshot.ScreenshotCapture(ctx.root)
        capture_tool.capture_region(lambda path: None)
        ctx.pump(0.4)
        yield 'idle', capture_tool.top
        left, top = capture_tool.top.winfo_rootx(), capture_tool.top.winfo_rooty()
        for handler, x, y in [(capture_tool._on_press, left + 300, top + 240),
                              (capture_tool._on_drag, left + 760, top + 520)]:
            handler(types.SimpleNamespace(x_root=x, y_root=y))
        ctx.pump(0.3)
        yield 'selecting', capture_tool.top
        capture_tool._close(call_callback=False)
    finally:
        ImageGrab.grab = original
        screenshot.get_virtual_screen_bounds = original_bounds


def surface_components(ctx: Context) -> Shots:
    """Every component in every state, on the window (left) and on a card (right) - the design
    system's own pages, in the theme the run was asked for. Several small windows rather than one
    big one: at 150-200 % a single page would be taller than the screen and clip by itself."""
    import tkinter as tk
    from src.ui import components as ui
    from src.ui.theme import engine
    root = ctx.root
    ui.get()                                                 # builds the styles if the app did not

    def page(title, fill):
        window = tk.Toplevel(root)
        window.title(title)
        window.geometry('+20+20')                            # natural size: it grows with the scale
        engine.attach(window)
        outer = ui.Frame(window, on='bg', padding=16)
        outer.pack(fill='both', expand=True)
        for plane in ('bg', 'surface'):
            holder = ui.Frame(outer, on='bg') if plane == 'bg' else ui.Card(outer, on='bg')
            holder.pack(side='left', fill='both', expand=True, padx=8, anchor='n')
            ui.Label(holder, f'on the {"window" if plane == "bg" else "card"}', kind='subtitle').pack(anchor='w')
            fill(holder)
        ctx.pump(0.5)
        return window

    def buttons(col):
        for kind in ('accent', 'soft', 'subtle', 'danger'):
            row = ui.Frame(col)
            row.pack(anchor='w', pady=3)
            for label, flags in (('Normal', []), ('Hover', ['active']), ('Pressed', ['pressed']),
                                 ('Focus', ['focus']), ('Off', ['disabled'])):
                button = ui.Button(row, f'{label}', kind=kind)
                button.pack(side='left', padx=(0, 6))
                if flags:
                    button.state(flags)
        row = ui.Frame(col)
        row.pack(anchor='w', pady=4)
        ui.Button(row, 'With icon', kind='accent', icon='translate').pack(side='left', padx=(0, 6))
        ui.Button(row, 'Copy', kind='soft', icon='clipboard').pack(side='left', padx=(0, 6))
        for icon in ('gear', 'clock-history', 'arrow-repeat', 'box-arrow-up-right'):
            ui.IconButton(row, icon, label=icon).pack(side='left', padx=2)
        off = ui.IconButton(row, 'trash')
        off.pack(side='left', padx=2)
        off.state(['disabled'])
        row = ui.Frame(col)
        row.pack(anchor='w', pady=6)
        ui.Chip(row, 'Japanese  \u2192  English').pack(side='left', padx=(0, 6))
        ui.Chip(row, 'Auto-detect', tone='tag').pack(side='left', padx=(0, 6))
        var = tk.StringVar(master=root, value='b')
        row = ui.Frame(col)
        row.pack(anchor='w', pady=4)
        for value, text in (('a', 'Formal'), ('b', 'Casual'), ('c', 'Concise')):
            ui.Choice(row, text, var, value).pack(side='left', padx=(0, 6))
        row = ui.Frame(col)
        row.pack(anchor='w', pady=4)
        ui.SegmentedControl(row, [('ja', 'Japanese'), ('zh', 'Chinese')], value='ja').pack()

    def fields(col):
        for text, setup in (('', None), ('Bonjour tout le monde', None), ('focused', 'focus'),
                            ('invalid', 'invalid'), ('disabled', 'disabled')):
            field = ui.TextField(col, placeholder='Search language...' if not text else '')
            if text:
                field.set(text)
            field.pack(fill='x', pady=3)
            if setup:
                field.state([setup])
        row = ui.Frame(col)
        row.pack(anchor='w', pady=6)
        for label, value, flags in (('Off', False, []), ('On', True, []), ('Focus', True, ['focus']),
                                    ('Disabled', True, ['disabled'])):
            switch = ui.Switch(row, label, tk.BooleanVar(master=root, value=value))
            switch.pack(side='left', padx=(0, 12))
            if flags:
                switch.state(flags)
        ui.Progress(col, value=60).pack(anchor='w', pady=6)
        ui.Separator(col).pack(fill='x', pady=6)

    def text(col):
        for kind in ('title', 'subtitle', 'strong', 'body', 'muted', 'reading', 'mono'):
            ui.Label(col, f'{kind}: The quick brown fox', kind=kind).pack(anchor='w')
        ui.Label(col, '\u306f\u3058\u3081\u307e\u3057\u3066 (Japanese title)', kind='title',
                 script='japanese').pack(anchor='w')

    def feedback(col):
        for kind, title, message, action in (
                ('info', 'Linked reading is on', 'Matching sentences light up in both texts.', ''),
                ('success', '', 'Copied to the clipboard.', ''),
                ('warning', 'Free translations are almost used up',
                 'Three left today - they come back at midnight.', 'Add a key'),
                ('danger', 'Your API key was rejected', 'It may have expired or has no quota left.',
                 'Open AI providers')):
            ui.InfoBar(col, message, kind=kind, title=title, action=action, closable=kind != 'success',
                       on_action=lambda: None).pack(fill='x', pady=4)
        ui.Keys(col, ['Win', 'Alt', 'E']).pack(anchor='w', pady=10)

    def navigation(col):
        nav = ui.NavigationView(col, [('general', 'General', 'sliders'),
                                      ('translation', 'Translation', 'translate'),
                                      ('shortcuts', 'Shortcuts', 'keyboard'), ('ai', 'AI providers', 'key')],
                                value='translation')
        nav.pack(anchor='w', fill='x', pady=6)
        nav.buttons['shortcuts'].state(['focus'])
        ui.EmptyState(col, 'Your translations will appear here', 'Translate something and it is kept here.',
                      icon='clock-history', action='Open the Translator').pack(pady=10)
        area = ui.ScrollArea(col)
        area.pack(fill='x')
        for n in range(8):
            ui.Label(area.content, f'A scrolling row {n + 1}').pack(anchor='w')
        area.adopt()
        area.canvas.configure(height=ui.get().px(70))

    for state, title, fill in (('buttons', 'Components - buttons', buttons),
                               ('fields', 'Components - fields', fields),
                               ('text', 'Components - text', text),
                               ('feedback', 'Components - feedback', feedback),
                               ('navigation', 'Components - navigation', navigation)):
        yield state, page(title, fill)

    dialog = ui.Dialog(root, 'Let\u2019s connect a translation service',
                       'Translation requires your own API key. Add your '
                       'own key - it takes about two minutes.', icon='stars')
    dialog.add_button('Open AI providers', default=True)
    dialog.add_button('Add a key')
    dialog.add_button('How to get a key', kind='subtle')
    dialog.show()
    ctx.pump(0.5)
    yield 'dialog', dialog.window


def surface_theme_switching(ctx: Context) -> Shots:
    """Exercise all published switch images in one live context, then revisit the first theme."""
    app = ctx.app
    original = app.theme_manager.mode
    app.show_settings()
    app.settings_window.open_page('general')
    try:
        for index, mode in enumerate((*available_themes(False), 'pink')):
            app.theme_manager.set_mode(mode)
            ctx.pump(0.25)
            yield f'{index + 1}-{mode}', app.popup
    finally:
        app.theme_manager.set_mode(original)


def surface_followup_responsive(ctx: Context) -> Shots:
    """Every embedded Settings page at supported minimum, normal and wide geometry, then live themes."""
    from src.core import settings_index
    app = ctx.app
    app.show_settings()
    ctx.pump(0.3)
    window, settings = app.popup, app.settings_window
    scale = ctx.scale / 100.0
    left, top, right, bottom = work_area()
    minimum = tuple(map(int, window.minsize()))
    outer = window_rect(int(window.wm_frame(), 16))
    chrome_width = max(0, outer[2] - outer[0] - window.winfo_width())
    chrome_height = max(0, outer[3] - outer[1] - window.winfo_height())
    for state, logical in (('minimum', (640, 560)), ('normal', (1120, 760)), ('wide', (1500, 940))):
        width = max(minimum[0], min(round(logical[0] * scale), right - left - chrome_width - 16))
        height = max(minimum[1], min(round(logical[1] * scale), bottom - top - chrome_height - 16))
        window.geometry(f'{width}x{height}{left:+d}{top:+d}')
        for page in settings_index.PAGES:
            settings.open_page(page.key)
            ctx.pump(0.25)
            yield f'{state}-{page.key}', window
    original = app.theme_manager.mode
    try:
        settings.open_page('providers')
        for index, theme in enumerate(('pink', 'blue', 'yellow', 'green', 'light', 'dark', original), 1):
            app.theme_manager.set_mode(theme)
            ctx.pump(0.2)
            yield f'live-{index}-{theme}', window
    finally:
        app.theme_manager.set_mode(original)


def surface_todo_live_themes(ctx: Context) -> Shots:
    """Retheme already-built affected controls, including the masked drag overlay."""
    app = ctx.app
    app.show_popup(JP_SRC, EN_TR, 'English', force_new=True)
    app.show_settings()
    settings = app.settings_window
    app.config.set_api_keys(GALLERY_KEYS)
    settings.rebuild_page('providers')
    original = app.theme_manager.mode
    try:
        for theme in ('pink', 'blue', 'yellow', 'green', 'light', 'dark', 'system'):
            app.theme_manager.set_mode(theme)
            settings.open_page('providers')
            page = settings.pages['providers']
            now = time.time()
            page.usage.apply(dict(id=page.cards[0].key_id, kind='success', at=now, seconds=1))
            page.cards[1].set_status('The provider did not accept this fixture key.', 'danger')
            page.refresh_usage(now)
            settings.reveal('providers', page.cards[0])
            ctx.pump(.2)
            yield theme + '-providers', app.popup
            page._begin_drag(page.cards[1])
            page._drag_target = 0
            page._show_drop_marker()
            page._show_drag_ghost(app.popup.winfo_rootx() + 70, app.popup.winfo_rooty() + 140)
            ghost = page._drag_ghost
            app.theme_manager.set_mode('dark' if theme != 'dark' else 'pink')
            ctx.pump(.08)
            app.theme_manager.set_mode(theme)
            assert page._drag_ghost is ghost
            ctx.pump(.15)
            yield theme + '-ghost', page._drag_ghost
            page._finish_drag(False)
            settings.open_page('shortcuts')
            ctx.pump(.2)
            yield theme + '-shortcuts', app.popup
            settings.open_page('dictionary')
            deadline = time.perf_counter() + 5
            while not settings.pages['dictionary'].scanned and time.perf_counter() < deadline:
                ctx.pump(.05)
            yield theme + '-packs', app.popup
            app._view.workspace.select_home('translate')
            ctx.pump(.2)
            yield theme + '-translate', app.popup
            app.show_settings()
    finally:
        app.theme_manager.set_mode(original)


def surface_quick_popup(ctx: Context) -> Shots:
    import popup_cases
    import popup_gallery
    manager = ctx.app.quick_translate_manager
    manager._last_mouse_x = ctx.root.winfo_screenwidth() // 2
    manager._last_mouse_y = ctx.root.winfo_screenheight() // 3
    for key in ('A', 'B', 'E', 'G', 'K', 'L', 'B-grammar', 'C-prompt', 'C-replace'):
        popup_cases.show_case(manager, popup_cases.case(key))
        popup_gallery.settle(manager, ctx.root)
        if not manager._layout or 'heights' not in manager._layout:
            ctx.add_finding('quick_popup', key.lower().replace('-', '_'), 'unmeasured_popup',
                            key, 'the watchdog revealed an estimated layout without measured text boxes')
        area = manager._layout['work']
        if manager.popup.winfo_height() > area.height:
            ctx.add_finding('quick_popup', key.lower().replace('-', '_'), 'outside_work_area', key,
                            'popup height exceeds its monitor work area')
        yield key.lower().replace('-', '_'), manager.popup
        manager.close()
    manager.show_loading("Japanese")
    ctx.pump(0.6)
    if manager.popup:
        yield 'loading', manager.popup
        manager.close()


def surface_audio_panel(ctx: Context) -> Shots:
    """The audio panel (Decision 88) in each phase the controller reports; the phases are told to it, nothing
    is recorded."""
    from types import SimpleNamespace
    app = ctx.app
    controller = app.audio_controller
    panel = app._audio_panel()
    pointer = (ctx.root.winfo_screenwidth() // 3, ctx.root.winfo_screenheight() // 4)
    panel.open(pointer)
    ctx.pump(0.5)
    yield 'idle', panel.window
    capture = SimpleNamespace(stop=lambda: None, cancel=lambda: None)
    controller.busy, controller.capture, controller.state = True, capture, 'recording'
    try:
        controller._notify('recording', elapsed=72, limit=180, levels={'PC': .62, 'Microphone': .18})
        ctx.pump(0.3)
        yield 'recording', panel.window
        controller.capture, controller.state = None, 'translating'
        controller._notify('processing', stage='translating', done=1, total=2)
        ctx.pump(0.3)
        yield 'processing', panel.window
        controller.state = 'decoding'
        controller._notify('processing', stage='decoding', done=95, total=600)
        ctx.pump(0.3)
        yield 'reading_file', panel.window
    finally:
        controller.busy, controller.capture, controller.state = False, None, 'idle'
    controller._notify('idle', outcome='error',
                       message='No active audio playback or microphone device was found. Connect speakers, '
                               'headphones, or a microphone, or check Audio devices in Settings > API keys.')
    ctx.pump(0.3)
    yield 'error', panel.window
    controller._notify('idle', outcome='ok', message='')
    panel.close()


def surface_followup_regressions(ctx: Context) -> Shots:
    """Focused recheck of inspected failures, retaining canonical editors and explicit geometry."""
    app = ctx.app
    app.show_popup(JP_SRC, EN_TR, 'English', force_new=True)
    window = app.popup
    ctx.pump(0.2)
    left, top, right, bottom = work_area()
    outer = window_rect(int(window.wm_frame(), 16))
    chrome = (outer[2] - outer[0] - window.winfo_width(), outer[3] - outer[1] - window.winfo_height())
    scale = ctx.scale / 100

    def size(width, height, translation=False):
        width = min(round(width * scale), right - left - chrome[0] - 16)
        height = min(round(height * scale), bottom - top - chrome[1] - 16)
        window.geometry(f'{width}x{height}{left:+d}{top:+d}')
        ctx.pump(0.3)
        deadline = time.perf_counter() + (4 if translation else 0)
        while time.perf_counter() < deadline:
            view = app._view
            settled = (window.winfo_width() == width and window.winfo_height() == height
                       and view._arrange_job is None and view.body_area._layout_job is None
                       and view._last_size == (view.frame.winfo_width(), view.frame.winfo_height()))
            if settled and all(widget.winfo_viewable() for widget in
                               (app.original_text, app.trans_text, view.source_chip.button, view.picker.button)):
                break
            ctx.pump(0.05)

    for index, width in enumerate((1400, 700, 1400, 700)):
        size(width, 700, translation=True)
        for name, widget in (('source', app.original_text), ('translation', app.trans_text),
                             ('source choice', app._view.source_chip.button), ('target choice', app._view.picker.button)):
            if not widget.winfo_viewable():
                ctx.add_finding('followup_regressions', f'resize-{index}', 'hidden_required_control', name,
                                'an editor or language control was not viewable after resize')
        yield f'resize-{index}', window
    size(1200, 780)
    app._update_translation_with_original(ERROR_TR)
    yield 'translation-error', window
    app.show_settings()
    settings = app.settings_window
    settings.open_page('translation')
    app.config.set_linked_reading_enabled(True)
    settings.pages['translation'].refresh()
    yield 'linked-on', window
    app.config.set_linked_reading_enabled(False)
    settings.pages['translation'].refresh()
    yield 'linked-off', window
    settings.open_page('shortcuts')
    editor = next(iter(settings.pages['shortcuts'].editors.values()))
    editor.set_value('')
    size(640, 700)
    yield 'shortcut-cleared-minimum', window
    settings.open_page('dictionary')
    deadline = time.perf_counter() + 5
    while not settings.pages['dictionary'].scanned and time.perf_counter() < deadline:
        ctx.pump(0.05)
    yield 'packs-minimum', window
    settings.open_page('appearance')
    yield 'appearance-minimum', window


def settle_audio_result(ctx):
    quick = ctx.app.quick_translate_manager
    deadline, previous, stable = time.perf_counter() + 3, None, 0
    while time.perf_counter() < deadline:
        ctx.pump(.05)
        layout = quick._layout or {}
        geometry = quick.popup.geometry()
        if layout.get('done') and layout.get('revealed') and geometry == previous:
            stable += 1
            if stable >= 3:
                return
        else:
            stable = 0
        previous = geometry
    raise RuntimeError('Audio result geometry did not settle before capture.')


def surface_audio(ctx: Context) -> Shots:
    """Audio states use fixtures only: no devices, playback, keyboard or provider requests."""
    from src.core.audio_media import AudioFiles
    from src.ui.i18n import tr
    app = ctx.app
    quick = app.quick_translate_manager
    quick.capture_mouse_position()
    quick.show_audio_status('Preparing recording…', 'Japanese')
    yield 'preparing', quick.popup
    quick.update_audio_status(tr('Recording · {input_name} · {time}', input_name=tr('PC + Microphone'), time='00:08'),
                               'Release all shortcut keys to translate', .65)
    yield 'recording', quick.popup
    quick.update_audio_status(tr('Recognizing speech: {done}/{total}', done=1, total=3),
                               'You can cancel while processing.')
    yield 'recognizing', quick.popup
    quick.close()
    quick.show('明日の会議は午後3時からです。', 'Japanese', original='The meeting starts at three tomorrow.',
               model_info='fixture (Groq)', audio_result=True, on_audio_play=lambda: None)
    settle_audio_result(ctx)
    yield 'result', quick.popup
    app.audio_controller.files = AudioFiles()
    app.audio_controller.request_close()
    yield 'save-question', app.audio_controller.dialog.window
    app.audio_controller.dialog.cancel()
    app.show_settings()
    settings = app.settings_window
    settings.open_page('providers')
    page = settings.pages['providers']
    for key in ('audio_language', 'audio_devices'):
        settings.reveal('providers', page.rows[key])
        ctx.pump(.4)
        yield key, app.popup
    settings.open_page('shortcuts')
    settings.reveal('shortcuts', settings.pages['shortcuts'].rows['audio_shortcut'])
    ctx.pump(.4)
    yield 'shortcut', app.popup
    settings.open_page('providers')
    yield 'capabilities', app.popup


def surface_audio_capabilities(ctx: Context) -> Shots:
    app = ctx.app
    app.show_settings()
    app.settings_window.open_page('providers')
    yield 'capabilities', app.popup


def surface_audio_result(ctx: Context) -> Shots:
    quick = ctx.app.quick_translate_manager
    quick.capture_mouse_position()
    quick.show('明日の会議は午後3時からです。', 'Japanese', original='The meeting starts at three tomorrow.',
               model_info='fixture (Groq)', audio_result=True, on_audio_play=lambda: None)
    settle_audio_result(ctx)
    yield 'result', quick.popup
    quick.close()


SURFACES: Dict[str, Callable[[Context], Shots]] = {
    'audio_capabilities': surface_audio_capabilities,
    'audio_result': surface_audio_result,
    'audio': surface_audio,
    'translator': surface_translator,
    'settings': surface_settings,
    'history': surface_history,
    'dictionary': surface_dictionary,
    'home_dictionary': surface_home_dictionary,
    'home_quick_tools': surface_home_quick_tools,
    'dialog.api_error': surface_dialog_api_error,
    'dialog.update_failed': surface_dialog_update_failed,
    'dialog.password': surface_dialog_password,
    'dialog.diagnostics': surface_dialog_diagnostics,
    'dialog.hello_waiting': surface_hello_waiting,
    'welcome': surface_welcome,
    'update_progress': surface_update_progress,
    'update_notice': surface_update_notice,
    'toasts': surface_toasts,
    'expanded': surface_expanded,
    'screenshot_overlay': surface_screenshot_overlay,
    'quick_popup': surface_quick_popup,
    'audio_panel': surface_audio_panel,
    'components': surface_components,
    'theme_switching': surface_theme_switching,
    'followup_responsive': surface_followup_responsive,
    'followup_regressions': surface_followup_regressions,
    'todo_live_themes': surface_todo_live_themes,
}
# `--surfaces dialogs` stands for every dialog.*
GROUPS = {'dialogs': [k for k in SURFACES if k.startswith('dialog.')],
          'product': [k for k in SURFACES if k not in ('components', 'theme_switching')]}


def available_themes(include_system=True):
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from src.ui.theme import tokens
    return tokens.MODES if include_system else tuple(tokens.PALETTES)


def select_surfaces(text: Optional[str]) -> List[str]:
    if not text:
        return list(SURFACES)
    chosen: List[str] = []
    for part in (p.strip() for p in text.split(',') if p.strip()):
        names = GROUPS.get(part) or [part]
        for name in names:
            if name not in SURFACES:
                raise SystemExit(f"ui_gallery: no surface {name!r}; known: "
                                 f"{', '.join(list(SURFACES) + list(GROUPS))}")
            chosen.append(name)
    return chosen


# --------------------------------------------------------------------------- #
# Safety and stubs
# --------------------------------------------------------------------------- #
def require_private_desktop(desktop_name, error) -> None:
    if not desktop_name:
        raise SystemExit(f"ui_gallery: no private desktop ({error}) - refusing to draw on the "
                         f"screen")


def isolate_settings() -> str:
    """APPDATA -> a scratch folder. Must run before `src` is imported (config reads it)."""
    import tempfile
    scratch = tempfile.mkdtemp(prefix='ui-gallery-')
    os.environ['APPDATA'] = scratch
    return scratch


def install_stubs() -> None:
    """Replace what a run must not do: the two background fetches the app starts, and the
    modal calls a dialog may make."""
    import tkinter
    from src.core import remote_config, update_ui_manager, auth
    auth.current_account = lambda: 'TEST-PC\\fixture-user'
    remote_config.RemoteConfigManager.fetch_remote_async = lambda self, force=False: None
    update_ui_manager.UpdateUIManager.startup_update_check = lambda self: None
    for name in ('wait_window', 'grab_set', 'grab_release'):
        setattr(tkinter.Misc, name, lambda self, *a, **k: None)


# --------------------------------------------------------------------------- #
# One scale, in this process
# --------------------------------------------------------------------------- #
def capture_tk_callback_errors(root, errors):
    """A swallowed Tk callback is a failed gallery, even when the captured pixels look correct."""
    previous = root.report_callback_exception
    def report(exc_type, exc_value, exc_tb):
        errors.append({'surface': 'tk_callback',
                       'error': ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))})
        previous(exc_type, exc_value, exc_tb)
    root.report_callback_exception = report
    def background_error(message):
        detail = str(root.tk.call('set', 'errorInfo'))
        errors.append({'surface': 'tcl_background', 'error': detail or str(message)})
        import logging
        logging.error('[Gallery Tcl] %s', detail or message)
    command = root.register(background_error)
    root.tk.call('proc', 'bgerror', 'message', command + ' $message')
    return previous


def run_scale(args, scale_percent: int) -> dict:
    scratch = isolate_settings()
    sys.path[:0] = [REPO, TESTS]
    import quiet_session
    quiet_session.install()
    require_private_desktop(quiet_session.DESKTOP_NAME, quiet_session.DESKTOP_ERROR)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)         # as main.py does
    except Exception:
        pass
    import src.core  # noqa: F401  - before `config` (project CLAUDE.md)
    install_stubs()
    import tkinter
    import ttkbootstrap
    from src.app import TranslatorApp
    # A system-DPI-aware Tk process is given its scaling when the interpreter starts, before any
    # style, font or image exists; emulate that exactly rather than changing it afterwards.
    original_loadtk = tkinter.Tk._loadtk

    def loadtk(self):
        original_loadtk(self)
        self.tk.call('tk', 'scaling', scaling_for(self.winfo_fpixels('1i') / 72.0, scale_percent))
    tkinter.Tk._loadtk = loadtk
    app = TranslatorApp()
    tkinter.Tk._loadtk = original_loadtk
    root = app.root
    errors = []
    capture_tk_callback_errors(root, errors)
    if getattr(app, 'theme_manager', None) is not None and args.theme != 'system':
        app.theme_manager.set_mode(args.theme)
        app.theme_manager.stop_watching()
        root.update()
    from src.ui.theme.accessibility import Preferences
    manager = app.theme_manager
    manager.stop_watching()
    high_contrast = getattr(args, 'high_contrast', False)
    preferences = Preferences(
        high_contrast=high_contrast, animations=not getattr(args, 'reduced_motion', False),
        notification_ms=0,
        colours=('#000000', '#FFFFFF', '#FFFF00', '#000000', '#00FFFF') if high_contrast else ())
    manager._read_accessibility = lambda: preferences
    manager.refresh_accessibility()
    manager.set_ui_language(getattr(args, 'ui_language', 'en'))
    manager.set_text_scale(getattr(args, 'text_scale', 1.0))
    area_override = getattr(args, 'work_area', None)
    if area_override:
        width, height = map(int, area_override.lower().split('x'))
        area = (0, 0, width, height)
        global work_area
        work_area = lambda: area
        from src.ui.components import dialog
        from src.ui import translator_window, expanded_window, history_dialog, welcome, quick_translate
        from src.ui.settings import main as settings_main
        for module in (dialog, translator_window, expanded_window, history_dialog, welcome, settings_main):
            module.work_area = lambda: area
        quick_translate.get_monitor_work_area = lambda *args: area
    root.update()
    style = ttkbootstrap.Style()
    out_dir = os.path.join(OUT_ROOT, args.label, f's{scale_percent}')
    if not args.no_shots:
        os.makedirs(out_dir, exist_ok=True)
    ctx = Context(app, root, scale_percent)
    rows = []
    for name in select_surfaces(args.surfaces):
        started = time.perf_counter()
        before = ctx.toplevels()
        try:
            builder = SURFACES[name](ctx)
            while True:
                try:
                    state, window = next(builder)
                except StopIteration:
                    break
                build_ms = round((time.perf_counter() - started) * 1000)
                if window is None or not window.winfo_exists():
                    raise RuntimeError(f"{name}.{state}: no window")
                ctx.pump(0.25)
                workspace = getattr(getattr(app, '_view', None), 'workspace', None)
                if window is app.popup and workspace is not None and workspace.primary == 'settings':
                    ctx.settle_settings()
                # The picture is taken first: contrast is judged on the pixels, so a rounded
                # image-element button is measured by what it looks like. --no-shots takes it
                # too (to a scratch file) so both modes report the same findings.
                if args.no_shots:
                    picture = os.path.join(scratch, 'capture.png')
                else:
                    picture = os.path.join(out_dir, f'{name}.{state}.png')
                capture_painted(window, picture, ctx)
                reading = getattr(app, '_reading_text', None)
                if reading is not None:
                    ctx.ignore_tiny.add(reading)
                image = None
                try:
                    from PIL import Image
                    with Image.open(picture) as opened:
                        image = opened.convert('RGB')
                except Exception:
                    pass
                row = {'surface': name, 'state': state, 'scale': scale_percent,
                       'build_ms': build_ms, **window_metrics(
                           window, root, scale_percent, style, ctx.ignore_tiny,
                           dark_expected=bool(ctx.app.theme_manager.is_dark), image=image)}
                if window is app.popup and (name == 'translator' or
                                             name == 'followup_regressions' and state.startswith('resize-')):
                    row['editor_geometry'] = []
                    area = app._view.body_area
                    row['body_viewport'] = dict(x=area.canvas.winfo_rootx(), y=area.canvas.winfo_rooty(),
                                               width=area.canvas.winfo_width(), height=area.canvas.winfo_height(),
                                               yview=area.canvas.yview(),
                                               scrollbar=bool(area.scrollbar.winfo_ismapped()))
                    for role, box in (('source', app.original_text), ('translation', app.trans_text)):
                        geometry = dict(role=role, x=box.winfo_rootx(), y=box.winfo_rooty(),
                                        width=box.winfo_width(), height=box.winfo_height(),
                                        mapped=bool(box.winfo_ismapped()), viewable=bool(box.winfo_viewable()))
                        row['editor_geometry'].append(geometry)
                        if not geometry['viewable'] or min(geometry['width'], geometry['height']) < 8:
                            row['findings'].append(vars(Finding('hidden_editor', role, str(geometry))))
                        elif image is not None and not canvas_clips(box):
                            colours = widget_colours(box, style)
                            origin = window_rect(int(window.wm_frame(), 16))[:2]
                            painted = pixel_background(image, box, origin)
                            if colours and painted and max(abs(a - b) for a, b in zip(colours[1], painted)) > 4:
                                row['findings'].append(vars(Finding('unpainted_editor', role,
                                    f'expected editor background {colours[1]}, captured {painted}')))
                if not args.no_shots:
                    row['image'] = report_path(picture, REPO)
                rows.append(row)
                print(summary_line(row), flush=True)
                started = time.perf_counter()
        except Exception:
            errors.append({'surface': name, 'scale': scale_percent,
                           'error': traceback.format_exc()})
            print(f"FAIL {name} @ {scale_percent} %:\n{errors[-1]['error']}", flush=True)
        for window in ctx.new_windows(before):
            with contextlib.suppress(Exception):
                window.destroy()
        with contextlib.suppress(Exception):
            app._stop_translate_animation()
        root.update()
    flat = [{'surface': r['surface'], 'state': r['state'], 'scale': r['scale'], **f}
            for r in rows for f in r['findings']] + ctx.extra
    from src.ui.theme.compat import ttkbootstrap_major
    report = {'scale': scale_percent, 'theme': args.theme, 'ui_language': args.ui_language, 'text_scale': args.text_scale,
              'high_contrast': args.high_contrast, 'reduced_motion': args.reduced_motion,
              'ttkbootstrap_major': ttkbootstrap_major(), 'tk_scaling': float(root.tk.call('tk', 'scaling')),
              'screen': [root.winfo_screenwidth(), root.winfo_screenheight()],
              'work_area': list(work_area()), 'dpi': round(root.winfo_fpixels('1i')),
              'captures': rows, 'extra_findings': ctx.extra, 'findings': flat,
              'errors': errors, 'blocked': [what for _, what in quiet_session.BLOCKED],
              'windows_on_user_desktop': len(quiet_session.our_windows_on('Default'))}
    root.destroy()
    return report


def summary_line(row: dict) -> str:
    kinds = summarise(row['findings'])
    shown = ', '.join(f"{k} {n}" for k, n in kinds.items()) or 'clean'
    name = f"{row['surface']}.{row['state']}"
    return (f"{row['scale']:>3}%  {name:<34} {row['outer'][2]:>4}x{row['outer'][3]:<4} "
            f"{row['build_ms']:>5} ms  {shown}")


# --------------------------------------------------------------------------- #
# The run: one child process per scale
# --------------------------------------------------------------------------- #
def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument('--label', required=True, help="names the output, e.g. before / after")
    parser.add_argument('--scales', default=','.join(map(str, ALL_SCALES)),
                        help="Windows scale factors in percent (default: 100,125,150,200)")
    parser.add_argument('--surfaces', help="comma-separated names or 'dialogs' (default: all)")
    parser.add_argument('--no-shots', action='store_true', help="metrics only, no images")
    parser.add_argument('--strict', action='store_true', help='exit 1 on any layout/contrast finding')
    parser.add_argument('--theme', choices=available_themes(), default='dark',
                        help="the theme the app is shown in (default dark: a gallery must not "
                             "depend on the setting of the machine it runs on)")
    parser.add_argument('--ui-language', choices=('en', 'ja'), default='en')
    parser.add_argument('--text-scale', type=float, choices=(1.0, 1.15, 1.3, 1.5), default=1.0)
    parser.add_argument('--high-contrast', action='store_true', help='simulate native contrast colour pairs')
    parser.add_argument('--reduced-motion', action='store_true')
    parser.add_argument('--work-area', help='simulate a work area, for example 1366x728')
    parser.add_argument('--expect-known-defects', action='store_true',
                        help="exit 1 unless every KNOWN_DEFECTS entry is seen (the before-run)")
    parser.add_argument('--child', type=int, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def git_commit() -> Optional[str]:
    try:
        return subprocess.run(['git', '-C', REPO, 'rev-parse', '--short', 'HEAD'],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return None


def child_main(args) -> int:
    report = run_scale(args, args.child)
    folder = os.path.join(OUT_ROOT, args.label)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f's{args.child}.json'), 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    bad = report['blocked'] or report['windows_on_user_desktop'] or report['errors']
    return 1 if bad else 0


def parent_main(args) -> int:
    scales = parse_scales(args.scales)
    select_surfaces(args.surfaces)                       # fail early on a typo
    folder = os.path.join(OUT_ROOT, args.label)
    os.makedirs(folder, exist_ok=True)
    failed = False
    for scale in scales:
        command = [sys.executable, os.path.abspath(__file__), '--label', args.label,
                   '--theme', args.theme, '--child', str(scale)] + (['--surfaces', args.surfaces] if args.surfaces else []) \
            + ['--ui-language', args.ui_language, '--text-scale', str(args.text_scale)] \
            + (['--high-contrast'] if args.high_contrast else []) \
            + (['--reduced-motion'] if args.reduced_motion else []) \
            + (['--work-area', args.work_area] if args.work_area else []) \
            + (['--no-shots'] if args.no_shots else [])
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        try:
            failed |= subprocess.run(command, env=env, timeout=180).returncode != 0
        except subprocess.TimeoutExpired:
            failed = True
            report = {'scale': scale, 'captures': [], 'findings': [], 'blocked': [],
                      'windows_on_user_desktop': 0,
                      'errors': [{'surface': 'gallery', 'error': 'child timed out after 180 seconds'}]}
            with open(os.path.join(folder, f's{scale}.json'), 'w', encoding='utf-8') as file:
                json.dump(report, file, indent=1)
    reports = []
    for scale in scales:
        path = os.path.join(folder, f's{scale}.json')
        if os.path.exists(path):
            with open(path, encoding='utf-8') as fh:
                reports.append(json.load(fh))
        else:
            failed = True
    findings = [f for r in reports for f in r['findings']]
    known = match_known_defects(findings)
    major = next((r.get('ttkbootstrap_major') for r in reports if r.get('ttkbootstrap_major')), 1)
    fixed = set(ENGINE_FIXED) if major and major >= 2 else set()
    unseen = [k for k, where in known.items() if not where and k not in fixed
              and max(scales) >= NEEDS_SCALE.get(k, 0)]
    returned = [k for k in fixed if known.get(k)]
    total = {'label': args.label, 'commit': git_commit(), 'python': platform.python_version(),
             'scales': scales, 'surfaces': select_surfaces(args.surfaces),
             'captures': sum(len(r['captures']) for r in reports),
             'theme': args.theme, 'ui_language': args.ui_language, 'text_scale': args.text_scale,
             'high_contrast': args.high_contrast, 'reduced_motion': args.reduced_motion,
             'ttkbootstrap_major': major, 'findings_by_kind': summarise(findings),
             'known_defects': known, 'engine_fixed': sorted(fixed),
             'errors': [e for r in reports for e in r['errors']],
             'blocked': [b for r in reports for b in r['blocked']],
             'windows_on_user_desktop': sum(r['windows_on_user_desktop'] for r in reports),
             'reports': [report_path(os.path.join(folder, f's{s}.json'), REPO)
                         for s in scales]}
    out = os.path.join(OUT_ROOT, f'{args.label}.json')
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(total, fh, ensure_ascii=False, indent=1)
    print(f"\n{total['captures']} captures at {scales} % -> {report_path(out, REPO)}")
    print("findings by kind:", total['findings_by_kind'])
    for key, where in known.items():
        if key in fixed:
            mark = 'BACK  ' if where else 'fixed '
        else:
            mark = 'seen  ' if where else 'UNSEEN'
        print(f"  {mark} {key}: {KNOWN_DEFECTS[key][0]}"
              f"{'  (' + ', '.join(where[:3]) + (' ...' if len(where) > 3 else '') + ')' if where else ''}")
    if total['blocked'] or total['windows_on_user_desktop'] or total['errors'] or failed:
        print(f"ui_gallery: FAILED - blocked {total['blocked']}, "
              f"{total['windows_on_user_desktop']} window(s) on the user's desktop, "
              f"{len(total['errors'])} surface error(s)")
        return 1
    if args.strict and findings:
        print(f'ui_gallery: FAILED - {len(findings)} layout/contrast finding(s)')
        return 1
    if args.expect_known_defects and (unseen or returned):
        if unseen:
            print(f"ui_gallery: the known defects {unseen} were not seen - the gallery is blind")
        if returned:
            print(f"ui_gallery: {returned} came back after the engine switch fixed them")
        return 1
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    return child_main(args) if args.child else parent_main(args)


if __name__ == '__main__':
    sys.exit(main())
