"""
Screenshot Capture Module for CrossTrans.
Supports multi-monitor setups with proper coordinate handling.
"""
import ctypes
import glob
import logging
import tkinter as tk
import tempfile
import os
from PIL import ImageGrab, ImageEnhance, ImageTk
from src.ui import components as ui
from src.ui.theme.compat import NO_AUTOSTYLE

# Every screenshot the app writes to %TEMP% starts with this, so leftovers can be found and removed without
# touching another program's files (CHANGELOG Phase 25).
SHOT_PREFIX = 'CrossTrans2-shot-'


def sweep_old_shots(directory=None) -> int:
    """Delete screenshots an earlier run left in the temp folder; only this app's own prefix. Returns how many."""
    removed = 0
    for path in glob.glob(os.path.join(directory or tempfile.gettempdir(), SHOT_PREFIX + '*.png')):
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            pass                                    # in use (an attachment the Translator still holds)
    return removed


def get_virtual_screen_bounds():
    """Get the bounding box of all monitors (virtual screen).

    Returns:
        Tuple of (left, top, width, height) for the virtual screen.
        Left and top can be negative if monitors are positioned to the left/above primary.
    """
    try:
        user32 = ctypes.windll.user32

        # SM_XVIRTUALSCREEN = 76 (left edge of virtual screen)
        # SM_YVIRTUALSCREEN = 77 (top edge of virtual screen)
        # SM_CXVIRTUALSCREEN = 78 (width of virtual screen)
        # SM_CYVIRTUALSCREEN = 79 (height of virtual screen)
        left = user32.GetSystemMetrics(76)
        top = user32.GetSystemMetrics(77)
        width = user32.GetSystemMetrics(78)
        height = user32.GetSystemMetrics(79)

        return left, top, width, height
    except Exception:
        # Fallback to primary monitor
        return 0, 0, 1920, 1080


def selection_strokes(palette):
    """Contrasting outer/inner edges surround the accent without changing the captured pixels."""
    return ((palette.bg, 5), (palette.text, 3), (palette.accent, 1))


def selection_box(start, end, origin, image_size):
    """A clamped image-local crop box, including negative monitor coordinates."""
    x1, x2 = sorted((start[0] - origin[0], end[0] - origin[0]))
    y1, y2 = sorted((start[1] - origin[1], end[1] - origin[1]))
    width, height = image_size
    return max(0, min(width, x1)), max(0, min(height, y1)), max(0, min(width, x2)), max(0, min(height, y2))


class ScreenshotCapture:
    """An opaque captured desktop, dimmed outside the current selection."""

    def __init__(self, root=None):
        self.root = root
        self.top = self.canvas = self.original_image = self.callback = None
        self.start_x = self.start_y = self.cur_x = self.cur_y = 0
        self._vscreen_left = self._vscreen_top = 0
        self.rect = self._selection_image = None
        self._screen_photo = self._crop_photo = None
        self._dragging = False

    def capture_region(self, callback):
        if self.top is not None:
            self._close(call_callback=True)
        self.callback = callback
        try:
            self.original_image = ImageGrab.grab(all_screens=True)
        except Exception:
            logging.exception('[Screenshot] Could not capture the desktop')
            self._finish(None)
            return
        left, top, width, height = get_virtual_screen_bounds()
        self._vscreen_left, self._vscreen_top = left, top
        self.ctx = ui.get()
        px, palette = self.ctx.px, self.ctx.palette
        self.top = tk.Toplevel(self.root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.attributes('-topmost', True)
        self.top.geometry(f'{width}x{height}{left:+d}{top:+d}')
        self.top.configure(cursor='cross')
        self.canvas = tk.Canvas(self.top, highlightthickness=0, bd=0, bg=palette.bg,
                                 cursor='cross', **NO_AUTOSTYLE)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        dimmed = ImageEnhance.Brightness(self.original_image).enhance(0.42)
        self._screen_photo = ImageTk.PhotoImage(dimmed, master=self.top)
        self.canvas.create_image(0, 0, image=self._screen_photo, anchor='nw')
        self._selection_image = self.canvas.create_image(0, 0, anchor='nw', state='hidden')
        self._selection_outlines = [
            self.canvas.create_rectangle(0, 0, 0, 0, outline=colour, width=px(stroke), state='hidden')
            for colour, stroke in selection_strokes(palette)]
        self.rect = self._selection_outlines[-1]
        self.instruction = ui.Chip(self.canvas, 'Drag to select · Esc to cancel', on='bg')
        self._instruction_item = self.canvas.create_window(width // 2, px(24),
                                                            window=self.instruction, anchor='n')
        self.dimensions = ui.Chip(self.canvas, '', on='bg')
        self._dimensions_item = self.canvas.create_window(0, 0, window=self.dimensions,
                                                           anchor='nw', state='hidden')
        self.top.bind('<ButtonPress-1>', self._on_press)
        self.top.bind('<B1-Motion>', self._on_drag)
        self.top.bind('<ButtonRelease-1>', self._on_release)
        self.top.bind('<Escape>', lambda event: self._close(call_callback=True))
        self.top.protocol('WM_DELETE_WINDOW', lambda: self._close(call_callback=True))
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()

    def _on_press(self, event):
        self.start_x, self.start_y = event.x_root, event.y_root
        self.cur_x, self.cur_y = self.start_x, self.start_y
        self._dragging = True
        self._draw_selection()

    def _on_drag(self, event):
        if not self._dragging:
            return
        self.cur_x, self.cur_y = event.x_root, event.y_root
        self._draw_selection()

    def _box(self):
        return selection_box((self.start_x, self.start_y), (self.cur_x, self.cur_y),
                              (self._vscreen_left, self._vscreen_top), self.original_image.size)

    def _draw_selection(self):
        if self.original_image is None:
            return
        x1, y1, x2, y2 = self._box()
        width, height = x2 - x1, y2 - y1
        for outline in self._selection_outlines:
            self.canvas.coords(outline, x1, y1, x2, y2)
            self.canvas.itemconfigure(outline, state='normal')
        if width > 0 and height > 0:
            self._crop_photo = ImageTk.PhotoImage(self.original_image.crop((x1, y1, x2, y2)), master=self.top)
            self.canvas.itemconfigure(self._selection_image, image=self._crop_photo, state='normal')
            self.canvas.coords(self._selection_image, x1, y1)
        self.dimensions.configure(text=f'{width} × {height} px')
        self.dimensions.update_idletasks()
        gap = self.ctx.px(12)
        canvas_w, canvas_h = self.original_image.size
        label_w, label_h = self.dimensions.winfo_reqwidth(), self.dimensions.winfo_reqheight()
        x = min(x2 + gap, canvas_w - label_w - gap)
        y = min(y2 + gap, canvas_h - label_h - gap)
        self.canvas.coords(self._dimensions_item, max(gap, x), max(gap, y))
        self.canvas.itemconfigure(self._dimensions_item, state='normal')

    def _on_release(self, event):
        if not self._dragging or self.original_image is None:
            self._close(call_callback=True)
            return
        self.cur_x, self.cur_y = event.x_root, event.y_root
        box = self._box()
        image = self.original_image
        self._close()
        if box[2] - box[0] < 10 or box[3] - box[1] < 10:
            self._finish(None)
            return
        path = None
        try:
            fd, path = tempfile.mkstemp(prefix=SHOT_PREFIX, suffix='.png')
            os.close(fd)
            image.crop(box).save(path)
        except Exception:
            logging.exception('[Screenshot] Could not save the selected region')
            if path and os.path.exists(path):
                os.unlink(path)
            path = None
        self._finish(path)

    def _finish(self, path):
        callback, self.callback = self.callback, None
        if callback:
            callback(path)

    def _close(self, call_callback=False):
        top, self.top = self.top, None
        if top is not None:
            top.destroy()
        self.canvas = self.original_image = None
        self._screen_photo = self._crop_photo = None
        self._vscreen_left = self._vscreen_top = 0
        self._dragging = False
        if call_callback:
            self._finish(None)
