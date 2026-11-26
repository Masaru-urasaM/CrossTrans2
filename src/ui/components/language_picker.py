"""
The Translator's language chooser (TODO.md U7, UQ8, V19).

It used to be a search field above a two-row list: two of 104 languages visible, no memory of the
last choice. Now it is a field-like button naming the language; its dropdown opens on the languages
the user has already said they use (Recent, and Pinned - the hotkey languages) with every language
below, and typing searches all of them. Arrow keys move, Enter chooses, Esc closes.

The dropdown is a borderless toplevel that takes the keyboard focus so the user can type, and it
closes the moment focus leaves it. It never calls `grab_set()` (CLAUDE.md Known Issues: a grab on a
window hanging off a withdrawn root freezes the application) and it wears the palette in force when
it opens - it lives a few seconds, so it does not follow a theme change.
"""
import time
import tkinter as tk
from src.utils.ui_helpers import schedule_window_callback
from typing import Callable, List, Optional, Sequence, Tuple

from src.core import language_prefs as prefs
from src.ui.components import context as C
from src.ui.components.feedback import Tooltip
from src.ui.components.widgets import AutoScrollbar, Button, Frame, TextField
from src.ui.theme import windows
from src.ui import i18n
from src.ui.theme.compat import NO_AUTOSTYLE

ROWS = 9                         # rows visible before the list scrolls
DROP_MIN_WIDTH = 300             # at 100 %: a language name and its native name fit
REOPEN_GUARD_S = 0.25            # a click on the button that closed the dropdown must not reopen it
HEADER = 'header'
LANGUAGE = 'language'


class LanguagePicker(Frame):
    """A button that opens a searchable list of languages.

    `languages` is `constants.LANGUAGES` ((name, code, native name) tuples); `value` the name
    shown; `on_change(name)` is called when the user chooses one (not when `set()` is called).
    `recent` and `pinned` are callables returning language names, read each time the dropdown opens.
    """

    PLACEHOLDER = 'Search languages'         # the search field's hint (`ModelPicker` says its own)

    def __init__(self, parent, languages: Sequence[Tuple[str, str, str]], value: str,
                 on_change: Callable[[str], None], recent: Optional[Callable[[], List[str]]] = None,
                 pinned: Optional[Callable[[], List[str]]] = None, label: str = 'Translate to',
                 on: Optional[str] = None, **kw):
        super().__init__(parent, on=on, **kw)
        self.languages = list(languages)
        self._value = value
        self._on_change = on_change
        self._recent = recent or (lambda: [])
        self._pinned = pinned or (lambda: [])
        self.label = label
        self.window: Optional[tk.Toplevel] = None
        self.rows: List[Tuple[str, object]] = []          # what the open list shows
        self._closed_at = 0.0
        self.button = Button(self, value, command=self.toggle, kind='soft', on=self.plane)
        self.button.set_icon('chevron-down')
        self.button.configure(compound='right')
        self.button.pack(fill='x')
        self.tooltip = Tooltip(self.button, label)
        self.button.bind('<Down>', lambda e: self._open_from_key(), add='+')
        self.bind('<Destroy>', lambda e: self.close() if e.widget is self else None, add='+')

    # --- the value ------------------------------------------------------------------------------
    @property
    def value(self) -> str:
        return self._value

    def set(self, name: str) -> None:
        """Show `name` without calling `on_change`."""
        self._value = name
        self.button.configure(text=name)

    # --- opening and closing --------------------------------------------------------------------
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
        if self.is_open:
            return
        ctx = C.get()
        p = ctx.palette
        win = tk.Toplevel(self, bg=p.surface, highlightthickness=1, highlightbackground=p.border,
                          highlightcolor=p.border, **NO_AUTOSTYLE)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes('-topmost', True)
        self.window = win
        pad = ctx.px(8)
        self.query = tk.StringVar()
        self.search = TextField(win, placeholder=self.PLACEHOLDER, textvariable=self.query, on='surface')
        self.search.pack(fill='x', padx=pad, pady=(pad, ctx.px(4)))
        body = tk.Frame(win, bg=p.surface, **NO_AUTOSTYLE)
        body.pack(fill='both', expand=True, padx=(pad, ctx.px(4)), pady=(0, pad))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(
            body, height=ROWS, activestyle='none', highlightthickness=0, borderwidth=0,
            exportselection=False, selectmode='browse', bg=p.surface, fg=p.text,
            selectbackground=p.soft, selectforeground=p.soft_text, font=ctx.font('body'),
            **NO_AUTOSTYLE)
        self.listbox.grid(row=0, column=0, sticky='nsew')
        self.scrollbar = AutoScrollbar(body, self.listbox)
        self._revealed = False
        self.listbox.bind('<Configure>', self._first_layout)
        self.query.trace_add('write', lambda *args: self._populate())
        self._bind_keys(win)
        self._populate(select=self._value)
        self._place(win, ctx)
        win.deiconify()
        win.update_idletasks()
        self._reveal()                      # the list could not scroll to the row until it was laid out
        win.bind('<Map>', lambda e, w=win, pal=p: windows.style_floating(w, pal) if e.widget is w else None,
                 add='+')
        win.focus_force()
        self.search.focus_set()

    def close(self) -> None:
        win, self.window = self.window, None
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

    def _place(self, win, ctx) -> None:
        place_below(win, self.button, ctx, ctx.px(DROP_MIN_WIDTH))

    # --- the rows -------------------------------------------------------------------------------
    def _populate(self, select: Optional[str] = None) -> None:
        shown = prefs.sections(self.languages, self.query.get() if hasattr(self, 'query') else '',
                               self._recent(), self._pinned())
        query = self.query.get().strip() if hasattr(self, 'query') else ''
        if query and i18n.language() == 'ja':
            matches = [item for item in self.languages if query.lower() in i18n.tr(item[0]).lower()]
            known = {item[0] for section in shown for item in section.languages}
            extra = [item for item in matches if item[0] not in known]
            if extra:
                existing = [item for section in shown for item in section.languages]
                shown = [prefs.Section(prefs.RESULTS, tuple(existing + extra))]
        self.rows = []
        self.listbox.delete(0, 'end')
        p = C.get().palette
        wanted = None
        for section in shown:
            if len(shown) > 1 or section.title != prefs.RESULTS:
                self.listbox.insert('end', i18n.tr(section.title))
                index = self.listbox.size() - 1
                self.rows.append((HEADER, section.title))
                self.listbox.itemconfigure(index, foreground=p.muted, background=p.surface,
                                           selectforeground=p.muted, selectbackground=p.surface)
            for language in section.languages:
                self.listbox.insert('end', '   ' + _row_text(language))
                self.rows.append((LANGUAGE, language))
                if wanted is None and select and language[0] == select:
                    wanted = self.listbox.size() - 1
        if wanted is None:
            wanted = self._first_language_row()
        self._select(wanted)

    def _first_language_row(self) -> Optional[int]:
        return next((i for i, (kind, _) in enumerate(self.rows) if kind == LANGUAGE), None)

    def _select(self, index: Optional[int]) -> None:
        self.listbox.selection_clear(0, 'end')
        if index is None:
            return
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        self._see(index)

    def _see(self, index: int) -> None:
        self.listbox.see(index)
        if index > 0 and self.rows[index - 1][0] == HEADER:
            self.listbox.see(index - 1)                    # show the section's title with its first row

    def _first_layout(self, event) -> None:
        """The list has its real height at its first <Configure>: scroll to the row once, then
        leave the scrolling to the user."""
        if not self._revealed and event.height > 1:
            self._revealed = True
            self._reveal()

    def _reveal(self) -> None:
        """Scroll to the highlighted row from the top, now that the list has its real height
        (`see()` on a list not yet laid out scrolls as if it were one row tall)."""
        picked = self.listbox.curselection()
        if picked:
            self.listbox.yview_moveto(0)
            self._see(picked[0])

    @property
    def selected_language(self) -> Optional[Tuple[str, str, str]]:
        picked = self.listbox.curselection()
        if picked and self.rows[picked[0]][0] == LANGUAGE:
            return self.rows[picked[0]][1]
        return None

    def _move(self, step: int) -> str:
        languages = [i for i, (kind, _) in enumerate(self.rows) if kind == LANGUAGE]
        if not languages:
            return 'break'
        picked = self.listbox.curselection()
        current = picked[0] if picked else None
        if current is None or current not in languages:
            self._select(languages[0] if step > 0 else languages[-1])
        else:
            at = languages.index(current) + step
            self._select(languages[max(0, min(at, len(languages) - 1))])
        return 'break'

    def choose(self, language: Optional[Tuple[str, str, str]] = None) -> None:
        """Choose `language` (default: the highlighted row), close, and tell the owner."""
        language = language or self.selected_language
        if language is None:
            return
        self.set(language[0])
        self.close()
        self._on_change(language[0])

    # --- keys, mouse, focus ---------------------------------------------------------------------
    def _bind_keys(self, win) -> None:
        for sequence, step in (('<Up>', -1), ('<Down>', 1), ('<Prior>', -ROWS), ('<Next>', ROWS)):
            self.search.bind(sequence, lambda e, s=step: self._move(s))
            self.listbox.bind(sequence, lambda e, s=step: self._move(s))
        for widget in (self.search, self.listbox):
            widget.bind('<Return>', lambda e: (self.choose(), 'break')[1])
            widget.bind('<Escape>', lambda e: (self.close(), 'break')[1])
        self.listbox.bind('<Motion>', self._hover)
        self.listbox.bind('<ButtonRelease-1>', self._click)
        win.bind('<FocusOut>', lambda e, owner=win: schedule_window_callback(
            self._root(), owner, 60, self._focus_left), add='+')

    def _hover(self, event) -> None:
        index = self.listbox.nearest(event.y)
        if 0 <= index < len(self.rows) and self.rows[index][0] == LANGUAGE:
            self._select(index)

    def _click(self, event) -> None:
        index = self.listbox.nearest(event.y)
        if 0 <= index < len(self.rows) and self.rows[index][0] == LANGUAGE:
            self.choose(self.rows[index][1])

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



class SourceLanguagePicker(LanguagePicker):
    """An editable source field with searchable suggestions and a real automatic mode."""
    def __init__(self, parent, value='Auto', on_change=None, label='Source language', on=None):
        from src.core.source_languages import languages, normalize_source
        self._normalize = normalize_source
        super().__init__(parent, languages(), 'Auto', on_change or (lambda value: None), label=label, on=on)
        self._editing = False
        self.field = TextField(self, width=18, on=self.plane)
        self.button.bind('<F2>', self._edit, add='+')
        self.button.bind('<KeyPress>', self._type_to_edit, add='+')
        self.set(value)
        self.field.bind('<Return>', lambda event: self._commit(), add='+')
        self.field.bind('<Escape>', lambda event: self._cancel_edit(), add='+')
        self.field.bind('<FocusOut>', lambda event: self._commit(), add='+')
        self.field.bind('<Down>', lambda event: self.open() or 'break')
        self._navigated = False
        i18n.register(self, '', 'refresh')
        self.bind('<Destroy>', lambda event: i18n.unregister(self)
                  if event.widget is self else None, add='+')

    def _refresh_language(self):
        previous_auto = self._auto_text
        self._auto_text = i18n.tr('Auto-detect')
        if self._value == 'Auto' and self.field.get() == previous_auto:
            self.field.set(self._auto_text)
            self.button.configure(text='Auto-detect')

    def _edit(self, event=None):
        self._editing = True
        self.field.place(x=0, y=0, relwidth=1, relheight=1)
        self.field.lift()
        self.field.focus_set()
        self.field.selection_range(0, 'end')
        return 'break'

    def _type_to_edit(self, event):
        if event.char and event.char.isprintable() and not event.state & 0x000c and event.char != ' ':
            self._edit()
            self.field.set(event.char)
            self.field.icursor('end')
            return 'break'

    @property
    def value(self):
        value = self.field.get()
        return 'Auto' if value in (i18n.tr('Auto-detect'), getattr(self, '_auto_text', ''), 'Auto-detect') else self._normalize(value) or 'Auto'

    def set(self, value):
        self._value = self._normalize(value) or 'Auto'
        if hasattr(self, 'field'):
            self._auto_text = i18n.tr('Auto-detect')
            self.field.set(self._auto_text if self._value == 'Auto' else self._value)
            self.button.configure(text='Auto-detect' if self._value == 'Auto' else self._value)
            self.field.place_forget()
            self._editing = False

    def _commit(self, value=None):
        if value is None:
            value = self.field.get()
        if value in (i18n.tr('Auto-detect'), getattr(self, '_auto_text', ''), 'Auto-detect'):
            value = 'Auto'
        previous = self._value
        self.set(value)
        if previous != self._value:
            self._on_change(self._value)
        return 'break'

    def _cancel_edit(self):
        self.set(self._value)
        return 'break'

    def open(self):
        self._commit()
        self._navigated = False
        super().open()

    def _place(self, win, ctx):
        place_below(win, self, ctx, ctx.px(DROP_MIN_WIDTH))

    def _move(self, step):
        self._navigated = True
        return super()._move(step)

    def choose(self, language=None):
        if language is not None:
            value = language[0]
        elif self._navigated:
            selected = self.selected_language
            value = selected[0] if selected else self.query.get()
        else:
            value = self.query.get().strip()
            if not value:
                selected = self.selected_language
                value = selected[0] if selected else 'Auto'
        self._commit(value)
        self.close()
        self.button.focus_set()

    def close(self):
        win, self.window = self.window, None
        if win is not None:
            self._closed_at = time.monotonic()
            try:
                win.destroy()
            except tk.TclError:
                pass

    def _focus_left(self):
        win = self.window
        if win is None:
            return
        try:
            focus = win.focus_get()
            if focus is not None and str(focus).startswith(str(win)):
                return
            if self.query.get().strip():
                self._commit(self.query.get())
        except (KeyError, tk.TclError):
            pass
        self.close()


def place_below(win, button, ctx, min_width: int = 0) -> None:
    """Put the dropdown `win` below `button`, or above it when the work area has no room; as wide as
    the button (and `min_width`, and what the window asks for) at least, and kept on the screen."""
    win.update_idletasks()
    width = max(button.winfo_width(), min_width, win.winfo_reqwidth())
    height = win.winfo_reqheight()
    left, top, right, bottom = _work_area(win)
    x = min(max(button.winfo_rootx(), left), right - width)
    below = button.winfo_rooty() + button.winfo_height() + ctx.px(4)
    y = below if below + height <= bottom else max(top, button.winfo_rooty() - height - ctx.px(4))
    win.geometry(f'{width}x{height}+{x}+{y}')


def _row_text(language: Tuple[str, str, str]) -> str:
    name, _, native = language
    shown = i18n.tr(name)
    return shown if not native or native == shown else f'{shown}   {native}'


def _work_area(window) -> Tuple[int, int, int, int]:
    try:
        from src.ui.components.dialog import work_area
        return work_area()
    except Exception:
        return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight()
