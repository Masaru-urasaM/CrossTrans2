"""
The Shortcuts page (TODO.md U8, Decision 51): a shortcut per language, one for screenshots and the
optional one for Fix grammar - each a `ShortcutEditor`, so each is shown as keycaps and changed by
pressing the keys, never by typing "win+alt+e".

Judging is in `shortcut_rules` and recording in `shortcut_recorder`; this page only wires them to the
configuration and to the other shortcuts (a combination used twice is refused, naming its owner).
Every change is stored at once and announced with `host.changed(...)`, and the application registers
the hotkeys again. What came of that - another app already holds one - is told back through
`refresh_status()` as a line under the shortcut.
"""
import logging
import tkinter as tk
from typing import Dict, Optional

from src.constants import LANGUAGES, SETTINGS_MAX_ADDED_LANGUAGES
from src.core import settings_index as index
from src.core import shortcut_rules as rules
from src.ui import components as ui
from src.ui.components import context as C
from src.ui.settings.page import SettingsPage

SCREENSHOT = 'Screenshot'                   # the owner's name in a message: "already used for Screenshot"
FIX_GRAMMAR = 'Fix grammar'
AUDIO = 'Record and translate'
OWNER_KEYS = {SCREENSHOT: '__screenshot__', FIX_GRAMMAR: '__fix_grammar__', AUDIO: '__audio__'}
ADD_LANGUAGE = 'Add a new shortcut'
LIMIT_REACHED = f'You can add up to {SETTINGS_MAX_ADDED_LANGUAGES} languages of your own.'
STATUS_TEXT = {
    'taken': 'Another app is already using this shortcut, so it does nothing yet. Choose another.',
    'invalid': 'CrossTrans cannot use this shortcut. Choose another.',
    'failed': 'Windows would not give CrossTrans this shortcut. Choose another.',
}


def status_text(state: Optional[str]) -> str:
    """What to tell the user about how a shortcut's registration went ('' when nothing is wrong)."""
    return STATUS_TEXT.get(state or '', '')


class ShortcutsPage(SettingsPage):
    key = 'shortcuts'

    def populate(self) -> None:
        cfg = self.config
        self.values: Dict[str, str] = self._read_languages()
        self.screenshot_value = rules.normalize(cfg.get_screenshot_hotkey())
        self.fix_value = rules.normalize(cfg.get_fix_grammar_hotkey())
        self.fix_enabled = bool(cfg.get_fix_grammar_hotkey_enabled())
        self.audio_enabled = cfg.get('audio_enabled', True) is True
        self.audio_value = rules.normalize(cfg.get('audio_hotkey', 'win+alt+a'))
        self.editors: Dict[str, ui.ShortcutEditor] = {}
        self.recording_owners = set()
        group = self.group()
        self.toggle(group, 'shortcuts_enabled', cfg.get_shortcuts_enabled, self._set_enabled)
        self.content = ui.Frame(self.body, on='bg')
        self.translate_section = ui.Frame(self.content, on='bg')
        self.translate_section.pack(fill='x')
        self._build_translate_section()
        self._build_screenshot_group()
        self._build_fix_grammar_group()
        self._build_audio_group()
        self._sync_enabled()
        self.refresh_status()

    def _set_enabled(self, enabled):
        self.config.set_shortcuts_enabled(enabled)
        self._sync_enabled()

    def _sync_enabled(self):
        if self.config.get_shortcuts_enabled():
            self.content.pack(fill='x')
        else:
            for editor in self.editors.values():
                editor.cancel_recording()
            self.content.pack_forget()

    # --- what is stored ---------------------------------------------------------------------------
    def _read_languages(self) -> Dict[str, str]:
        """Every language with a shortcut: the ones that ship first (shown even when cleared), then
        the ones the user added, in the order they were added."""
        cfg = self.config
        stored = cfg.get_all_hotkeys()
        order = list(cfg.DEFAULT_LANGUAGES) + [name for name in stored if name not in cfg.DEFAULT_LANGUAGES]
        return {name: rules.normalize(stored.get(name, '')) for name in order}

    def _others(self, owner: str) -> Dict[str, str]:
        """The shortcuts a recording for `owner` must not repeat (owner -> combination). The rules
        ignore the owner's own entry, so it need not be taken out here."""
        table = dict(self.values)
        table[SCREENSHOT] = self.screenshot_value
        if self.fix_enabled:
            table[FIX_GRAMMAR] = self.fix_value
        if self.audio_enabled:
            table[AUDIO] = self.audio_value
        return {name: combo for name, combo in table.items() if combo}

    def _save_languages(self) -> None:
        self.config.set_language_hotkeys(self.values)

    # --- one editor -------------------------------------------------------------------------------
    def _source(self):
        factory = self.host.key_source_factory or ui.KeySource
        return factory()

    def _editor(self, parent, owner: str, value: str, default: str, on_change, plane: str) -> ui.ShortcutEditor:
        editor = ui.ShortcutEditor(
            parent, value, owner=owner, default=default,
            validate=lambda combo, name=owner: rules.validate(combo, name, self._others(name)),
            on_change=on_change, on_recording=lambda active, name=owner: self._recording(name, active),
            source_factory=self._source, on=plane)
        self.editors[owner] = editor
        return editor

    def _recording(self, owner: str, active: bool) -> None:
        """One recording at a time, and the application is told while any is on (it stops answering
        its hotkeys: recording the one that translates must not translate)."""
        if active:
            self.recording_owners.add(owner)
            for other, editor in list(self.editors.items()):
                if other != owner and editor.recording:
                    editor.cancel_recording()
        else:
            self.recording_owners.discard(owner)
        self.host.set_recording(bool(self.recording_owners))

    def _revalidate_all(self) -> None:
        for owner, editor in self.editors.items():
            if owner == FIX_GRAMMAR and not self.fix_enabled:
                continue
            if owner == AUDIO and not self.audio_enabled:
                continue
            editor.revalidate()

    # --- the language shortcuts -----------------------------------------------------------------------
    def _build_translate_section(self) -> None:
        px = C.get().px
        for child in self.translate_section.winfo_children():
            child.destroy()
        self.groups = [g for g in self.groups if g.winfo_exists()]
        for name in [n for n in self.editors if n not in (SCREENSHOT, FIX_GRAMMAR)]:
            self.editors.pop(name)
        cfg = self.config
        added = [name for name in self.values if name not in cfg.DEFAULT_LANGUAGES]
        remaining = [entry for entry in LANGUAGES if entry[0] not in self.values]
        offer = bool(remaining) and len(added) < SETTINGS_MAX_ADDED_LANGUAGES
        entry = index.entry('language_shortcuts')
        group = self.group(entry.title, entry.description, parent=self.translate_section,
                           gap=8 if offer or len(added) >= SETTINGS_MAX_ADDED_LANGUAGES else 24)
        for position, name in enumerate(self.values):
            row = group.row(name, '', key='language_shortcuts' if position == 0 else None)
            if position == 0:
                self.rows['language_shortcuts'] = row
            editor = self._editor(row.control, name, self.values[name], cfg.DEFAULT_HOTKEYS.get(name, ''),
                                  lambda combo, language=name: self._language_changed(language, combo),
                                  row.plane)
            editor.pack(side='left', fill='x', expand=True)
            if name not in cfg.DEFAULT_LANGUAGES:
                remove = ui.IconButton(row.control, 'trash', command=lambda language=name: self._remove(language),
                                       label=f'Remove {name}', on=row.plane)
                remove.pack(side='left', padx=(px(8), 0), anchor='n')
        self.add_picker = None
        if offer:
            bar = ui.Frame(self.translate_section, on='bg')
            bar.pack(fill='x', pady=(0, px(24)))
            self.add_picker = ui.LanguagePicker(bar, remaining, ADD_LANGUAGE, on_change=self._add,
                                                label=ADD_LANGUAGE, on='bg')
            self.add_picker.pack(anchor='w')
        elif len(added) >= SETTINGS_MAX_ADDED_LANGUAGES:
            ui.Label(self.translate_section, LIMIT_REACHED, kind='muted', on='bg').pack(
                anchor='w', pady=(0, px(24)))
        self.adopt()

    def _language_changed(self, language: str, combo: str) -> None:
        self.values[language] = combo
        self._save_languages()
        self.changed('language_shortcuts')
        self._revalidate_all()

    def _add(self, language: str) -> None:
        if language in self.values or len([n for n in self.values if n not in self.config.DEFAULT_LANGUAGES]) \
                >= SETTINGS_MAX_ADDED_LANGUAGES:
            return
        self.values[language] = ''
        self._build_translate_section()
        editor = self.editors.get(language)
        if editor is not None:
            editor.change_btn.focus_set()
        logging.info(f"[Settings] language added to the shortcuts: {language}")

    def _remove(self, language: str) -> None:
        """Takes the language out of the list. The row's own button is what was clicked, so the rows are
        built again once its click has finished."""
        if language in self.config.DEFAULT_LANGUAGES or language not in self.values:
            return
        del self.values[language]
        self._save_languages()
        self.body.after_idle(self._after_remove)

    def _after_remove(self) -> None:
        try:
            self._build_translate_section()
        except Exception:
            logging.exception("[Settings] could not rebuild the language shortcuts")
            return
        self.changed('language_shortcuts')
        self._revalidate_all()

    # --- screenshot -----------------------------------------------------------------------------------
    def _build_screenshot_group(self) -> None:
        group = self.group(parent=self.content)
        row = self.row(group, 'screenshot_shortcut')
        editor = self._editor(row.control, SCREENSHOT, self.screenshot_value,
                              self.config.SCREENSHOT_HOTKEY_DEFAULT, self._screenshot_changed, row.plane)
        editor.pack(fill='x', expand=True)

    def _screenshot_changed(self, combo: str) -> None:
        self.screenshot_value = combo
        self.config.set_screenshot_hotkey(combo)
        self.changed('screenshot_shortcut')
        self._revalidate_all()

    def _build_audio_group(self):
        group = self.group(parent=self.content)
        row = self.row(group, 'audio_shortcut')
        self.audio_variable = tk.BooleanVar(master=self.body, value=self.audio_enabled)
        switch = ui.Switch(row.control, variable=self.audio_variable, command=self._audio_toggled, on=row.plane)
        switch.pack(side='right')
        self.audio_host = ui.Frame(row.control, on=row.plane)
        self._editor(self.audio_host, AUDIO, self.audio_value, 'win+alt+a',
                     self._audio_changed, row.plane).pack(fill='x', expand=True)
        self.variables['audio_shortcut'], self.switches['audio_shortcut'] = self.audio_variable, switch
        self._sync_audio_editor()

    def _sync_audio_editor(self):
        if self.audio_enabled:
            self.audio_host.pack(side='right', fill='x', expand=True, padx=(0, C.get().px(16)))
        else:
            self.audio_host.pack_forget()

    def sync_hotkeys(self):
        cfg = self.config
        self.audio_enabled = cfg.get('audio_enabled', True) is True
        self.audio_value = rules.normalize(cfg.get('audio_hotkey', 'win+alt+a'))
        if AUDIO in self.editors:
            self.editors[AUDIO].set_value(self.audio_value)
        if hasattr(self, 'audio_variable'):
            self.audio_variable.set(self.audio_enabled)
        self._sync_audio_editor()

    def _audio_toggled(self):
        self.audio_enabled = self.audio_variable.get()
        self.config.set('audio_enabled', self.audio_enabled)
        self._sync_audio_editor()
        self.changed('audio_shortcut')
        self._revalidate_all()

    def _audio_changed(self, combo):
        self.audio_value = combo
        self.config.set('audio_hotkey', combo)
        self.changed('audio_shortcut')
        self._revalidate_all()

    # --- fix grammar: off until switched on, its default is also the Xbox Game Bar's -------------------------
    def _build_fix_grammar_group(self) -> None:
        group = self.group(parent=self.content)
        row = self.row(group, 'fix_grammar_shortcut')
        self.fix_variable = tk.BooleanVar(master=self.body, value=self.fix_enabled)
        self.fix_switch = ui.Switch(row.control, variable=self.fix_variable, command=self._fix_toggled,
                                    on=row.plane)
        self.fix_switch.pack(side='right')
        self.fix_host = ui.Frame(row.control, on=row.plane)
        editor = self._editor(self.fix_host, FIX_GRAMMAR, self.fix_value, self.config.FIX_GRAMMAR_HOTKEY_DEFAULT,
                              self._fix_changed, row.plane)
        editor.pack(fill='x', expand=True)
        self.variables['fix_grammar_shortcut'] = self.fix_variable
        self.switches['fix_grammar_shortcut'] = self.fix_switch
        self._sync_fix_editor()
        if self.fix_enabled:
            editor.revalidate()

    def _sync_fix_editor(self) -> None:
        px = C.get().px
        if self.fix_enabled:
            self.fix_host.pack(side='right', fill='x', expand=True, padx=(0, px(16)))
        else:
            self.fix_host.pack_forget()

    def _fix_toggled(self) -> None:
        self.fix_enabled = bool(self.fix_variable.get())
        self.config.set_fix_grammar_hotkey_enabled(self.fix_enabled)
        self._sync_fix_editor()
        self.changed('fix_grammar_shortcut')
        self._revalidate_all()

    def _fix_changed(self, combo: str) -> None:
        self.fix_value = combo
        self.config.set_fix_grammar_hotkey(combo)
        self.changed('fix_grammar_shortcut')
        self._revalidate_all()

    # --- what came of registering them ---------------------------------------------------------------------
    def refresh_status(self) -> None:
        try:
            state = self.host.shortcut_status() or {}
        except Exception:
            logging.exception("[Settings] could not read how the shortcuts registered")
            state = {}
        for owner, editor in self.editors.items():
            editor.set_status(status_text(state.get(OWNER_KEYS.get(owner, owner))))

    def refresh(self) -> None:
        """Read everything again (Restore defaults): the page is built afresh."""
        for child in self.body.winfo_children():
            child.destroy()
        self.groups, self.rows, self.variables, self.switches, self._readers = [], {}, {}, {}, []
        self.populate()
        self.adopt()
