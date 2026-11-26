"""
The audio shortcut's control panel (Decision 88): a small window at the mouse cursor, like Win + G, from which a
recording is started, stopped and cancelled, with the source, the processing mode and the language beside it.

The panel only presents. `AudioController` records, recognizes and translates, and tells its watchers each
phase; the panel is one of them, so a recording started by the held shortcut shows here too. The result still
opens in the quick popup.

It stays on top and keeps its place until it is closed with its close button or Esc, and neither closes it while
a recording is running or being processed. It takes the keyboard focus when it opens. Space records or stops
wherever the focus is inside it (one handler, ahead of the buttons' own Space), Enter presses the focused control,
and Esc closes it.
"""
import tkinter as tk
from tkinter import filedialog
from typing import Callable, Dict, Optional, Tuple

from src.constants import LANGUAGES
from src.core.audio_media import MEDIA_EXTENSIONS, QUICK_LIMIT
from src.core.audio_service import one_step_outlook
from src.ui import components as ui, popup_layout
from src.ui.components.styles import name as style_name
from src.ui.i18n import tr
from src.ui.settings.audio_settings import AUTO_LANGUAGE, MODE_OPTIONS
from src.ui.theme import windows
from src.ui.theme.compat import NO_AUTOSTYLE

PANEL_PADDING = 16
PANEL_RING_PX = 1
PANEL_MIN_WIDTH = 380
METER_LENGTH = 168
STATUS_MIN_WRAP = 120
KEYS_TAG = 'CrossTransAudioPanelKeys'

# The panel's order: what plays on the PC, the microphone, both. The words are Settings' own.
SOURCES = (('pc', 'PC audio'), ('microphone', 'Microphone'), ('both', 'PC + Microphone'))
SOURCE_ICONS = {'pc': 'volume-up', 'microphone': 'mic', 'both': 'headset'}
DEVICE_ICONS = {'PC': 'volume-up', 'Microphone': 'mic'}
DEVICES_OF = {'pc': ('PC',), 'microphone': ('Microphone',), 'both': ('PC', 'Microphone')}
MODES = (('two_step', 'Two-step'), ('one_step', 'One-step'))
MODE_HINTS = dict(MODE_OPTIONS)

ONE_STEP_NEEDS_GEMINI = 'One-step needs a Google Gemini audio key.'
NO_AUDIO_KEY = 'Add a working API key in Settings > API keys to translate audio.'
ONE_STEP_WITH_BOTH = ('When both PC audio and the microphone have sound, the audio is recognized first and then '
                      'translated.')


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


class AudioPanel:
    """One panel per application; `open()` shows it at the cursor and `close()` hides it when idle."""

    def __init__(self, root, controller, config, *, on_settings: Optional[Callable] = None,
                 on_change: Optional[Callable] = None, work_area: Optional[Callable] = None):
        self.root, self.controller, self.config = root, controller, config
        self.on_settings = on_settings
        self.on_change = on_change          # on_change(settings_index key) after the panel stored an option
        self._work_area_at = work_area or windows.monitor_work_area
        self.window: Optional[tk.Toplevel] = None
        self._phase, self._info = 'idle', {}
        self._elapsed = 0.0
        self._drag: Optional[Tuple[int, int]] = None
        self._unsubscribe = None
        self._rebuild_job = None
        self.meters: Dict[str, ui.Progress] = {}

    # --- showing and closing --------------------------------------------------------------------
    def is_open(self) -> bool:
        return self.window is not None and self.window.winfo_exists()

    def open(self, pointer: Optional[Tuple[int, int]] = None) -> None:
        """Show the panel beside `pointer` (the cursor by default) and give it the keyboard focus. An open
        panel is only brought forward: it stays where the user put it."""
        if self.is_open():
            self._focus()
            return
        ctx = ui.get()
        p = ctx.palette
        window = tk.Toplevel(self.root, bg=p.surface, highlightthickness=ctx.px(PANEL_RING_PX),
                             highlightbackground=p.border, highlightcolor=p.border, **NO_AUTOSTYLE)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes('-topmost', True)
        window.title(tr('Audio translation'))
        window.bind('<Map>', lambda event: windows.style_floating(window, ui.get().palette)
                    if event.widget is window else None, add='+')
        window.bind('<Destroy>', self._destroyed, add='+')
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.window = window
        self._phase, self._info = self.controller.phase, {}
        self._build()
        x, y = pointer if pointer is not None else (self.root.winfo_pointerx(), self.root.winfo_pointery())
        width, height = self._size(x, y)
        left, top, _ = popup_layout.place((x, y), width, height, self._work(x, y), min_height=height)
        window.geometry(f'{width}x{height}+{left}+{top}')
        if self._watch not in self.controller.watchers:
            self.controller.watchers.append(self._watch)
        self._unsubscribe = ctx.manager.subscribe(self._preferences_changed)
        window.deiconify()
        self._focus()

    def close(self) -> bool:
        """Close the panel unless a recording is running or being processed (Cancel stops those)."""
        if not self.is_open():
            return True
        if self.controller.phase != 'idle':
            return False
        self.window.destroy()
        return True

    def rect(self) -> Optional[Tuple[int, int, int, int]]:
        """(left, top, width, height) of the open panel in screen pixels, else None."""
        if not self.is_open():
            return None
        self.window.update_idletasks()
        return (self.window.winfo_rootx(), self.window.winfo_rooty(),
                self.window.winfo_width(), self.window.winfo_height())

    def hotkey(self) -> None:
        """The audio shortcut while the panel is open: Record, or Stop."""
        self._focus()
        self.controller.toggle_recording()

    def _focus(self):
        try:
            self.window.lift()
            self.window.focus_force()
            target = self.record if str(self.record.cget('state')) != 'disabled' else self.window
            target.focus_set()
        except tk.TclError:
            pass

    def _destroyed(self, event):
        if event.widget is not self.window:
            return
        if self._watch in self.controller.watchers:
            self.controller.watchers.remove(self._watch)
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._rebuild_job is not None:
            try:
                self.root.after_cancel(self._rebuild_job)
            except tk.TclError:
                pass
            self._rebuild_job = None
        self.window, self.meters, self._drag = None, {}, None

    def _work(self, x, y) -> popup_layout.WorkArea:
        area = self._work_area_at(x, y)
        if area:
            return popup_layout.WorkArea(*area)
        return popup_layout.WorkArea(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight() - 50)

    def _size(self, x=None, y=None) -> Tuple[int, int]:
        """What the content asks for, no narrower than the minimum and no wider than the work area round (x, y)
        (the panel's own top-left corner by default)."""
        ctx = ui.get()
        self.status.configure(wraplength=ctx.px(STATUS_MIN_WRAP))   # the rest decides the width first
        self.window.update_idletasks()
        ring = 2 * ctx.px(PANEL_RING_PX)
        width = max(ctx.px(PANEL_MIN_WIDTH), self.body.winfo_reqwidth() + ring)
        if x is None:
            x, y = self.window.winfo_x(), self.window.winfo_y()
        width = min(width, self._work(x, y).width - 2 * popup_layout.SCREEN_MARGIN)
        # The status then wraps in the room the key hints leave it, and the height follows. A line of text without
        # spaces (Japanese) may run a character past `wraplength`, so the overflow is measured and taken off.
        room = (width - ring - 2 * ctx.px(PANEL_PADDING) - self.status_icon.winfo_reqwidth() - ctx.px(6)
                - self.hints.winfo_reqwidth() - ctx.px(12))
        wrap = max(ctx.px(STATUS_MIN_WRAP), room - self._status_inset)
        for _ in range(3):
            self.status.configure(wraplength=wrap)
            self.window.update_idletasks()
            over = self.status.winfo_reqwidth() - room
            if over <= 0 or wrap <= ctx.px(STATUS_MIN_WRAP):
                break
            wrap = max(ctx.px(STATUS_MIN_WRAP), wrap - over)
        return width, self.body.winfo_reqheight() + ring

    # --- the window's content -------------------------------------------------------------------
    def _build(self):
        ctx = ui.get()
        p, px = ctx.palette, ctx.px
        self.body = body = ui.Frame(self.window, on='surface', padding=px(PANEL_PADDING))
        body.pack(fill='both', expand=True)

        # Title bar: what the panel is, its settings and close. Dragging it moves the panel.
        header = ui.Frame(body, on='surface')
        header.pack(fill='x')
        self._mark = ui.Label(header, '', on='surface')
        self._mark.configure(image=ctx.icon('mic-fill', 18, p.accent))
        self._mark.pack(side='left', padx=(0, px(8)))
        title = ui.Label(header, 'Audio translation', kind='strong', on='surface')
        title.pack(side='left')
        self.close_button = ui.IconButton(header, 'x-lg', command=self._close_clicked, label='Close', on='surface')
        self.close_button.pack(side='right')
        self.settings_button = ui.IconButton(header, 'gear', command=self._settings_clicked,
                                             label='Audio settings', on='surface')
        self.settings_button.pack(side='right', padx=(0, px(4)))
        for handle in (header, self._mark, title):
            handle.bind('<ButtonPress-1>', self._drag_start, add='+')
            handle.bind('<B1-Motion>', self._drag_move, add='+')
            handle.bind('<ButtonRelease-1>', self._drag_end, add='+')
        ui.Separator(body).pack(fill='x', pady=px(12))

        # Options: where the sound comes from, how it is processed, what it is translated into.
        options = ui.Frame(body, on='surface')
        options.pack(fill='x')
        options.columnconfigure(1, weight=1)
        row_gap = px(10)

        def cell(row, label):
            caption = ui.Label(options, label, kind='muted', on='surface')
            caption.grid(row=row, column=0, sticky='w', padx=(0, px(12)), pady=(0, row_gap))
            holder = ui.Frame(options, on='surface')
            holder.grid(row=row, column=1, sticky='ew', pady=(0, row_gap))
            holder.caption = caption
            return holder

        self.source = ui.SegmentedControl(cell(0, 'Source'), SOURCES, self._config('audio_source', 'pc'),
                                          on_change=self._source_chosen, on='surface', icons=SOURCE_ICONS)
        self.source.pack(fill='x')
        mode_cell = cell(1, 'Mode')
        self.mode = ui.SegmentedControl(mode_cell, MODES, self._config('audio_mode', 'two_step'),
                                        on_change=self._mode_chosen, on='surface')
        self.mode.pack(fill='x')
        for (value, _), button in zip(MODES, self.mode.buttons):
            ui.Tooltip(button, tr(MODE_HINTS[value]))
        # Its own row under the pills, so 'Mode' stays level with them.
        self.mode_note = ui.Label(options, '', kind='caption', on='surface', wraplength=px(300), justify='left')
        self._mode_row = mode_cell
        self._gaps = (row_gap, px(4))     # under the mode row without the note, and above the note
        language_cell = cell(3, 'Translate into')
        recent = getattr(self.config, 'get_recent_languages', None) or []
        pinned = getattr(self.config, 'get_pinned_languages', None) or []
        self.language = ui.LanguagePicker(language_cell, [AUTO_LANGUAGE] + list(LANGUAGES),
                                          self._config('audio_target_language', 'Auto'),
                                          on_change=self._language_chosen, recent=recent, pinned=pinned,
                                          label='Translate audio into', on='surface')
        self.language.pack(anchor='w')
        ui.Separator(body).pack(fill='x', pady=(px(2), px(12)))

        # Recording: the one primary action, the time against the limit, a meter per device.
        recording = ui.Frame(body, on='surface')
        recording.pack(fill='x')
        self.record = ui.Button(recording, 'Record', command=self.controller.toggle_recording, kind='accent',
                                icon='record-fill', on='surface')
        self.record.pack(side='left', anchor='center', padx=(0, px(16)))
        readout = ui.Frame(recording, on='surface')
        readout.pack(side='left', fill='x', expand=True)
        self.timer = ui.Label(readout, '', kind='subtitle', on='surface', localize=False)
        self.timer.pack(anchor='w')
        self.meter_box = ui.Frame(readout, on='surface')
        self.meter_box.pack(anchor='w', pady=(px(4), 0))
        self._meter_roles = ()
        self._shown_status = None
        self._clock_text = f'{clock(0)} / {clock(QUICK_LIMIT)}'
        ui.Separator(body).pack(fill='x', pady=px(12))

        # Status and the ways out.
        footer = ui.Frame(body, on='surface')
        footer.pack(fill='x')
        footer.columnconfigure(1, weight=1)
        self.status_icon = ui.Label(footer, '', on='surface')
        self.status_icon.grid(row=0, column=0, sticky='nw', padx=(0, px(6)))
        self.status = ui.Label(footer, '', kind='muted', on='surface', wraplength=px(STATUS_MIN_WRAP),
                               justify='left')
        self.status.grid(row=0, column=1, sticky='w')
        self.status.update_idletasks()
        self._status_inset = self.status.winfo_reqwidth()      # the label's own padding and border, empty
        self.hints = hints = ui.Frame(footer, on='surface')
        hints.grid(row=0, column=2, sticky='ne', padx=(px(12), 0))
        for key, meaning in (('Space', 'Record or stop'), ('Esc', 'Close')):
            ui.Keycap(hints, key, on='surface').pack(side='left', padx=(px(8) if key == 'Esc' else 0, px(4)))
            ui.Label(hints, meaning, kind='caption', on='surface').pack(side='left')
        actions = ui.Frame(body, on='surface')
        actions.pack(fill='x', pady=(px(12), 0))
        self.cancel_button = ui.Button(actions, 'Cancel', command=self.controller.cancel_from_panel,
                                       kind='subtle', on='surface')
        self.cancel_button.pack(side='left')
        self.file_button = ui.Button(actions, 'Translate a file…', command=self._file_clicked, kind='soft',
                                     icon='file-earmark-music', on='surface')
        self.file_button.pack(side='right')
        ui.Tooltip(self.file_button, tr('Audio or video, up to 30 minutes'))
        self._bind_keys(self.window)
        self._refresh_mode_note()
        self._apply()

    def _bind_keys(self, widget):
        """Space, Enter and Esc for every control: our tag comes after the widget's own bindings (a tooltip
        hiding, a button's Enter) and before its class, whose Space would press it as well."""
        self.window.bind_class(KEYS_TAG, '<KeyPress-space>', self._space)
        self.window.bind_class(KEYS_TAG, '<Return>', self._enter)
        self.window.bind_class(KEYS_TAG, '<Escape>', self._escape)
        tags = list(widget.bindtags())
        if KEYS_TAG not in tags:
            tags.insert(1 if widget is not self.window else 0, KEYS_TAG)
            widget.bindtags(tuple(tags))
        for child in widget.winfo_children():
            if child.winfo_toplevel() is self.window:
                self._bind_keys(child)

    def _space(self, event=None):
        self.controller.toggle_recording()
        return 'break'

    def _enter(self, event=None):
        widget = event.widget if event is not None else None
        if widget is not None and hasattr(widget, 'invoke') and str(widget.cget('state')) != 'disabled':
            widget.invoke()
        return 'break'

    def _escape(self, event=None):
        self.close()
        return 'break'

    # --- following the controller ---------------------------------------------------------------
    def _watch(self, phase, info):
        self._phase, self._info = phase, dict(info)
        if self.is_open():
            self._apply()

    def _apply(self):
        """Draw the controller's phase: one source of truth, so the held shortcut shows here too."""
        ctx = ui.get()
        p = ctx.palette
        phase, info = self._phase, self._info
        busy = phase != 'idle'
        capturing = phase in ('preparing', 'recording')
        self._record_face('danger' if capturing else 'accent', 'Stop' if capturing else 'Record',
                          'stop-fill' if capturing else 'record-fill')
        self.record.configure(state='disabled' if phase == 'processing' else 'normal')
        for control in self.source.buttons + self.mode.buttons:
            control.configure(state='disabled' if busy else 'normal')
        if not busy:
            self._refresh_mode_note()
        self.language.button.configure(state='disabled' if busy else 'normal')
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        self.file_button.configure(state='disabled' if busy else 'normal')
        self.close_button.configure(state='disabled' if busy else 'normal')
        limit = float(info.get('limit') or QUICK_LIMIT)
        if phase == 'recording':
            self._elapsed = float(info.get('elapsed', 0.0))
            self._clock_text = f'{clock(self._elapsed)} / {clock(limit)}'
        elif phase in ('idle', 'preparing'):
            self._elapsed = 0.0
            self._clock_text = f'{clock(0)} / {clock(QUICK_LIMIT)}'
        elif info.get('stage') == 'decoding' and info.get('total'):
            # A file: how much of it has been read, against its length
            self._clock_text = f"{clock(info.get('done') or 0)} / {clock(info['total'])}"
        self.timer.configure(text=self._clock_text)
        levels = info.get('levels') or {}
        roles = tuple(levels) if phase == 'recording' and levels else DEVICES_OF.get(self.source.value, ())
        self._meters_for(roles)
        for role, meter in self.meters.items():
            meter.configure(value=float(levels.get(role, 0.0)) if phase == 'recording' else 0.0)
        icon, words = self._status_words(phase, info)
        colour = {'record': p.danger, 'warning': p.warning, 'done': p.success}.get(icon)
        self.status_icon.configure(image=ctx.icon({'record': 'record-fill', 'warning': 'exclamation-triangle-fill',
                                                   'done': 'check-circle-fill'}[icon], 14, colour) if icon else '')
        self.status.configure(text=words)
        if words != self._shown_status:
            self._shown_status = words
            self._fit()                     # an error can take several lines; the panel grows to show them

    def _status_words(self, phase, info) -> Tuple[Optional[str], str]:
        if phase == 'preparing':
            return None, tr('Preparing recording…')
        if phase == 'recording':
            return 'record', tr('Recording')
        if phase == 'processing':
            stage, done, total = info.get('stage'), info.get('done') or 0, info.get('total') or 0
            if stage == 'translating' and total:
                return None, tr('Translating audio: {done}/{total}', done=int(done), total=int(total))
            if stage == 'transcribing' and total:
                return None, tr('Recognizing speech: {done}/{total}', done=int(done), total=int(total))
            if stage == 'decoding':
                return None, tr('Reading the file…')
            return None, tr('Recognizing speech…')
        outcome = info.get('outcome')
        if outcome == 'error':
            return 'warning', tr(info.get('message') or 'The speech provider could not process the audio.')
        if outcome == 'cancelled':
            return None, tr('Audio translation was cancelled.')
        if outcome == 'ok':
            return 'done', tr('Done')
        return None, tr('Ready')

    def _record_face(self, kind, words, icon):
        if self.record.kind != kind:
            self.record.kind = kind
            self.record.configure(style=style_name(kind, self.record.plane, 'TButton'))
        if self.record.icon_name != icon or self.record.cget('text') != tr(words):
            self.record.configure(text=words)
            self.record.set_icon(icon)

    def _meters_for(self, roles):
        if roles == self._meter_roles:
            return
        ctx = ui.get()
        for child in self.meter_box.winfo_children():
            child.destroy()
        self.meters = {}
        for row, role in enumerate(roles):
            mark = ui.Label(self.meter_box, '', on='surface')
            mark.configure(image=ctx.icon(DEVICE_ICONS.get(role, 'soundwave'), 14, ctx.palette.muted))
            mark.grid(row=row, column=0, padx=(0, ctx.px(8)), pady=(ctx.px(3) if row else 0, 0))
            ui.Tooltip(mark, tr('PC audio' if role == 'PC' else role))
            meter = ui.Progress(self.meter_box, mode='determinate', maximum=1, length=ctx.px(METER_LENGTH))
            meter.grid(row=row, column=1, sticky='w', pady=(ctx.px(3) if row else 0, 0))
            self.meters[role] = meter
        self._meter_roles = roles
        if self.is_open():
            self._bind_keys(self.meter_box)
            self._fit()

    # --- the options ----------------------------------------------------------------------------
    def _config(self, key, default):
        value = self.config.get(key, default)
        return default if value is None else value

    def _store(self, key, value, entry=None):
        self.config.set(key, value)
        if self.on_change:
            self.on_change(entry or key)

    def _source_chosen(self, value):
        self._store('audio_source', value)
        self._refresh_mode_note()
        self._apply()

    def _mode_chosen(self, value):
        self._store('audio_mode', value)
        self._refresh_mode_note()

    def _language_chosen(self, value):
        self._store('audio_target_language', value, 'audio_language')

    def _refresh_mode_note(self):
        """One-step is offered only when the first audio key can do it; with PC + Microphone it may still run
        in two steps (Decision 87), and the note says so before the recording, not after."""
        outlook = one_step_outlook(self.config)
        one_step = self.mode.buttons[1]
        one_step.configure(state='normal' if outlook == '' and self._phase == 'idle' else 'disabled')
        note = ''
        if outlook == 'no_key':
            note = NO_AUDIO_KEY
        elif outlook == 'engine_without_one_step':
            note = ONE_STEP_NEEDS_GEMINI
        elif self.mode.value == 'one_step' and self.source.value == 'both':
            note = ONE_STEP_WITH_BOTH
        if note:
            self.mode_note.configure(text=note)
            self.mode_note.grid(row=2, column=1, sticky='w', pady=(0, self._gaps[0]))
            self._space_mode_row(self._gaps[1])
        else:
            self.mode_note.grid_remove()
            self._space_mode_row(self._gaps[0])
        if self.is_open():
            self._fit()

    def _space_mode_row(self, below):
        for widget in (self._mode_row, self._mode_row.caption):
            widget.grid_configure(pady=(0, below))

    def sync_from_config(self):
        """Settings changed a value the panel shows: read them again."""
        if not self.is_open():
            return
        self.source.value = self._config('audio_source', 'pc')
        self.mode.value = self._config('audio_mode', 'two_step')
        self.language.set(self._config('audio_target_language', 'Auto'))
        self._refresh_mode_note()
        self._apply()

    def _fit(self):
        """Take the height the content now needs, keeping the top-left corner unless the bottom would leave the
        work area."""
        if not self.is_open() or not self.window.winfo_viewable():
            return
        width, height = self._size()
        x, y = self.window.winfo_x(), self.window.winfo_y()
        work = self._work(x, y)
        y = max(work.top, min(y, work.bottom - height))
        self.window.geometry(f'{width}x{height}+{x}+{y}')

    # --- title bar ------------------------------------------------------------------------------
    def _close_clicked(self):
        self.close()

    def _file_clicked(self):
        """Choose an audio or video file and translate it; the result opens under the panel."""
        path = filedialog.askopenfilename(
            parent=self.window, title=tr('Translate an audio or video file'),
            filetypes=[(tr('Audio and video'), ' '.join('*' + suffix for suffix in MEDIA_EXTENSIONS))])
        if path:
            self.controller.translate_file_from_panel(path)

    def _settings_clicked(self):
        if self.on_settings:
            self.on_settings()

    def _drag_start(self, event):
        self._drag = (event.x_root - self.window.winfo_x(), event.y_root - self.window.winfo_y())

    def _drag_move(self, event):
        if self._drag is None or not self.is_open():
            return
        work = self._work(event.x_root, event.y_root)
        width, height = self.window.winfo_width(), self.window.winfo_height()
        x = max(work.left, min(event.x_root - self._drag[0], work.right - width))
        y = max(work.top, min(event.y_root - self._drag[1], work.bottom - height))
        self.window.geometry(f'+{x}+{y}')

    def _drag_end(self, event=None):
        self._drag = None

    # --- theme, text size and language ----------------------------------------------------------
    def _preferences_changed(self, manager):
        if self.is_open() and self._rebuild_job is None:
            self._rebuild_job = self.root.after_idle(self._rebuild)

    def _rebuild(self):
        """Draw the panel again in the new theme, size or language, where it is, in the phase it is in."""
        self._rebuild_job = None
        if not self.is_open():
            return
        p = ui.get().palette
        x, y = self.window.winfo_x(), self.window.winfo_y()
        self.window.configure(bg=p.surface, highlightbackground=p.border, highlightcolor=p.border)
        self.body.destroy()
        self.meters, self._meter_roles = {}, ()
        self._build()
        width, height = self._size()
        self.window.geometry(f'{width}x{height}+{x}+{y}')
        windows.style_floating(self.window, p)
