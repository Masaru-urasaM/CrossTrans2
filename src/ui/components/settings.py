"""
The pieces of the Settings window (TODO.md U8, Decision 51): a card of setting rows, a dropdown for a
short list, and a shortcut editor.

A setting is a row - its name, one sentence under it, the control at the right - and a group of rows
is one card with hairlines between them (the Windows 11 Settings look). The old tabs were stacks of
labels and ttkbootstrap check buttons with grey 8 pt hints; nothing in them lined up and a setting's
words and its control were far apart.

`ShortcutEditor` shows a combination as keycaps and records a new one without ever running Tk code
from the keyboard hook: the hook only queues key events, and the editor drains the queue on the UI
thread while it is recording (the rule of Decision 43). The `source` is injectable, so a test feeds
it key events instead of touching the real keyboard.
"""
import math
import queue
from tkinter import font as tkfont
import time
import tkinter as tk
from src.utils.ui_helpers import schedule_window_callback, cancel_window_callback
from typing import Callable, List, Optional, Sequence, Tuple

from src.core import shortcut_recorder as recording
from src.core import shortcut_rules as rules
from src.ui.components import context as C
from src.ui.components.composite import Keys
from src.ui.components.language_picker import place_below
from src.ui.components.widgets import Button, Card, Frame, IconButton, Label, Separator, ThinScrollbar
from src.ui.theme import windows
from src.ui.i18n import tr
from src.ui import i18n
from src.ui.theme.compat import NO_AUTOSTYLE

ROW_PAD_X = 16               # at 100 %: inside a row, each side
ROW_PAD_Y = 12
GAP = 16                     # between a row's words and its control
WRAP_MIN = 160
ROWS = 8                     # rows a dropdown shows before it scrolls
REOPEN_GUARD_S = 0.25        # a click on the button that closed the list must not reopen it
POLL_MS = 25                 # how often a recording drains the key queue
NOT_SET = 'Not set'
PRESS_KEYS = 'Press the keys...'


# --------------------------------------------------------------------------------------------------
# Rows and groups
# --------------------------------------------------------------------------------------------------
class SettingRow(Frame):
    """One setting: `title`, an optional sentence, and a control host at the right.

    The caller places its control in `row.control` (a `Frame`). With `stacked=True` the control sits
    under the words and spans the row - for a list, a field or anything wider than a switch.
    `row.key` names the setting in `settings_index`; `highlight()` marks it for a moment (a search
    result the window scrolled to).
    """

    def __init__(self, parent, title: str, description: str = '', *, key: Optional[str] = None,
                 stacked: bool = False, on: Optional[str] = None):
        ctx = C.get()
        px = ctx.px
        super().__init__(parent, on=on, padding=(px(ROW_PAD_X), px(ROW_PAD_Y)))
        self.key, self.stacked, self.separator = key, stacked, None
        self._responsive_stacked = stacked
        self.hidden, self._group = False, None
        self.columnconfigure(0, weight=1, minsize=ctx.px(WRAP_MIN))
        words = Frame(self, on=self.plane)
        self.words = words
        words.grid(row=0, column=0, sticky='ew')
        self.title_label = Label(words, title, kind='strong', on=self.plane)
        self.title_label.pack(anchor='w')
        self.description_label = None
        if description:
            self.description_label = Label(words, description, kind='muted', on=self.plane,
                                           justify='left', wraplength=px(420))
            self.description_label.pack(anchor='w', pady=(px(2), 0))
        self.control = Frame(self, on=self.plane)
        if stacked:
            self.control.grid(row=1, column=0, sticky='ew', pady=(px(10), 0))
        else:
            self.control.grid(row=0, column=1, sticky='e', padx=(px(GAP), 0))
        self._bar = None
        self.bind('<Configure>', self._fit, add='+')
        self.control.bind('<Configure>', self._fit, add='+')

    def _fit(self, event=None) -> None:
        """Wrap the sentence to what is left of the row once the control and the padding have their
        share. Both are read off widgets that do not depend on the wrap, so asking again changes
        nothing and a wrap can never feed back into the width it came from (InfoBar.fit_to)."""
        if self.winfo_width() <= 1:
            return
        ctx = C.get()
        total = max(1, self.winfo_width() - 2 * ctx.px(ROW_PAD_X))
        control_width = self.control.winfo_reqwidth()
        # Switches/icons remain trailing controls. Wider controls have a hysteresis band so
        # a natural-size host cannot alternate between its horizontal and stacked requests.
        threshold = control_width + ctx.px(GAP + WRAP_MIN + (64 if self._responsive_stacked else 0))
        stacked = self.stacked or (control_width > ctx.px(96) and total < threshold)
        if stacked != self._responsive_stacked:
            self._responsive_stacked = stacked
            if stacked:
                self.control.grid_configure(row=1, column=0, sticky='ew', padx=0, pady=(ctx.px(10), 0))
            else:
                self.control.grid_configure(row=0, column=1, sticky='e', padx=(ctx.px(GAP), 0), pady=0)
        room = total
        if not stacked:
            room -= self.control.winfo_reqwidth() + ctx.px(GAP)
        room = max(1, room - ctx.px(4))
        for label in (self.title_label, self.description_label):
            if label is not None:
                current = int(float(str(label.cget('wraplength')) or 0))
                if abs(room - current) > 2:
                    label.configure(wraplength=room)

    def set_description(self, text: str) -> None:
        if self.description_label is not None:
            self.description_label.configure(text=text)

    def set_visible(self, visible: bool) -> None:
        """Show or hide the row. In a group the group lays out again, so a row comes back where it
        was and the first row shown never has a hairline above it."""
        self.hidden = not visible
        if self._group is not None:
            self._group.relayout()
        elif visible:
            self.pack(fill='x')
        else:
            self.pack_forget()

    @property
    def visible(self) -> bool:
        return bool(self.winfo_manager())

    def highlight(self, on: bool = True) -> None:
        """A short accent bar at the row's left edge."""
        ctx = C.get()
        if not on:
            if self._bar is not None:
                self._bar.place_forget()
            return
        if self._bar is None:
            self._bar = tk.Frame(self, bg=ctx.palette.accent, **NO_AUTOSTYLE)
        self._bar.configure(bg=ctx.palette.accent)
        self._bar.place(x=0, y=ctx.px(6), width=ctx.px(3), relheight=1.0, height=-ctx.px(12))

    @property
    def highlighted(self) -> bool:
        return self._bar is not None and bool(self._bar.winfo_manager())



class SettingGroup(Frame):
    """A card of setting rows with hairlines between them, and an optional heading above it."""

    def __init__(self, parent, heading: str = '', on: Optional[str] = None):
        ctx = C.get()
        super().__init__(parent, on=on)
        self.heading_label = None
        if heading:
            self.heading_label = Label(self, heading, kind='strong', on=self.plane)
            self.heading_label.pack(anchor='w', pady=(0, ctx.px(8)))
        self.card = Card(self, on=self.plane, padding=0)
        self.card.pack(fill='x')
        self.rows: List[SettingRow] = []

    def row(self, title: str, description: str = '', *, key: Optional[str] = None,
            stacked: bool = False) -> SettingRow:
        """Add a row (with a hairline above it unless it is the first shown) and return it."""
        row = SettingRow(self.card, title, description, key=key, stacked=stacked, on=self.card.plane)
        row.separator = Separator(self.card, on=self.card.plane)
        row._group = self
        self.rows.append(row)
        self.relayout()
        return row

    def relayout(self) -> None:
        """Pack the rows that are shown in order, a hairline between each pair."""
        px = C.get().px
        for row in self.rows:
            row.pack_forget()
            row.separator.pack_forget()
        first = True
        for row in self.rows:
            if row.hidden:
                continue
            if not first:
                row.separator.pack(fill='x', padx=px(ROW_PAD_X))
            row.pack(fill='x')
            first = False

    @property
    def shown_rows(self) -> List[SettingRow]:
        return [row for row in self.rows if not row.hidden]

    def by_key(self, key: str) -> Optional[SettingRow]:
        return next((row for row in self.rows if row.key == key), None)


# --------------------------------------------------------------------------------------------------
# A dropdown for a short list
# --------------------------------------------------------------------------------------------------
class Dropdown(Frame):
    """A button naming the current choice that opens a short list. `options` is [(value, label)];
    `on_change(value)` runs when the user chooses (not when `set()` is called).

    The list is a borderless toplevel that takes the keyboard focus, closes when focus leaves it and
    never calls `grab_set()` (CLAUDE.md Known Issues). For a long list that needs searching, use
    `LanguagePicker`.
    """

    def __init__(self, parent, options: Sequence[Tuple[object, str]], value,
                 on_change: Optional[Callable] = None, label: str = '', on: Optional[str] = None):
        super().__init__(parent, on=on)
        self.options: List[Tuple[object, str]] = list(options)
        self._value = value
        self._on_change = on_change
        self.window: Optional[tk.Toplevel] = None
        self.scrollbar = None
        self._closed_at = 0.0
        self.button = Button(self, self._label_of(value), command=self.toggle, kind='soft', on=self.plane)
        self.button.set_icon('chevron-down')
        self.button.configure(compound='right')
        self.button.pack(fill='x')
        self._refresh_language()
        i18n.register(self, '', 'refresh')
        if label:
            from src.ui.components.feedback import Tooltip
            self.tooltip = Tooltip(self.button, label)
        self.button.bind('<Down>', lambda e: self._open_from_key(), add='+')
        self.bind('<Destroy>', lambda e: self.close() if e.widget is self else None, add='+')

    def _refresh_language(self):
        font = tkfont.Font(root=self.button, font=C.get().font('body_strong'))
        widest = max(font.measure(tr(text)) for _, text in self.options)
        units = math.ceil(widest / max(1, font.measure('0')))
        self.button.configure(text=self._label_of(self._value), width=units)

    def _label_of(self, value) -> str:
        return next((text for option, text in self.options if option == value), str(value))

    @property
    def value(self):
        return self._value

    def set(self, value) -> None:
        self._value = value
        self.button.configure(text=self._label_of(value))

    @property
    def is_open(self) -> bool:
        return self.window is not None

    def toggle(self) -> None:
        if self.is_open:
            self.close()
        elif time.monotonic() - self._closed_at > REOPEN_GUARD_S:
            self.open()

    def _open_from_key(self) -> str:
        if not self.is_open:
            self.open()
        return 'break'

    def open(self) -> None:
        if self.is_open or not self.options:
            return
        ctx = C.get()
        p = ctx.palette
        win = tk.Toplevel(self, bg=p.surface, highlightthickness=1, highlightbackground=p.border,
                          highlightcolor=p.border, **NO_AUTOSTYLE)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes('-topmost', True)
        self.window = win
        pad = ctx.px(4)
        self.listbox = tk.Listbox(
            win, height=min(len(self.options), ROWS), activestyle='none', highlightthickness=0,
            borderwidth=0, exportselection=False, selectmode='browse', bg=p.surface, fg=p.text,
            selectbackground=p.soft, selectforeground=p.soft_text, font=ctx.font('body'), **NO_AUTOSTYLE)
        for _, text in self.options:
            self.listbox.insert('end', '  ' + tr(text))
        self.listbox.pack(side='left', fill='both', expand=True, padx=pad, pady=pad)
        if len(self.options) > ROWS:
            self.scrollbar = ThinScrollbar(win, command=self.listbox.yview)
            self.scrollbar.pack(side='right', fill='y', padx=(0, pad), pady=pad)
            self.listbox.configure(yscrollcommand=self.scrollbar.set)
        current = next((i for i, (option, _) in enumerate(self.options) if option == self._value), 0)
        self._select(current)
        for sequence, step in (('<Up>', -1), ('<Down>', 1), ('<Prior>', -ROWS), ('<Next>', ROWS)):
            self.listbox.bind(sequence, lambda e, s=step: self._move(s))
        self.listbox.bind('<Home>', lambda e: self._go(0))
        self.listbox.bind('<End>', lambda e: self._go(len(self.options) - 1))
        self.listbox.bind('<Return>', lambda e: (self.choose(), 'break')[1])
        self.listbox.bind('<Escape>', lambda e: (self.close(), 'break')[1])
        self.listbox.bind('<Motion>', self._hover)
        self.listbox.bind('<ButtonRelease-1>', self._click)
        win.bind('<FocusOut>', lambda e, owner=win: schedule_window_callback(
            self._root(), owner, 60, self._focus_left), add='+')
        place_below(win, self.button, ctx)
        win.deiconify()
        win.update_idletasks()
        win.bind('<Map>', lambda e, w=win, pal=p: windows.style_floating(w, pal) if e.widget is w else None,
                 add='+')
        win.focus_force()
        self.listbox.focus_set()

    def close(self) -> None:
        win, self.window = self.window, None
        self.scrollbar = None
        if win is None:
            return
        self._closed_at = time.monotonic()
        try:
            win.destroy()
        except tk.TclError:
            pass
        try:
            self.button.focus_set()
        except tk.TclError:
            pass

    def choose(self, index: Optional[int] = None) -> None:
        """Choose the row at `index` (default: the highlighted one), close, and tell the owner."""
        if index is None:
            picked = self.listbox.curselection() if self.is_open else ()
            index = picked[0] if picked else None
        if index is None or not 0 <= index < len(self.options):
            return
        value = self.options[index][0]
        self.set(value)
        self.close()
        if self._on_change:
            self._on_change(value)

    def _select(self, index: int) -> None:
        self.listbox.selection_clear(0, 'end')
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        self.listbox.see(index)

    def _go(self, index: int) -> str:
        self._select(max(0, min(index, len(self.options) - 1)))
        return 'break'

    def _move(self, step: int) -> str:
        picked = self.listbox.curselection()
        return self._go((picked[0] if picked else 0) + step)

    def _hover(self, event) -> None:
        index = self.listbox.nearest(event.y)
        if 0 <= index < len(self.options):
            self._select(index)

    def _click(self, event) -> None:
        index = self.listbox.nearest(event.y)
        if 0 <= index < len(self.options):
            self.choose(index)

    def _focus_left(self) -> None:
        win = self.window
        if win is None:
            return
        try:
            focus = win.focus_get()
        except (KeyError, tk.TclError):
            focus = None
        if focus is None or not str(focus).startswith(str(win)):
            self.close()


# --------------------------------------------------------------------------------------------------
# A shortcut
# --------------------------------------------------------------------------------------------------
class KeySource:
    """The real keyboard, through the `keyboard` library. Its callback runs on that library's own
    thread, so it only queues the event - it never touches Tk."""

    def __init__(self):
        self.events: 'queue.Queue[Tuple[str, str]]' = queue.Queue()
        self._remove = None

    def start(self) -> None:
        import keyboard
        self._remove = keyboard.hook(self._on_event)

    def stop(self) -> None:
        remove, self._remove = self._remove, None
        if remove is not None:
            try:
                import keyboard
                keyboard.unhook(remove)
            except Exception:
                pass

    def _on_event(self, event) -> None:
        self.events.put((event.event_type, event.name))


class ShortcutEditor(Frame):
    """A shortcut as keycaps, with Change (records a new one), Restore default and a line for what
    is wrong with it.

    `validate(combination) -> shortcut_rules.Verdict` judges a recording against the other
    shortcuts (default: the rules alone). An error keeps the old value and says why; a warning keeps
    the new one and says why it may not work. `on_change(combination)` runs for every accepted
    change, `on_recording(active)` when recording starts and ends (the app stops answering hotkeys
    meanwhile), `source_factory()` returns an object with `events`, `start()`, `stop()`.
    """

    def __init__(self, parent, value: str = '', *, owner: str = '', default: str = '',
                 validate: Optional[Callable] = None, on_change: Optional[Callable] = None,
                 on_recording: Optional[Callable] = None, source_factory: Optional[Callable] = None,
                 on: Optional[str] = None):
        super().__init__(parent, on=on)
        ctx = C.get()
        px = ctx.px
        self.owner, self.default = owner, rules.normalize(default)
        self._validate = validate or (lambda combo: rules.validate(combo, owner))
        self._on_change, self._on_recording = on_change, on_recording
        self._source_factory = source_factory or KeySource
        self._value = rules.normalize(value)
        self._source = None
        self._recorder = recording.ShortcutRecorder()
        self._poll_job = None
        self._message_kind = ''
        self._own_message = ('', '')            # (kind, text) from a verdict
        self._status = ''                       # what is wrong with a saved shortcut (another app holds it)
        self.recording = False
        self._line_wrapped = None
        self._size_job = None
        self._requested_size = None
        self.pack_propagate(False)

        self.line = Frame(self, on=self.plane)
        self.line.pack(fill='x')
        self.keys_host = Frame(self.line, on=self.plane)
        self.keys_host.pack(side='left', padx=(0, px(8)))
        self.keycaps = None
        self.hint = None
        self.restore_btn = None
        if self.default:
            self.restore_btn = IconButton(self.line, 'arrow-counterclockwise', command=self.restore,
                                          label='Restore the default', on=self.plane)
        self.change_btn = Button(self.line, 'Change', command=self.toggle_recording, kind='soft', on=self.plane)
        self.change_btn.pack(side='left')
        self.clear_btn = IconButton(self.line, 'x-lg', command=self.confirm_clear,
                                    label='Clear shortcut', on=self.plane, tone='danger')
        self.clear_btn.pack(side='left', padx=(px(4), 0))
        self.message = Label(self, '', kind='caption', on=self.plane, justify='right', wraplength=px(340))
        self._show_value()
        self.line.bind('<Configure>', self._fit_line, add='+')
        self.keys_host.bind('<Configure>', self._fit_line, add='+')
        self.bind('<Configure>', self._fit_line, add='+')
        self._unsubscribe = ctx.manager.subscribe(self._retheme)
        self.bind('<Destroy>', self._on_destroy, add='+')

    # --- the value ------------------------------------------------------------------------------
    @property
    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        """Show `value` without calling `on_change` (the app changed it, or Restore all)."""
        self._value = rules.normalize(value)
        self._show_value()

    def _show_value(self) -> None:
        self.clear_btn.configure(state='normal' if self._value and not self.recording else 'disabled')
        for child in self.keys_host.winfo_children():
            child.destroy()
        self.keycaps = self.hint = None
        caps = rules.display(self._value)
        if caps:
            self.keycaps = Keys(self.keys_host, caps, on=self.plane, on_layout=self._fit_line)
            self.keycaps.pack(fill='x', expand=True)
        else:
            self.hint = Label(self.keys_host, NOT_SET, kind='muted', on=self.plane)
            self.hint.pack()
        if self.restore_btn is not None:
            if self._value != self.default and not self.recording:
                if not self.restore_btn.winfo_manager():
                    self.restore_btn.pack(side='left', padx=(0, C.get().px(4)), before=self.change_btn)
            else:
                self.restore_btn.pack_forget()
        self._fit_line()

    def preferred_width(self):
        keys = self.keycaps.preferred_width() if self.keycaps is not None else self.keys_host.winfo_reqwidth()
        width = keys + self.change_btn.winfo_reqwidth() + self.clear_btn.winfo_reqwidth() + C.get().px(12)
        if self.restore_btn is not None and self._value != self.default and not self.recording:
            width += self.restore_btn.winfo_reqwidth() + C.get().px(4)
        return width

    def _fit_line(self, event=None):
        if self._size_job is None:
            self._size_job = schedule_window_callback(self._root(), self, 'idle', self._sync_size)

    def _sync_size(self):
        self._size_job = None
        preferred = self.preferred_width()
        width = self.winfo_width() if self.winfo_width() > 1 else preferred
        wrapped = preferred > width
        if wrapped != self._line_wrapped:
            self._line_wrapped = wrapped
            self.keys_host.pack_configure(side='top' if wrapped else 'left', fill='x',
                                          expand=not wrapped, anchor='w',
                                          padx=0 if wrapped else (0, C.get().px(8)),
                                          pady=(0, C.get().px(6)) if wrapped else 0)
            self._fit_line()
            return
        height = self.line.winfo_reqheight()
        if self.message.winfo_manager():
            room = max(1, width - C.get().px(4))
            if abs(float(self.message.cget('wraplength')) - room) > 2:
                self.message.configure(wraplength=room)
                self._fit_line()
            height += self.message.winfo_reqheight() + C.get().px(4)
        requested = max(1, preferred), max(1, height)
        if requested != self._requested_size:
            self._requested_size = requested
            # Preserve the complete inline request even while its controls wrap. The row
            # can then choose a layout without feeding its own allocated width back into it.
            self.configure(width=requested[0], height=requested[1])

    def restore(self) -> None:
        """Put the default back (through the same judgement as a recording)."""
        if self.recording:
            self.cancel_recording()
        self._accept(self.default)

    def confirm_clear(self):
        """Ask before changing the saved binding; closing the dialog always keeps it."""
        if not self._value or self.recording:
            return None
        from src.ui.components.dialog import Dialog
        dialog = Dialog(self.winfo_toplevel(), 'Remove this shortcut?',
                        'The shortcut will stop working until you assign it again.', icon='trash')
        dialog.add_button('No', default=True)
        dialog.add_button('Yes', command=self.clear, kind='danger')
        dialog.show()
        self.clear_dialog = dialog
        return dialog

    def clear(self) -> None:
        """An explicit empty binding is a saved choice, separate from recording validation."""
        if self.recording:
            self.cancel_recording()
        self.set_value('')
        self.set_status('')
        self.show_message('', '')
        if self._on_change:
            self._on_change('')

    # --- messages -------------------------------------------------------------------------------
    def show_message(self, kind: str, text: str) -> None:
        """The editor's own message about the value (`kind` is 'error' or 'warning'); '' text clears
        it. It outranks a status."""
        self._own_message = (kind, text) if text else ('', '')
        self._render_message()

    def set_status(self, text: str = '') -> None:
        """What is wrong with the saved shortcut itself - another app holds it - shown as a warning
        unless the editor has a message of its own; '' clears it."""
        self._status = text
        self._render_message()

    def _render_message(self) -> None:
        kind, text = self._own_message
        if not text and self._status:
            kind, text = 'warning', self._status
        self._message_kind = kind if text else ''
        self.message.configure(text=text)
        self._colour_message()
        if text:
            if not self.message.winfo_manager():
                self.message.pack(anchor='e', pady=(C.get().px(4), 0))
        else:
            self.message.pack_forget()
        self._fit_line()

    def revalidate(self) -> None:
        """Judge the current value again and say what is wrong with it, without changing it (a
        saved shortcut that has become a conflict, or one the Game Bar also wants)."""
        if not self._value or self.recording:
            return
        verdict = self._validate(self._value)
        self.show_message({rules.ERROR: 'error', rules.WARNING: 'warning'}.get(verdict.kind, ''),
                          verdict.message)

    def _colour_message(self) -> None:
        p = C.get().palette
        colour = {'error': p.danger, 'warning': p.warning}.get(self._message_kind, p.muted)
        self.message.configure(foreground=colour)

    @property
    def message_text(self) -> str:
        return str(self.message.cget('text'))

    def _retheme(self, manager) -> None:
        try:
            self._colour_message()
            self._fit_line()
        except tk.TclError:
            pass

    # --- recording ------------------------------------------------------------------------------
    def toggle_recording(self) -> None:
        if self.recording:
            self.cancel_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        if self.recording:
            return
        try:
            source = self._source_factory()
            source.start()
        except Exception as error:
            self.show_message('error', 'CrossTrans could not listen to the keyboard here.')
            import logging
            logging.warning(f"[Shortcuts] recording could not start: {error}")
            return
        self._source = source
        self._recorder.reset()
        self.recording = True
        self.clear_btn.configure(state='disabled')
        self.show_message('', '')
        self.change_btn.configure(text='Cancel')
        self._show_live('')
        if self._on_recording:
            self._on_recording(True)
        # The keys are read by the hook, not by this window: take the focus off the button, or its
        # Space and Enter would press it again.
        try:
            self.winfo_toplevel().focus_set()
        except tk.TclError:
            pass
        self._poll_job = self.after(POLL_MS, self._poll)

    def _show_live(self, live: str) -> None:
        for child in self.keys_host.winfo_children():
            child.destroy()
        self.keycaps = self.hint = None
        caps = rules.display(live)
        if caps:
            self.keycaps = Keys(self.keys_host, caps, on=self.plane, on_layout=self._fit_line)
            self.keycaps.pack(fill='x', expand=True)
        else:
            self.hint = Label(self.keys_host, PRESS_KEYS, kind='muted', on=self.plane)
            self.hint.pack()
        if self.restore_btn is not None:
            self.restore_btn.pack_forget()
        self._fit_line()

    def _poll(self) -> None:
        self._poll_job = None
        if not self.recording or self._source is None:
            return
        result = None
        shown = self._recorder.live
        while result is None:
            try:
                event_type, name = self._source.events.get_nowait()
            except queue.Empty:
                break
            result = self._recorder.feed(event_type, name)
        if result is None:
            if self._recorder.live != shown:
                self._show_live(self._recorder.live)
            self._poll_job = self.after(POLL_MS, self._poll)
        elif recording.cancelled(result):
            self.cancel_recording()
        else:
            self._finish(result)

    def _stop_source(self) -> None:
        source, self._source = self._source, None
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except (tk.TclError, ValueError):
                pass
            self._poll_job = None
        if source is not None:
            try:
                source.stop()
            except Exception:
                pass

    def _end_recording(self) -> None:
        was = self.recording
        self.recording = False
        self._stop_source()
        try:
            self.change_btn.configure(text='Change')
        except tk.TclError:
            return
        if was and self._on_recording:
            self._on_recording(False)

    def cancel_recording(self) -> None:
        if not self.recording:
            return
        self._end_recording()
        self._show_value()
        self._refocus()

    def _finish(self, combination: str) -> None:
        self._end_recording()
        self._accept(combination)
        self._refocus()

    def _accept(self, combination: str) -> None:
        verdict = self._validate(combination) if combination else rules.validate('', self.owner)
        if verdict.kind == rules.ERROR:
            self.show_message('error', verdict.message)
            self._show_value()
            return
        self._value = verdict.normalized
        self.show_message('warning' if verdict.kind == rules.WARNING else '', verdict.message)
        self._show_value()
        if self._on_change:
            self._on_change(self._value)

    def _refocus(self) -> None:
        # Later: the key that finished the recording is still down, and its release must not land
        # on the button it would press.
        schedule_window_callback(self._root(), self, 200, self._focus_button)

    def _focus_button(self) -> None:
        try:
            if self.winfo_viewable():
                self.change_btn.focus_set()
        except tk.TclError:
            pass

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        was = self.recording
        self.recording = False
        if self._size_job is not None:
            cancel_window_callback(self._root(), self, self._size_job)
            self._size_job = None
        self._stop_source()
        self._unsubscribe()
        if was and self._on_recording:
            self._on_recording(False)
