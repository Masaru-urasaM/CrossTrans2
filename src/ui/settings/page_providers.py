"""
API keys (TODO.md U8, step 4, Decision 51): your keys, priority, capabilities and recent usage.

The old tab was one 1,000-line mixin: seven widgets per key in a row, a Test button that looped over the
network with `window.update()` inside the loop, a trial check that called `window.after()` from a worker
thread, and nine ttkbootstrap message boxes. Here a key is a compact row - provider, model, the key with a show
button, Test, and one sentence saying what the last test found. A test runs on a worker thread whose
results the UI thread collects from a queue (`BackgroundRunner`, Decision 43). What a Test tries, how the
outcome is worded and how the cards become the saved list are pure functions of `core/key_testing.py`.

Every change is saved at once: a provider or model as soon as it is chosen, a key when typing pauses or the
field loses focus, and anything still pending when the window closes.
"""
import gc
import logging
import math
import time
from datetime import datetime
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, List, Optional, Tuple

from src.constants import SETTINGS_KEY_SAVE_MS
from src.core import key_testing as kt
from src.core import model_names
from src.core.key_usage import KeyUsage, TTL
from src.ui import i18n
from src.core.api_manager import AIAPIManager
from src.core.auth import require_auth
from src.core.remote_config import get_config
from src.ui import components as ui
from src.ui.components import context as C
from src.ui.settings.background import BackgroundRunner
from src.ui.settings.page import SettingsPage
from src.ui.theme.compat import NO_AUTOSTYLE
from src.ui.theme import windows
from src.utils.log_events import log_event
from src.utils.ui_helpers import schedule_window_callback, cancel_window_callback

CARD_PAD = 6                       # at 100 %: inside a key card
CARD_GAP = 4                       # between two cards
STATUS_MIN_WRAP = 160
AUTO = 'Auto'
SHOW_KEY, HIDE_KEY = 'Show key', 'Hide key'
TEST, STOP = 'Test', 'Stop'
DELETE_TITLE = 'Delete all API keys?'
DELETE_MESSAGE = ('Every key is removed from this computer. Translation needs a key before it can run '
                  'again. Your models, history and other settings stay.')
NO_KEY = 'Paste a key first.'
NO_MODEL = 'There is no model to try for this provider. Pick a model.'
TEST_FAILED_TO_RUN = 'The test could not run.'
CAPABILITIES = (('vision', 'Reads images', 'No image reading yet'),
                ('file', 'Reads files', 'No file reading yet'),
                ('audio', 'Reads audio', 'No audio reading yet'))



def filter_text_models(models):
    return [m for m in models if not (m.lower().startswith('whisper-') or m.lower().startswith('distil-whisper-') or 'voxtral' in m.lower())]
def title_for(number: int) -> str:
    return 'Primary key' if number == 0 else f'Backup key {number}'






class KeyCard(ui.Frame):
    """One API key: which provider and model, the key itself, a Test button and what the last test found.

    The card draws and reports; the page decides. `on_edited(immediate)` says a value changed (a key being
    typed is not immediate, everything else is), `on_test(card)`, `on_remove(card)` and `on_reveal(card)`
    are the three buttons. `values()` is what the card holds - (provider, model, key) - and still answers
    after the card was destroyed, from what it last saw.
    """

    def __init__(self, parent, *, number: int, provider: str, model: str, key: str, providers: List[str],
                 model_map, on_edited: Callable[[bool], None], on_test: Callable, on_remove: Callable,
                 on_reveal: Callable, serial: int, status: Tuple[str, str] = ('', kt.MUTED)):
        ctx = C.get()
        px = ctx.px
        super().__init__(parent, on='alt', padding=px(CARD_PAD))
        self.serial, self.tag = serial, f'test-{serial}'
        self.model_map = model_map
        self._key = key or ''
        self._on_edited, self._on_test, self._on_remove, self._on_reveal = on_edited, on_test, on_remove, on_reveal
        self.revealed = self.testing = False
        self.status, self.tone = '', kt.MUTED
        for column, weight in ((0, 0), (1, 1), (2, 0), (3, 0)):
            self.columnconfigure(column, weight=weight)

        self.configure(takefocus=True)
        self.focus_ring = tk.Frame(self, bg=ctx.palette.focus, borderwidth=0, **NO_AUTOSTYLE)
        self.bind('<FocusIn>', lambda event: self.focus_ring.place(x=0, y=0, width=px(2), relheight=1)
                  if event.widget is self else None, add='+')
        self.bind('<FocusOut>', lambda event: self.focus_ring.place_forget()
                  if event.widget is self else None, add='+')
        self.meta_line = ui.Frame(self, on='alt')
        self.meta_line.pack(fill='x')
        self.key_line = ui.Frame(self, on='alt')
        self.key_line.pack(fill='x', pady=(px(4), 0))
        self.meta_fields = ui.Frame(self.meta_line, on='alt')
        self.status_slot = ui.Frame(self.key_line, on='alt')
        self.title_label = ui.Label(self.meta_line, '', kind='strong', on='alt')
        self.remove_btn = ui.IconButton(self.key_line, 'trash', command=lambda: self._on_remove(self),
                                       label='Remove this key', on='alt')
        self.set_number(number)
        self.provider = ui.Dropdown(self.meta_line, [(name, 'Provider: Auto' if name == AUTO else name) for name in providers], provider or AUTO,
                                    on_change=self._provider_chosen, label='Provider', on='alt')
        models = filter_text_models(model_names.models_for(provider or AUTO, model_map))
        self.model = ui.ModelPicker(self.meta_line, models, model or AUTO,
                                   on_change=lambda name: self._on_edited(True), label='Model', on='alt',
                                   auto_label='Model: Auto')
        self.model.button.configure(width=0)
        self.model.configure(width=px(150))
        self.field = ui.TextField(self.key_line, on='alt', show='*', width=8)
        if self._key:
            self.field.set(self._key)
        self.field.bind('<KeyRelease>', self._typed, add='+')
        self.field.bind('<<Paste>>', lambda e: schedule_window_callback(
            self._root(), self, 'idle', self._typed), add='+')
        self.field.bind('<FocusOut>', lambda e: self._finished(), add='+')
        self.field.bind('<Return>', lambda e: self._finished(), add='+')
        self.eye = ui.IconButton(self.key_line, 'eye', command=lambda: self._on_reveal(self), label=SHOW_KEY, on='alt')
        self.test_btn = ui.Button(self.key_line, TEST, command=lambda: self._on_test(self), kind='soft', on='alt')
        self.status_label = ui.Label(self.key_line, '', kind='muted', on='alt', justify='left')
        self.status_label.tooltip = ui.Tooltip(self.status_label, '')
        self.key_id = None
        self.last_used_label = ui.Label(self.meta_line, '← Last used', kind='caption', on='alt',
                                      foreground=ctx.palette.danger)
        self.usage_label = ui.Label(self.key_line, '', kind='caption', on='alt', justify='left', localize=False)
        self.usage_label.tooltip = ui.Tooltip(self.usage_label, '')
        self._compact = None
        self.set_status(*status)
        self.bind('<Configure>', self._wrap, add='+')
        self._unsubscribe = ctx.manager.subscribe(self._retheme)
        self.bind('<Destroy>', self._on_destroy, add='+')

    # --- what the card holds -----------------------------------------------------------------------------
    @property
    def key_text(self) -> str:
        try:
            self._key = self.field.get()
        except tk.TclError:
            pass                                    # destroyed: the last thing it showed
        return self._key

    def values(self) -> Tuple[str, str, str]:
        return self.provider.value, self.model.value, self.key_text.strip()

    def set_number(self, number: int) -> None:
        self.number = number
        self.title_label.configure(text=title_for(number))
        if number == 0:
            self.remove_btn.grid_remove()
        elif getattr(self, '_compact', None) is not None:
            self.remove_btn.grid()

    def focus_key(self) -> None:
        self.field.focus_set()

    def clear_key(self) -> None:
        self.field.set('')
        self._key = ''

    def apply_result(self, provider: str, model: str) -> None:
        """Show the pair a test found working: the provider, and the model or Auto (Decision 104)."""
        self.provider.set(provider)
        self.model.set_models(filter_text_models(model_names.models_for(provider, self.model_map)))
        self.model.set(model)

    # --- editing -------------------------------------------------------------------------------------------
    def _provider_chosen(self, name: str) -> None:
        self.model.set_models(filter_text_models(model_names.models_for(name, self.model_map)))
        self._on_edited(True)

    def _typed(self, event=None) -> None:
        _ = self.key_text                           # keep what was typed even if the window closes now
        self._on_edited(False)

    def _finished(self) -> None:
        _ = self.key_text
        self._on_edited(True)

    def set_revealed(self, flag: bool) -> None:
        self.revealed = bool(flag)
        self.field.configure(show='' if self.revealed else '*')
        self.eye.set_on(self.revealed)
        self.eye.tooltip.text = HIDE_KEY if self.revealed else SHOW_KEY

    def set_testing(self, flag: bool) -> None:
        self.testing = bool(flag)
        self.test_btn.configure(text=STOP if self.testing else TEST)

    # --- the sentence under the key -------------------------------------------------------------------------
    def set_status(self, text: str, tone: str = kt.MUTED) -> None:
        self.status, self.tone = text, tone
        self.status_at = time.time() if text else None
        summary = ('' if text.startswith('Works with ') else
                   text.split('. ', 1)[0] + '.' if len(text) > 100 and '. ' in text else text)
        self.status_label.configure(text=summary)
        self.status_label.tooltip.text = text
        self._paint_status()
        if summary:
            self.status_label.grid()
        else:
            self.status_label.grid_remove()
        self._wrap()

    def _paint_status(self) -> None:
        p = C.get().palette
        colour = {kt.SUCCESS: p.success, kt.WARNING: p.warning, kt.DANGER: p.danger}.get(self.tone)
        self.status_label.configure(foreground=colour or '')        # '' = the label style's own colour
        usage_colour = {'test_success': p.success, 'success': p.success, 'test_error': p.danger,
                        'error': p.danger, 'quota': p.warning}.get(getattr(self, 'usage_kind', ''), p.muted)
        self.usage_label.configure(foreground=usage_colour)

    def _wrap(self, event=None) -> None:
        if self.winfo_width() <= 1:
            return
        ctx, px = C.get(), C.get().px
        font = tkfont.Font(root=self, font=ctx.font('body_strong'))
        room = max(1, self.winfo_width() - px(2 * CARD_PAD))
        status_width = max(px(160), font.measure(i18n.tr('Test OK {time}', time='00:00')) + px(12),
                           font.measure(i18n.tr('← Last used')) + px(12))
        eye_width = self.eye.winfo_reqwidth() + px(4)
        test_width = max(font.measure(i18n.tr(text)) for text in ('Test', 'Stop')) + px(32)
        delete_width = self.remove_btn.winfo_reqwidth() + px(4)
        title_width = max(px(96), font.measure(i18n.tr('Backup key {0}', 99)) + px(8))
        provider_width = self.provider.winfo_reqwidth()
        header_minimum = title_width + provider_width + px(130) + status_width + delete_width + px(16)
        narrow = room < max(px(620), header_minimum)
        if narrow:
            status_width = min(status_width, max(px(40), room - eye_width - test_width - delete_width))
        mode = (narrow, status_width, eye_width, test_width, delete_width, title_width, provider_width)
        if mode != getattr(self, '_row_mode', None):
            self._row_mode = mode
            self._compact = narrow
            for line in (self.meta_line, self.key_line, self.meta_fields, self.status_slot):
                for child in line.winfo_children():
                    child.grid_forget()
            for line in (self.meta_line, self.key_line):
                for col, width in enumerate((0, eye_width, test_width, status_width, delete_width)):
                    line.columnconfigure(col, weight=1 if col == 0 else 0, minsize=width)
            self.meta_fields.grid(row=0, column=0, columnspan=5 if narrow else 3, sticky='ew')
            self.meta_fields.columnconfigure(0, minsize=0 if narrow else title_width)
            self.meta_fields.columnconfigure(1, minsize=0 if narrow else provider_width)
            self.meta_fields.columnconfigure(2, weight=1)
            self.title_label.grid(in_=self.meta_fields, row=0, column=0,
                                  columnspan=3 if narrow else 1, sticky='w', padx=(0, px(8)))
            self.provider.grid(in_=self.meta_fields, row=1 if narrow else 0,
                               column=0 if narrow else 1, columnspan=3 if narrow else 1,
                               sticky='ew', padx=(0, px(8)))
            self.model.grid(in_=self.meta_fields, row=2 if narrow else 0,
                            column=0 if narrow else 2, columnspan=3 if narrow else 1, sticky='ew',
                            pady=(px(4) if narrow else 0, 0))
            self.last_used_label.grid(row=1 if narrow else 0, column=3, sticky='w')
            self.field.grid(row=0, column=0, columnspan=5 if narrow else 1, sticky='ew')
            self.eye.grid(row=1 if narrow else 0, column=1, sticky='w', padx=(px(4), 0))
            self.test_btn.grid(row=1 if narrow else 0, column=2, sticky='w', padx=(px(4), 0))
            self.status_slot.grid(row=1 if narrow else 0, column=3, sticky='ew')
            self.status_slot.columnconfigure(0, weight=1)
            self.status_label.grid(in_=self.status_slot, row=0, column=0, sticky='w')
            self.usage_label.grid(in_=self.status_slot, row=1, column=0, sticky='w')
            self.remove_btn.grid(row=1 if narrow else 0, column=4, sticky='e')
        for label, show in ((self.status_label, bool(str(self.status_label.cget('text')))),
                            (self.usage_label, bool(str(self.usage_label.cget('text')))),
                            (self.last_used_label, getattr(self, 'is_last_success', False))):
            if show:
                label.grid()
            else:
                label.grid_remove()
        if self.number == 0:
            self.remove_btn.grid_remove()
        self.status_label.configure(wraplength=status_width)
        self.usage_label.configure(wraplength=status_width)
        self.last_used_label.configure(wraplength=status_width)
        if hasattr(self.model, 'tooltip'):
            self.model.tooltip.text = self.model.value

    def _retheme(self, manager) -> None:
        try:
            self._paint_status()
            self.last_used_label.configure(foreground=manager.palette.danger)
            self.focus_ring.configure(bg=manager.palette.focus)
        except tk.TclError:
            pass

    def _on_destroy(self, event=None) -> None:
        if event is None or event.widget is self:
            self._unsubscribe()


class ProvidersPage(SettingsPage):
    key = 'providers'

    def populate(self) -> None:
        cfg = self.config
        px = C.get().px
        self.runner = BackgroundRunner(self.body)
        self.cards: List[KeyCard] = []
        self._serial = 0
        self._authenticated = False
        self._auth_pending = False
        self._auth_epoch = 0
        self._save_job: Optional[str] = None
        self._saved_rows: List[Tuple[str, str, str]] = []
        self.dialog: Optional[ui.Dialog] = None
        self.capability_box = None
        self.usage = KeyUsage(cfg)
        self._usage_job = None
        self._usage_unsubscribe = None
        self._built = False
        self._drag_card = None
        self._drag_target = 0
        self._drag_job = None
        self._drop_marker = None
        self._drag_pending = None
        self._drag_ghost = None
        self._marker_target = None

        group = self.group()
        row = self.row(group, 'api_keys', stacked=True)
        self.capability_row = row
        self.rows['capabilities'] = row
        self.api_heading = ui.Frame(row.words, on=row.plane)
        self.api_heading.pack(fill='x', before=row.description_label)
        row.title_label.destroy()
        row.title_label = ui.Label(self.api_heading, 'API keys', kind='strong', on=row.plane)
        row.title_label.pack(side='left')
        self.capability_box = ui.Frame(self.api_heading, on=row.plane)
        self.capability_box.pack(side='right')
        self._draw_capabilities()
        self.keys_box = ui.Frame(row.control, on=row.plane)
        self.keys_box.pack(fill='x')
        self._unmap_binding = self.host.window.bind('<Unmap>', self._on_unmap, add='+')
        for entry in cfg.get_api_keys() or [{}]:
            self._add_card(entry)
        self._saved_rows = self._rows()
        bar = ui.ActionBar(row.control, on=row.plane)
        bar.pack(fill='x', pady=(px(8), 0))
        self.add_btn = bar.add(ui.Button(bar, 'Add backup key', command=self.add_backup,
                                        icon='plus-lg', kind='soft', on=row.plane))
        self.show_all_btn = bar.add(ui.Button(bar, 'Show all keys', command=self.toggle_all_keys,
                                             kind='soft', on=row.plane))
        self.delete_all_btn = bar.add(ui.Button(bar, 'Delete all keys', command=self.confirm_delete_all,
                                               kind='danger', on=row.plane))
        self._update_add_button()
        group = self.group()
        self.toggle(group, 'failover', cfg.get_model_rotation, cfg.set_model_rotation)

        from src.ui.settings.audio_settings import AudioSection
        self.audio_section = AudioSection(self)
        self.audio_section.build()

        self.body.bind('<Destroy>', self._on_destroy, add='+')
        self._built = True
        self._usage_unsubscribe = C.get().manager.subscribe(self._theme_changed)
        self.refresh_usage()

    # --- the cards ------------------------------------------------------------------------------------------
    def _add_card(self, entry: dict) -> KeyCard:
        px = C.get().px
        remote = get_config()
        key = entry.get('api_key') or ''
        card = KeyCard(self.keys_box, number=len(self.cards), provider=entry.get('provider') or AUTO,
                       model=entry.get('model_name') or '', key=key, providers=remote.providers_list,
                       model_map=remote.model_provider_map, on_edited=self._card_edited, on_test=self.test_card,
                       on_remove=self.remove_card, on_reveal=self.reveal_clicked, serial=self._serial,
                       status=('', kt.MUTED))
        card.key_id = entry.get('id')
        for surface in (card, card.focus_ring, card.meta_line, card.meta_fields, card.key_line,
                        card.status_slot, card.title_label, card.last_used_label,
                        card.status_label, card.usage_label):
            surface.bind('<ButtonPress-1>', lambda event: self._begin_drag(card, event))
            surface.bind('<B1-Motion>', self._drag_motion)
            surface.bind('<ButtonRelease-1>', self._drop)
        card.bind('<space>', lambda event: self._begin_drag(card))
        card.bind('<Up>', lambda event: self._keyboard_drag(-1))
        card.bind('<Down>', lambda event: self._keyboard_drag(1))
        card.bind('<Return>', lambda event: self._finish_drag(True))
        card.bind('<Escape>', lambda event: self._finish_drag(False))
        self._serial += 1
        card.pack(fill='x', pady=(px(CARD_GAP) if self.cards else 0, 0))
        self.cards.append(card)
        return card

    def _rows(self) -> List[Tuple[str, str, str]]:
        return [card.values() for card in self.cards]

    def _has_keys(self) -> bool:
        return any(card.key_text.strip() for card in self.cards)

    def _update_add_button(self) -> None:
        if not self.body.winfo_exists():
            return
        self.add_btn.configure(state='normal')
        if hasattr(self, 'show_all_btn'):
            shown = any(card.revealed for card in self.cards)
            self.show_all_btn.configure(text='Hide all keys' if shown else 'Show all keys',
                                        state='normal' if (shown or self._has_keys()) and not self._auth_pending else 'disabled')
            self.delete_all_btn.configure(state='normal' if self._has_keys() else 'disabled')
            for card in self.cards:
                card.eye.configure(state='disabled' if self._auth_pending else 'normal')

    def _begin_drag(self, card, event=None):
        if self._drag_card is not None:
            self._finish_drag(False)
        if event is not None:
            self._drag_pending = (card, event.x_root, event.y_root)
            return 'break'
        self._start_drag(card)
        return 'break'

    def _start_drag(self, card):
        self._drag_card = card
        self._drag_target = self.cards.index(card)
        self._marker_target = None
        ctx = C.get()
        self._drop_marker = tk.Frame(self.keys_box, height=max(ctx.px(40), card.winfo_height()),
                                     background=ctx.palette.soft, highlightthickness=ctx.px(2),
                                     highlightbackground=ctx.palette.accent, **NO_AUTOSTYLE)
        self._drop_marker.pack_propagate(False)
        card.focus_set()
        self._show_drop_marker()

    def _show_drag_ghost(self, x, y):
        if self._drag_ghost is None:
            ctx = C.get()
            card = self._drag_card
            ghost = self._drag_ghost = tk.Toplevel(self.host.window)
            ghost.withdraw()
            ghost.overrideredirect(True)
            ghost.attributes('-topmost', True)
            ghost.attributes('-alpha', 0.90)
            ghost.bind('<Map>', lambda event: windows.no_activate(ghost) if event.widget is ghost else None,
                       add='+')
            face = ui.Card(ghost, padding=ctx.px(10))
            face.pack(fill='both', expand=True)
            ui.Label(face, title_for(card.number), kind='strong').pack(anchor='w')
            provider, model, _ = card.values()
            ui.Label(face, f'{provider}  /  {model}', localize=False,
                     wraplength=max(ctx.px(160), card.winfo_width() - ctx.px(24))).pack(anchor='w')
            ui.Label(face, '••••••••••••••••••••••••', localize=False, kind='muted').pack(anchor='w')
            self._ghost_width = max(ctx.px(200), card.winfo_width())
            ghost.update_idletasks()
            self._ghost_height = max(ctx.px(88), card.winfo_height(), face.winfo_reqheight())
        ghost = self._drag_ghost
        offset = C.get().px(14)
        ghost.geometry(f'{self._ghost_width}x{self._ghost_height}{int(x + offset):+d}{int(y + offset):+d}')
        ghost.deiconify()

    def _show_drop_marker(self):
        if self._drop_marker is None:
            return
        if self._marker_target == self._drag_target:
            return
        self._marker_target = self._drag_target
        others = [card for card in self.cards if card is not self._drag_card]
        self._drop_marker.pack_forget()
        if self._drag_target < len(others):
            self._drop_marker.pack(fill='x', before=others[self._drag_target])
        else:
            self._drop_marker.pack(fill='x')

    def _drag_motion(self, event=None):
        if self._drag_card is None and self._drag_pending is not None and event is not None:
            card, start_x, start_y = self._drag_pending
            if max(abs(event.x_root - start_x), abs(event.y_root - start_y)) < C.get().px(6):
                return 'break'
            self._drag_pending = None
            self._start_drag(card)
        if self._drag_card is None:
            return 'break'
        if not self.keys_box.winfo_ismapped():
            return self._finish_drag(False)
        y = event.y_root if event is not None else self.body.winfo_pointery()
        x = event.x_root if event is not None else self.body.winfo_pointerx()
        self._show_drag_ghost(x, y)
        others = [card for card in self.cards if card is not self._drag_card]
        self._drag_target = sum(y > card.winfo_rooty() + card.winfo_height() // 2 for card in others)
        self._show_drop_marker()
        area = getattr(self.host, '_areas', {}).get(self.key)
        if area is not None:
            canvas = area.canvas
            top, bottom = canvas.winfo_rooty(), canvas.winfo_rooty() + canvas.winfo_height()
            margin = C.get().px(28)
            if y < top + margin:
                canvas.yview_scroll(-1, 'units')
            elif y > bottom - margin:
                canvas.yview_scroll(1, 'units')
        if self._drag_job is None:
            self._drag_job = schedule_window_callback(self.body._root(), self.body, 65, self._drag_tick)
        return 'break'

    def _drag_tick(self):
        self._drag_job = None
        if self._drag_card is not None:
            self._drag_motion()

    def _keyboard_drag(self, delta):
        if self._drag_card is not None:
            self._drag_target = max(0, min(len(self.cards) - 1, self._drag_target + delta))
            self._show_drop_marker()
            self.host.reveal(self.key, self._drop_marker)
        return 'break'

    def _on_unmap(self, event):
        if event.widget in (self.host.window, self.body, self.keys_box):
            if not self.keys_box.winfo_ismapped():
                self._finish_drag(False)

    def _drop(self, event):
        box = self.keys_box
        left, top = box.winfo_rootx(), box.winfo_rooty()
        right, bottom = left + box.winfo_width(), top + box.winfo_height()
        parent = box.master
        while parent is not None:
            is_window = parent.winfo_toplevel() is parent
            if is_window or parent.winfo_class() == 'Canvas':
                x, y = parent.winfo_rootx(), parent.winfo_rooty()
                left, top = max(left, x), max(top, y)
                right = min(right, x + parent.winfo_width())
                bottom = min(bottom, y + parent.winfo_height())
            if is_window:
                break
            parent = parent.master
        inside = box.winfo_ismapped() and left <= event.x_root <= right and top <= event.y_root <= bottom
        return self._finish_drag(inside)

    def _finish_drag(self, commit):
        self._drag_pending = None
        card, self._drag_card = self._drag_card, None
        if self._drag_job is not None:
            cancel_window_callback(self.body._root(), self.body, self._drag_job)
            self._drag_job = None
        if self._drop_marker is not None:
            self._drop_marker.destroy()
            self._drop_marker = None
        if self._drag_ghost is not None:
            self._drag_ghost.destroy()
            self._drag_ghost = None
        self._marker_target = None
        if card is not None and commit and card in self.cards:
            old = self.cards.index(card)
            self.move_card(card, self._drag_target - old)
        return 'break'

    def add_backup(self) -> KeyCard:
        self._finish_drag(False)
        card = self._add_card({})
        self._update_add_button()
        self.adopt()
        card.update_idletasks()
        self.host.reveal(self.key, card)
        card.focus_key()
        return card

    def move_card(self, card, delta):
        if card not in self.cards:
            return
        old = self.cards.index(card)
        new = old + delta
        if new < 0 or new >= len(self.cards):
            return
        self.cards.insert(new, self.cards.pop(old))
        for position, each in enumerate(self.cards):
            each.pack_forget()
            each.pack(fill='x', pady=(C.get().px(CARD_GAP) if position else 0, 0))
            each.set_number(position)
        self._update_add_button()
        self._cancel_save()
        self.save()

    def refresh_usage(self, now=None):
        now = time.time() if now is None else now
        root = self.body._root()
        if self._usage_job is not None:
            root.after_cancel(self._usage_job)
            self._usage_job = None
        deadlines = []
        for card in self.cards:
            row = self.usage.row(card.key_id, now)
            card.is_last_success = row['is_last_success']
            if row['is_last_success']:
                card.last_used_label.grid()
            else:
                card.last_used_label.grid_remove()
            parts = []
            if row['kind']:
                stamp = datetime.fromtimestamp(row['at']).strftime('%H:%M')
                messages = {'quota': 'Usage limit reached at {time}', 'error': 'Request failed at {time}',
                            'success': 'Translation succeeded at {time}',
                            'test_success': 'Connection test succeeded at {time}',
                            'test_error': 'Connection test failed at {time}'}
                parts.append(i18n.tr(messages.get(row['kind'], 'Request failed at {time}'), time=stamp))
            if row['average_seconds'] is not None:
                parts.append(i18n.tr('Average translation: {seconds:.1f} s', seconds=row['average_seconds']))
            full_status = ' | '.join(parts)
            compact = []
            if row['kind']:
                brief = {'quota': 'Limit {time}', 'error': 'Failed {time}', 'success': 'OK {time}',
                         'test_success': 'Test OK {time}', 'test_error': 'Test failed {time}'}
                compact.append(i18n.tr(brief.get(row['kind'], 'Failed {time}'), time=stamp))
            if row['average_seconds'] is not None:
                compact.append(i18n.tr('Avg {seconds:.1f} s', seconds=row['average_seconds']))
            card.usage_kind = row['kind']
            card.usage_label.configure(text=' | '.join(compact))
            card._paint_status()
            card.usage_label.tooltip.text = full_status
            if parts:
                card.usage_label.grid()
            else:
                card.usage_label.grid_remove()
            card._wrap()
            status_at = getattr(card, 'status_at', None)
            if status_at is not None:
                if now - status_at >= TTL and not card.testing:
                    card.set_status('')
                elif now < status_at + TTL:
                    deadlines.append(status_at + TTL)
        expiry = self.usage.next_expiry(now)
        if expiry is not None:
            deadlines.append(expiry)
        if deadlines:
            self._usage_job = root.after(max(1, math.ceil((min(deadlines) - now) * 1000)), self.refresh_usage)

    def remove_card(self, card: KeyCard) -> None:
        """Take a backup out. Its own button was clicked, so the widget goes once that click is done."""
        if card not in self.cards or card is self.cards[0]:
            return
        self._finish_drag(False)
        self.runner.cancel(card.tag)
        self.cards.remove(card)
        card.pack_forget()
        schedule_window_callback(self.body._root(), self.body, 'idle', card.destroy)
        for number, each in enumerate(self.cards):
            each.set_number(number)
        self._update_add_button()
        self._cancel_save()
        self.save()

    # --- saving ---------------------------------------------------------------------------------------------
    def _card_edited(self, immediate: bool) -> None:
        self._cancel_save()
        if immediate:
            self._save_if_changed()
        else:
            self._save_job = self.body.after(SETTINGS_KEY_SAVE_MS, self._save_after_pause)

    def _save_after_pause(self) -> None:
        self._save_job = None
        self._save_if_changed()

    def _cancel_save(self) -> None:
        if self._save_job is not None:
            try:
                self.body.after_cancel(self._save_job)
            except (tk.TclError, ValueError):
                pass
            self._save_job = None

    def _save_if_changed(self) -> None:
        if self._rows() != self._saved_rows:
            self.save()

    def save(self, *, announce: bool = True, redraw: bool = True) -> None:
        """Write the cards as the saved key list. A key that is gone is overwritten on disk (`secure`),
        and its startup-check result forgotten. `announce` shows "Saved" and tells the application."""
        rows = self._rows()
        now = {key for _, _, key in rows if key}
        removed = {key for _, _, key in self._saved_rows if key} - now
        merged = kt.merge_rows(rows, self.config.get_api_keys())
        active_cards = [card for card in self.cards if card.key_text.strip() or not kt.is_auto(card.model.value)]
        for card, entry in zip(active_cards, merged):
            if card.key_id:
                entry['id'] = card.key_id
        self.config.set_api_keys(merged, secure=bool(removed))
        for card, entry in zip(active_cards, self.config.get_api_keys()):
            card.key_id = entry.get('id')
        self.config._auto_update_toggles()
        self.config.save()
        if redraw:
            self._update_add_button()
        for key in removed:
            self.config.api_status_cache.pop(key, None)
        self._saved_rows = rows
        logging.info(f"[Settings] API keys saved: {len(merged)}")        # a count, never a key
        if redraw:
            self._draw_capabilities()
            self.refresh_usage()
        if announce:
            self.changed('api_keys')
            self.host.api_changed()

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self.body:
            return
        self.invalidate_auth()
        self._finish_drag(False)
        binding = getattr(self, '_unmap_binding', None)
        if binding:
            try:
                self.host.window.unbind('<Unmap>', binding)
            except tk.TclError:
                pass
            self._unmap_binding = None
        if self._usage_job is not None:
            self.body._root().after_cancel(self._usage_job)
            self._usage_job = None
        if self._usage_unsubscribe is not None:
            self._usage_unsubscribe()
            self._usage_unsubscribe = None
        if self._save_job is not None:
            self._cancel_save()
            if self._rows() != self._saved_rows:
                self.save(announce=False, redraw=False)
                self.host.api_changed()                 # no "Saved": the window is closing

    # --- showing keys -----------------------------------------------------------------------------------------
    def invalidate_auth(self) -> None:
        self._auth_epoch += 1
        self._authenticated = False

    def _authorise(self) -> bool:
        """Windows asks once per time the window is open (and not at all when the account has no password)."""
        if not self.body.winfo_exists() or self._auth_pending:
            return False
        if not self._authenticated:
            self._auth_pending = True
            self._update_add_button()
            try:
                epoch = self._auth_epoch
                accepted = bool(require_auth(self.host.window))
                if epoch != self._auth_epoch or not self.body.winfo_exists():
                    return False
                self._authenticated = accepted
            finally:
                self._auth_pending = False
                self._update_add_button()
        return self._authenticated

    def reveal_clicked(self, card: KeyCard) -> None:
        if self._auth_pending or not card.winfo_exists():
            return
        if card.revealed:
            card.set_revealed(False)
        elif not card.key_text.strip() or self._authorise():
            if card.winfo_exists():
                card.set_revealed(True)
        self._update_add_button()

    def toggle_all_keys(self) -> None:
        if self._auth_pending:
            return
        if any(card.revealed for card in self.cards):
            for card in self.cards:
                card.set_revealed(False)
        elif self._has_keys() and self._authorise():
            for card in self.cards:
                card.set_revealed(True)
        self._update_add_button()

    # --- visible bulk actions and Delete all keys ------------------------------------------------------------------
    def confirm_delete_all(self) -> ui.Dialog:
        """Ask first; Cancel is the default button, so a stray Enter keeps the keys."""
        dialog = ui.Dialog(self.host.window, DELETE_TITLE, DELETE_MESSAGE, icon='trash')
        dialog.add_button('Cancel', default=True)
        dialog.add_button('Delete all keys', command=self.delete_all, kind='danger')
        dialog.show()
        self.dialog = dialog
        return dialog

    def delete_all(self) -> None:
        """Every backup goes and the primary keeps its provider and model but loses its key."""
        self._finish_drag(False)
        self.runner.cancel()
        self._cancel_save()
        for card in self.cards[1:]:
            card.destroy()
        del self.cards[1:]
        first = self.cards[0]
        first.clear_key()
        first.set_revealed(False)
        first.set_testing(False)
        first.set_status('')
        self.save()
        self._update_add_button()
        gc.collect()                                # the keys' text out of memory too
        logging.info("[Settings] all API keys deleted")

    # --- Test --------------------------------------------------------------------------------------------------
    def test_card(self, card: KeyCard) -> None:
        """Test the key on `card`: a worker tries what `key_testing.plan` lists, the UI thread shows each step.
        Pressed again while it runs, the button is Stop."""
        if self.runner.busy(card.tag):
            self.runner.cancel(card.tag)
            card.set_testing(False)
            card.set_status(kt.summary(kt.Outcome(False, cancelled=True)))
            return
        provider, model, key = card.values()
        if not key:
            card.set_status(NO_KEY, kt.WARNING)
            return
        remote = get_config()
        combinations = kt.plan(provider, model, key, remote.model_provider_map, remote.api_key_patterns,
                               remote.default_models_by_provider)
        if not combinations:
            card.set_status(NO_MODEL, kt.WARNING)
            return
        card.set_testing(True)
        card.set_status(kt.progress_text(1, len(combinations), 'connect'))
        started, defaults = time.monotonic(), remote.default_models_by_provider

        def work(progress, cancelled):
            return kt.run(AIAPIManager(), combinations, key, progress=progress, cancelled=cancelled,
                          audio_model=None)

        self.runner.start(card.tag, work,
                          on_progress=lambda done, total, stage: card.set_status(
                              kt.progress_text(done, total, stage)),
                          on_done=lambda outcome, error: self._test_done(
                              card, key, outcome, error, expected=(provider, model, key), started=started,
                              total=len(combinations), defaults=defaults))

    def _test_done(self, card: KeyCard, key: str, outcome: Optional[kt.Outcome], error, expected=None,
                   started: Optional[float] = None, total: int = 0, defaults=None) -> None:
        card.set_testing(False)
        # Model = Auto stays Auto when the model that answered is one Auto tries anyway (Decision 104)
        keep_auto = error is None and outcome is not None and kt.keeps_auto((expected or ('', ''))[1], outcome,
                                                                           defaults)
        self._log_test(card, outcome, error, expected, started, total, kept_auto=keep_auto)
        if error is not None:
            card.set_status(TEST_FAILED_TO_RUN, kt.DANGER)
            return
        if card.values()[2] != key or (expected is not None and card.values() != expected):
            card.set_status('')
            return
        if outcome.ok:
            card.apply_result(outcome.provider, kt.AUTO if keep_auto else outcome.model)
        self.save(announce=False)
        if outcome.ok:                              # saved with model '' when Auto stayed, so match that
            self.config.update_api_capabilities(key, '' if keep_auto else outcome.model, outcome.vision,
                                                outcome.files, provider=outcome.provider)
        self.config.api_status_cache[key] = outcome.ok
        self._draw_capabilities()
        card.set_status(kt.summary(outcome, auto=keep_auto), kt.SUCCESS if outcome.ok else kt.DANGER)
        if outcome.ok and outcome.vision is None:
            card.set_status('Images could not be verified. Test again.', kt.WARNING)
        self.usage.apply(dict(id=card.key_id, at=time.time(), seconds=0,
                              kind='test_success' if outcome.ok else 'test_error'))
        self.refresh_usage()
        self.changed('api_keys')
        self.host.api_changed()

    def _log_test(self, card: KeyCard, outcome: Optional[kt.Outcome], error, expected, started, total,
                  kept_auto: bool = False) -> None:
        """One line per Test (Decision 102): the card's place, what answered or was asked for, how many pairs
        were tried and how long it took. Never the key, never the provider's message (Decision 86)."""
        provider, model = (expected or ('', ''))[:2]
        ok = error is None and outcome is not None and outcome.ok
        fields = dict(card=self.cards.index(card) + 1 if card in self.cards else None,
                      provider=(outcome.provider if ok else provider) or kt.AUTO,
                      model=(outcome.model if ok else model) or kt.AUTO,
                      tried=outcome.tried if outcome is not None else 0, total=total,
                      ms=round((time.monotonic() - started) * 1000) if started is not None else None)
        if ok:
            log_event('Settings', 'key_test.ok', vision=outcome.vision, kept_auto=kept_auto, **fields)
        elif error is not None or outcome is None:
            log_event('Settings', 'key_test.fail', level=logging.WARNING, error=type(error).__name__, **fields)
        else:
            log_event('Settings', 'key_test.fail', level=logging.WARNING, status=outcome.status,
                      cancelled=outcome.cancelled, **fields)

    # --- capabilities -------------------------------------------------------------------------------------------
    def _draw_capabilities(self) -> None:
        if self.capability_box is None:
            return
        for child in self.capability_box.winfo_children():
            child.destroy()
        self.capability_chips = []
        for name, yes, no in CAPABILITIES:
            if name == 'audio':
                supported = self.config.has_any_audio_capable()
                audio_keys = self.config.get_audio_keys()
                detail = ('Supported' if supported else
                          'Unsupported' if audio_keys and all(k.get('audio_capable') is False for k in audio_keys) else 'Not tested')
            else:
                flag = {'vision': 'vision_capable', 'file': 'file_capable'}[name]
                entries = [entry for entry in self.config.get_api_keys() if entry.get('api_key', '').strip()]
                values = [entry.get(flag) for entry in entries]
                supported = any(value is True for value in values)
                detail = ('Supported' if supported else
                          'Unsupported' if values and all(value is False for value in values) else 'Not tested')
            chip = ui.Chip(self.capability_box, yes, tone='soft', on=self.capability_row.plane,
                           image=C.get().icon('check-lg' if supported else
                                              'question-circle' if name == 'audio' and detail == 'Not tested' else 'x-lg', 16,
                                              C.get().palette.success if supported else
                                              C.get().palette.muted if name == 'audio' and detail == 'Not tested' else C.get().palette.danger),
                           compound='right')
            chip.capability_state = detail
            chip.tooltip = ui.Tooltip(chip, detail)
            self.capability_chips.append(chip)
        self._capability_layout = None
        self.api_heading.bind('<Configure>', self._fit_capabilities)
        self._fit_capabilities()
        if self._built:
            self.adopt()                            # the new chips pass the wheel on like everything else

    def _fit_capabilities(self, event=None):
        gap = C.get().px(6)
        room = self.api_heading.winfo_width() - self.capability_row.title_label.winfo_reqwidth() - gap
        vertical = room > 1 and sum(chip.winfo_reqwidth() + gap for chip in self.capability_chips) > room
        below = room > 1 and max((chip.winfo_reqwidth() for chip in self.capability_chips), default=0) > room
        layout = 'below' if below else 'vertical' if vertical else 'inline'
        if layout == self._capability_layout:
            return
        self._capability_layout = layout
        self.capability_box.pack_forget()
        self.capability_row.title_label.pack_configure(side='top' if below else 'left',
                                                       anchor='w', fill='x' if below else 'none')
        self.capability_box.pack(side='top' if below else 'right', anchor='w',
                                  fill='x' if below else 'none', pady=(gap if below else 0, 0))
        for index, chip in enumerate(self.capability_chips):
            chip.pack_forget()
            chip.pack(side='top' if vertical or below else 'left', anchor='w',
                      padx=(0 if vertical or below or not index else gap, 0),
                      pady=(gap if (vertical or below) and index else 0, 0))

    def _theme_changed(self, manager):
        self._draw_capabilities()
        self.refresh_usage()

    def _theme_changed(self, manager):
        self._draw_capabilities()
        self.refresh_usage()

    # --- capability summary -----------------------------------------------------------------------------------



    def refresh(self) -> None:
        super().refresh()
        self._draw_capabilities()
        if hasattr(self, 'audio_section'):
            self.audio_section.refresh()
