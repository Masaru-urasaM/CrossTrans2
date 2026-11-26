"""
The component set, part three (TODO.md U4b): keycaps, an empty state, a scroll area and the
navigation of the Settings window - the pieces that are made of other pieces.
"""
import tkinter as tk
import weakref
from tkinter import ttk
from typing import Callable, List, Optional, Sequence, Tuple

from src.ui.components import context as C
from src.ui.components.styles import name
from src.ui.components.widgets import (Button, Choice, Frame, Label, ThinScrollbar, plane_of)
from src.ui.theme.compat import NO_AUTOSTYLE
from src.utils.ui_helpers import schedule_window_callback, cancel_window_callback


class Keycap(ttk.Label):
    """One key of a shortcut: Win, Alt, E."""

    def __init__(self, parent, text: str, on: Optional[str] = None, **kw):
        C.get()
        kw.setdefault('style', name('key', plane_of(parent, on), 'TLabel'))
        super().__init__(parent, text=text, **kw)


class Keys(Frame):
    """A whole shortcut: `Keys(parent, ['Win', 'Alt', 'E'])` -> [Win] + [Alt] + [E]."""

    def __init__(self, parent, keys: Sequence[str], on: Optional[str] = None, on_layout=None, **kw):
        super().__init__(parent, on=on, **kw)
        ctx = C.get()
        self._items = []
        self._positions = None
        self._requested = None
        self._natural_width = 1
        self._on_layout = on_layout
        self.grid_propagate(False)
        self.caps: List[Keycap] = []
        for index, key in enumerate(keys):
            group = Frame(self, on=self.plane)
            if index:
                Label(group, '+', kind='muted').pack(side='left', padx=ctx.px(2))
            cap = Keycap(group, key)
            cap.pack(side='left')
            self.caps.append(cap)
            self._items.append(group)
            group.bind('<Configure>', self._fit_caps, add='+')
        self.bind('<Configure>', self._fit_caps, add='+')
        self._fit_caps()

    def preferred_width(self):
        return self._natural_width

    def _fit_caps(self, event=None):
        gap = C.get().px(4)
        widths = [widget.winfo_reqwidth() for widget in self._items]
        self._natural_width = max(1, sum(widths) + gap * max(0, len(widths) - 1))
        room = self.winfo_width() if self.winfo_width() > 1 else self._natural_width
        line_height = max((widget.winfo_reqheight() for widget in self._items), default=1)
        row = used = 0
        positions = []
        for width in widths:
            if used and used + gap + width > room:
                row, used = row + 1, 0
            x = used + (gap if used else 0)
            positions.append((x, row * (line_height + gap)))
            used = x + width
        if positions != self._positions:
            self._positions = positions
            # Rows have independent widths; grid columns would share the widest cell
            # across rows and could overflow despite the measured wrapping boundary.
            for widget, (x, y) in zip(self._items, positions):
                widget.place(x=x, y=y)
        requested = self._natural_width, (row + 1) * line_height + row * gap
        if requested != self._requested:
            self._requested = requested
            # Width stays independent of wrapping. Otherwise an auto-sized recorder can
            # alternate forever between the width of a wrapped row and its unwrapped keys.
            self.configure(width=requested[0], height=requested[1])
            if self._on_layout:
                self._on_layout()

    @property
    def text(self) -> str:
        return ' + '.join(str(cap.cget('text')) for cap in self.caps)


class EmptyState(Frame):
    """What a page shows when there is nothing to show yet: a picture, one line, one next step.

    `image` is a Tk image name (the mascot, from U5) or None for a large icon (`icon`, a Bootstrap
    Icons name). `action` is the label of an accent button that runs `on_action`.
    """

    def __init__(self, parent, title: str, message: str = '', icon: str = 'inbox', image=None,
                 action: str = '', on_action: Optional[Callable] = None, on: Optional[str] = None, **kw):
        super().__init__(parent, on=on, **kw)
        ctx = C.get()
        self.picture = ttk.Label(self, style=name('body', self.plane, 'TLabel'),
                                 image=image or ctx.icon(icon, 48, ctx.palette.muted))
        self.picture.pack(pady=(0, ctx.px(12)))
        self.title_label = Label(self, title, kind='subtitle')
        self.title_label.pack()
        self.message_label = None
        if message:
            self.message_label = Label(self, message, kind='muted', justify='center',
                                       wraplength=ctx.px(360))
            self.message_label.pack(pady=(ctx.px(4), 0))
        self.button = None
        if action:
            self.button = Button(self, action, kind='accent', command=on_action)
            self.button.pack(pady=(ctx.px(16), 0))


class ScrollArea(Frame):
    """A scrolling region with a thin bar that appears only when the content is taller.

    Put widgets in `.content`. The wheel works over the area and over everything inside it - bound
    per widget as they appear, never with `bind_all` (which would take the wheel from every other
    window, CLAUDE.md Known Issues). The canvas follows the theme.
    """

    def __init__(self, parent, on: Optional[str] = None, stretch: bool = False, **kw):
        super().__init__(parent, on=on, **kw)
        self.stretch = stretch
        ctx = C.get()
        # The canvas asks for almost nothing: the area takes the space it is given, and the bar -
        # in its own grid column - keeps its width however small that is.
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, width=1, height=1,
                                bg=ctx.plane_colour(self.plane), **NO_AUTOSTYLE)
        self.scrollbar = ThinScrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        # Keep viewport allocation with its host's pack cycle. A retained grid
        # allocation can outlive a native host shrink and cover sibling actions.
        self.canvas.pack(side='left', fill='both', expand=True)
        self.content = Frame(self.canvas, on=self.plane)
        self._window = self.canvas.create_window((0, 0), window=self.content, anchor='nw')
        self.content.bind('<Configure>', self._content_resized, add='+')
        self.canvas.bind('<Configure>', self._canvas_resized, add='+')
        self._bound = weakref.WeakSet()
        self._bar_needed, self._bar_job = False, None
        self._timer_root = ctx.root
        self._layout_job = None
        self._layout_width = self._layout_floor = None
        self._unsubscribe = ctx.manager.subscribe(self._retheme)
        self.bind('<Destroy>', self._on_destroy, add='+')
        self.bind('<Map>', self._viewport_resized, add='+')
        self.bind('<Configure>', self._viewport_resized, add='+')
        self.bind_wheel(self.canvas)
        self.bind_wheel(self.content)

    # --- theme ---
    def _retheme(self, manager) -> None:
        try:
            self.canvas.configure(bg=C.get().plane_colour(self.plane))
        except tk.TclError:
            pass

    def _on_destroy(self, event=None) -> None:
        if event is None or event.widget is self:
            self._unsubscribe()
            if self._layout_job is not None:
                cancel_window_callback(self._timer_root, self, self._layout_job)
                self._layout_job = None
            if self._bar_job is not None:
                try:
                    self._timer_root.after_cancel(self._bar_job)
                except tk.TclError:
                    pass
                self._bar_job = None

    # --- scrolling ---
    def _on_scroll(self, first, last) -> None:
        self.scrollbar.set(first, last)
        self._bar_needed = float(first) > 0.0 or float(last) < 1.0
        if self._bar_job is None:
            # Defer viewport changes until the canvas's redraw has returned.
            self._bar_job = self._timer_root.after_idle(self._sync_bar)

    def _sync_bar(self) -> None:
        self._bar_job = None
        try:
            shown = bool(self.scrollbar.winfo_manager())
            if self._bar_needed and not shown:
                self.scrollbar.pack(side='right', fill='y', before=self.canvas)
            elif not self._bar_needed and shown:
                self.scrollbar.pack_forget()
        except tk.TclError:
            pass                                             # destroyed meanwhile

    def _content_resized(self, event=None) -> None:
        self._schedule_layout()

    def _viewport_resized(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        self._schedule_layout()

    def _schedule_layout(self):
        if self._layout_job is None:
            self._layout_job = schedule_window_callback(self._timer_root, self, 'idle', self._sync_layout)

    def _sync_layout(self):
        self._layout_job = None
        if not self.canvas.winfo_exists() or not self.content.winfo_exists():
            return
        width = max(1, self.canvas.winfo_width())
        if width != self._layout_width:
            self._layout_width = width
            self.canvas.itemconfigure(self._window, width=width)
        self._stretch_content()
        bounds = self.canvas.bbox('all')
        current = tuple(map(float, self.canvas.tk.splitlist(self.canvas.cget('scrollregion'))))
        if bounds and tuple(bounds) != current:
            self.canvas.configure(scrollregion=bounds)

    def _stretch_content(self):
        if self.stretch:
            height = max(1, self.canvas.winfo_height())
            if height != self._layout_floor:
                self._layout_floor = height
                self.content.columnconfigure(0, weight=1)
                self.content.rowconfigure(0, weight=1, minsize=height)

    def _canvas_resized(self, event) -> None:
        self._schedule_layout()

    def bind_wheel(self, widget) -> None:
        """Make the wheel scroll this area while the pointer is over `widget` (and its children,
        as they are added: call again, or let `adopt()` do it)."""
        if widget in self._bound:
            return
        self._bound.add(widget)
        widget.bind('<MouseWheel>', self._wheel, add='+')
        widget.bind('<FocusIn>', lambda event: self.reveal(event.widget, C.get().px(8)), add='+')

    def adopt(self, widget=None) -> None:
        """Bind the wheel on `widget` (default: the content) and all its descendants."""
        widget = widget or self.content
        self.bind_wheel(widget)
        for child in widget.winfo_children():
            self.adopt(child)

    def _wheel(self, event) -> str:
        if self.canvas.yview() != (0.0, 1.0):
            self.canvas.yview_scroll(int(-event.delta / 120) * 3, 'units')
        return 'break'

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0)

    def reveal(self, widget, margin: int = 0) -> None:
        """Scroll so `widget` (anywhere inside the content) is wholly in view, `margin` pixels clear
        of the edge it was beyond. Nothing moves when it is already in view or the content fits."""
        top_window = self.winfo_toplevel()
        if hasattr(top_window, 'update_idletasks'):
            top_window.update_idletasks()
        else:
            self.update_idletasks()
        if getattr(self, '_layout_job', None) is not None:
            self._sync_layout()
        total, view = self.content.winfo_height(), self.canvas.winfo_height()
        if total <= view:
            return
        top = widget.winfo_rooty() - self.content.winfo_rooty()
        bottom = top + widget.winfo_height()
        first = self.canvas.canvasy(0)
        if top - margin >= first and bottom + margin <= first + view:
            return
        self.canvas.yview_moveto(max(0.0, min(1.0, (top - margin) / total)))


class NavigationView(Frame):
    """The left-hand list of a window with several pages (Settings): an icon and a name each, the
    current one softly filled with a short accent bar. Up and Down move between entries.

    `items` is [(key, label, icon_name), ...]; `on_select(key)` runs when the user picks one.
    """

    def __init__(self, parent, items: Sequence[Tuple[str, str, str]], value: Optional[str] = None,
                 on_select: Optional[Callable] = None, on: Optional[str] = None, **kw):
        super().__init__(parent, on=on, **kw)
        ctx = C.get()
        p = ctx.palette
        self.variable = tk.StringVar(master=self, value=value or items[0][0])
        self._on_select = on_select
        self.buttons = {}
        self._order = [key for key, _, _ in items]
        self._nav_icons = {key: icon for key, _, icon in items}
        for key, label, icon in items:
            button = Choice(
                self, text=f"  {label}", variable=self.variable, value=key, takefocus=True,
                style=name('nav', self.plane, 'TRadiobutton'), command=self._changed,
                image=(ctx.icon(icon, 18, p.muted), 'selected', ctx.icon(icon, 18, p.soft_text)),
                compound='left')
            button.pack(fill='x', pady=ctx.px(1))
            button.bind('<Up>', lambda e, k=key: self._move(k, -1))
            button.bind('<Down>', lambda e, k=key: self._move(k, 1))
            self.buttons[key] = button
        self._unsubscribe = ctx.manager.subscribe(self._retheme)
        self.bind('<Destroy>', lambda event: self._unsubscribe() if event.widget is self else None, add='+')

    def _retheme(self, manager):
        ctx, p = C.get(), manager.palette
        for key, button in self.buttons.items():
            icon = self._nav_icons[key]
            button.configure(image=(ctx.icon(icon, 18, p.muted), 'selected', ctx.icon(icon, 18, p.soft_text)))

    def _changed(self) -> None:
        if self._on_select:
            self._on_select(self.value)

    def _move(self, key: str, step: int) -> str:
        index = (self._order.index(key) + step) % len(self._order)
        target = self._order[index]
        self.value = target
        self.buttons[target].focus_set()
        self._changed()
        return 'break'

    @property
    def value(self) -> str:
        return self.variable.get()

    @value.setter
    def value(self, key: str) -> None:
        self.variable.set(key)
