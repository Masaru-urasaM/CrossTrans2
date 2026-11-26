"""Audio preferences and multi-provider audio key management hosted in Settings > API keys."""
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import tkinter as tk
from tkinter import ttk

from src.constants import LANGUAGES
from src.core import key_testing as kt
from src.core import shortcut_rules as rules
from src.core.audio_media import list_devices
from src.core.audio_service import eligible_keys, one_step_outlook, resolve_audio_source_rows
from src.core.speech_engines import (
    AUDIO_MODEL_SUGGESTIONS, get_default_model, probe_speech, SpeechProbeOutcome,
    resolve_audio_provider, select_audio_model,
)
from src.ui import components as ui
from src.ui.i18n import tr
from src.ui.settings.background import BackgroundRunner
from src.ui.settings.page_shortcuts import status_text
from src.utils.log_events import log_event
from src.ui.components import context as C
from src.utils.ui_helpers import schedule_window_callback


SOURCE_OPTIONS = (('microphone', 'Microphone'), ('pc', 'PC audio'), ('both', 'PC + Microphone'))
SAVE_OPTIONS = (('ask', 'Ask when closing'), ('never', 'Do not save'), ('always', 'Always offer to save'))
HOTKEY_ACTIONS = (('panel', 'Open the audio panel'), ('hold', 'Hold to record, release to translate'))
MODE_OPTIONS = (
    ('two_step', 'Two-step (Transcribe speech → Translate text)'),
    ('one_step', 'One-step (Translate speech directly in one pass)'),
)
AUTO_LANGUAGE = ('Auto', 'auto', 'Same as the Translator')

AUDIO_PROVIDERS = [
    'Auto', 'Groq', 'OpenAI', 'Google', 'Mistral',
    'OpenRouter', 'Together', 'SambaNova', 'SiliconFlow'
]


class AudioKeyCard(ui.Frame):
    """One audio speech recognition key card: provider, model, key, and live test."""

    def __init__(self, parent, *, number: int, provider: str, model: str, key: str,
                 from_key_id: Optional[str], on_edited: Callable[[bool], None],
                 on_test: Callable, on_remove: Callable, serial: int,
                 get_api_keys: Callable[[], List[Dict[str, Any]]]):
        ctx = C.get()
        px = ctx.px
        super().__init__(parent, on='alt', padding=px(8))
        self.serial = serial
        self.number = number
        self._key = key or ''
        self.from_key_id = from_key_id
        self._on_edited = on_edited
        self._on_test = on_test
        self._on_remove = on_remove
        self._get_api_keys = get_api_keys
        self.revealed = False
        self.audio_capable = None
        self.tag = f'audio-test-{serial}'

        self.meta_line = ui.Frame(self, on='alt')
        self.meta_line.pack(fill='x')
        self.key_line = ui.Frame(self, on='alt')
        self.key_line.pack(fill='x', pady=(px(4), 0))

        title = tr('Primary audio key') if number == 0 else tr('Backup audio key {0}', number)
        self.title_label = ui.Label(self.meta_line, title, kind='strong', on='alt')
        self.title_label.pack(side='left', padx=(0, px(8)))

        self.provider = ui.Dropdown(
            self.meta_line,
            [(p, f'Provider: {p}') for p in AUDIO_PROVIDERS],
            provider or 'Auto',
            on_change=self._provider_chosen,
            label='Provider',
            on='alt'
        )
        self.provider.pack(side='left', padx=(0, px(6)))

        # Model Combobox: suggestions + free text entry
        prov_val = provider or 'Auto'
        model_val = model or ('Auto' if prov_val == 'Auto' else get_default_model(prov_val))
        self.model_var = tk.StringVar(value=model_val)
        self.model_combo = ttk.Combobox(
            self.meta_line,
            textvariable=self.model_var,
            values=AUDIO_MODEL_SUGGESTIONS.get(prov_val, AUDIO_MODEL_SUGGESTIONS['Groq']),
            width=22
        )
        self.model_combo.pack(side='left', padx=(0, px(6)))
        self.model_combo.bind('<<ComboboxSelected>>', lambda e: self._model_edited())
        self.model_combo.bind('<KeyRelease>', lambda e: self._model_edited())

        # "Pick from API keys" button
        self.pick_btn = ui.Button(
            self.meta_line,
            'Pick from API keys',
            command=self._show_api_keys_menu,
            kind='soft',
            on='alt'
        )
        self.pick_btn.pack(side='left', padx=(0, px(6)))

        # Delete / Reset button
        self.remove_btn = ui.IconButton(
            self.meta_line,
            'trash',
            command=lambda: self._on_remove(self),
            label='Reset to Auto' if number == 0 else 'Remove audio key',
            on='alt'
        )
        self.remove_btn.pack(side='right')

        # Key line
        self.field = ui.TextField(self.key_line, on='alt', show='*', width=24)
        if self._key:
            self.field.set(self._key)
        self.field.pack(side='left', fill='x', expand=True, padx=(0, px(6)))
        self.field.bind('<KeyRelease>', self._typed, add='+')
        self.field.bind('<<Paste>>', lambda e: schedule_window_callback(
            self._root(), self, 'idle', self._typed), add='+')

        self.eye = ui.IconButton(self.key_line, 'eye', command=self._toggle_reveal, label='Show key', on='alt')
        self.eye.pack(side='left', padx=(0, px(6)))

        self.test_btn = ui.Button(self.key_line, 'Test', command=lambda: self._on_test(self), kind='soft', on='alt')
        self.test_btn.pack(side='left', padx=(0, px(6)))

        self.status_label = ui.Label(self.key_line, '', kind='muted', on='alt', justify='left')
        self.status_label.pack(side='left', fill='x', expand=True)

    @property
    def key_text(self) -> str:
        try:
            self._key = self.field.get()
        except tk.TclError:
            pass
        return self._key

    def values(self) -> Tuple[str, str, str, Optional[str]]:
        return self.provider.value, self.model_var.get().strip(), self.key_text.strip(), self.from_key_id

    def set_status(self, text: str, tone: str = kt.MUTED):
        ctx = C.get()
        colors = {
            kt.SUCCESS: ctx.palette.success,
            kt.DANGER: ctx.palette.danger,
            kt.WARNING: ctx.palette.accent,
            kt.MUTED: ctx.palette.muted,
        }
        self.status_label.configure(text=tr(text), foreground=colors.get(tone, ctx.palette.text))

    def _sync_auto_hint(self):
        if self.number == 0 and not self.key_text.strip() and not self.from_key_id and self.provider.value == 'Auto':
            self.set_status('Auto: uses main API keys', kt.MUTED)

    def _model_edited(self):
        self.audio_capable = None
        self._on_edited(True)

    def _typed(self, event=None):
        self._key = self.field.get()
        self.from_key_id = None
        self.audio_capable = None
        if not self._key.strip() and self.number == 0 and self.provider.value == 'Auto':
            self.model_var.set('Auto')
            self.set_status('Auto: uses main API keys', kt.MUTED)
        else:
            self.set_status('', kt.MUTED)
        self._on_edited(False)

    def _provider_chosen(self, prov: str):
        self.audio_capable = None
        if prov == 'Auto' and self.from_key_id:
            self._reset_to_auto()
            return
        suggestions = AUDIO_MODEL_SUGGESTIONS.get(prov, AUDIO_MODEL_SUGGESTIONS['Groq'])
        self.model_combo.configure(values=suggestions)
        cur = self.model_var.get().strip()
        other_defaults = {
            m for p, ms in AUDIO_MODEL_SUGGESTIONS.items() if p != prov for m in ms
        }
        if prov == 'Auto' and not self.key_text.strip():
            self.model_var.set('Auto')
            self._sync_auto_hint()
        elif suggestions and (not cur or cur == 'Auto' or cur in other_defaults):
            self.model_var.set(suggestions[0])
        self._on_edited(True)

    def _reset_to_auto(self):
        self.provider.set('Auto')
        self.model_combo.configure(values=AUDIO_MODEL_SUGGESTIONS['Groq'])
        self.model_var.set('Auto')
        self.field.set('')
        self._key = ''
        self.from_key_id = None
        self.audio_capable = None
        self.set_status('Auto: uses main API keys', kt.MUTED)
        self._on_edited(True)

    def _toggle_reveal(self):
        self.revealed = not self.revealed
        self.field.configure(show='' if self.revealed else '*')
        self.eye.configure(image=C.get().icon('eye-slash' if self.revealed else 'eye', 16, C.get().palette.text))

    def _show_api_keys_menu(self):
        ctx = C.get()
        p = ctx.palette
        keys = self._get_api_keys()
        menu = ui.Menu(self, tearoff=0, bg=p.surface, fg=p.text, activebackground=p.soft,
                       activeforeground=p.soft_text, font=ctx.font('body'), relief='flat', bd=1)
        if self.number == 0:
            menu.add_command(label=tr('Auto (use main API keys)'), command=self._reset_to_auto)
            menu.add_separator()
        valid = [k for k in keys if k.get('api_key', '').strip()]
        if not valid:
            menu.add_command(label='No API keys saved yet', state='disabled')
        else:
            for idx, item in enumerate(valid):
                name = item.get('name') or ('Primary key' if idx == 0 else f'Backup key {idx}')
                prov = item.get('provider', 'Auto')
                label = f"{name} ({prov})"
                menu.add_command(label=label, command=lambda it=item: self._apply_picked_key(it))
        try:
            menu.tk_popup(self.pick_btn.winfo_rootx(),
                          self.pick_btn.winfo_rooty() + self.pick_btn.winfo_height())
        finally:
            menu.grab_release()

    def _apply_picked_key(self, item: dict):
        key = item.get('api_key', '')
        prov = item.get('provider', 'Auto')
        self.field.set(key)
        self._key = key
        self.from_key_id = item.get('id')
        self.audio_capable = None
        self.set_status('', kt.MUTED)
        if prov and prov != 'Auto':
            self.provider.set(prov)
            self._provider_chosen(prov)
            return
        self.provider.set('Auto')
        self._on_edited(True)


class AudioSection:
    """Audio translation section embedded in Settings > API keys."""

    def __init__(self, page):
        self.page = page
        self.config = page.config
        self.audio_cards: List[AudioKeyCard] = []
        self._serial = 0
        self.audio_enabled_var = tk.BooleanVar(value=self.config.get('audio_enabled', True))
        self.device_runner = BackgroundRunner(page.body)
        self.audio_device_choices = {}

    def build(self):
        cfg = self.config
        px = C.get().px
        group = self.page.group(
            'Audio translation',
            'The audio shortcut opens the audio panel, where you record and stop. Recordings: up to 3 minutes. '
            'Uploaded audio/video: up to 30 minutes.'
        )
        self.group = group

        # 1. Switch row (audio_enabled)
        row_enabled = self.page.row(group, 'audio_enabled')
        self.audio_switch = ui.Switch(
            row_enabled.control,
            variable=self.audio_enabled_var,
            command=self._on_audio_toggled,
            on=row_enabled.plane
        )
        self.audio_switch.pack(side='right')

        # 2. Audio model / API keys row (audio_model)
        row_model = self.page.row(group, 'audio_model', stacked=True)
        self.cards_box = ui.Frame(row_model.control, on=row_model.plane)
        self.cards_box.pack(fill='x')

        existing_audio_keys = cfg.get_audio_keys() or [{}]
        for entry in existing_audio_keys:
            self._add_audio_card(entry)

        bar = ui.ActionBar(row_model.control, on=row_model.plane)
        bar.pack(fill='x', pady=(px(6), 0))
        self.add_audio_btn = bar.add(ui.Button(
            bar, 'Add backup audio key', command=self.add_backup_audio_key,
            icon='plus-lg', kind='soft', on=row_model.plane
        ))

        # 3. Audio shortcut row (audio_shortcut)
        row_shortcut = self.page.row(group, 'audio_shortcut')
        current_combo = rules.normalize(cfg.get('audio_hotkey', 'win+alt+a'))
        self.shortcut_editor = ui.ShortcutEditor(
            row_shortcut.control,
            current_combo,
            owner='__audio__',
            default='win+alt+a',
            validate=lambda combo: rules.validate(combo, 'Record and translate', self._get_other_shortcuts()),
            on_change=self._audio_shortcut_changed,
            on_recording=self._audio_shortcut_recording,
            source_factory=self._key_source,
            on=row_shortcut.plane
        )
        self.shortcut_editor.pack(fill='x', expand=True)

        # 3b. What the shortcut does (audio_hotkey_action): the panel, or today's hold-to-record (Decision 88)
        row_action = self.page.row(group, 'audio_hotkey_action')
        self.page.audio_hotkey_action = ui.Dropdown(
            row_action.control, HOTKEY_ACTIONS, cfg.get('audio_hotkey_action', 'panel'),
            on_change=lambda val: self._store('audio_hotkey_action', val),
            label='Shortcut action', on=row_action.plane
        )
        self.page.audio_hotkey_action.pack(fill='x')

        # 4. Processing Mode (audio_mode)
        row_mode = self.page.row(group, 'audio_mode')
        self.page.audio_mode = ui.Dropdown(
            row_mode.control, MODE_OPTIONS, cfg.get('audio_mode', 'two_step'),
            on_change=self._mode_chosen,
            label='Processing mode', on=row_mode.plane
        )
        self.page.audio_mode.pack(fill='x')
        self.mode_note = ui.Label(row_mode.control, '', kind='muted', wraplength=px(380), justify='left',
                                  on=row_mode.plane)
        self._mode_note_pad = px(4)
        self._refresh_mode_note()

        # 5. Target language (audio_language)
        languages = list(LANGUAGES)
        recent = cfg.get_recent_languages
        pinned = cfg.get_pinned_languages if hasattr(cfg, 'get_pinned_languages') else []
        row_lang = self.page.row(group, 'audio_language')
        self.page.audio_picker = ui.LanguagePicker(
            row_lang.control, [AUTO_LANGUAGE] + languages, cfg.get('audio_target_language', 'Auto'),
            on_change=lambda val: self._store('audio_target_language', val, 'audio_language'),
            recent=recent, pinned=pinned, label='Translate audio into', on=row_lang.plane
        )
        self.page.audio_picker.pack()

        # 6. Recording source (audio_source)
        row_src = self.page.row(group, 'audio_source')
        self.page.audio_source = ui.Dropdown(
            row_src.control, SOURCE_OPTIONS, cfg.get('audio_source', 'pc'),
            on_change=lambda val: self._store('audio_source', val),
            label='Recording source', on=row_src.plane
        )
        self.page.audio_source.pack()

        # 7. Noise reduction (audio_denoise)
        self.page.toggle(
            group, 'audio_denoise',
            lambda: cfg.get('audio_denoise', True),
            lambda val: cfg.set('audio_denoise', val)
        )

        # 8. Save policy (audio_save_policy)
        row_save = self.page.row(group, 'audio_save_policy')
        self.page.audio_save_policy = ui.Dropdown(
            row_save.control, SAVE_OPTIONS, cfg.get('audio_save_policy', 'ask'),
            on_change=lambda val: self._store('audio_save_policy', val),
            label='Save recordings', on=row_save.plane
        )
        self.page.audio_save_policy.pack()

        # 9. Audio devices (audio_devices)
        row_dev = self.page.row(group, 'audio_devices', stacked=True)
        self.audio_device_host = ui.Frame(row_dev.control, on=row_dev.plane)
        self.audio_device_host.pack(fill='x')
        self.audio_device_status = ui.Label(
            row_dev.control, 'System default devices are used until you choose others.',
            kind='muted', wraplength=px(380), justify='left', on=row_dev.plane
        )
        self.audio_device_status.pack(anchor='w')
        self.audio_devices_button = ui.Button(
            row_dev.control, 'Refresh audio devices',
            command=self._refresh_devices, kind='soft', on=row_dev.plane
        )
        self.audio_devices_button.pack(anchor='w', pady=(px(6), 0))

        self._sync_visibility()
        self.refresh_status()

    def _get_other_shortcuts(self) -> Dict[str, str]:
        cfg = self.config
        stored = dict(cfg.get_all_hotkeys() or {})
        stored['Screenshot'] = rules.normalize(cfg.get_screenshot_hotkey())
        if cfg.get_fix_grammar_hotkey_enabled():
            stored['Fix grammar'] = rules.normalize(cfg.get_fix_grammar_hotkey())
        return {name: combo for name, combo in stored.items() if combo}

    def _audio_shortcut_changed(self, combo: str):
        self.config.set('audio_hotkey', combo)
        self.page.changed('audio_shortcut')

    def _audio_shortcut_recording(self, active: bool):
        self.page.host.set_recording(active)

    def _key_source(self):
        factory = self.page.host.key_source_factory or ui.KeySource
        return factory()

    def _sync_visibility(self):
        enabled = self.audio_enabled_var.get()
        sub_keys = ('audio_model', 'audio_shortcut', 'audio_mode', 'audio_language', 'audio_source',
                    'audio_denoise', 'audio_save_policy', 'audio_devices')
        for k in sub_keys:
            r = self.page.row_for(k)
            if r:
                r.hidden = not enabled
        self.group.relayout()

    def _on_audio_toggled(self):
        enabled = self.audio_enabled_var.get()
        if not enabled and hasattr(self, 'shortcut_editor') and self.shortcut_editor.recording:
            self.shortcut_editor.cancel_recording()
        self.config.set('audio_enabled', enabled)
        self._sync_visibility()
        self.page.changed('audio_enabled')
        self.page.host.api_changed()

    def _mode_chosen(self, value):
        self._store('audio_mode', value)
        self._refresh_mode_note()

    def _refresh_mode_note(self):
        """Say so when One-step is chosen but the audio key in use can only transcribe (it runs as two steps)."""
        note = getattr(self, 'mode_note', None)
        if note is None:
            return
        if self.config.get('audio_mode', 'two_step') == 'one_step' and one_step_outlook(self.config) == 'engine_without_one_step':
            note.configure(text=tr('One-step needs a Google Gemini audio key. With the current key, audio is '
                                   'recognized first and then translated (two steps).'))
            note.pack(anchor='w', pady=(getattr(self, '_mode_note_pad', 4), 0))
        else:
            note.pack_forget()

    def _store(self, key, value, entry=None):
        self.config.set(key, value)
        self.page.changed(entry or key)

    def _add_audio_card(self, entry: dict) -> AudioKeyCard:
        px = C.get().px
        card = AudioKeyCard(
            self.cards_box,
            number=len(self.audio_cards),
            provider=entry.get('provider') or 'Auto',
            model=entry.get('model_name') or '',
            key=entry.get('api_key') or '',
            from_key_id=entry.get('from_key_id'),
            on_edited=lambda immediate: self.save(),
            on_test=self.test_audio_card,
            on_remove=self.remove_audio_card,
            serial=self._serial,
            get_api_keys=self.config.get_api_keys
        )
        card.audio_capable = entry.get('audio_capable')
        if card.audio_capable is True:
            card.set_status('Audio recognition works.', kt.SUCCESS)
        elif card.audio_capable is False:
            card.set_status('Audio recognition failed.', kt.DANGER)
        else:
            card._sync_auto_hint()
        self._serial += 1
        card.pack(fill='x', pady=(px(4) if self.audio_cards else 0, 0))
        self.audio_cards.append(card)
        return card

    def add_backup_audio_key(self):
        self._add_audio_card({})
        self.save()

    def remove_audio_card(self, card: AudioKeyCard):
        if card in self.audio_cards:
            self.audio_cards.remove(card)
            card.destroy()
            for idx, c in enumerate(self.audio_cards):
                c.number = idx
                c.title_label.configure(text=tr('Primary audio key') if idx == 0 else tr('Backup audio key {0}', idx))
                if hasattr(c.remove_btn, 'tooltip'):
                    c.remove_btn.tooltip.text = tr('Reset to Auto' if idx == 0 else 'Remove audio key')
            if not self.audio_cards:
                self._add_audio_card({})
            self.save()

    def test_audio_card(self, card: AudioKeyCard):
        provider, model, key, _ = card.values()
        testing_auto = False
        if not key:
            mode, source_rows = resolve_audio_source_rows(self.config)
            auto_rows = eligible_keys(source_rows, self.config.get('audio_model', 'whisper-large-v3')) if mode == 'auto' else []
            if not auto_rows:
                card.set_status('Paste or select a key first.', kt.WARNING)
                return
            first_row = auto_rows[0]
            key = first_row['api_key']
            provider = resolve_audio_provider(first_row.get('provider', 'Auto'), first_row.get('model_name', ''), key)
            model = select_audio_model(provider, first_row, self.config.get('audio_model', 'whisper-large-v3'))
            testing_auto = True

        card.set_status('Testing audio recognition...', kt.MUTED)
        card.test_btn.configure(state='disabled')
        started = time.monotonic()

        def work(progress, cancelled):
            return probe_speech(key, provider, model, cancelled=cancelled)

        def done(outcome: Optional[SpeechProbeOutcome], error):
            card.test_btn.configure(state='normal')
            self._log_test(card, provider, model, outcome, error, started, testing_auto)
            if error is not None:
                card.set_status(str(error), kt.DANGER)
                if not testing_auto:
                    card.audio_capable = False
            elif outcome is None or (outcome.supported is None and outcome.error == 'Cancelled'):
                card.set_status(tr('Test cancelled.'), kt.MUTED)
            elif outcome.supported is True:
                msg = tr('Audio recognition works.')
                if testing_auto:
                    msg += f' (Auto: {provider})'
                elif outcome.one_step:
                    msg += ' ' + tr('(One-step supported)')
                card.status_label.configure(text=msg, foreground=C.get().palette.success)
                if not testing_auto:
                    card.audio_capable = True
            else:
                card.set_status(outcome.error or 'Audio recognition could not be verified.', kt.DANGER)
                if not testing_auto:
                    card.audio_capable = False
            if not testing_auto:
                self.save()
            self.page._draw_capabilities()

        self.page.runner.start(card.tag, work, on_done=done)

    def _log_test(self, card, provider, model, outcome: Optional[SpeechProbeOutcome], error, started, auto) -> None:
        """One line per audio Test, like the API cards' (Decision 102): measurements, never the key or a message."""
        answered = outcome.model if outcome is not None else ''
        fields = dict(card=self.audio_cards.index(card) + 1 if card in self.audio_cards else None,
                      provider=provider or kt.AUTO, model=answered or model or kt.AUTO, auto=auto,
                      ms=round((time.monotonic() - started) * 1000))
        if error is None and outcome is not None and outcome.supported is True:
            log_event('Settings', 'audio_test.ok', one_step=outcome.one_step, **fields)
        elif error is not None or outcome is None:
            log_event('Settings', 'audio_test.fail', level=logging.WARNING, error=type(error).__name__, **fields)
        else:
            log_event('Settings', 'audio_test.fail', level=logging.WARNING, supported=outcome.supported,
                      status=outcome.status, cancelled=outcome.error == 'Cancelled', **fields)

    def save(self):
        rows = []
        for card in self.audio_cards:
            provider, model, key, from_id = card.values()
            if not key and not from_id:
                continue
            rows.append({
                'provider': provider,
                'model_name': model,
                'api_key': key,
                'from_key_id': from_id,
                'audio_capable': card.audio_capable,
                'is_audio_key': True,
            })
        self.config.set_audio_keys(rows)
        self._refresh_mode_note()
        if getattr(self.page, '_built', False):
            self.page._draw_capabilities()
            self.page.host.api_changed()

    def refresh_status(self):
        if not hasattr(self, 'shortcut_editor'):
            return
        try:
            state = self.page.host.shortcut_status() or {}
        except Exception:
            state = {}
        self.shortcut_editor.set_status(status_text(state.get('__audio__')))

    def refresh(self):
        cfg = self.config
        self.audio_enabled_var.set(cfg.get('audio_enabled', True))
        self._sync_visibility()
        if hasattr(self, 'shortcut_editor'):
            self.shortcut_editor.set_value(rules.normalize(cfg.get('audio_hotkey', 'win+alt+a')))
        self.refresh_status()
        if hasattr(self.page, 'audio_mode'):
            self.page.audio_mode.set(cfg.get('audio_mode', 'two_step'))
            self._refresh_mode_note()
        if hasattr(self.page, 'audio_picker'):
            self.page.audio_picker.set(cfg.get('audio_target_language', 'Auto'))
        if hasattr(self.page, 'audio_source'):
            self.page.audio_source.set(cfg.get('audio_source', 'pc'))
        if hasattr(self.page, 'audio_save_policy'):
            self.page.audio_save_policy.set(cfg.get('audio_save_policy', 'ask'))
        if hasattr(self.page, 'audio_hotkey_action'):
            self.page.audio_hotkey_action.set(cfg.get('audio_hotkey_action', 'panel'))

    def _refresh_devices(self):
        self.audio_devices_button.configure(state='disabled')

        def done(devices, error):
            self.audio_devices_button.configure(state='normal')
            if error:
                self.audio_device_status.configure(text=tr(str(error)))
                return
            for child in self.audio_device_host.winfo_children():
                child.destroy()
            for key, loopback, title in (('audio_playback', True, 'PC playback device'),
                                          ('audio_microphone', False, 'Microphone device')):
                options = [(None, 'System default')] + [({'index': item['index'], 'name': item['name']}, item['name'])
                            for item in devices if item['loopback'] is loopback]
                ui.Label(self.audio_device_host, title, kind='muted').pack(anchor='w')
                picker = ui.Dropdown(self.audio_device_host, options, self.config.get(key),
                                      on_change=lambda value, name=key: self._store(name, value, 'audio_devices'))
                picker.pack(fill='x', pady=(0, C.get().px(8)))
                self.audio_device_choices[key] = picker
            if not devices:
                self.audio_device_status.configure(
                    text=tr('No active audio playback or microphone devices were found. Connect speakers, headphones, or a microphone, then refresh.')
                )
            else:
                self.audio_device_status.configure(
                    text=tr('Device names may change after reconnecting. Use headphones with both sources to reduce echo.')
                )
            self.page.adopt()

        self.device_runner.start('devices', lambda progress, cancelled: list_devices(), on_done=done)
