"""A hollow resize marker made of four small native, nonactivating windows."""
import ctypes
from ctypes import wintypes as W
import logging
import os
import threading

from src.ui.theme import tokens


class WindowClass(ctypes.Structure):
    _fields_ = [('style', W.UINT), ('procedure', ctypes.c_void_p),
                ('class_extra', ctypes.c_int), ('window_extra', ctypes.c_int),
                ('instance', W.HINSTANCE), ('icon', W.HICON), ('cursor', W.HANDLE),
                ('background', W.HBRUSH), ('menu', W.LPCWSTR), ('name', W.LPCWSTR)]


class ResizeOutline:
    """Only native border pixels move; no Tk widgets, captures or content paint."""
    def __init__(self, owner, colour=None, thickness=2):
        colour = colour or tokens.LIGHT.accent
        self.thread = threading.get_ident()
        self.handles = []
        self.visible = False
        self.rect = None
        self.atom = self.brush = None
        self.thickness = max(1, int(thickness))
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = [W.LPCWSTR], W.HMODULE
        self.instance = kernel.GetModuleHandleW(None)
        signatures = (
            ('RegisterClassW', [ctypes.POINTER(WindowClass)], W.ATOM),
            ('UnregisterClassW', [W.LPCWSTR, W.HINSTANCE], W.BOOL),
            ('CreateWindowExW', [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                W.HWND, W.HMENU, W.HINSTANCE, ctypes.c_void_p], W.HWND),
            ('SetWindowPos', [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, W.UINT], W.BOOL),
            ('ShowWindow', [W.HWND, ctypes.c_int], W.BOOL),
            ('SetLayeredWindowAttributes', [W.HWND, W.DWORD, W.BYTE, W.DWORD], W.BOOL),
            ('DestroyWindow', [W.HWND], W.BOOL), ('IsWindow', [W.HWND], W.BOOL))
        for name, args, result in signatures:
            method = getattr(self.user, name)
            method.argtypes, method.restype = args, result
        self.gdi.CreateSolidBrush.argtypes, self.gdi.CreateSolidBrush.restype = [W.DWORD], W.HBRUSH
        self.gdi.DeleteObject.argtypes, self.gdi.DeleteObject.restype = [W.HANDLE], W.BOOL
        red, green, blue = (int(colour[index:index+2], 16) for index in (1, 3, 5))
        self.brush = self.gdi.CreateSolidBrush(red | green << 8 | blue << 16)
        self.name = f'CrossTransResizeOutline_{os.getpid()}_{id(self)}'
        definition = WindowClass(procedure=ctypes.cast(self.user.DefWindowProcW, ctypes.c_void_p),
            instance=self.instance, background=self.brush, name=self.name)
        try:
            if not self.brush:
                raise ctypes.WinError(ctypes.get_last_error())
            self.atom = self.user.RegisterClassW(ctypes.byref(definition))
            if not self.atom:
                raise ctypes.WinError(ctypes.get_last_error())
            for _ in range(4):
                # Tool-window, click-through and no-activate; no Python WndProc.
                handle = self.user.CreateWindowExW(0x08000000 | 0x80000 | 0x80 | 0x20,
                    self.name, '', 0x80000000, 0, 0, 1, 1, owner, None, self.instance, None)
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handles.append(handle)
                if not self.user.SetLayeredWindowAttributes(handle,0,255,2):
                    raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

    def show(self, rect):
        if threading.get_ident() != self.thread:
            raise RuntimeError('Resize outline belongs to the window thread')
        rect = tuple(map(int, rect))
        if self.visible and rect == self.rect:
            return
        x, y, width, height = rect
        edge = min(self.thickness, max(1, min(width, height)//2))
        bars = ((x, y, width, edge), (x, y+height-edge, width, edge),
                (x, y+edge, edge, max(1, height-2*edge)),
                (x+width-edge, y+edge, edge, max(1, height-2*edge)))
        for handle, bar in zip(self.handles, bars):
            if not self.user.SetWindowPos(handle, -1, *bar, 0x0010 | 0x0200 | 0x0040):
                raise ctypes.WinError(ctypes.get_last_error())
        self.rect, self.visible = rect, True

    def hide(self):
        for handle in self.handles:
            if self.user.IsWindow(handle):
                self.user.ShowWindow(handle, 0)
        self.visible = False

    def close(self):
        self.hide()
        for handle in self.handles:
            if self.user.IsWindow(handle):
                self.user.DestroyWindow(handle)
        self.handles.clear()
        if self.atom:
            if not self.user.UnregisterClassW(self.name, self.instance):
                logging.warning('[Resize] outline class cleanup failed: %s', ctypes.get_last_error())
                return
            self.atom = None
        if self.brush:
            self.gdi.DeleteObject(self.brush)
            self.brush = None
