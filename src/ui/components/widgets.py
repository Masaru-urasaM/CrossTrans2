"""
The component set, part one (TODO.md U4b): buttons, labels, frames and cards, chips, a text field
with a real placeholder, a switch, a segmented control, thin scroll bars and progress.

Every component takes `on=` - the plane it sits on ('bg' the window, 'surface' a card, 'alt' a card
on a card) - and by default inherits it from its parent: a `Frame` and a `Card` carry a `.plane`
(a card's is the plane its *children* sit on), so
`Card(window)` -> `Label(card, ...)` just works, light or dark, at any scale.

Rules these classes keep, because the old UI broke each of them:
  - no literal colour, font or pixel size: palette, type ramp and `px()` only;
  - the keyboard works - Tab reaches it, Space and Enter act, a focus ring shows (styles.py);
  - nothing grabs input or takes the focus on its own;
  - a widget never outlives its image: icons are held by ttkbootstrap's cache.
"""
import tkinter as tk
from src.utils.ui_helpers import schedule_window_callback
from tkinter import ttk
from typing import Callable, List, Optional, Sequence, Tuple

from src.ui.components import context as C
from src.ui.components.styles import BUTTON_KINDS, INNER_PLANE, name
from src.ui.theme import tokens
from src.ui.i18n import LocalizedText
from src.ui import i18n


def plane_of(parent, on: Optional[str]) -> str:
    """The plane a component sits on: asked for, else its parent's, else the window's."""
    if on is not None:
        return on
    return getattr(parent, 'plane', 'bg')


# --- containers ------------------------------------------------------------------------------------
class Frame(ttk.Frame):
    """A plain frame painted with its plane's colour. `plane` is what its children sit on."""

    def __init__(self, parent, on: Optional[str] = None, **kw):
        C.get()
        self.plane = plane_of(parent, on)
        kw.setdefault('style', name('plane', self.plane, 'TFrame'))
        super().__init__(parent, **kw)


class Card(ttk.Frame):
    """A rounded surface with a hairline border. Children read `card.plane`."""

    def __init__(self, parent, on: Optional[str] = None, padding=16, **kw):
        ctx = C.get()
        outer = plane_of(parent, on)
        self.plane = INNER_PLANE[outer]
        kw.setdefault('style', name('card', outer, 'TFrame'))
        # `-padding` of a ttk.Frame is a widget option; the style's padding is not read for frames
        kw['padding'] = ctx.px(padding) if isinstance(padding, (int, float)) \
            else tuple(ctx.px(v) for v in padding)
        super().__init__(parent, **kw)


class Separator(ttk.Separator):
    def __init__(self, parent, orient: str = 'horizontal', on: Optional[str] = None, **kw):
        C.get()
        kw.setdefault('style', name('line', plane_of(parent, on), 'TSeparator'))
        super().__init__(parent, orient=orient, **kw)


# --- text ------------------------------------------------------------------------------------------
class Label(LocalizedText, ttk.Label):
    """kind: body, muted, strong, subtitle, title, caption, reading, mono."""

    def __init__(self, parent, text: str = '', kind: str = 'body', on: Optional[str] = None,
                 script: Optional[str] = None, **kw):
        ctx = C.get()
        self.plane = plane_of(parent, on)
        kw.setdefault('style', name(kind, self.plane, 'TLabel'))
        if script is not None:                       # e.g. a Japanese title: its own UI font
            from src.ui.components.styles import LABEL_FONT
            kw.setdefault('font', ctx.font(LABEL_FONT[kind], script))
        super().__init__(parent, text=text, **kw)


class Chip(LocalizedText, ttk.Label):
    """A static pill: a language pair, a status. tone 'soft' (accent tint) or 'tag' (neutral)."""

    def __init__(self, parent, text: str = '', tone: str = 'soft', on: Optional[str] = None, **kw):
        C.get()
        kw.setdefault('style', name('chip' if tone == 'soft' else 'tag', plane_of(parent, on), 'TLabel'))
        super().__init__(parent, text=text, **kw)


# --- buttons ---------------------------------------------------------------------------------------
class Button(LocalizedText, ttk.Button):
    """kind: accent (the one primary action), soft (default), subtle, danger. `icon` is a
    Bootstrap Icons name drawn before the text in the button's own text colour."""

    def __init__(self, parent, text: str = '', command: Optional[Callable] = None, kind: str = 'soft',
                 on: Optional[str] = None, icon: Optional[str] = None, icon_side: str = 'left', **kw):
        ctx = C.get()
        if kind not in BUTTON_KINDS:
            raise ValueError(f"unknown button kind {kind!r}; expected one of {BUTTON_KINDS}")
        destinations = {'Settings', 'Open Settings', 'Open settings', 'API keys',
                        'Open API keys', 'Language packs', 'Open Language packs',
                        'Open language packs', 'Open Translation settings', 'Open Shortcuts',
                        'Open Dictionary Settings', 'API Settings', 'Replace settings', 'How to get a key'}
        if text in destinations or 'settings' in text.casefold():
            icon, icon_side = 'gear', 'left'
        self.icon_side = icon_side
        self.kind, self.plane = kind, plane_of(parent, on)
        kw.setdefault('style', name(kind, self.plane, 'TButton'))
        kw.setdefault('takefocus', True)
        super().__init__(parent, text=text, command=command, **kw)
        self.icon_name = None
        if icon:
            self.set_icon(icon)
        self.bind('<Return>', self._invoke_on_enter)
        self._unsubscribe = ctx.manager.subscribe(self._retheme)
        self.bind('<Destroy>', self._on_destroy, add='+')

    def set_icon(self, icon: str) -> None:
        """Draw `icon` (a Bootstrap Icons name) before the text, in the button's own text colour -
        also how a button shows a moment of feedback (a tick on "Copied")."""
        ctx = C.get()
        p = ctx.palette
        fg = {'accent': p.on_accent, 'danger': p.on_accent, 'soft': p.soft_text,
              'subtle': p.text}[self.kind]
        self.icon_name = icon
        self._images = (ctx.icon(icon, 16, fg), ctx.icon(icon, 16, p.muted))
        self.configure(image=(self._images[0], 'disabled', self._images[1]), compound=self.icon_side)

    def _invoke_on_enter(self, event=None):
        if str(self.cget('state')) != 'disabled':
            self.invoke()
        return 'break'

    def _retheme(self, manager) -> None:
        """The icon's images are drawn in the theme's colours: draw them again for a new theme in a
        window that stays open."""
        try:
            self.configure(style=name(self.kind, self.plane, 'TButton'))
            if self.icon_name:
                self.set_icon(self.icon_name)
        except tk.TclError:
            pass

    def _on_destroy(self, event=None) -> None:
        if event is None or event.widget is self:
            self._unsubscribe()


class IconButton(ttk.Button):
    """A square, quiet button holding one icon; `label` is its tooltip and accessible name."""

    def __init__(self, parent, icon: str, command: Optional[Callable] = None, label: str = '',
                 on: Optional[str] = None, size: int = 16, tone: str = 'muted', **kw):
        ctx = C.get()
        self.plane, self.label, self.icon_name, self.size = plane_of(parent, on), label, icon, size
        self.is_on = False
        self.tone = tone
        kw.setdefault('style', name('icon', self.plane, 'TButton'))
        kw.setdefault('takefocus', True)
        # The face is one CONTROL_HEIGHT square image (styles.icon_button): pad the icon to exactly
        # that, in physical pixels, so the widget is the image's size at any scale.
        box = ctx.style.scaling.logical(tokens.CONTROL_HEIGHT)
        icon_px = int(ctx.root.tk.call('image', 'width', ctx.icon(icon, size, ctx.palette.muted)))
        before = max(0, (box - icon_px) // 2)
        after = max(0, box - icon_px - before)
        kw.setdefault('padding', (before, before, after, after))
        super().__init__(parent, command=command, **kw)
        self._paint(getattr(ctx.palette, tone), getattr(ctx.palette, tone)
                    if tone in ('danger', 'on_accent') else ctx.palette.text)
        self.bind('<Return>', lambda e: self.invoke() or 'break')
        if label:
            from src.ui.components.feedback import Tooltip
            self.tooltip = Tooltip(self, label)
        self._unsubscribe = ctx.manager.subscribe(self._retheme)
        self.bind('<Destroy>', self._on_destroy, add='+')

    def _retheme(self, manager) -> None:
        try:
            self.set_on(self.is_on)
        except tk.TclError:
            pass

    def _on_destroy(self, event=None) -> None:
        if event is None or event.widget is self:
            self._unsubscribe()

    def _paint(self, resting: str, hover: str) -> None:
        ctx = C.get()
        self._images = (ctx.icon(self.icon_name, self.size, resting),
                        ctx.icon(self.icon_name, self.size, hover),
                        ctx.icon(self.icon_name, self.size,
                                 ctx.palette.danger if self.tone == 'danger' else ctx.palette.border))
        self.configure(image=(self._images[0], 'active', self._images[1], 'disabled', self._images[2]),
                       compound='image')

    def set_on(self, flag: bool) -> None:
        """For an icon that is a switch: on shows the soft tinted face and an accent-coloured icon."""
        p = C.get().palette
        self.is_on = bool(flag)
        self.configure(style=name('iconon' if self.is_on else 'icon', self.plane, 'TButton'))
        resting = getattr(p, self.tone)
        self._paint(*((p.soft_text, p.soft_text) if self.is_on else
                      (resting, resting if self.tone in ('danger', 'on_accent') else p.text)))


# --- choosing ------------------------------------------------------------------------------------------
class Choice(LocalizedText, ttk.Radiobutton):
    """One pill of a group that shares a variable: a tone, a language pair, a filter."""

    def __init__(self, parent, text: str, variable: tk.Variable, value, command=None,
                 on: Optional[str] = None, **kw):
        C.get()
        kw.setdefault('style', name('choice', plane_of(parent, on), 'TRadiobutton'))
        kw.setdefault('takefocus', True)
        super().__init__(parent, text=text, variable=variable, value=value, command=command, **kw)


class Toggle(LocalizedText, ttk.Checkbutton):
    """A pill that is on or off (a filter). For a setting, use `Switch`."""

    def __init__(self, parent, text: str, variable: Optional[tk.Variable] = None, command=None,
                 on: Optional[str] = None, **kw):
        C.get()
        kw.setdefault('style', name('choice', plane_of(parent, on), 'TCheckbutton'))
        kw.setdefault('takefocus', True)
        super().__init__(parent, text=text, variable=variable, command=command, **kw)


class SegmentedControl(Frame):
    """A row of mutually exclusive pills ('Japanese | Chinese', 'Formal | Casual | Concise').
    `options` is [(value, label), ...]; `on_change(value)` runs when the user picks one."""

    def __init__(self, parent, options: Sequence[Tuple[object, str]], value=None,
                 on_change: Optional[Callable] = None, on: Optional[str] = None, gap: int = 6, icons=None, **kw):
        super().__init__(parent, on=on, **kw)
        self.icons = icons or {}
        self._gap = gap
        self._layout = None
        self.variable = tk.StringVar(master=self, value='' if value is None else str(value))
        self._values = {str(v): v for v, _ in options}
        self._on_change = on_change
        self.buttons: List[Choice] = []
        for index, (option, label) in enumerate(options):
            pill = Choice(self, label, self.variable, str(option), command=self._changed, on=self.plane)
            self.buttons.append(pill)
        self._paint_icons()
        self._icons_unsubscribe = C.get().manager.subscribe(lambda manager: self._paint_icons())
        self.bind('<Configure>', self._fit, add='+')
        self._parent_fit = parent.bind('<Configure>', self._fit, add='+')
        self.bind('<Destroy>', self._release_layout, add='+')
        self._fit()

    def _paint_icons(self):
        self._tab_images = []
        for option, button in zip(self._values.values(), self.buttons):
            if option in self.icons:
                picture = C.get().icon(self.icons[option], 16, C.get().palette.text)
                self._tab_images.append(picture)
                button.configure(image=picture, compound='left')

    def _fit(self, event=None):
        # The host's allocated width is independent of the rows this control requests.
        room = self.master.winfo_width()
        gap = C.get().px(self._gap)
        row, column, used, layout = 0, 0, 0, []
        for button in self.buttons:
            width = button.winfo_reqwidth()
            if column and room > 1 and used + gap + width > room:
                row, column, used = row + 1, 0, 0
            layout.append((row, column))
            used += (gap if column else 0) + width
            column += 1
        if layout == self._layout:
            return
        self._layout = layout
        for button, (row, column) in zip(self.buttons, layout):
            button.grid(row=row, column=column, sticky='w',
                        padx=(gap if column else 0, 0), pady=(gap if row else 0, 0))

    def _release_layout(self, event):
        if event.widget is self and self._parent_fit:
            self.master.unbind('<Configure>', self._parent_fit)
            self._parent_fit = None
            self._icons_unsubscribe()

    def _changed(self):
        if self._on_change:
            self._on_change(self.value)

    @property
    def value(self):
        return self._values.get(self.variable.get())

    @value.setter
    def value(self, option) -> None:
        self.variable.set(str(option))


# --- fields -------------------------------------------------------------------------------------------
class TextField(ttk.Entry):
    """A one-line field with a real placeholder: muted, never mistaken for content, never sent.

    `get()` returns '' while the placeholder is showing. `invalid(True)` rings it in the danger
    colour. With a `textvariable` the variable holds what the user typed (not the placeholder).
    """

    def __init__(self, parent, placeholder: str = '', textvariable: Optional[tk.StringVar] = None,
                 on: Optional[str] = None, **kw):
        ctx = C.get()
        self.plane = plane_of(parent, on)
        kw.setdefault('style', name('field', self.plane, 'TEntry'))
        self._placeholder = i18n.tr(placeholder)
        self._showing = False
        self._variable = textvariable
        super().__init__(parent, **kw)
        i18n.register(self, placeholder, 'placeholder')
        self._muted = ctx.palette.muted
        self._text = ctx.palette.text
        self.bind('<FocusIn>', self._focus_in, add='+')
        self.bind('<FocusOut>', self._focus_out, add='+')
        if textvariable is not None:
            # The entry has no `textvariable` of its own (the placeholder is real text and must not reach
            # it), so the variable is kept up with the typing here. A paste or cut is read once the class
            # binding has done it - ours runs first.
            self.bind('<KeyRelease>', self._typed, add='+')
            for sequence in ('<<Paste>>', '<<Cut>>'):
                self.bind(sequence, lambda e: schedule_window_callback(
                    self._root(), self, 'idle', self._typed), add='+')
        self._unsubscribe = ctx.manager.subscribe(self._retheme)
        self.bind('<Destroy>', self._on_destroy, add='+')
        if textvariable is not None and textvariable.get():
            super().insert(0, textvariable.get())
            self._sync_colour()
        else:
            self._show_placeholder()

    def _set_localized_placeholder(self, text):
        showing = self._showing
        self._clear_placeholder()
        self._placeholder = text
        if showing:
            self._show_placeholder()

    # the placeholder is real text in the entry, so every read goes through here
    def get(self) -> str:
        return '' if self._showing else super().get()

    def set(self, text: str) -> None:
        self._clear_placeholder()
        super().delete(0, 'end')
        super().insert(0, text)
        if self._variable is not None:
            self._variable.set(text)
        if not text and self.focus_get() is not self:
            self._show_placeholder()
        self._sync_colour()

    def clear(self) -> None:
        self.set('')

    def insert(self, index, string):
        self._clear_placeholder()
        super().insert(index, string)
        self._sync_colour()

    def delete(self, first, last=None):
        self._clear_placeholder()
        super().delete(first, last)

    @property
    def showing_placeholder(self) -> bool:
        return self._showing

    def invalid(self, flag: bool = True) -> None:
        self.state(['invalid'] if flag else ['!invalid'])

    # --- internals ---
    def _show_placeholder(self) -> None:
        if self._placeholder and not super().get():
            self._showing = True
            super().insert(0, self._placeholder)
            self.configure(foreground=self._muted)

    def _clear_placeholder(self) -> None:
        if self._showing:
            self._showing = False
            super().delete(0, 'end')
            self.configure(foreground=self._text)

    def _sync_colour(self) -> None:
        self.configure(foreground=self._muted if self._showing else self._text)

    def _retheme(self, manager) -> None:
        """The two colours are cached (they are what the placeholder swaps between), so a theme
        change has to refresh them or a long-lived field keeps the old theme's text colour."""
        self._muted, self._text = manager.palette.muted, manager.palette.text
        try:
            self._sync_colour()
        except tk.TclError:
            pass

    def _on_destroy(self, event=None) -> None:
        if event is None or event.widget is self:
            self._unsubscribe()

    def _focus_in(self, event=None) -> None:
        self._clear_placeholder()

    def _typed(self, event=None) -> None:
        """Hand what was typed to the variable as it is typed, so a list that follows it can narrow with
        every letter. Only a change counts: an arrow key's own key-up must not look like typing."""
        if self._variable is None or self._showing:
            return
        try:
            text = super().get()
        except tk.TclError:
            return                                  # the field went away before the idle callback ran
        if text != self._variable.get():
            self._variable.set(text)

    def _focus_out(self, event=None) -> None:
        if not super().get():
            self._show_placeholder()
        if self._variable is not None and not self._showing:
            self._variable.set(super().get())


class Switch(LocalizedText, ttk.Checkbutton):
    """An on/off setting, label to the right of a pill-shaped switch."""

    def __init__(self, parent, text: str = '', variable: Optional[tk.BooleanVar] = None, command=None,
                 on: Optional[str] = None, **kw):
        C.get()
        kw.setdefault('style', name('switch', plane_of(parent, on), 'TCheckbutton'))
        kw.setdefault('takefocus', True)
        super().__init__(parent, text=text, variable=variable, command=command, **kw)


# --- scroll and progress --------------------------------------------------------------------------------
class ThinScrollbar(ttk.Scrollbar):
    """A thin rounded thumb on the window colour; grows on hover."""

    def __init__(self, parent, orient: str = 'vertical', command: Optional[Callable] = None, **kw):
        C.get()
        kw.setdefault('style', f"CtThin.{'Vertical' if orient == 'vertical' else 'Horizontal'}.TScrollbar")
        super().__init__(parent, orient=orient, command=command, **kw)


class AutoScrollbar(ThinScrollbar):
    """A thin bar for a text box or list that shows itself only while the content is taller than its
    window. It lives in its own grid cell - `row`, `column` of the parent's grid, beside the
    `target` - and keeps that cell's width however small the target is: put the target in
    `column - 1` with weight 1 and let this bar come and go.

    The bar is shown or hidden after idle, never from inside the target's own redraw: re-gridding a
    slave from there leaves the grid un-arranged after a resize (measured in `ScrollArea`).
    """

    def __init__(self, parent, target, row: int = 0, column: int = 1, **kw):
        super().__init__(parent, command=target.yview, **kw)
        self._cell = dict(row=row, column=column, sticky='ns')
        self._needed, self._job = False, None
        target.configure(yscrollcommand=self._on_scroll)
        self.bind('<Destroy>', self._on_destroy, add='+')

    @property
    def shown(self) -> bool:
        return bool(self.grid_info())

    def _on_scroll(self, first, last) -> None:
        self.set(first, last)
        self._needed = float(first) > 0.0 or float(last) < 1.0
        if self._job is None:
            self._job = self.after_idle(self._sync)

    def _sync(self) -> None:
        self._job = None
        try:
            if self._needed and not self.shown:
                self.grid(**self._cell)
            elif not self._needed and self.shown:
                self.grid_remove()
        except tk.TclError:
            pass                                             # destroyed meanwhile

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
            self._job = None


class Progress(ttk.Progressbar):
    def __init__(self, parent, mode: str = 'determinate', maximum: float = 100, **kw):
        C.get()
        kw.setdefault('style', 'CtBar.Horizontal.TProgressbar')
        kw.setdefault('length', C.get().px(220))
        super().__init__(parent, mode=mode, maximum=maximum, **kw)
        self._running = False
        self._interval = 50
        self._unsubscribe = C.get().manager.subscribe(self._motion_changed)
        self.bind('<Destroy>', self._destroyed, add='+')

    def start(self, interval=50):
        self._running, self._interval = True, interval
        if C.get().manager.animations_enabled:
            super().start(interval)

    def stop(self):
        self._running = False
        super().stop()

    def _motion_changed(self, manager):
        if self._running:
            if manager.animations_enabled:
                super().start(self._interval)
            else:
                super().stop()

    def _destroyed(self, event):
        if event.widget is self:
            self._unsubscribe()


class ActionBar(Frame):
    """Visible actions wrap to the available width instead of entering an overflow menu."""
    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.actions = []
        self._positions = None
        self.bind('<Configure>', self._fit_actions, add='+')

    def add(self, widget):
        self.actions.append(widget)
        widget.bind('<Configure>', self._fit_actions, add='+')
        self._fit_actions()
        return widget

    def _fit_actions(self, event=None):
        room, gap = self.winfo_width(), C.get().px(6)
        row = column = used = 0
        positions = []
        for widget in self.actions:
            width = widget.winfo_reqwidth()
            if column and room > 1 and used + gap + width > room:
                row, column, used = row + 1, 0, 0
            positions.append((row, column))
            used += width + (gap if column else 0)
            column += 1
        if positions == self._positions:
            return
        self._positions = positions
        for widget, (row, column) in zip(self.actions, positions):
            widget.grid(row=row, column=column, sticky='w',
                        padx=(gap if column else 0, 0), pady=(gap if row else 0, 0))
