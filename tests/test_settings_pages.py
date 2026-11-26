"""
Tests for the Settings pages (TODO.md U8, Decision 51): General, Appearance, Translation, Shortcuts and
About. Each page is built against a stand-in host (`tests/settings_support.py`) that records what the
window would be told, over a configuration that never touches the registry or the disk, and the
shortcut editors are fed key events through a queue - the real keyboard is never listened to.
"""
import tkinter as tk
from unittest.mock import Mock

import pytest

from src.core import settings_index as index
from src.ui import components as ui
from src.ui.settings import page_about, page_shortcuts, pages_basic
from tests.settings_support import FakeSource, Host, SettingsConfig, pump, wait_for
from tests.test_components import env, holder, light, shown  # noqa: F401  (shared fixtures)

JAPANESE = ('Japanese', 'ja', 'Japanese')
FRENCH = ('French', 'fr', 'French')


@pytest.fixture
def make(shown, light):
    """Build a page of the given class in a mapped window; returns (page, host)."""
    built = []
    shown.geometry('760x760+0+0')           # a fixed size, as the real window has: rows wrap to it

    def build(page_class, config=None, **config_overrides):
        host = Host(shown, config or SettingsConfig(**config_overrides))
        page = page_class(host)
        page.build(shown)
        page.body.pack(fill='both', expand=True, padx=20, pady=20)
        shown.update()
        built.append(page)
        return page, host
    return build


def invoke(widget):
    widget.invoke()
    widget.update()


# --------------------------------------------------------------------------- #
# The base: rows say what the index says
# --------------------------------------------------------------------------- #
class TestRowsUseTheIndex:
    def test_a_row_has_its_entrys_title_and_sentence(self, make):
        page, host = make(pages_basic.GeneralPage)
        row = page.row_for('autostart')
        entry = index.entry('autostart')
        assert row.title_label.cget('text') == entry.title
        assert row.description_label.cget('text') == entry.description

    @pytest.mark.parametrize('page_class', [pages_basic.GeneralPage, pages_basic.AppearancePage,
                                            pages_basic.TranslationPage, page_shortcuts.ShortcutsPage,
                                            page_about.AboutPage])
    def test_every_entry_of_a_page_has_a_row(self, make, page_class):
        page, host = make(page_class)
        for entry in index.entries_of(page_class.key):
            assert page.row_for(entry.key) is not None, entry.key

    def test_a_toggle_writes_and_announces_its_key(self, make):
        page, host = make(pages_basic.TranslationPage)
        assert page.variables['furigana'].get() is True
        invoke(page.switches['furigana'])
        assert host.config.get_furigana_enabled() is False
        assert host.changes == ['furigana']
        invoke(page.switches['furigana'])
        assert host.config.get_furigana_enabled() is True and host.changes == ['furigana', 'furigana']

    def test_refresh_reads_the_settings_again(self, make):
        page, host = make(pages_basic.TranslationPage)
        host.config.set_furigana_enabled(False)
        page.refresh()
        assert page.variables['furigana'].get() is False


# --------------------------------------------------------------------------- #
# General
# --------------------------------------------------------------------------- #
class TestGeneralPage:
    def test_it_shows_the_current_settings(self, make):
        page, host = make(pages_basic.GeneralPage, autostart=True, auto_check_updates=False)
        assert page.variables['autostart'].get() is True
        assert page.variables['auto_update'].get() is False

    def test_start_with_windows_is_written_once_after_a_pause(self, make, shown, monkeypatch):
        monkeypatch.setattr(pages_basic, 'AUTOSTART_DELAY_MS', 60)
        page, host = make(pages_basic.GeneralPage)
        invoke(page.switches['autostart'])
        assert host.config.autostart_calls == []                     # not yet
        invoke(page.switches['autostart'])
        invoke(page.switches['autostart'])
        assert wait_for(shown, lambda: host.config.autostart_calls)
        pump(shown, 0.15)
        assert host.config.autostart_calls == [True]                 # three flips, one write, the last
        assert host.changes == ['autostart'] * 3

    def test_a_pending_write_is_not_lost_when_the_window_closes(self, make, shown, monkeypatch):
        monkeypatch.setattr(pages_basic, 'AUTOSTART_DELAY_MS', 5000)
        page, host = make(pages_basic.GeneralPage)
        invoke(page.switches['autostart'])
        assert host.config.autostart_calls == []
        page.body.destroy()
        assert host.config.autostart_calls == [True]

    def test_nothing_is_written_when_nothing_was_flipped(self, make):
        page, host = make(pages_basic.GeneralPage)
        page.body.destroy()
        assert host.config.autostart_calls == []

    def test_updates_apply_at_once(self, make):
        page, host = make(pages_basic.GeneralPage)
        invoke(page.switches['auto_update'])
        assert host.config.get_auto_check_updates() is False
        host.update_manager.auto_check_changed.assert_called_once()
        assert host.changes == ['auto_update']


class TestRestoreDefaults:
    def changed_config(self):
        cfg = SettingsConfig(furigana_enabled=False, quick_replace=True, linked_reading_enabled=False,
                             fix_grammar_enabled=False, auto_check_updates=False, autostart=True,
                             screenshot_hotkey='ctrl+alt+x', default_target_language='English')
        cfg._config['hotkeys'] = {'English': 'ctrl+alt+e'}
        cfg._config['custom_hotkeys'] = {'French': 'ctrl+alt+f'}
        cfg._config['api_keys'] = [{'api_key': 'k', 'model_name': 'm'}]
        return cfg

    def test_the_button_asks_first_and_nothing_changes(self, make, shown):
        page, host = make(pages_basic.GeneralPage, config=self.changed_config())
        invoke(page.restore_btn)
        assert page.dialog is not None and page.dialog.exists
        assert host.config.get_furigana_enabled() is False and host.refreshed == 0
        page.dialog.close()

    def test_the_focus_starts_on_cancel_and_cancel_changes_nothing(self, make, shown):
        page, host = make(pages_basic.GeneralPage, config=self.changed_config())
        invoke(page.restore_btn)
        shown.update()
        cancel = page.dialog._button_list[0]
        assert cancel.cget('text') == 'Cancel'
        assert page.dialog.window.focus_get() is cancel
        cancel.invoke()
        assert not page.dialog.exists
        assert host.config.get_furigana_enabled() is False and host.changes == []

    def test_escape_cancels(self, make, shown):
        page, host = make(pages_basic.GeneralPage, config=self.changed_config())
        invoke(page.restore_btn)
        page.dialog.window.event_generate('<Escape>')
        page.dialog.window.update()
        assert not page.dialog.exists and host.config.get_furigana_enabled() is False

    def test_confirming_puts_the_settings_back_and_keeps_the_keys(self, make, shown):
        page, host = make(pages_basic.GeneralPage, config=self.changed_config())
        invoke(page.restore_btn)
        restore = page.dialog._button_list[1]
        assert restore.cget('text') == 'Restore defaults'
        restore.invoke()
        shown.update()
        cfg = host.config
        assert cfg.get_furigana_enabled() is True and cfg.get_quick_replace() is False
        assert cfg.get_screenshot_hotkey() == cfg.SCREENSHOT_HOTKEY_DEFAULT
        assert cfg.get_default_target_language() == 'Vietnamese'
        assert cfg.get_all_hotkeys() == cfg.DEFAULT_HOTKEYS
        assert cfg.get_api_keys() == [{'api_key': 'k', 'model_name': 'm'}]
        assert cfg.autostart_calls == [False]
        host.update_manager.auto_check_changed.assert_called()
        assert host.refreshed == 1 and host.changes == ['restore_defaults']

    def test_the_theme_returns_to_what_the_configuration_says(self, make, light):
        cfg = self.changed_config()
        cfg._config['theme_mode'] = 'dark'
        page, host = make(pages_basic.GeneralPage, config=cfg)
        light.manager.set_mode('dark')
        page.restore()
        assert light.manager.mode == 'system'
        light.manager.set_mode('light')

    def test_a_pending_autostart_write_is_dropped_by_a_restore(self, make, shown, monkeypatch):
        monkeypatch.setattr(pages_basic, 'AUTOSTART_DELAY_MS', 5000)
        page, host = make(pages_basic.GeneralPage)
        invoke(page.switches['autostart'])
        page.restore()
        page.body.destroy()
        assert host.config.autostart_calls == [False]


# --------------------------------------------------------------------------- #
# Appearance
# --------------------------------------------------------------------------- #
class TestAppearancePage:
    def test_it_offers_all_palettes_plus_follow_windows_and_shows_the_one_in_force(self, make, light):
        page, host = make(pages_basic.AppearancePage)
        from src.ui.theme import tokens
        assert [key for key, _ in pages_basic.THEME_OPTIONS] == list(tokens.MODES)
        assert len(page.theme.options) == 15
        assert set(page.theme_preview.tiles) == set(tokens.PALETTES)
        assert page.theme.value == 'light'

    def test_choosing_changes_the_theme_and_says_so(self, make, light):
        page, host = make(pages_basic.AppearancePage)
        page.theme_preview.choose('dark')
        assert light.manager.mode == 'dark' and light.manager.is_dark
        assert host.changes == ['theme']
        page.theme_preview.choose('light')
        assert light.manager.mode == 'light'

    def test_refresh_follows_the_manager(self, make, light):
        page, host = make(pages_basic.AppearancePage)
        light.manager.set_mode('system')
        page.refresh()
        assert page.theme.value == 'system'
        light.manager.set_mode('light')

    def test_last_theme_is_reachable_by_keyboard_and_selection_closes_the_list(self, make, light):
        page, host = make(pages_basic.AppearancePage)
        page.theme.open()
        page.theme.listbox.focus_force()
        page.theme.window.update()
        page.theme.listbox.event_generate('<End>')
        page.theme.window.update()
        assert page.theme.scrollbar is not None
        assert page.theme.listbox.bbox(len(page.theme.options) - 1) is not None
        page.theme.listbox.event_generate('<Return>')
        page.body.update()
        assert not page.theme.is_open
        assert page.theme.value == light.manager.mode == 'graphite'
        assert host.changes == ['theme']
        light.manager.set_mode('light')


# --------------------------------------------------------------------------- #
# Translation
# --------------------------------------------------------------------------- #
class TestTranslationPage:
    def test_the_pickers_show_the_stored_languages(self, make):
        page, host = make(pages_basic.TranslationPage, default_target_language='Japanese',
                          screenshot_target_language='English')
        assert page.default_picker.value == 'Japanese'
        assert page.screenshot_picker.value == 'English'

    def test_choosing_the_default_language_stores_it(self, make):
        page, host = make(pages_basic.TranslationPage)
        page.default_picker.choose(JAPANESE)
        assert host.config.get_default_target_language() == 'Japanese'
        assert host.changes == ['default_language']

    def test_the_screenshot_picker_offers_auto_first_and_stores_a_choice(self, make):
        page, host = make(pages_basic.TranslationPage, screenshot_target_language='English')
        assert page.screenshot_picker.languages[0][0] == 'Auto'
        assert page.default_picker.languages[0][0] != 'Auto'
        page.screenshot_picker.choose(pages_basic.AUTO_LANGUAGE)
        assert host.config.get_screenshot_target_language() == 'Auto'
        assert host.changes == ['screenshot_language']

    def test_a_language_with_a_shortcut_is_pinned_in_the_pickers(self, make):
        page, host = make(pages_basic.TranslationPage)
        assert 'English' in page.default_picker._pinned()

    def test_recent_languages_come_from_the_configuration(self, make):
        page, host = make(pages_basic.TranslationPage, recent_languages=['Korean'])
        assert page.default_picker._recent() == ['Korean']

    def test_replace_offers_preview_or_immediately(self, make):
        page, host = make(pages_basic.TranslationPage)
        assert page.replace_dropdown.options == [(False, 'Preview first'), (True, 'Replace immediately')]
        assert page.replace_dropdown.button.cget('text') == 'Preview first'
        page.replace_dropdown.choose(True)
        assert host.config.get_quick_replace() is True and host.changes == ['replace_mode']

    def test_the_fix_grammar_button_switch(self, make):
        page, host = make(pages_basic.TranslationPage)
        assert page.variables['fix_grammar_button'].get() is True
        invoke(page.switches['fix_grammar_button'])
        assert host.config.get_fix_grammar_enabled() is False
        assert host.changes == ['fix_grammar_button']

    def test_linked_reading_tells_the_rest_of_the_application(self, make, shown):
        page, host = make(pages_basic.TranslationPage)
        heard = []
        shown._root().bind('<<LinkedReadingChanged>>', lambda e: heard.append(1), add='+')
        invoke(page.switches['linked_reading'])
        pump(shown, 0.05)
        assert host.config.get_linked_reading_enabled() is False and heard


    def test_the_popups_button_moves_the_switch(self, make, shown):
        page, host = make(pages_basic.TranslationPage)
        host.config.set_linked_reading_enabled(False)
        shown._root().event_generate('<<LinkedReadingChanged>>', when='tail')
        pump(shown, 0.05)
        assert page.variables['linked_reading'].get() is False


    def test_the_binding_goes_with_the_page(self, make, shown):
        root = shown._root()
        before = root.bind('<<LinkedReadingChanged>>')
        page, host = make(pages_basic.TranslationPage)
        assert root.bind('<<LinkedReadingChanged>>').strip() != before.strip()
        page.body.destroy()
        assert root.bind('<<LinkedReadingChanged>>').strip() == before.strip()


    def test_refresh_reads_every_control_again(self, make):
        page, host = make(pages_basic.TranslationPage)
        cfg = host.config
        cfg.set_default_target_language('Korean')
        cfg.set_screenshot_target_language('Japanese')
        cfg.set_quick_replace(True)
        page.refresh()
        assert page.default_picker.value == 'Korean' and page.screenshot_picker.value == 'Japanese'
        assert page.replace_dropdown.value == True  # noqa: E712


# --------------------------------------------------------------------------- #
# Shortcuts
# --------------------------------------------------------------------------- #
@pytest.fixture
def shortcuts(make, shown):
    """The Shortcuts page with a fake keyboard; `record(owner, *keys)` records a chord for an editor."""
    def build(**overrides):
        page, host = make(page_shortcuts.ShortcutsPage, **overrides)
        source = FakeSource()
        host.key_source_factory = lambda: source

        def record(owner, *keys):
            editor = page.editors[owner]
            editor.start_recording()
            source.press(*keys)
            wait_for(shown, lambda: not editor.recording)
            shown.update()
        return page, host, source, record
    return build


def caps(editor):
    return editor.keycaps.text if editor.keycaps is not None else None


class TestShortcutsPageShowsThem:
    def test_the_languages_that_ship_come_first_in_order(self, shortcuts):
        page, host, source, record = shortcuts()
        assert list(page.values)[:4] == ['English', 'Japanese', 'Chinese Simplified', 'Vietnamese']
        tops = [page.editors[name].winfo_rooty() for name in page.values]
        assert tops == sorted(tops)
        assert caps(page.editors['English']) == 'Win + Alt + E'
        assert caps(page.editors['Chinese Simplified']) == 'Win + Alt + C'

    def test_added_languages_follow_in_the_order_they_were_added(self, shortcuts):
        cfg = SettingsConfig()
        cfg._config['hotkeys'] = dict(cfg.DEFAULT_HOTKEYS, Korean='ctrl+alt+k')
        cfg._config['custom_hotkeys'] = {'French': 'ctrl+alt+f'}
        page, host, source, record = shortcuts(config=cfg)
        assert list(page.values)[4:6] == ['Korean', 'French']
        assert page.editors['Korean'].winfo_rooty() < page.editors['French'].winfo_rooty()

    def test_a_cleared_language_that_ships_still_has_its_row(self, shortcuts):
        cfg = SettingsConfig()
        cfg._config['hotkeys'] = {'English': 'win+alt+e'}
        page, host, source, record = shortcuts(config=cfg)
        assert page.editors['Japanese'].keycaps is None and page.editors['Japanese'].hint is not None

    def test_the_screenshot_and_fix_grammar_shortcuts_are_shown(self, shortcuts):
        page, host, source, record = shortcuts()
        assert caps(page.editors[page_shortcuts.SCREENSHOT]) == 'Win + Alt + S'
        assert caps(page.editors[page_shortcuts.FIX_GRAMMAR]) == 'Win + Alt + G'

    def test_the_languages_row_is_what_a_search_result_points_at(self, shortcuts):
        page, host, source, record = shortcuts()
        assert page.row_for('language_shortcuts').title_label.cget('text') == 'English'
        assert page.row_for('screenshot_shortcut') is not None
        assert page.row_for('fix_grammar_shortcut') is not None

    def test_the_languages_have_the_entrys_heading_and_sentence(self, shortcuts):
        page, host, source, record = shortcuts()
        entry = index.entry('language_shortcuts')
        group = next(group for group in page.groups if group.heading_label is not None
                     and str(group.heading_label.cget('text')) == entry.title)
        assert group.heading_label.cget('text') == entry.title
        assert group.caption_label.cget('text') == entry.description


class TestRecordingAShortcut:
    def test_a_new_combination_is_stored_and_announced(self, shortcuts):
        page, host, source, record = shortcuts()
        record('English', 'ctrl', 'alt', 'k')
        assert host.config.get_all_hotkeys()['English'] == 'ctrl+alt+k'
        assert caps(page.editors['English']) == 'Ctrl + Alt + K'
        assert host.changes == ['language_shortcuts']
        assert source.started and source.stopped

    def test_the_application_is_told_while_recording_and_when_it_ends(self, shortcuts):
        page, host, source, record = shortcuts()
        page.editors['English'].start_recording()
        assert host.recordings == [True]
        source.press('ctrl', 'alt', 'k')
        wait_for(page.body, lambda: not page.editors['English'].recording)
        assert host.recordings == [True, False]

    def test_a_combination_another_language_uses_is_refused_by_name(self, shortcuts):
        page, host, source, record = shortcuts()
        record('English', 'win', 'alt', 'j')
        assert 'Japanese' in page.editors['English'].message_text
        assert host.config.get_all_hotkeys()['English'] == 'win+alt+e'
        assert host.changes == []

    def test_a_combination_the_screenshot_shortcut_uses_is_refused(self, shortcuts):
        page, host, source, record = shortcuts()
        record('English', 'win', 'alt', 's')
        assert 'Screenshot' in page.editors['English'].message_text
        assert host.config.get_all_hotkeys()['English'] == 'win+alt+e'

    def test_the_fix_grammar_shortcut_counts_only_while_it_is_on(self, shortcuts):
        page, host, source, record = shortcuts(fix_grammar_hotkey='ctrl+alt+f')
        record('English', 'ctrl', 'alt', 'f')
        assert host.config.get_all_hotkeys()['English'] == 'ctrl+alt+f'          # off: free to take
        invoke(page.fix_switch)
        record('Japanese', 'ctrl', 'alt', 'f')
        assert host.config.get_all_hotkeys()['Japanese'] == 'win+alt+j'           # on, and English has it

    def test_recording_the_shortcut_it_already_has_is_fine(self, shortcuts):
        page, host, source, record = shortcuts()
        record('English', 'win', 'alt', 'e')
        assert page.editors['English'].message_text == ''
        assert host.config.get_all_hotkeys()['English'] == 'win+alt+e'
        assert host.changes == ['language_shortcuts']

    def test_a_hidden_fix_grammar_editor_says_nothing(self, shortcuts):
        page, host, source, record = shortcuts()                  # off, and its default is the Game Bar's
        record('English', 'ctrl', 'alt', 'k')
        assert page.editors[page_shortcuts.FIX_GRAMMAR].message_text == ''

    def test_a_clash_is_found_in_what_is_already_saved_when_another_shortcut_changes(self, shortcuts):
        page, host, source, record = shortcuts()
        record('Japanese', 'ctrl', 'alt', 'k')
        assert page.editors['English'].message_text == ''

    def test_only_one_recording_at_a_time(self, shortcuts):
        page, host, source, record = shortcuts()
        page.editors['English'].start_recording()
        page.editors['Japanese'].start_recording()
        assert not page.editors['English'].recording and page.editors['Japanese'].recording
        assert host.recordings[-1] is True
        page.editors['Japanese'].cancel_recording()
        assert host.recordings[-1] is False and page.recording_owners == set()

    def test_the_game_bar_default_is_warned_about_when_it_is_on(self, shortcuts):
        page, host, source, record = shortcuts(fix_grammar_hotkey_enabled=True)
        assert 'Xbox Game Bar' in page.editors[page_shortcuts.FIX_GRAMMAR].message_text

    def test_restore_returns_a_language_to_its_default(self, shortcuts):
        page, host, source, record = shortcuts()
        record('English', 'ctrl', 'alt', 'k')
        page.editors['English'].restore()
        assert host.config.get_all_hotkeys()['English'] == 'win+alt+e'
        assert host.changes == ['language_shortcuts', 'language_shortcuts']


class TestAddingAndRemovingLanguages:
    def test_the_picker_offers_only_languages_without_a_shortcut(self, shortcuts):
        page, host, source, record = shortcuts()
        names = [language[0] for language in page.add_picker.languages]
        assert 'French' in names and 'English' not in names and 'Vietnamese' not in names

    def test_an_added_language_gets_a_row_with_nothing_set_and_a_remove_button(self, shortcuts, shown):
        page, host, source, record = shortcuts()
        page.add_picker.choose(FRENCH)
        shown.update()
        editor = page.editors['French']
        assert editor.keycaps is None and editor.hint is not None
        assert list(page.values)[4] == 'French'
        assert page.editors['Chinese Simplified'].winfo_rooty() < editor.winfo_rooty()
        assert 'French' not in host.config.get_all_hotkeys()          # kept only once it has a shortcut

    def test_it_is_stored_once_it_has_a_shortcut(self, shortcuts, shown):
        page, host, source, record = shortcuts()
        page.add_picker.choose(FRENCH)
        shown.update()
        record('French', 'ctrl', 'alt', 'f')
        assert host.config.get_all_hotkeys()['French'] == 'ctrl+alt+f'
        assert host.config.get_custom_hotkeys() == {}                 # one store, not two

    def test_the_picker_no_longer_offers_it(self, shortcuts, shown):
        page, host, source, record = shortcuts()
        page.add_picker.choose(FRENCH)
        shown.update()
        assert 'French' not in [language[0] for language in page.add_picker.languages]

    def test_removing_takes_the_row_and_the_stored_shortcut_away(self, shortcuts, shown):
        cfg = SettingsConfig()
        cfg._config['hotkeys'] = dict(cfg.DEFAULT_HOTKEYS, French='ctrl+alt+f')
        page, host, source, record = shortcuts(config=cfg)
        buttons = self.remove_buttons(page)
        assert [b.label for b in buttons] == ['Remove French']
        buttons[0].invoke()
        wait_for(shown, lambda: 'French' not in page.editors)
        assert 'French' not in host.config.get_all_hotkeys() and host.changes == ['language_shortcuts']

    def remove_buttons(self, page):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ui.IconButton) and 'Remove' in getattr(child, 'label', ''):
                    found.append(child)
                walk(child)
        walk(page.translate_section)
        return found

    def test_the_languages_that_ship_cannot_be_removed(self, shortcuts):
        page, host, source, record = shortcuts()
        assert self.remove_buttons(page) == []
        page._remove('English')
        assert 'English' in page.values

    def test_the_limit_replaces_the_picker_with_a_sentence(self, shortcuts, shown):
        cfg = SettingsConfig()
        cfg._config['hotkeys'] = dict(cfg.DEFAULT_HOTKEYS, Korean='ctrl+alt+1', French='ctrl+alt+2',
                                      German='ctrl+alt+3', Spanish='ctrl+alt+4')
        page, host, source, record = shortcuts(config=cfg)
        assert page.add_picker is None
        texts = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == 'TLabel':
                    texts.append(str(child.cget('text')))
                walk(child)
        walk(page.translate_section)
        assert page_shortcuts.LIMIT_REACHED in texts
        page._add('Italian')
        assert 'Italian' not in page.values


class TestFixGrammarShortcut:
    def test_it_is_off_by_default_and_its_keys_are_hidden(self, shortcuts):
        page, host, source, record = shortcuts()
        assert page.variables['fix_grammar_shortcut'].get() is False
        assert not page.fix_host.winfo_ismapped()

    def test_switching_it_on_shows_the_keys_and_stores_the_flag(self, shortcuts, shown):
        page, host, source, record = shortcuts()
        invoke(page.fix_switch)
        shown.update()
        assert host.config.get_fix_grammar_hotkey_enabled() is True
        assert page.fix_host.winfo_ismapped()
        assert host.changes == ['fix_grammar_shortcut']
        assert 'Xbox Game Bar' in page.editors[page_shortcuts.FIX_GRAMMAR].message_text

    def test_switching_it_off_hides_them_again(self, shortcuts, shown):
        page, host, source, record = shortcuts(fix_grammar_hotkey_enabled=True)
        assert page.fix_host.winfo_ismapped()
        invoke(page.fix_switch)
        shown.update()
        assert not page.fix_host.winfo_ismapped()
        assert host.config.get_fix_grammar_hotkey_enabled() is False

    def test_a_recorded_combination_is_stored(self, shortcuts):
        page, host, source, record = shortcuts(fix_grammar_hotkey_enabled=True)
        record(page_shortcuts.FIX_GRAMMAR, 'ctrl', 'alt', 'f')
        assert host.config.get_fix_grammar_hotkey() == 'ctrl+alt+f'
        assert host.changes == ['fix_grammar_shortcut']


class TestScreenshotShortcut:
    def test_a_recorded_combination_is_stored(self, shortcuts):
        page, host, source, record = shortcuts()
        record(page_shortcuts.SCREENSHOT, 'ctrl', 'alt', 's')
        assert host.config.get_screenshot_hotkey() == 'ctrl+alt+s'
        assert host.changes == ['screenshot_shortcut']

    def test_restore_returns_the_default(self, shortcuts):
        page, host, source, record = shortcuts(screenshot_hotkey='ctrl+alt+s')
        page.editors[page_shortcuts.SCREENSHOT].restore()
        assert host.config.get_screenshot_hotkey() == 'win+alt+s'


class TestHowTheyRegistered:
    def test_a_shortcut_another_app_holds_says_so(self, shortcuts):
        page, host, source, record = shortcuts()
        host.status = {'Japanese': 'taken'}
        page.refresh_status()
        assert 'Another app' in page.editors['Japanese'].message_text
        assert page.editors['English'].message_text == ''

    def test_the_screenshot_and_fix_grammar_use_the_managers_names(self, shortcuts):
        page, host, source, record = shortcuts(fix_grammar_hotkey_enabled=True,
                                               fix_grammar_hotkey='ctrl+alt+f')
        host.status = {'__screenshot__': 'failed', '__fix_grammar__': 'invalid'}
        page.refresh_status()
        assert 'Windows would not' in page.editors[page_shortcuts.SCREENSHOT].message_text
        assert 'cannot use' in page.editors[page_shortcuts.FIX_GRAMMAR].message_text

    def test_a_later_registration_that_went_well_clears_it(self, shortcuts):
        page, host, source, record = shortcuts()
        host.status = {'English': 'taken'}
        page.refresh_status()
        host.status = {'English': 'ok'}
        page.refresh_status()
        assert page.editors['English'].message_text == ''

    def test_an_error_reading_the_status_is_not_fatal(self, shortcuts):
        page, host, source, record = shortcuts()
        host.shortcut_status = Mock(side_effect=RuntimeError('boom'))
        page.refresh_status()                       # no exception

    @pytest.mark.parametrize('state,expected', [('taken', True), ('failed', True), ('invalid', True),
                                                ('ok', False), (None, False), ('', False)])
    def test_status_text(self, state, expected):
        assert bool(page_shortcuts.status_text(state)) is expected


class TestRefreshingTheShortcuts:
    def test_refresh_reads_everything_from_the_configuration(self, shortcuts):
        page, host, source, record = shortcuts()
        host.config.restore_defaults()
        host.config._config['hotkeys'] = {'English': 'ctrl+alt+z'}
        host.config._config['screenshot_hotkey'] = 'ctrl+alt+q'
        page.refresh()
        assert caps(page.editors['English']) == 'Ctrl + Alt + Z'
        assert caps(page.editors[page_shortcuts.SCREENSHOT]) == 'Ctrl + Alt + Q'
        assert page.editors['Japanese'].keycaps is None

    def test_a_recording_in_progress_ends_when_the_page_is_rebuilt(self, shortcuts):
        page, host, source, record = shortcuts()
        page.editors['English'].start_recording()
        page.refresh()
        assert host.recordings[-1] is False and source.stopped


# --------------------------------------------------------------------------- #
# About
# --------------------------------------------------------------------------- #
class TestAboutPage:
    def test_it_names_the_version_and_hands_the_update_widgets_to_the_host(self, make):
        page, host = make(page_about.AboutPage)
        from src.constants import VERSION
        labels = [str(w.cget('text')) for w in page.row_for('version').control.winfo_children()
                  if w.winfo_class() == 'TLabel']
        assert f'CrossTrans v{VERSION}' in labels
        for name in ('update_check_frame', 'check_update_btn', 'update_status_label', 'update_offer_frame',
                     'update_offer_label', 'update_offer_button'):
            assert getattr(host, name).winfo_exists(), name
        assert host.update_offer_frame.master is host.update_check_frame.master

    def test_the_check_button_calls_the_hosts_check(self, make):
        page, host = make(page_about.AboutPage)
        invoke(host.check_update_btn)
        host._check_for_updates_click.assert_called_once()

    def test_the_update_state_is_shown_when_the_page_is_built(self, make):
        page, host = make(page_about.AboutPage)
        assert host.update_refreshes == 1

    def test_the_help_button_opens_the_help_page(self, make):
        page, host = make(page_about.AboutPage)
        invoke(page.buttons['guide'])
        assert host.opened == ['help']

    def test_feedback_and_github_open_in_the_browser(self, make, monkeypatch):
        opened = []
        monkeypatch.setattr(page_about.webbrowser, 'open', lambda url: opened.append(url))
        page, host = make(page_about.AboutPage)
        invoke(page.buttons['feedback'])
        invoke(page.buttons['source'])
        from src.constants import FEEDBACK_URL
        assert opened == [FEEDBACK_URL, page_about.GITHUB_URL]
        assert page_about.GITHUB_URL.startswith('https://github.com/')


def test_history_switch_stops_new_records_and_keeps_existing_history(make):
    from src.core.history import HistoryManager
    page, host = make(pages_basic.GeneralPage)
    history = HistoryManager(host.config)
    history.add_entry('first source', 'first translation', 'English')
    before = host.config.get('history')
    assert len(before) == 1
    invoke(page.switches['history_saving'])
    assert host.config.get('history_enabled') is False
    history.add_entry('second source', 'second translation', 'English')
    assert host.config.get('history') == before
    page.restore()
    assert host.config.get('history_enabled') is False
    assert host.config.get('history') == before
    assert page.variables['history_saving'].get() is False


def test_private_update_token_is_masked_saved_separately_and_removed(make):
    page, host = make(page_about.AboutPage)
    assert page.update_token.cget('show') == '*'
    page.update_token.set('test_update_credential')
    assert page.save_update_token()
    assert host.config.get_github_update_token() == 'test_update_credential'
    assert page.update_token.get() == ''
    assert host.config.get_api_keys() == []
    assert host.changes[-1] == 'github_update_token'
    host.update_manager.credentials_changed.assert_called_once()
    page.remove_update_token()
    assert host.config.get_github_update_token() == ''
    assert host.update_manager.credentials_changed.call_count == 2


def test_private_update_token_save_failure_keeps_the_draft_and_old_credential(make, monkeypatch):
    page, host = make(page_about.AboutPage)
    page.update_token.set('bad token')
    assert not page.save_update_token()
    assert page.update_token.get() == 'bad token'
    assert host.config.get_github_update_token() == ''
    host.update_manager.credentials_changed.assert_not_called()
