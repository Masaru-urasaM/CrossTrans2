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
from src.ui.theme import windows
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


def scale_box(box, view_size, image_size):
    """Map a canvas-space box to the pixels of the captured image (identity unless the overlay was
    shown at a different size than the capture, e.g. mixed-DPI desktops)."""
    if tuple(view_size) == tuple(image_size):
        return tuple(box)
    sx, sy = image_size[0] / view_size[0], image_size[1] / view_size[1]
    x1, y1, x2, y2 = box
    width, height = image_size
    return (max(0, min(width, round(x1 * sx))), max(0, min(height, round(y1 * sy))),
            max(0, min(width, round(x2 * sx))), max(0, min(height, round(y2 * sy))))


def primary_monitor_rect():
    """The primary monitor's rectangle in virtual-screen coordinates, or None."""
    try:
        user32 = ctypes.windll.user32
        return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return None


def monitor_bounds(point, origin, view_size, image_size, finder=None):
    """The canvas-local rectangle of the monitor holding `point` (virtual-screen coordinates).

    Falls back to the primary monitor, then to the whole canvas, and is clamped to the canvas so a
    chip placed from it never lands in the dead space an L-shaped monitor layout leaves in the
    virtual screen."""
    width, height = view_size
    whole = (0, 0, width, height)
    rect = (finder or windows.monitor_rect)(*point) or primary_monitor_rect()
    if not rect:
        return whole
    sx, sy = width / image_size[0], height / image_size[1]
    left, top = round((rect[0] - origin[0]) * sx), round((rect[1] - origin[1]) * sy)
    right, bottom = round((rect[2] - origin[0]) * sx), round((rect[3] - origin[1]) * sy)
    left, top, right, bottom = max(0, left), max(0, top), min(width, right), min(height, bottom)
    return (left, top, right, bottom) if right > left and bottom > top else whole


def instruction_position(bounds, gap):
    """Top-centre of a monitor, in canvas coordinates."""
    return (bounds[0] + bounds[2]) // 2, bounds[1] + gap


def dimensions_position(selection_end, label_size, gap, bounds):
    """Next to the selection's far corner, kept inside the monitor the pointer is on."""
    left, top, right, bottom = bounds
    x = min(selection_end[0] + gap, right - label_size[0] - gap)
    y = min(selection_end[1] + gap, bottom - label_size[1] - gap)
    return max(left + gap, x), max(top + gap, y)


class ScreenshotCapture:
    """An opaque captured desktop, dimmed outside the current selection."""

    def __init__(self, root=None):
        self.root = root
        self.top = self.canvas = self.original_image = self.callback = None
        self.start_x = self.start_y = self.cur_x = self.cur_y = 0
        self._vscreen_left = self._vscreen_top = 0
        self._view_size = None
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
        if width <= 0 or height <= 0:
            width, height = self.original_image.size
        self._view_size = (width, height)
        # A bare `-1080` would mean "1080 from the right edge"; tk_geometry writes the `+-1080` form.
        self.top.geometry(windows.tk_geometry(left, top, width, height))
        self.top.configure(cursor='cross')
        self.canvas = tk.Canvas(self.top, highlightthickness=0, bd=0, bg=palette.bg,
                                 cursor='cross', **NO_AUTOSTYLE)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        dimmed = ImageEnhance.Brightness(self.original_image).enhance(0.42)
        if dimmed.size != self._view_size:
            dimmed = dimmed.resize(self._view_size)
        self._screen_photo = ImageTk.PhotoImage(dimmed, master=self.top)
        self.canvas.create_image(0, 0, image=self._screen_photo, anchor='nw')
        self._selection_image = self.canvas.create_image(0, 0, anchor='nw', state='hidden')
        self._selection_outlines = [
            self.canvas.create_rectangle(0, 0, 0, 0, outline=colour, width=px(stroke), state='hidden')
            for colour, stroke in selection_strokes(palette)]
        self.rect = self._selection_outlines[-1]
        self.instruction = ui.Chip(self.canvas, 'Drag to select · Esc to cancel', on='bg')
        bounds = self._cursor_bounds()
        self._instruction_item = self.canvas.create_window(*instruction_position(bounds, px(24)),
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
        windows.place_exact(self.top, left, top, width, height)
        self.top.focus_force()

    def _view(self):
        """The size of the picture on the canvas (the capture's own size until the overlay is built)."""
        return self._view_size or self.original_image.size

    def _canvas_origin(self):
        """Where the canvas really is on the virtual screen: its mapped root position, else where the
        overlay was asked to go. Pointer positions are measured from it so the drawn rectangle and the
        crop can never disagree."""
        canvas = self.canvas
        if canvas is not None:
            try:
                if canvas.winfo_ismapped():
                    x, y = canvas.winfo_rootx(), canvas.winfo_rooty()
                    if isinstance(x, int) and isinstance(y, int):
                        return x, y
            except tk.TclError:
                pass
        return self._vscreen_left, self._vscreen_top

    def _cursor_bounds(self):
        """Canvas-local bounds of the monitor the pointer is on."""
        return monitor_bounds(self._pointer(), (self._vscreen_left, self._vscreen_top), self._view(),
                              self.original_image.size)

    def _pointer(self):
        """The pointer in virtual-screen coordinates."""
        try:
            return self.top.winfo_pointerxy()
        except (tk.TclError, AttributeError):
            return self._vscreen_left, self._vscreen_top

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
        """The selection in canvas pixels (what is drawn)."""
        return selection_box((self.start_x, self.start_y), (self.cur_x, self.cur_y),
                              self._canvas_origin(), self._view())

    def _image_box(self, box=None):
        """The selection in captured-image pixels (what is cropped)."""
        return scale_box(box or self._box(), self._view(), self.original_image.size)

    def _draw_selection(self):
        if self.original_image is None:
            return
        x1, y1, x2, y2 = box = self._box()
        crop = self._image_box(box)
        width, height = crop[2] - crop[0], crop[3] - crop[1]
        for outline in self._selection_outlines:
            self.canvas.coords(outline, x1, y1, x2, y2)
            self.canvas.itemconfigure(outline, state='normal')
        if width > 0 and height > 0:
            piece = self.original_image.crop(crop)
            if piece.size != (x2 - x1, y2 - y1):
                piece = piece.resize((x2 - x1, y2 - y1))
            self._crop_photo = ImageTk.PhotoImage(piece, master=self.top)
            self.canvas.itemconfigure(self._selection_image, image=self._crop_photo, state='normal')
            self.canvas.coords(self._selection_image, x1, y1)
        self.dimensions.configure(text=f'{width} × {height} px')
        self.dimensions.update_idletasks()
        gap = self.ctx.px(12)
        label = (self.dimensions.winfo_reqwidth(), self.dimensions.winfo_reqheight())
        self.canvas.coords(self._dimensions_item,
                           *dimensions_position((x2, y2), label, gap, self._cursor_bounds()))
        self.canvas.itemconfigure(self._dimensions_item, state='normal')

    def _on_release(self, event):
        if not self._dragging or self.original_image is None:
            self._close(call_callback=True)
            return
        self.cur_x, self.cur_y = event.x_root, event.y_root
        box = self._image_box()
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
        self._view_size = None
        self._dragging = False
        if call_callback:
            self._finish(None)
