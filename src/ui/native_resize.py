"""Native hit testing, outline-only sizing and safe Tk UI bindings.

Windows enters its own sizing loop from non-client input. Never synchronously
start that loop from a Python Tk callback or call Tk from a ctypes procedure:
both bypass _tkinter's Tcl/Python thread-state boundary. Native outline operations
use only Win32; final content work runs from normal Tk callbacks.
"""
import ctypes
from ctypes import wintypes
import logging
import sys
import threading
import tkinter as tk

from src.ui import popup_layout
from src.ui.theme import tokens


WM_SIZE = 0x0005
WM_GETMINMAXINFO = 0x0024
WM_WINDOWPOSCHANGED = 0x0047
WM_WINDOWPOSCHANGING = 0x0046
WM_NCCALCSIZE = 0x0083
WM_NCDESTROY = 0x0082
WM_SYSCOMMAND = 0x0112
WM_SIZING = 0x0214
WM_NCHITTEST = 0x0084
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONDBLCLK = 0x00A3
WM_ENTERSIZEMOVE = 0x0231
WM_EXITSIZEMOVE = 0x0232
SC_SIZE = 0xF000
SC_MOVE = 0xF010
WM_CANCELMODE = 0x001F
WM_CAPTURECHANGED = 0x0215
WM_STYLECHANGING = 0x007C
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
HIT_EDGES = {'w': 10, 'e': 11, 'n': 12, 'nw': 13, 'ne': 14, 's': 15, 'sw': 16, 'se': 17}
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
SWP_FRAMECHANGED = 0x0020
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | SWP_FRAMECHANGED
SIZING_EDGES = {'w': 1, 'e': 2, 'n': 3, 'nw': 4, 'ne': 5, 's': 6, 'sw': 7, 'se': 8}
_INSTALLED = {}


class MinMaxInfo(ctypes.Structure):
    _fields_ = [(name, wintypes.POINT) for name in
                ('reserved', 'max_size', 'max_position', 'min_track', 'max_track')]


class StyleInfo(ctypes.Structure):
    _fields_ = [('old', wintypes.DWORD), ('new', wintypes.DWORD)]


class WindowPosition(ctypes.Structure):
    _fields_ = [('hwnd', wintypes.HWND), ('after', wintypes.HWND),
                ('x', ctypes.c_int), ('y', ctypes.c_int), ('width', ctypes.c_int),
                ('height', ctypes.c_int), ('flags', wintypes.UINT)]


class WindowsSizing:
    """Own wrapper/child hit-test subclasses; dispatch UI work through Tk bindings."""

    def __init__(self, window, *, rect, minimum, bounds, on_rect,
                 on_start=None, on_end=None, on_reset=None, edge_px=6, corner_px=12,
                 noactivate=None, custom_frame=True, outline=False, outline_colour=None,
                 client_edges=False):
        self.window = window
        self.rect, self.minimum = rect, minimum
        self.bounds, self.on_rect = bounds, on_rect
        self.hwnd = None
        self.client_hwnd = None
        self.callback = None
        self.original_style = None
        self.running = False
        self.resizing = False
        self.custom_frame = bool(custom_frame)
        self.client_edges = bool(client_edges)
        self.outline_mode = bool(outline)
        self.outline_colour = outline_colour or tokens.LIGHT.accent
        self.outline = None
        self.cancelled = False
        self._outline_origin = None
        self._outline_apply = False
        self._size_command = False
        self.enabled = False
        self.on_start, self.on_end, self.on_reset = on_start, on_end, on_reset
        self.edge_px, self.corner_px, self.noactivate = edge_px, corner_px, noactivate
        self.edge, self.start_rect, self.pointer = 'se', None, None
        self._pending_rect = self._pending_end = self._pending_reset = False
        self._started = self._close_pending = False
        self.child_hwnds = set()
        self.bindings = []
        self.thread = threading.get_ident()

    def _api(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.common = ctypes.WinDLL('comctl32', use_last_error=True)
        self.proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
        self.common.SetWindowSubclass.argtypes = [wintypes.HWND, self.proc_type,
                                                  ctypes.c_size_t, ctypes.c_size_t]
        self.common.SetWindowSubclass.restype = wintypes.BOOL
        self.common.RemoveWindowSubclass.argtypes = [wintypes.HWND, self.proc_type, ctypes.c_size_t]
        self.common.RemoveWindowSubclass.restype = wintypes.BOOL
        self.common.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT,
                                                wintypes.WPARAM, wintypes.LPARAM]
        self.common.DefSubclassProc.restype = ctypes.c_ssize_t
        self.user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                           wintypes.WPARAM, wintypes.LPARAM]
        self.user.SendMessageW.restype = ctypes.c_ssize_t
        self.user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user.PostMessageW.restype = wintypes.BOOL
        self.user.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
        self.user.InvalidateRect.restype = wintypes.BOOL
        self.user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.GetWindowLongW.restype = wintypes.LONG
        self.user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
        self.user.SetWindowLongW.restype = wintypes.LONG
        self.user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.GetWindowRect.restype = wintypes.BOOL
        self.user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.GetClientRect.restype = wintypes.BOOL
        self.user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user.GetAsyncKeyState.restype = ctypes.c_short
        self.user.WindowFromPoint.argtypes = [wintypes.POINT]
        self.user.WindowFromPoint.restype = wintypes.HWND
        self.user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetAncestor.restype = wintypes.HWND
        self.user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user.SetWindowPos.restype = wintypes.BOOL
        self.user.IsWindow.argtypes = [wintypes.HWND]
        self.user.IsWindow.restype = wintypes.BOOL
        self.user.GetThreadDesktop.argtypes = [wintypes.DWORD]
        self.user.GetThreadDesktop.restype = wintypes.HANDLE
        self.user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.user.OpenInputDesktop.restype = wintypes.HANDLE
        self.user.CloseDesktop.argtypes = [wintypes.HANDLE]
        self.user.CloseDesktop.restype = wintypes.BOOL
        self.user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetUserObjectInformationW.restype = wintypes.BOOL

    def _is_input_desktop(self):
        """Never enter an input loop on a hidden test desktop or a locked session."""
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentThreadId.restype = wintypes.DWORD
        current = self.user.GetThreadDesktop(kernel.GetCurrentThreadId())
        desktop = self.user.OpenInputDesktop(0, False, 0x0001)
        if not desktop:
            return False
        try:
            def name(handle):
                value, needed = ctypes.create_unicode_buffer(512), wintypes.DWORD()
                if not self.user.GetUserObjectInformationW(handle, 2, value, ctypes.sizeof(value),
                                                            ctypes.byref(needed)):
                    return None
                return value.value
            current_name = name(current)
            return bool(current_name and current_name == name(desktop))
        finally:
            self.user.CloseDesktop(desktop)

    def begin(self, edge, x, y):
        """Client/synthetic events use Tk; only OS non-client input starts sizing."""
        return False

    def attach(self):
        """Install the scoped bridge without entering an input loop (also testable)."""
        if sys.platform != 'win32':
            return False
        if threading.get_ident() != self.thread:
            raise RuntimeError('Windows sizing belongs to the Tk UI thread')
        if self.hwnd is not None:
            return True
        if not hasattr(self, 'user'):
            self._api()
        self.hwnd = int(self.window.wm_frame(), 16)
        self.client_hwnd = int(self.window.winfo_id())
        self.callback = self.proc_type(self._message)
        if not self.common.SetWindowSubclass(self.hwnd, self.callback, id(self), 0):
            self.hwnd = None
            return False
        _INSTALLED[id(self)] = self
        try:
            self.original_style = self.user.GetWindowLongW(self.hwnd, GWL_STYLE)
            ctypes.set_last_error(0)
            if self.custom_frame:
                previous = self.user.SetWindowLongW(self.hwnd, GWL_STYLE,
                                                    self.original_style | WS_THICKFRAME)
                if not previous and ctypes.get_last_error():
                    raise ctypes.WinError(ctypes.get_last_error())
                if not self.user.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, SWP_FLAGS):
                    raise ctypes.WinError(ctypes.get_last_error())
            self.enabled = self._is_input_desktop()
            for sequence in ('<Configure>', '<Expose>'):
                self.bindings.append((sequence, self.window.bind(sequence, self._tk_event, add='+')))
            self.bindings.append(('<Destroy>', self.window.bind('<Destroy>', self._destroyed, add='+')))
            if self.custom_frame:
                self.update_children()
            return True
        except Exception:
            self.close()
            raise

    def update_children(self):
        """Let edge hits reach the wrapper through the existing child widgets."""
        if self.hwnd is None:
            return
        stack = [self.window]
        while stack:
            widget = stack.pop()
            if widget.winfo_toplevel() is not self.window:
                continue
            child = int(widget.winfo_id())
            if child == self.hwnd:
                stack.extend(widget.winfo_children())
                continue
            if child not in self.child_hwnds:
                if not self.common.SetWindowSubclass(child, self.callback, id(self), 0):
                    raise ctypes.WinError(ctypes.get_last_error())
                self.child_hwnds.add(child)
            stack.extend(widget.winfo_children())

    def _notify(self, rect):
        value = rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        if value == self.rect:
            return
        self.rect = value
        self._pending_rect = True

    def show_outline(self, rect):
        if self.outline is None:
            from src.ui.resize_outline import ResizeOutline
            self.outline = ResizeOutline(self.hwnd, self.outline_colour)
        self.outline.show(rect)

    def _hide_outline(self, *, close=False):
        if self.outline is not None:
            if close:
                self.outline.close()
                self.outline = None
            else:
                self.outline.hide()

    def _finish_outline(self, cancelled):
        self.cancelled = bool(cancelled)
        self._hide_outline()
        if self.cancelled and self._outline_origin is not None:
            self.rect = self._outline_origin
            self._pending_rect = True
        self._outline_apply = not self.cancelled and self.rect != self._outline_origin

    def _apply_outline(self):
        self._outline_apply = False
        rect = wintypes.RECT()
        if self.hwnd and self.user.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            actual = rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top
            if actual != self.rect:
                client = wintypes.RECT()
                if not self.user.GetClientRect(self.hwnd,ctypes.byref(client)):
                    raise ctypes.WinError(ctypes.get_last_error())
                x,y,width,height=self.rect
                width-=actual[2]-(client.right-client.left)
                height-=actual[3]-(client.bottom-client.top)
                # Enter Tcl through _tkinter. Direct ctypes SetWindowPos can
                # synchronously re-enter Tk without its saved thread state.
                self.window.geometry(f'{max(1,width)}x{max(1,height)}+{x}+{y}')

    def _tk_event(self, event):
        """A Tk command callback owns the Tcl/Python boundary needed for UI work."""
        if event.widget is not self.window:
            return
        if self._pending_end and self._outline_apply:
            self._apply_outline()
        if (self.running or self._pending_end) and self.resizing and not self._started:
            self._started = True
            if self.on_start:
                self.on_start(self.edge, self.start_rect or self.rect, self.pointer)
        if self._pending_rect and (self.running or self._started or self._pending_end):
            self._pending_rect = False
            if self.on_rect:
                self.on_rect(self.rect)
        if self._pending_end:
            self._pending_end = False
            if self.on_end:
                self.on_end()
            self._started = False
            self.resizing = False
            if self._close_pending:
                self.close()
        if self._pending_reset:
            self._pending_reset = False
            if self.on_reset:
                self.on_reset()

    def _destroyed(self, event):
        if event.widget is self.window:
            self.close(restore=False, destroying=True)

    def _hit_edge(self, lparam):
        x = ctypes.c_short(lparam & 0xffff).value
        y = ctypes.c_short((lparam >> 16) & 0xffff).value
        rect = wintypes.RECT()
        if self.hwnd and self.user.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            if not (rect.left <= x < rect.right and rect.top <= y < rect.bottom):
                return ''
            return popup_layout.edge_at(x - rect.left, y - rect.top,
                rect.right - rect.left, rect.bottom - rect.top, self.edge_px, self.corner_px)
        return ''

    def _message(self, hwnd, message, wparam, lparam, _identifier, _data):
        """Never let a Python exception cross the ctypes window-procedure boundary."""
        forwarded = False
        result = 0
        try:
            if hwnd != self.hwnd:
                if (message == WM_NCHITTEST and self.enabled and not self.client_edges
                        and self._hit_edge(lparam)):
                    return -1  # HTTRANSPARENT: preserve the wrapper's edge hit.
                if message == WM_NCDESTROY:
                    self.common.RemoveWindowSubclass(hwnd, self.callback, id(self))
                    self.child_hwnds.discard(hwnd)
                return self.common.DefSubclassProc(hwnd, message, wparam, lparam)
            if message == WM_NCHITTEST and self.enabled and self.custom_frame:
                if self.client_edges:
                    return 1  # All Quick resize targets use the same client pointer tracker.
                return HIT_EDGES.get(self._hit_edge(lparam), 1)
            if message == WM_NCLBUTTONDOWN and wparam in HIT_EDGES.values():
                self._size_command = True
                self.edge = next(edge for edge, value in HIT_EDGES.items() if value == wparam)
                self.start_rect = self.rect
                self.pointer = (ctypes.c_short(lparam & 0xffff).value,
                                ctypes.c_short((lparam >> 16) & 0xffff).value)
            if message == WM_SYSCOMMAND:
                command = int(wparam) & 0xFFF0
                if command in (SC_SIZE, SC_MOVE):
                    self._size_command = command == SC_SIZE
            if message == WM_NCLBUTTONDBLCLK and wparam in HIT_EDGES.values() and self.on_reset:
                self._pending_reset = True
                self.user.InvalidateRect(self.client_hwnd or hwnd, None, False)
                return 0
            if message == WM_ENTERSIZEMOVE:
                self.running = True
                self.resizing = self._size_command
                self._started = False
                self.start_rect = self.start_rect or self.rect
                if self.outline_mode and self.resizing:
                    origin = wintypes.RECT()
                    if self.user.GetWindowRect(hwnd, ctypes.byref(origin)):
                        self._outline_origin = (origin.left, origin.top,
                            origin.right-origin.left, origin.bottom-origin.top)
                        self.rect = self.start_rect = self._outline_origin
                    self.cancelled = self._outline_apply = False
                    self.user.InvalidateRect(self.client_hwnd or hwnd, None, False)
            if (message == WM_WINDOWPOSCHANGING and self.outline_mode
                    and self.running and self.resizing and self._outline_origin is not None):
                position = ctypes.cast(lparam, ctypes.POINTER(WindowPosition)).contents
                position.x, position.y, position.width, position.height = self._outline_origin
                position.flags |= 0x0001 | 0x0002 | 0x0008
                return 0
            if (message == WM_STYLECHANGING and self.noactivate is not None
                    and ctypes.c_ssize_t(wparam).value == GWL_EXSTYLE):
                style = ctypes.cast(lparam, ctypes.POINTER(StyleInfo)).contents
                style.new = ((style.new | WS_EX_NOACTIVATE) if self.noactivate
                             else (style.new & ~WS_EX_NOACTIVATE))
            if message == WM_NCCALCSIZE and self.custom_frame:
                return 0  # Keep the existing custom client area and hairline.
            if message == WM_GETMINMAXINFO and self.custom_frame:
                result = self.common.DefSubclassProc(hwnd, message, wparam, lparam)
                forwarded = True
                info = ctypes.cast(lparam, ctypes.POINTER(MinMaxInfo)).contents
                info.min_track.x, info.min_track.y = self.minimum
                return result
            if message == WM_SIZING:
                self.resizing = True
                if not self.custom_frame:
                    if self.outline_mode:
                        rect = ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT)).contents
                        self._notify(rect)
                        self.show_outline(self.rect)
                        return 1
                    forwarded = True
                    result = self.common.DefSubclassProc(hwnd, message, wparam, lparam)
                    return result
                rect = ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT)).contents
                edge = next((name for name, value in SIZING_EDGES.items() if value == wparam), '')
                if edge:
                    x, y, width, height = self.rect
                    dx = rect.left - x if 'w' in edge else rect.right - x - width
                    dy = rect.top - y if 'n' in edge else rect.bottom - y - height
                    point = wintypes.POINT()
                    self.user.GetCursorPos(ctypes.byref(point))
                    resized = popup_layout.resize(self.rect, edge, dx, dy,
                        min_width=self.minimum[0], min_height=self.minimum[1],
                        bounds=self.bounds(point.x, point.y))
                    rect.left, rect.top = resized[:2]
                    rect.right, rect.bottom = rect.left + resized[2], rect.top + resized[3]
                    if self.outline_mode:
                        self._notify(rect)
                        self.show_outline(self.rect)
                    return 1
            if (message in (WM_SIZE, WM_WINDOWPOSCHANGED) and self.hwnd
                    and not (self.outline_mode and (self.running or self._pending_end))):
                rect = wintypes.RECT()
                if self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
                    self._notify(rect)
            result = self.common.DefSubclassProc(hwnd, message, wparam, lparam)
            forwarded = True
            if message == WM_EXITSIZEMOVE or (message in (WM_CANCELMODE, WM_CAPTURECHANGED)
                    and self.running and self.resizing):
                if self.outline_mode and self.running and self.resizing:
                    escape = self.enabled and bool(self.user.GetAsyncKeyState(27) & 0x8000)
                    lost_while_pressed = (message == WM_CAPTURECHANGED and self.enabled
                        and bool(self.user.GetAsyncKeyState(1) & 0x8000))
                    self._finish_outline(message == WM_CANCELMODE or escape or lost_while_pressed)
                self.running = False
                self._pending_end = self.resizing or self._started
                self._size_command = False
                rect = wintypes.RECT()
                if not self.outline_mode and self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
                    self._notify(rect)
                self.user.InvalidateRect(self.client_hwnd or hwnd, None, False)
            elif message == WM_NCDESTROY:
                self._hide_outline(close=True)
                self.common.RemoveWindowSubclass(hwnd, self.callback, id(self))
                self.hwnd = None
                self.running = False
                self.resizing = False
                self.on_rect = None
                _INSTALLED.pop(id(self), None)
            return result
        except Exception:
            logging.exception('[Resize] native sizing callback failed')
            return result if forwarded else self.common.DefSubclassProc(hwnd, message, wparam, lparam)

    def close(self, *, restore=True, destroying=False):
        if threading.get_ident() != self.thread:
            raise RuntimeError('Windows sizing teardown belongs to the Tk UI thread')
        self._hide_outline(close=True)
        if self.running and restore:
            self.enabled = False
            self._close_pending = True
            self.user.PostMessageW(self.hwnd, 0x001F, 0, 0)  # WM_CANCELMODE, no input injection.
            return
        for sequence, binding in self.bindings:
            if destroying and sequence == '<Destroy>':
                continue
            try:
                self.window.unbind(sequence, binding)
            except tk.TclError:
                pass
        self.bindings.clear()
        if self.hwnd is None:
            self.on_rect = self.on_start = self.on_end = self.on_reset = None
            self.running = self.resizing = self.enabled = False
            _INSTALLED.pop(id(self), None)
            return
        hwnd = self.hwnd
        failed_children = set()
        for child in list(self.child_hwnds):
            if self.user.IsWindow(child):
                if not self.common.RemoveWindowSubclass(child, self.callback, id(self)):
                    failed_children.add(child)
        self.child_hwnds = failed_children
        if self.user.IsWindow(hwnd):
            if restore and self.original_style is not None and self.custom_frame:
                style = self.user.GetWindowLongW(hwnd, GWL_STYLE)
                self.user.SetWindowLongW(hwnd, GWL_STYLE,
                    (style & ~WS_THICKFRAME) | (self.original_style & WS_THICKFRAME))
                self.user.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)
            if failed_children:
                self.enabled = False
                self.on_rect = self.on_start = self.on_end = self.on_reset = None
                logging.warning('[Resize] child subclass retained until wrapper destruction')
                return
            if not self.common.RemoveWindowSubclass(hwnd, self.callback, id(self)):
                # Keep the callable alive until NCDESTROY if Windows refuses removal.
                self.on_rect = None
                logging.warning('[Resize] subclass retained until wrapper destruction')
                return
        self.hwnd = None
        self.running = False
        self.resizing = False
        self.enabled = False
        self.on_rect = None
        self.on_start = self.on_end = self.on_reset = None
        _INSTALLED.pop(id(self), None)
