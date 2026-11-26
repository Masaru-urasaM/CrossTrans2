"""
The component set, part two: things that talk back (TODO.md U4b) - tooltips now; info bars, toasts,
dialogs and empty states follow in this module.
"""
import tkinter as tk
from tkinter import ttk
from typing import Optional

from src.ui.components import context as C
from src.ui.theme import windows
from src.ui import i18n
from src.ui.components.widgets import Label, Button
from src.ui.theme.compat import NO_AUTOSTYLE
from src.utils.ui_helpers import schedule_window_callback


def tooltip_position(anchor, size, area, gap):
    """Place below an anchor, or above it, including negative virtual-screen origins."""
    ax, ay, aw, ah = anchor
    width, height = size
    left, top, right, bottom = area
    x = max(left, min(ax + (aw - width) // 2, right - width))
    y = ay + ah + gap
    if y + height > bottom:
        y = ay - height - gap
    return x, max(top, min(y, bottom - height))


class Tooltip:
    """A small hint under a widget after a short hover (or when it takes keyboard focus).

    Never takes the focus, never grabs input, and is built when it is shown - so it wears the
    palette in force at that moment. Hidden on leave, click, key press and when the widget goes.
    """

    def __init__(self, widget, text: str, delay_ms: int = 500, gap: int = 6):
        self.widget, self.text, self.delay_ms, self.gap = widget, text, delay_ms, gap
        self._timer_root = C.get().root
        self.window: Optional[tk.Toplevel] = None
        self._job = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')
        widget.bind('<KeyPress>', self.hide, add='+')
        widget.bind('<FocusIn>', self._schedule, add='+')
        widget.bind('<FocusOut>', self.hide, add='+')
        widget.bind('<Destroy>', self.hide, add='+')

    def _schedule(self, event=None):
        self._cancel()
        if self.text:
            self._job = self._timer_root.after(self.delay_ms, self.show)

    def _cancel(self):
        if self._job is not None:
            try:
                self._timer_root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def show(self):
        self._job = None
        if self.window is not None or not self.text:
            return
        try:
            if not self.widget.winfo_exists() or not self.widget.winfo_viewable():
                return
        except tk.TclError:
            return
        ctx = C.get()
        p = ctx.palette
        anchor = (self.widget.winfo_rootx(), self.widget.winfo_rooty(),
                  self.widget.winfo_width(), self.widget.winfo_height())
        area = windows.monitor_work_area(anchor[0] + anchor[2] // 2, anchor[1] + anchor[3] // 2)
        area = area or (0, 0, self.widget.winfo_screenwidth(), self.widget.winfo_screenheight())
        win = tk.Toplevel(self.widget)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes('-topmost', True)
        win.configure(bg=p.border)                                   # the hairline
        body = tk.Label(win, text=i18n.tr(self.text), bg=p.surface, fg=p.text, font=ctx.font('caption'),
                        padx=ctx.px(8), pady=ctx.px(4), justify='left',
                        wraplength=max(1, min(ctx.px(440), area[2] - area[0] - 2 * ctx.px(8) - 2)),
                        **NO_AUTOSTYLE)
        body.pack(padx=1, pady=1)                                    # 1 px of border colour around
        win.update_idletasks()
        width, height = win.winfo_reqwidth(), win.winfo_reqheight()
        x, y = tooltip_position(anchor, (width, height), area, ctx.px(self.gap))
        win.geometry(f'+{x}+{y}')
        win.bind('<Map>', lambda e: windows.no_activate(win) if e.widget is win else None, add='+')
        windows.no_activate(win)
        win.deiconify()
        win.update_idletasks()
        windows.no_activate(win)
        schedule_window_callback(self._timer_root, win, 'idle', lambda: windows.no_activate(win))
        self.window = win

    def hide(self, event=None):
        self._cancel()
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None


# --- info bars -------------------------------------------------------------------------------------
INFO_ICONS = {'info': 'info-circle-fill', 'success': 'check-circle-fill',
              'warning': 'exclamation-triangle-fill', 'danger': 'x-circle-fill'}


class InfoBar(ttk.Frame):
    """A message that says what happened and offers the next step - in the page, not a dialog.

    kind: info, success, warning, danger. `title` is optional; `message` wraps to the bar's width.
    `action` is the text of a link ("Open API keys") that runs `on_action`; `closable` adds a
    close button that runs `on_close` (default: the bar removes itself). Never takes the focus.
    """

    def __init__(self, parent, message: str, kind: str = 'info', title: str = '',
                 action: str = '', on_action=None, closable: bool = False, on_close=None,
                 on: Optional[str] = None, **kw):
        from src.ui.components.styles import INFO_KINDS, infobar_colours, name
        from src.ui.components.widgets import plane_of
        ctx = C.get()
        if kind not in INFO_KINDS:
            raise ValueError(f"unknown info bar kind {kind!r}; expected one of {INFO_KINDS}")
        self.kind, self.plane = kind, plane_of(parent, on)
        key = f"Info{kind.capitalize()}"
        kw.setdefault('style', name(key, self.plane, 'TFrame'))
        kw.setdefault('padding', (ctx.px(14), ctx.px(10)))     # a frame's padding is a widget option
        super().__init__(parent, **kw)
        self._on_close = on_close
        p = ctx.palette
        colours = infobar_colours(p, kind, ctx.plane_colour(self.plane))
        self.icon = ttk.Label(self, image=ctx.icon(INFO_ICONS[kind], 20, colours['base']),
                              style=name(f"{key}Text", self.plane, 'TLabel'))
        self.icon.grid(row=0, column=0, rowspan=2, sticky='n', padx=(0, ctx.px(10)), pady=(ctx.px(1), 0))
        column = 1
        self.title_label = None
        if title:
            self.title_label = Label(self, text=title, style=name(f"{key}Title", self.plane, 'TLabel'))
            self.title_label.grid(row=0, column=column, sticky='w')
        self.text = Label(self, text=message, style=name(f"{key}Text", self.plane, 'TLabel'),
                              justify='left', wraplength=ctx.px(520))
        self.text.grid(row=1 if title else 0, column=column, sticky='w')
        self.action = None
        self._action_stacked = False
        if action:
            self.action = Button(self, text=action, command=on_action, takefocus=True,
                                     style=name(f"{key}Link", self.plane, 'TButton'))
            self.action.grid(row=0, column=column + 1, rowspan=2, sticky='e', padx=(ctx.px(10), 0))
        self.close_button = None
        if closable:
            self.close_button = ttk.Button(self, command=self.close, takefocus=True,
                                           image=ctx.icon('x-lg', 14, p.text),
                                           style=name(f"{key}Close", self.plane, 'TButton'))
            self.close_button.grid(row=0, column=column + 2, rowspan=2, sticky='ne',
                                   padx=(ctx.px(6), 0))
        self.columnconfigure(column, weight=1)
        self.bind('<Configure>', self._fit_text, add='+')
        self._unsubscribe = ctx.manager.subscribe(self._retheme)
        self.bind('<Destroy>', lambda event: self._unsubscribe() if event.widget is self else None, add='+')

    def _retheme(self, manager):
        from src.ui.components.styles import infobar_colours
        ctx = C.get()
        try:
            colours = infobar_colours(manager.palette, self.kind, ctx.plane_colour(self.plane))
            self.icon.configure(image=ctx.icon(INFO_ICONS[self.kind], 20, colours['base']))
            if self.close_button is not None:
                self.close_button.configure(image=ctx.icon('x-lg', 14, manager.palette.text))
            self.fit_to(self.winfo_width())
        except tk.TclError:
            pass

    def _fixed_width(self, include_action=True) -> int:
        """What the bar asks for besides its message: the frame's padding, the icon, the action and
        the close button with their gaps. Read off those widgets, which know their size at once -
        the bar's own request would also hold the message, and so depend on the last wrap."""
        px = C.get().px
        # Reserve the rounded frame and label insets as well as the icon's visible gap.
        total = 2 * px(14) + self.icon.winfo_reqwidth() + px(16)
        if self.action is not None and include_action:
            total += self.action.winfo_reqwidth() + px(10)
        if self.close_button is not None:
            total += self.close_button.winfo_reqwidth() + px(6)
        return total

    def fit_to(self, width: int) -> None:
        """Wrap the message for a bar `width` px wide: what is left after everything else the bar
        asks for, which does not depend on the wrap - so asking again changes nothing, and a wrap
        can never feed back into the width it was computed from. A change under 3 px is ignored."""
        ctx = C.get()
        stacked = self.action is not None and width - self._fixed_width() < ctx.px(220) * ctx.text_scale
        if stacked != self._action_stacked:
            self._action_stacked = stacked
            self.text.grid_configure(columnspan=2 if stacked else 1)
            if self.title_label is not None:
                self.title_label.grid_configure(columnspan=2 if stacked else 1)
            self.action.grid_configure(row=2 if stacked else 0, column=1 if stacked else 2,
                                       rowspan=1 if stacked else 2, columnspan=2 if stacked else 1,
                                       sticky='w' if stacked else 'e',
                                       padx=0 if stacked else (ctx.px(10), 0),
                                       pady=(ctx.px(6), 0) if stacked else 0)
        room = max(ctx.px(120), width - self._fixed_width(include_action=not stacked))
        current = int(float(str(self.text.cget('wraplength')) or 0))
        if abs(room - current) > 2:
            self.text.configure(wraplength=room)
        if self.title_label is not None:
            self.title_label.configure(wraplength=room)

    def _fit_text(self, event=None):
        """Wrap the message to the bar's width (`fit_to`).

        The label's wrap width is its requested width, which is part of the bar's own request, so
        deriving it from the bar's width can feed back: measured, a margin a few pixels too wide
        made every pass shrink the window by those pixels - 25 s of layout for eight bars.
        """
        self.fit_to(self.winfo_width())

    def set_message(self, message: str) -> None:
        self.text.configure(text=message)

    def close(self) -> None:
        callback, self._on_close = self._on_close, None
        if callback:
            callback()
        else:
            self.destroy()
