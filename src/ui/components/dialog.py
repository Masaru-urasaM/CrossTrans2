"""
The dialog (TODO.md U4b; V10, V11, V21): sized to its content and never larger than the screen
allows, with the app's icon and a title bar of the theme's colour, a primary and a secondary
button, Enter for the primary and Esc for the way out - and no `grab_set()`.

Why not modal: Tk's `grab_set()` on a window whose master is withdrawn freezes the application
(CLAUDE.md Known Issues, Decision 12). A dialog here is an ordinary top-level that comes to the
front and asks; the caller reacts in a callback.
"""
import tkinter as tk
from typing import Callable, List, Optional

from src.ui.components import context as C
from src.ui.components.widgets import Button, Frame, Label
from src.ui.components.composite import ScrollArea
from src.ui.theme import engine, windows

MIN_WIDTH = 360
MAX_WORK_SHARE = 0.9


def work_area() -> tuple:
    """Usable rectangle at the pointer, with the primary monitor as a native fallback."""
    try:
        import win32api
        area = windows.monitor_work_area(*win32api.GetCursorPos())
        if area is not None:
            return area
    except Exception:
        pass
    import ctypes
    from ctypes import wintypes
    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)    # SPI_GETWORKAREA
    return rect.left, rect.top, rect.right, rect.bottom


def fit_geometry(content_w: int, content_h: int, area: tuple, parent_rect: Optional[tuple],
                 min_w: int = 0) -> tuple:
    """(width, height, x, y) for a dialog whose content wants `content_w` x `content_h`: at least
    `min_w` wide, at most 90 % of the work area each way, centred on the parent when there is one
    (kept fully on the work area), else on the work area. Pure."""
    left, top, right, bottom = area
    max_w = int((right - left) * MAX_WORK_SHARE)
    max_h = int((bottom - top) * MAX_WORK_SHARE)
    width = min(max(content_w, min_w), max_w)
    height = min(content_h, max_h)
    if parent_rect:
        px, py, pw, ph = parent_rect
        x, y = px + (pw - width) // 2, py + (ph - height) // 2
    else:
        x, y = left + (right - left - width) // 2, top + (bottom - top - height) // 2
    x = max(left, min(x, right - width))
    y = max(top, min(y, bottom - height))
    return width, height, x, y


class Dialog:
    """`Dialog(parent, 'Title').body` is a Frame to fill; `add_button` adds to the button row;
    `show()` fits, places and raises it. Esc runs `on_cancel`; Enter runs the default button."""

    def __init__(self, parent, title: str, message: str = '', icon: Optional[str] = None,
                 on_cancel: Optional[Callable] = None, min_width: int = MIN_WIDTH):
        ctx = C.get()
        self.parent, self.min_width = parent, ctx.px(min_width)
        self._on_cancel = on_cancel
        self._timer_root, self._front_job = ctx.root, None
        self._over = None            # a window to centre on instead of the parent (stay_in_front)
        self._keep_front = False     # stay in the topmost band while open (stay_in_front)
        self.window = tk.Toplevel(parent)
        self.window.sizes_from_work_area = True
        self.window.withdraw()
        self.window.title(title)
        self.window.resizable(False, False)
        engine.attach(self.window)
        self.root = Frame(self.window, on='bg', padding=ctx.px(24))
        self.root.pack(fill='both', expand=True)
        self.scroll = ScrollArea(self.root)
        self.scroll.pack(fill='both', expand=True)
        header = Frame(self.scroll.content)
        header.pack(fill='x')
        if icon:
            from tkinter import ttk
            ttk.Label(header, image=ctx.icon(icon, 28, ctx.palette.accent),
                      style='CtBodyBg.TLabel').pack(side='left', padx=(0, ctx.px(12)), anchor='n')
        Label(header, title, kind='subtitle', wraplength=ctx.px(420), justify='left').pack(
            side='left', anchor='w')
        self.body = Frame(self.scroll.content, padding=(0, ctx.px(12), 0, 0))
        self.body.pack(fill='both', expand=True)
        if message:
            Label(self.body, message, kind='body', wraplength=ctx.px(440), justify='left').pack(anchor='w')
        self.buttons = Frame(self.root, padding=(0, ctx.px(20), 0, 0))
        self.buttons.pack(fill='x', side='bottom', before=self.scroll)
        self._button_list: List[Button] = []
        self._default: Optional[Button] = None
        self.window.bind('<Escape>', self._escape)
        self.window.bind('<Return>', self._enter)
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        self.window.bind('<Destroy>', self._destroyed, add='+')
        self._stop_preferences = ctx.manager.subscribe(self._preferences_changed)

    # --- buttons ---
    def add_button(self, text: str, command: Optional[Callable] = None, kind: str = 'soft',
                   default: bool = False, close: bool = True) -> Button:
        """Add a button at the right of the row; a click runs `command`, then closes the dialog
        unless `close=False`. The first button added is placed rightmost."""
        def run():
            if command:
                command()
            if close:
                self.close()
        button = Button(self.buttons, text, command=run, kind='accent' if default else kind)
        button.pack(side='right', padx=(C.get().px(8), 0))
        self._button_list.append(button)
        if default:
            self._default = button
        return button

    # --- keys ---
    def _escape(self, event=None) -> str:
        self.cancel()
        return 'break'

    def _enter(self, event=None):
        focused = self.window.focus_get()
        if isinstance(focused, Button) or self._default is None:
            return None                                   # Enter on a button is that button's own
        if str(self._default.cget('state')) != 'disabled':
            self._default.invoke()
        return 'break'

    def cancel(self) -> None:
        callback, self._on_cancel = self._on_cancel, None
        self.close()
        if callback:
            callback()

    # --- showing ---
    def fit(self) -> tuple:
        """Size to the content, within the work area, and place it. Returns (w, h, x, y)."""
        self.window.update_idletasks()
        px = C.get().px
        area = work_area()
        room = max(px(120), int((area[2] - area[0]) * MAX_WORK_SHARE) - 2 * px(24))
        buttons = list(reversed(self._button_list))
        needed = sum(button.winfo_reqwidth() + px(8) for button in buttons)
        columns = len(buttons) if needed <= room else (2 if buttons and 2 * max(b.winfo_reqwidth() + px(8) for b in buttons) <= room else 1)
        for button in buttons:
            button.pack_forget()
            button.grid_forget()
        self.buttons.columnconfigure(0, weight=1)
        for index, button in enumerate(buttons):
            button.grid(row=index // max(1, columns), column=1 + index % max(1, columns),
                        sticky='e', padx=(px(8), 0), pady=(px(4), 0))
        self.scroll.adopt()
        self.window.update_idletasks()
        content_w = min(room, self.scroll.content.winfo_reqwidth())
        content_h = self.scroll.content.winfo_reqheight()
        footer_h = self.buttons.winfo_reqheight()
        available_h = max(px(80), int((area[3] - area[1]) * MAX_WORK_SHARE) - footer_h - 2 * px(24))
        self.scroll.canvas.configure(width=content_w, height=min(content_h, available_h))
        self.window.update_idletasks()
        parent_rect = self._over
        try:
            if parent_rect is None and self.parent is not None and self.parent.winfo_viewable():
                parent_rect = (self.parent.winfo_rootx(), self.parent.winfo_rooty(),
                               self.parent.winfo_width(), self.parent.winfo_height())
        except tk.TclError:
            pass
        geometry = fit_geometry(self.window.winfo_reqwidth(), self.window.winfo_reqheight(),
                                area, parent_rect, self.min_width)
        width, height, x, y = geometry
        self.window.geometry(f'{width}x{height}+{x}+{y}')
        return geometry

    def show(self) -> 'Dialog':
        """Fit, map and raise - without taking a grab, and without waiting."""
        self.fit()
        self.window.deiconify()
        self.window.attributes('-topmost', True)
        self.window.lift()
        if self._front_job:
            self._timer_root.after_cancel(self._front_job)
            self._front_job = None
        if not self._keep_front:
            self._front_job = self._timer_root.after(150, self._release_topmost)
        try:
            (self._default or (self._button_list[0] if self._button_list else self.window)).focus_set()
        except tk.TclError:
            pass
        return self

    def stay_in_front(self, rect: Optional[tuple] = None) -> 'Dialog':
        """Keep the dialog in the topmost band while it is open, centred on `rect` (left, top, width, height)
        when given: a question about a window that is itself always on top (the audio panel) must not open
        under it. Call it before `show()`, so the first placement is the right one (a window that was just
        mapped does not always follow a second one); the dialog leaves the band when it closes."""
        self._keep_front = True
        self._over = tuple(rect) if rect else None
        if self._front_job:
            self._timer_root.after_cancel(self._front_job)
            self._front_job = None
        return self

    def _release_topmost(self) -> None:
        self._front_job = None
        try:
            self.window.attributes('-topmost', False)
        except tk.TclError:
            pass

    def _preferences_changed(self, manager):
        if self.exists and self.window.winfo_viewable():
            self.fit()

    def _destroyed(self, event) -> None:
        if event.widget is self.window:
            self._stop_preferences()
        if event.widget is self.window and self._front_job:
            try:
                self._timer_root.after_cancel(self._front_job)
            except tk.TclError:
                pass
            self._front_job = None

    def close(self) -> None:
        try:
            if self.window.winfo_exists():
                self.window.destroy()
        except tk.TclError:
            pass

    @property
    def exists(self) -> bool:
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False
