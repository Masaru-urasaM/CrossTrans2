"""
General, Appearance and Translation: the Settings pages made of switches, pickers and one choice
(TODO.md U8, Decision 51). Each is a few rows from `settings_index`; none draws anything of its own.

Every change is stored the moment it is made (there is no Save button, as before) and announced with
`host.changed(key)`, which is what shows the quiet "Saved" and lets the application react.
"""
import logging
import tkinter as tk
from typing import Optional

from src.constants import LANGUAGES
from src.core import language_prefs
from src.ui import components as ui
from src.ui.components import context as C
from src.ui.theme.tokens import THEME_OPTIONS
from src.ui.settings.page import SettingsPage
from src.ui.components.theme_picker import ThemePicker

AUTOSTART_DELAY_MS = 400            # a quickly flipped switch writes the registry once
RESTORE_TITLE = 'Restore defaults?'
RESTORE_MESSAGE = ('The shortcuts and the settings in this window go back to how CrossTrans came. '
                   'Your API keys, history, history-saving choice and language packs stay.')
AUTO_LANGUAGE = ('Auto', 'auto', 'Same as the Translator')      # the screenshot picker's extra entry
REPLACE_OPTIONS = ((False, 'Preview first'), (True, 'Replace immediately'))


class GeneralPage(SettingsPage):
    key = 'general'

    def populate(self) -> None:
        cfg = self.config
        self._autostart_job: Optional[str] = None
        self._autostart_wanted: Optional[bool] = None
        group = self.group()
        self.toggle(group, 'autostart', cfg.is_autostart_enabled, self._queue_autostart)
        self.toggle(group, 'auto_update', cfg.get_auto_check_updates, self._set_auto_update)
        self.toggle(group, 'history_saving', lambda: cfg.get('history_enabled', True),
                    lambda enabled: cfg.set('history_enabled', enabled))

        group = self.group()
        row = self.row(group, 'restore_defaults')
        self.restore_btn = ui.Button(row.control, 'Restore defaults', command=self.confirm_restore,
                                     kind='soft', icon='arrow-counterclockwise', on=row.plane)
        self.restore_btn.pack()
        self.dialog: Optional[ui.Dialog] = None
        self.body.bind('<Destroy>', self._on_destroy, add='+')

    # --- start with Windows: debounced, and never lost when the window closes first ----------------
    def _queue_autostart(self, enabled: bool) -> None:
        self._autostart_wanted = enabled
        self._cancel_autostart()
        self._autostart_job = self.body.after(AUTOSTART_DELAY_MS, self._apply_autostart)

    def _cancel_autostart(self) -> None:
        if self._autostart_job is not None:
            try:
                self.body.after_cancel(self._autostart_job)
            except (tk.TclError, ValueError):
                pass
            self._autostart_job = None

    def _apply_autostart(self) -> None:
        self._autostart_job = None
        wanted, self._autostart_wanted = self._autostart_wanted, None
        if wanted is not None:
            self.config.set_autostart(wanted)
            logging.info(f"[Settings] start with Windows: {wanted}")

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self.body:
            return
        if self._autostart_job is not None:
            self._cancel_autostart()
            self._apply_autostart()

    # --- updates ----------------------------------------------------------------------------------
    def _set_auto_update(self, enabled: bool) -> None:
        self.config.set_auto_check_updates(enabled)
        self.host.update_manager.auto_check_changed()
        logging.info(f"[Settings] check for updates automatically: {enabled}")

    # --- restore defaults -------------------------------------------------------------------------
    def confirm_restore(self) -> ui.Dialog:
        """Ask first. Cancel is the default button, so it has the focus (`Dialog.show`): this puts
        shortcuts back, and a stray Enter must not."""
        dialog = ui.Dialog(self.host.window, RESTORE_TITLE, RESTORE_MESSAGE, icon='arrow-counterclockwise')
        dialog.add_button('Cancel', default=True)
        dialog.add_button('Restore defaults', command=self.restore, kind='danger')
        dialog.show()
        self.dialog = dialog
        return dialog

    def restore(self) -> None:
        self._cancel_autostart()
        self._autostart_wanted = None
        self.config.restore_defaults()
        self.config.set_autostart(False)         # the registry's as much as the file's
        C.get().manager.set_mode(self.config.get_theme_mode())
        C.get().manager.set_text_scale(self.config.get('text_scale', 1.0))
        C.get().manager.set_ui_language(self.config.get('ui_language', 'en'))
        self.host.update_manager.auto_check_changed()
        self.host.refresh_pages()
        self.changed('restore_defaults')
        logging.info("[Settings] defaults restored")

    def refresh(self) -> None:
        self._cancel_autostart()
        self._autostart_wanted = None
        super().refresh()


class AppearancePage(SettingsPage):
    key = 'appearance'

    def populate(self) -> None:
        group = self.group()
        row = self.row(group, 'theme')
        self.theme = ui.Dropdown(row.control, [(key, dict(system='Windows', pink='Pink', blue='Blue', yellow='Yellow', green='Green').get(key, label)) for key, label in THEME_OPTIONS], C.get().manager.mode,
                                on_change=self._chosen, label='Theme', on=row.plane)
        self.theme.pack()
        self.theme_preview = ThemePicker(self.body, self.config, on_change=self._chosen)
        self.theme_preview.pack(fill='x', pady=(0, C.get().px(20)))
        row = self.row(group, 'ui_language')
        self.ui_language = ui.Dropdown(row.control, (('en', 'English'), ('ja', '日本語')),
                                      C.get().manager.ui_language, on_change=self._language_chosen,
                                      label='Interface language', on=row.plane)
        self.ui_language.pack()
        row = self.row(group, 'text_size')
        self.text_size = ui.Dropdown(row.control, ((1.0, '100%'), (1.15, '115%'),
                                                  (1.3, '130%'), (1.5, '150%')),
                                      C.get().text_scale, on_change=self._text_size_chosen,
                                      label='Application text size', on=row.plane)
        self.text_size.pack()
        note = ui.Label(self.body, 'Contrast, motion and notification duration follow Windows accessibility settings.',
                        kind='muted', justify='left')
        note.pack(anchor='w')
        self.body.bind('<Configure>', lambda event: self._wrap(note, self.body), add='+')

    def _language_chosen(self, value):
        C.get().manager.set_ui_language(value)
        self.changed('ui_language')

    def _text_size_chosen(self, value):
        C.get().manager.set_text_scale(value)
        self.changed('text_size')

    def _chosen(self, mode: str) -> None:
        self.theme.set(mode)
        C.get().manager.set_mode(mode)
        self.changed('theme')

    def refresh(self) -> None:
        self.theme.set(C.get().manager.mode)
        self.text_size.set(C.get().text_scale)
        self.ui_language.set(C.get().manager.ui_language)


class TranslationPage(SettingsPage):
    key = 'translation'

    def populate(self) -> None:
        cfg = self.config
        languages = list(LANGUAGES)
        recent = cfg.get_recent_languages
        pinned = lambda: language_prefs.pinned_names(cfg.get_all_hotkeys())    # noqa: E731

        group = self.group('Languages')
        row = self.row(group, 'default_language')
        self.default_picker = ui.LanguagePicker(
            row.control, languages, cfg.get_default_target_language(), on_change=self._default_chosen,
            recent=recent, pinned=pinned, label='Translate into', on=row.plane)
        self.default_picker.pack()
        row = self.row(group, 'screenshot_language')
        self.screenshot_picker = ui.LanguagePicker(
            row.control, [AUTO_LANGUAGE] + languages, cfg.get_screenshot_target_language(),
            on_change=self._screenshot_chosen, recent=recent, pinned=pinned,
            label='Translate screenshots into', on=row.plane)
        self.screenshot_picker.pack()



        group = self.group('Reading aids')
        self.toggle(group, 'furigana', cfg.get_furigana_enabled, cfg.set_furigana_enabled)
        self.toggle(group, 'linked_reading', cfg.get_linked_reading_enabled, self._set_linked_reading)
        self.linked_button_row = self.toggle(group, 'show_linked_reading', cfg.get_show_linked_reading,
                                             self._set_show_linked_reading)
        self.linked_button_row.configure(padding=(ui.get().px(40), ui.get().px(12),
                                                 ui.get().px(16), ui.get().px(12)))
        self._sync_linked_branch()
        self._follow_linked_reading()

        group = self.group('Buttons')
        row = self.row(group, 'replace_mode')
        self.replace_dropdown = ui.Dropdown(row.control, REPLACE_OPTIONS, cfg.get_quick_replace(),
                                            on_change=self._replace_chosen, label='Replace button',
                                            on=row.plane)
        self.replace_dropdown.pack()
        self.toggle(group, 'fix_grammar_button', cfg.get_fix_grammar_enabled, cfg.set_fix_grammar_enabled)

    # --- languages ----------------------------------------------------------------------------------
    def _default_chosen(self, name: str) -> None:
        self.config.set_default_target_language(name)
        self.changed('default_language')

    def _screenshot_chosen(self, name: str) -> None:
        self.config.set_screenshot_target_language(name)
        self.changed('screenshot_language')

    # --- linked reading: the popup's own button changes it too ---------------------------------------


    # --- the Replace button ---------------------------------------------------------------------------
    def _replace_chosen(self, quick: bool) -> None:
        self.config.set_quick_replace(bool(quick))
        self.changed('replace_mode')

    def refresh(self) -> None:
        super().refresh()
        cfg = self.config
        self.default_picker.set(cfg.get_default_target_language())
        self.screenshot_picker.set(cfg.get_screenshot_target_language())

        self.replace_dropdown.set(cfg.get_quick_replace())
        self._sync_linked_branch()

    def _set_linked_reading(self, enabled: bool) -> None:
        self.config.set_linked_reading_enabled(enabled)
        self._sync_linked_branch()
        self.body._root().event_generate('<<LinkedReadingChanged>>', when='tail')

    def _set_show_linked_reading(self, enabled: bool) -> None:
        self.config.set_show_linked_reading(enabled)
        self.body._root().event_generate('<<LinkedReadingChanged>>', when='tail')


    def _follow_linked_reading(self) -> None:
        root = self.body._root()
        token = root.bind('<<LinkedReadingChanged>>', lambda event: self._sync_linked_branch(), add='+')

        def release(event):
            if event.widget is self.body:
                root.unbind('<<LinkedReadingChanged>>', token)
        self.body.bind('<Destroy>', release, add='+')

    def _sync_linked_branch(self):
        enabled = self.config.get_linked_reading_enabled()
        self.variables['linked_reading'].set(enabled)
        self.variables['show_linked_reading'].set(self.config.get_show_linked_reading())
        self.linked_button_row.set_visible(enabled)

    def row_for(self, key):
        if key == 'show_linked_reading' and not self.config.get_linked_reading_enabled():
            key = 'linked_reading'
        return super().row_for(key)
