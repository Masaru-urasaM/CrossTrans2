"""
The Translator window's widgets (TODO.md U7).

`TranslatorApp.show_popup()` used to build all of this in one 350-line method out of raw `tk` and
ttkbootstrap widgets with literal colours and fixed pixel sizes. This class builds it from the
design system and hands the app the widgets it works with, under the names the app and its tests
have always used (`original_text`, `translate_btn`, ...). It owns no behaviour: what a button does
is a callback, given with `configure_callbacks()` like the other extracted modules.

Source tools share the source pane's width: attachments first, editable source language last.
The translation target stays above its own pane. A themed swap button sits between the two panes,
including their vertical arrangement on narrow windows (`translator_layout`). More options and
the bottom action row retain their existing behavior.
"""
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from src.core import tone_prompts
from src.ui import components as ui
from src.ui import translator_layout as layout
from src.ui.components import context as C
from src.ui.components.dialog import work_area
from src.ui.theme import engine, tokens
from src.ui.theme.compat import NO_AUTOSTYLE
from src.ui.i18n import tr
from src.utils.ui_helpers import schedule_window_callback, cancel_window_callback
from src.ui.deferred_resize import DeferredContentResize, defer_update

SOURCE_AUTO = 'Auto-detect'
MIDDLE_DOT = chr(0x00B7)
TRANSLATE = 'Translate'
FIX_GRAMMAR = 'Fix grammar'
OPTIONS_OPEN, OPTIONS_CLOSED = 'chevron-down', 'chevron-right'
CUSTOM_PLACEHOLDER = 'Custom instruction, for example: keep the names as they are'
SWAP_ICONS = {layout.SIDE: 'swap-horizontal', layout.STACKED: 'swap-vertical'}
GAP = 12                    # between the cards, and the swap button's column on each side
CARD_PADDING = 16
BODY_ROWS = 4               # rows of the source box the window needs at least (the body's floor)


class TranslatorWindow:
    """Builds the window, answers for its widgets and its layout, calls back for everything else."""

    CALLBACKS = ('on_translate', 'on_fix_grammar', 'on_cancel', 'on_close', 'on_copy', 'on_expand',
                 'on_dictionary', 'on_history', 'on_settings', 'on_swap', 'on_language_change',
                 'on_retheme', 'on_resize', 'on_source_change')

    def __init__(self, root, config, languages: Sequence[Tuple[str, str, str]]):
        self.root, self.config, self.languages = root, config, list(languages)
        self.ctx = C.get()
        self.callbacks: Dict[str, Callable] = {}
        self.window: Optional[tk.Toplevel] = None
        self.mode: Optional[str] = None
        self._arrange_job = None
        self._unsubscribe = None
        self.tone_vars: Dict[str, tk.BooleanVar] = {}
        self._syncing_tones = False
        self._busy = False
        self._empty_input = self._empty_output = True
        self._input_focused = False
        self._last_size = None
        self._last_saved_size = None

    def configure_callbacks(self, **callbacks) -> None:
        unknown = set(callbacks) - set(self.CALLBACKS)
        if unknown:
            raise TypeError(f"unknown callbacks: {sorted(unknown)}")
        self.callbacks.update(callbacks)

    def _call(self, name: str, *args):
        callback = self.callbacks.get(name)
        return callback(*args) if callback else None

    # ------------------------------------------------------------------ #
    # Building
    # ------------------------------------------------------------------ #
    def build(self, original: str, target_language: str, *, recent: Callable[[], List[str]],
              pinned: Callable[[], List[str]], fix_grammar: bool = True) -> tk.Toplevel:
        """Create the window and every widget; it is shown by `show()`."""
        ctx = self.ctx
        px = ctx.px
        self.window = w = tk.Toplevel(self.root)
        self._empty_input = not bool(original.strip())
        w.withdraw()
        w.title('CrossTrans')
        w.sizes_from_work_area = True       # so a gallery judges its rule, not one screen's pixels
        engine.attach(w)
        size = layout.window_size(work_area(), self.config.get_main_window_size(), ctx.scale)
        x, y = layout.window_position(size, work_area())
        w.geometry(f'{size.width}x{size.height}+{x}+{y}')
        w.minsize(size.min_width, size.min_height)

        from src.ui.main_workspace import MainWorkspace
        self._content_resize = DeferredContentResize(w, background=ctx.manager.palette.bg,
            outline_colour=ctx.manager.palette.accent,
            size=(size.width, size.height), on_start=self._resize_started,
            on_commit=self._resize_committed, on_settled=self.remember_size,
            editors=lambda: (getattr(self, 'original_text', None), getattr(self, 'trans_text', None)))
        self.workspace = MainWorkspace(w, None, lambda: self._call('on_settings'),
                                       container=self._content_resize.host)
        w.bind('<Map>', lambda event: self._content_resize.attach_observer()
               if event.widget is w else None, add='+')
        self.frame = ui.Frame(self.workspace.translate_host, on='bg', padding=(px(24), px(16), px(24), px(16)))
        self.frame.pack(fill='both', expand=True)

        # The action row is packed first, at the bottom, so it is never the thing a small window
        # pushes out (V11); the options and the body take what is left.
        self.actions = ui.Frame(self.frame)
        self.actions.pack(side='bottom', fill='x', pady=(px(GAP), 0))
        self.options = ui.Frame(self.frame)
        self.body_area = ui.ScrollArea(self.frame, stretch=True)
        self.body_area.pack(side='top', fill='both', expand=True)
        self.body = ui.Frame(self.body_area.content)
        self.body.grid(row=0, column=0, sticky='nsew')

        self._build_body(original, target_language, recent, pinned)
        self._build_options()
        self._build_actions(fix_grammar)
        self.body_area.adopt()
        self._fit_text_floor()
        self.sync_empty()

        self.mode = None
        self.arrange(layout.layout_mode(size.width, ctx.scale))
        self.frame.bind('<Configure>', self._on_configure, add='+')
        w.bind('<Configure>', self._on_configure, add='+')
        self._unsubscribe = ctx.manager.subscribe(self._retheme)
        w.bind('<Destroy>', self._on_destroy, add='+')
        return w

    def show(self) -> None:
        w = self.window
        w.deiconify()
        w.update_idletasks()
        w.focus_force()

    def _build_body(self, original, target_language, recent, pinned) -> None:
        ctx = self.ctx
        px = ctx.px
        body = self.body
        # One grid for the language bar and the cards: [source | swap | translation], so each
        # language control stands over its own card. The two panes share their width (uniform).
        body.columnconfigure(0, weight=1, uniform='pane')
        body.columnconfigure(1, weight=0, minsize=px(tokens.CONTROL_HEIGHT) + 2 * px(GAP))
        body.columnconfigure(2, weight=1, uniform='pane')
        body.rowconfigure(0, weight=0)
        body.rowconfigure(2, weight=1)

        # --- the language bar ---
        self.source_bar = ui.Frame(body)
        self.source_bar.columnconfigure(0, weight=1)
        self.attach_host = ui.Frame(self.source_bar)
        self.attach_host.grid(row=0, column=0, sticky='ew', padx=(0, px(8)))
        self.source_chip = ui.SourceLanguagePicker(self.source_bar,
            on_change=lambda name: self._call('on_source_change', name))
        self.source_chip.grid(row=0, column=1, sticky='e')
        self.swap_host = ui.Frame(body)
        self.swap_host.rowconfigure(0, weight=1)
        self.swap_btn = ui.IconButton(self.swap_host, SWAP_ICONS[layout.SIDE],
            command=lambda: self._call('on_swap'), label='Swap languages', size=20, tone='text')
        self.swap_btn.grid(row=0, column=0)
        self.target_bar = ui.Frame(body)
        self.picker = ui.LanguagePicker(self.target_bar, self.languages, target_language,
                                        on_change=lambda name: self._call('on_language_change', name),
                                        recent=recent, pinned=pinned, label='Translate to')
        self.picker.pack(side='left')
        # --- the cards ---
        self.source_pane = ui.Frame(body)
        self.translation_pane = ui.Frame(body)
        for pane in (self.source_pane, self.translation_pane):
            pane.columnconfigure(0, weight=1)
            pane.rowconfigure(0, weight=1)
        self.source_card = ui.Card(self.source_pane, padding=CARD_PADDING)
        self.source_card.grid(row=0, column=0, sticky='nsew')
        self._build_source(self.source_card, original)
        self.translation_card = ui.Card(self.translation_pane, padding=CARD_PADDING)
        self.translation_card.grid(row=0, column=0, sticky='nsew')
        self._build_translation(self.translation_card)
        self.source_footer.bind('<Configure>', self._align_swap, add='+')

    def _fit_text_floor(self):
        if defer_update(self.window, 'main-text-floor', self._fit_text_floor):
            return
        line = tkfont.Font(root=self.root, font=self.ctx.font('reading')).metrics('linespace')
        minimum = 3 * (line + 4)
        self.source_card.rowconfigure(1, minsize=minimum)
        self.translation_card.rowconfigure(1, minsize=minimum)

    def _text_box(self, parent, editable: bool, script: str = 'latin', **options) -> tk.Text:
        """A plain `tk.Text` wearing the palette, for a card's surface."""
        ctx = self.ctx
        p = ctx.palette
        box = tk.Text(parent, wrap='word', width=1, height=1, borderwidth=0, highlightthickness=0,
                      relief='flat', padx=0, pady=0, spacing1=2, spacing3=2,
                      bg=ctx.plane_colour(parent.plane), fg=p.text, insertbackground=p.text,
                      selectbackground=p.selection, selectforeground=p.selection_text,
                      inactiveselectbackground=p.selection, font=ctx.font('reading', script),
                      cursor='xterm', **NO_AUTOSTYLE, **options)
        return box

    def _build_source(self, card, original: str) -> None:
        ctx = self.ctx
        px = ctx.px
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        self.source_header = ui.Frame(card)                        # empty - and no height - until asked
        self.source_header.grid(row=0, column=0, sticky='ew')
        self.source_row = ui.Frame(self.source_header)             # the Japanese / Chinese question (K5)
        holder = ui.Frame(card)
        holder.grid(row=1, column=0, sticky='nsew')
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.original_text = self._text_box(holder, True, undo=True, maxundo=-1)
        self.original_text.insert('1.0', original)
        self.original_text.grid(row=0, column=0, sticky='nsew')
        self.input_placeholder = ui.Label(holder, 'Enter text to translate', kind='muted',
                                          on='surface', cursor='xterm', wraplength=px(180), justify='left')
        self.input_placeholder.bind('<Button-1>', self._focus_input)
        self.original_text.bind('<FocusIn>', lambda event: self._input_focus_changed(True), add='+')
        self.original_text.bind('<FocusOut>', lambda event: self._input_focus_changed(False), add='+')
        holder.bind('<Configure>', lambda event: self.input_placeholder.configure(
            wraplength=max(1, event.width - px(4))), add='+')
        self._input_holder = holder
        self.original_bar = ui.AutoScrollbar(holder, self.original_text)
        self._bind_input_keys(self.original_text)
        self.source_footer = footer = ui.Frame(self.source_pane)
        footer.grid(row=1, column=0, sticky='ew', pady=(px(8), 0))
        self.dict_btn = ui.Button(footer, 'Dictionary', command=lambda: self._call('on_dictionary'),
                                  kind='subtle', icon='book')
        self.dict_btn.pack(side='left')
        self.count_label = ui.Label(footer, '', kind='muted', localize=False, justify='right',
                                   anchor='e', wraplength=px(220))
        self.count_label.pack(side='right', fill='x', expand=True, padx=(px(8), 0))
        footer.bind('<Configure>', self._fit_count, add='+')

    def _build_translation(self, card) -> None:
        ctx = self.ctx
        px = ctx.px
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        self.error_host = ui.Frame(card)                           # a failure is told here, in words
        self.error_host.grid(row=0, column=0, sticky='ew')
        self.error_bar = None
        self.output_host = ui.Frame(card)                          # the app puts its RubyText here
        self.output_host.grid(row=1, column=0, sticky='nsew')
        self.output_host.columnconfigure(0, weight=1)
        self.output_host.rowconfigure(0, weight=1)
        self.output_placeholder = ui.Label(self.output_host, 'Translation appears here', kind='muted',
                                           on='surface', wraplength=px(180), justify='left')
        self.output_host.bind('<Configure>', lambda event: self.output_placeholder.configure(
            wraplength=max(1, event.width - px(4))), add='+')
        self.translation_footer = footer = ui.Frame(self.translation_pane)
        footer.grid(row=1, column=0, sticky='ew', pady=(px(8), 0))
        self.copy_btn = ui.Button(footer, 'Copy', command=lambda: self._call('on_copy'), kind='subtle',
                                  icon='copy')
        self.copy_btn.pack(side='left')
        self.expand_btn = ui.Button(footer, 'Expand', command=lambda: self._call('on_expand'),
                                    kind='subtle', icon='arrows-fullscreen')
        self.expand_btn.pack(side='left', padx=(px(4), 0))


    def _bind_input_keys(self, box: tk.Text) -> None:
        box.bind('<MouseWheel>', lambda e: box.yview_scroll(int(-3 * (e.delta / 120)), 'units'))
        for sequence, action in (('<Control-z>', box.edit_undo), ('<Control-Z>', box.edit_undo),
                                 ('<Control-Shift-z>', box.edit_redo), ('<Control-Shift-Z>', box.edit_redo)):
            box.bind(sequence, lambda e, a=action: a() or 'break')

    def _build_options(self) -> None:
        """"More translate options": the tone chips write the instruction, the field is free text."""
        ctx = self.ctx
        px = ctx.px
        self.options_open = False
        self.options_panel = ui.Frame(self.options)
        self.custom_prompt = ui.TextField(self.options_panel, placeholder=CUSTOM_PLACEHOLDER)
        self.custom_prompt.pack(side='bottom', fill='x', expand=True, pady=(px(8), 0))
        self.custom_prompt.bind('<KeyRelease>', lambda e: self._sync_tones(), add='+')

        for key, label, _ in tone_prompts.TONES:
            variable = tk.BooleanVar(master=self.window, value=False)
            self.tone_vars[key] = variable
            ui.Toggle(self.options_panel, label, variable=variable,
                      command=lambda k=key: self._tone_clicked(k)).pack(side='left', padx=(0, px(6)))

    def _build_actions(self, fix_grammar: bool) -> None:
        px = self.ctx.px
        self.options_btn = ui.Button(self.actions, 'More translate options', command=self.toggle_options,
                                     kind='subtle', icon=OPTIONS_CLOSED)
        self.fix_grammar_btn = None
        if fix_grammar:
            self.fix_grammar_btn = ui.Button(self.actions, FIX_GRAMMAR,
                command=lambda: self._call('on_fix_grammar'), kind='soft', icon='magic')
        self.history_group = ui.Frame(self.actions)
        self.history_btn = ui.Button(self.history_group, 'History', command=lambda: self._call('on_history'),
                                     kind='soft', icon='clock-history')
        self.history_btn.pack(side='left')
        self.linked_bar = ui.Frame(self.history_group)
        self.linked_bar.pack(side='left', fill='y', padx=(px(6), 0))
        self.history_group.bind('<Configure>', self._fit_actions, add='+')
        self.translate_btn = ui.Button(self.actions, TRANSLATE, command=lambda: self._call('on_translate'),
                                       kind='accent', icon='translate')
        self.actions.columnconfigure(0, weight=1)
        self.busy_frame = ui.Frame(self.actions)
        self.status_label = ui.Label(self.busy_frame, '', kind='muted')
        self.status_label.pack(side='left', padx=(0, px(8)))
        self.cancel_btn = ui.Button(self.busy_frame, 'Cancel', command=lambda: self._call('on_cancel'),
                                    kind='soft')
        self.cancel_btn.pack(side='left')
        self._actions_mode = None
        self._action_rows = []
        self.actions.bind('<Configure>', self._fit_actions, add='+')
        for widget in (self.options_btn, self.fix_grammar_btn, self.history_group, self.translate_btn):
            if widget is not None:
                widget.bind('<Configure>', self._fit_actions, add='+')
        self._fit_actions()

    def set_fix_grammar_visible(self, visible):
        if self.fix_grammar_btn is not None:
            self.fix_grammar_btn.destroy()
            self.fix_grammar_btn = None
        if visible:
            self.fix_grammar_btn = ui.Button(self.actions, FIX_GRAMMAR,
                command=lambda: self._call('on_fix_grammar'), kind='soft', icon='magic')
            self.fix_grammar_btn.configure(state='disabled' if self._busy else 'normal')
            self.fix_grammar_btn.bind('<Configure>', self._fit_actions, add='+')
        self._actions_mode = None
        self._fit_actions()

    def _fit_actions(self, event=None):
        if defer_update(self.window, 'main-action-fit', self._fit_actions):
            return
        px, width = self.ctx.px, self.actions.winfo_width()
        widgets = [self.options_btn]
        if self.fix_grammar_btn:
            widgets.append(self.fix_grammar_btn)
        widgets += [self.history_group, self.translate_btn]
        row = column = used = 0
        positions = []
        for widget in widgets:
            wanted = widget.winfo_reqwidth()
            if column and width > 1 and used + px(6) + wanted > width:
                row, column, used = row + 1, 0, 0
            positions.append((row, column))
            used += wanted + (px(6) if column else 0)
            column += 1
        signature = (width, positions, self._busy,
                     tuple((str(widget), widget.winfo_reqwidth(), widget.winfo_reqheight()) for widget in widgets),
                     self.busy_frame.winfo_reqheight() if self._busy else 0)
        if signature == self._actions_mode:
            return
        self._actions_mode = signature
        # Keep each action in its actual parent. Cross-parent pack(in_=row) leaves
        # Tk MaintainGeometry idle records vulnerable during repeated window teardown.
        y = 0
        for row in range(max(position[0] for position in positions) + 1):
            members = [widget for widget, position in zip(widgets, positions) if position[0] == row]
            height = max(widget.winfo_reqheight() for widget in members)
            if self.options_btn in members:
                self.options_btn.place(x=0, y=y + (height - self.options_btn.winfo_reqheight()) // 2,
                                       anchor='nw')
                members.remove(self.options_btn)
            right = width
            for widget in reversed(members):
                widget.lift()
                widget.place(x=right, y=y + (height - widget.winfo_reqheight()) // 2, anchor='ne')
                right -= widget.winfo_reqwidth() + px(6)
            y += height + px(6)
        y = max(1, y - px(6))
        if self._busy:
            self.busy_frame.place(x=0, y=y + px(6), relwidth=1)
            y += px(6) + self.busy_frame.winfo_reqheight()
        else:
            self.busy_frame.place_forget()
        self.actions.configure(height=y)

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def arrange(self, mode: str) -> None:
        """Put the cards side by side (SIDE) or one above the other (STACKED)."""
        if mode == self.mode:
            return
        self.mode = mode
        px = self.ctx.px
        gap = px(GAP)
        for widget in (self.source_bar, self.swap_host, self.target_bar, self.source_pane,
                       self.translation_pane):
            widget.grid_forget()
        body = self.body
        for row in range(5):
            body.rowconfigure(row, weight=0)
        for column in (0, 2):
            body.columnconfigure(column, uniform='pane' if mode == layout.SIDE else '')
        self._set_swap_icon(mode)
        self._align_swap()
        if mode == layout.SIDE:
            body.rowconfigure(1, weight=1)
            self.source_bar.grid(row=0, column=0, sticky='ew', pady=(0, px(8)))
            self.swap_host.grid(row=1, column=1, sticky='ns')
            self.target_bar.grid(row=0, column=2, sticky='new',
                                 pady=(px(8) if getattr(self, '_attachments_visible', False) else 0, px(8)))
            self.source_pane.grid(row=1, column=0, sticky='nsew')
            self.translation_pane.grid(row=1, column=2, sticky='nsew')
        else:
            body.rowconfigure(1, weight=1)
            body.rowconfigure(4, weight=1)
            self.source_bar.grid(row=0, column=0, columnspan=3, sticky='ew', pady=(0, px(8)))
            self.source_pane.grid(row=1, column=0, columnspan=3, sticky='nsew')
            self.swap_host.grid(row=2, column=0, columnspan=3, pady=(gap, gap))
            self.target_bar.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(0, px(8)))
            self.translation_pane.grid(row=4, column=0, columnspan=3, sticky='nsew')
        self._call('on_resize', mode)

    def _set_swap_icon(self, mode: str) -> None:
        button = self.swap_btn
        button.icon_name = SWAP_ICONS[mode]
        button.set_on(False)

    def _align_swap(self, event=None) -> None:
        """Center on the text card, excluding the source's footer from the rail."""
        footer = self.source_footer.winfo_reqheight() + self.ctx.px(8) if self.mode == layout.SIDE else 0
        self.swap_host.rowconfigure(1, minsize=footer)

    def _on_configure(self, event) -> None:
        if getattr(self, '_content_resize', None) and self._content_resize.holding:
            return
        if event.widget not in (self.window, self.frame) or self._arrange_job is not None:
            return
        # Consume the latest dimensions once per pending frame, never restart a
        # trailing debounce. Native grid geometry continues independently.
        self._arrange_job = schedule_window_callback(self.root, self.window, 16, self._arrange_for_width)

    def _arrange_for_width(self) -> None:
        self._arrange_job = None
        if getattr(self, '_content_resize', None) and self._content_resize.holding:
            return
        try:
            size = (self.frame.winfo_width(), self.frame.winfo_height())
            if size == self._last_size:
                return
            previous_size, self._last_size = self._last_size, size
            previous_mode = self.mode
            self.arrange(layout.resize_mode(size[0], self.mode, self.ctx.scale))
            if previous_mode == self.mode and (previous_size is None or size[1] != previous_size[1]):
                self._call('on_resize', self.mode)
        except tk.TclError:
            pass

    def _resize_started(self):
        if self._arrange_job is not None:
            cancel_window_callback(self.root, self.window, self._arrange_job)
            self._arrange_job = None

    def _resize_committed(self, width, height):
        self._resize_started()
        self._arrange_job = schedule_window_callback(self.root, self.window, 'idle', self._arrange_for_width)

    # ------------------------------------------------------------------ #
    # Small state the app drives
    # ------------------------------------------------------------------ #
    def set_source_label(self, language: Optional[str]) -> None:
        """What the chip over the source card says: 'Auto-detect', or 'Auto-detect . Japanese' once
        the language is settled (the app only says one it can prove)."""
        self.source_chip.tooltip.text = language or SOURCE_AUTO

    def set_attachments_visible(self, visible: bool) -> None:
        self._attachments_visible = visible
        if visible:
            self.attach_host.grid()
        else:
            self.attach_host.grid_remove()
        self.source_chip.grid_configure(sticky='ne', pady=(self.ctx.px(8) if visible else 0, 0))
        self.target_bar.grid_configure(pady=(self.ctx.px(8) if visible and self.mode == layout.SIDE else 0,
                                             self.ctx.px(8)))

    def show_error(self, message: str, kind: str = 'danger', action: Optional[str] = None,
                   on_action: Optional[Callable] = None) -> None:
        """Tell a failure above the translation, in plain words, with what helps first as the
        bar's action. The raw notice stays in the box below as the details."""
        if defer_update(self.window, 'main-error', lambda: self.show_error(message, kind, action, on_action)):
            return
        self.clear_error()
        self.error_bar = ui.InfoBar(self.error_host, message, kind=kind, action=action, on_action=on_action)
        self.error_bar.pack(fill='x', pady=(0, self.ctx.px(8)))
        self.sync_empty()

    def clear_error(self) -> None:
        if defer_update(self.window, 'main-error', self.clear_error):
            return
        if self.error_bar is not None:
            try:
                self.error_bar.destroy()
            except tk.TclError:
                pass
            self.error_bar = None
        if hasattr(self, 'output_placeholder'):
            self.sync_empty()

    def set_swap_enabled(self, enabled: bool, reason: Optional[str] = None) -> None:
        self.swap_btn.configure(state='normal' if enabled else 'disabled')
        self.swap_btn.tooltip.text = reason or ('Swap languages' if enabled else
                                               'Choose a supported source language to swap.')

    def set_count(self, characters: int, words: Optional[int] = None) -> None:
        if defer_update(self.window, 'main-count', lambda: self.set_count(characters, words)):
            return
        if words is None:
            words = len(self.original_text.get('1.0', 'end-1c').split())
        self._counts = characters, words
        char_label = tr('{count} character' if characters == 1 else '{count} characters', count=f'{characters:,}')
        word_label = tr('{count} word' if words == 1 else '{count} words', count=f'{words:,}')
        self.count_label.configure(text=char_label + ' · ' + word_label)

    def _fit_count(self, event=None):
        if defer_update(self.window, 'main-count-fit', self._fit_count):
            return
        room = max(self.ctx.px(30), self.source_footer.winfo_width()
                   - self.dict_btn.winfo_reqwidth() - self.ctx.px(12))
        if abs(float(self.count_label.cget('wraplength')) - room) > 2:
            self.count_label.configure(wraplength=room)

    def set_input_script(self, script: str) -> None:
        if defer_update(self.window, 'main-input-script', lambda: self.set_input_script(script)):
            return
        """The input box's font follows the script it holds (Yu Gothic for Japanese, ...)."""
        self.original_text.configure(font=self.ctx.font('reading', script))

    def set_busy(self, busy: bool, status: str = '') -> None:
        """While a translation runs: Translate is unavailable, a status and a Cancel show beside
        it. The button keeps its meaning and its colour (V25)."""
        if defer_update(self.window, 'main-busy', lambda: self.set_busy(busy, status)):
            return
        self._busy = busy
        self.sync_empty()
        state = 'disabled' if busy else 'normal'
        if busy:
            self.set_status(status, 0)
        self._fit_actions()
        self.translate_btn.configure(state=state)
        if self.fix_grammar_btn is not None:
            self.fix_grammar_btn.configure(state=state)

    def sync_empty(self, original=None, translated=None):
        if original is not None:
            self._empty_input = not bool(original.strip())
        if translated is not None:
            self._empty_output = not bool(translated.strip())
        for label, visible in ((self.input_placeholder, self._empty_input and not self._input_focused),
                               (self.output_placeholder, self._empty_output and not self._busy
                                and self.error_bar is None)):
            if visible:
                label.place(x=0, y=self.ctx.px(3))
                label.lift()
            else:
                label.place_forget()

    def _focus_input(self, event=None):
        self._input_focus_changed(True)
        self.original_text.focus_set()
        self.original_text.mark_set('insert', '1.0')
        return 'break'

    def _input_focus_changed(self, focused):
        self._input_focused = focused
        try:
            self.sync_empty()
        except tk.TclError:
            pass  # Focus can leave while the owning window is being destroyed.

    def set_status(self, status: str, dots: int = 0) -> None:
        """The running request's status: its words and 0-3 dots, in a label as wide as the longest
        of them, so the dots never move what is beside them."""
        if not defer_update(self.window, 'main-status', lambda: self.set_status(status, dots)):
            self.status_label.configure(text=status + '.' * dots, width=len(status) + 3)
            if self._busy:
                self._fit_actions()

    # --- More translate options -----------------------------------------------------------------------
    def toggle_options(self) -> None:
        self.show_options(not self.options_open)

    def show_options(self, show: bool) -> None:
        if defer_update(self.window, 'main-options', lambda: self.show_options(show)):
            return
        if show == self.options_open:
            return
        self.options_open = show
        px = self.ctx.px
        if show:
            self.options.pack(side='bottom', fill='x', before=self.body_area)
            self.options_panel.pack(fill='x', pady=(px(GAP), 0))
        else:
            self.options_panel.pack_forget()
            # Tk retains an empty frame's last requested height. Unmap the wrapper too.
            self.options.pack_forget()
        self.options_btn.set_icon(OPTIONS_OPEN if show else OPTIONS_CLOSED)

    def custom_instruction(self) -> str:
        """What the user wants beyond translating, '' when nothing: the placeholder is never it."""
        return self.custom_prompt.get().strip()

    def _tone_clicked(self, key: str) -> None:
        if self._syncing_tones:
            return
        self.custom_prompt.set(tone_prompts.toggle(self.custom_prompt.get(), key))
        self._sync_tones()

    def _sync_tones(self) -> None:
        """Light the chips whose sentence is in the field."""
        lit = set(tone_prompts.active(self.custom_prompt.get()))
        self._syncing_tones = True
        try:
            for key, variable in self.tone_vars.items():
                variable.set(key in lit)
        finally:
            self._syncing_tones = False

    # --- the keyboard ---------------------------------------------------------------------------
    # ------------------------------------------------------------------ #
    # Theme and teardown
    # ------------------------------------------------------------------ #
    def _retheme(self, manager) -> None:
        """The `tk.Text` boxes are not styled by ttk: they take the palette by hand. The app
        recolours what it owns (the translation box, the Reading pane) through the callback."""
        if defer_update(self.window, 'main-theme', lambda: self._retheme(manager)):
            return
        p = manager.palette
        self._content_resize.set_background(p.bg)
        self._content_resize.set_outline_colour(p.accent)
        try:
            surface = self.ctx.plane_colour('surface')
            self.original_text.configure(bg=surface, fg=p.text, insertbackground=p.text,
                                         selectbackground=p.selection, selectforeground=p.selection_text,
                                         inactiveselectbackground=p.selection)
        except tk.TclError:
            return
        self._fit_text_floor()
        self._fit_actions()
        if hasattr(self, '_counts'):
            self.set_count(*self._counts)
        self._call('on_retheme', manager)

    def _on_destroy(self, event) -> None:
        if event.widget is not self.window:
            return
        if getattr(self, '_content_resize', None):
            self._content_resize.close()
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._arrange_job is not None:
            cancel_window_callback(self.root, self.window, self._arrange_job)
            self._arrange_job = None

    def remember_size(self) -> None:
        """Keep the size the user left the window at (not while it is maximised or minimised)."""
        try:
            if self.window.state() == 'normal':
                size = self.window.winfo_width(), self.window.winfo_height()
                if size != self._last_saved_size:
                    self.config.set_main_window_size(*size)
                    self._last_saved_size = size
        except tk.TclError:
            pass
