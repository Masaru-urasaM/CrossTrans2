"""
Tests for the Settings window (TODO.md U8, Decision 51): the shell around the pages - navigation, lazy
building, the "Saved" notice, search, Escape, the reading width, and what it tells the application.

The configuration is in memory, the update manager a stand-in and the keyboard a queue; the window is a
real Toplevel on the test run's private desktop.
"""
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core import settings_index as index
from src.ui.components import context as C
from src.ui.settings import main as window_module
from src.ui.settings.main import SettingsWindow
from tests.settings_support import FakeSource, SettingsConfig, pump, wait_for
from tests.test_components import env, holder, light, shown  # noqa: F401  (shared fixtures)


def update_manager():
    return SimpleNamespace(available_version=None, ready_update=None, auto_check_changed=Mock(),
                           request_check=Mock(), request_update=Mock(), stop=Mock())


@pytest.fixture
def settings(tk_root, light):
    """Build a Settings window; it is destroyed after the test. `.changes` and `.recordings` log what
    the application was told."""
    built = []

    def build(config=None, **kw):
        changes, recordings = [], []
        kw.setdefault('update_manager', update_manager())
        window = SettingsWindow(tk_root, config or SettingsConfig(), on_changed=changes.append,
                                on_shortcut_recording=recordings.append, **kw)
        window.changes, window.recordings = changes, recordings
        window.window.geometry('+0+0')
        window.window.update()
        built.append(window)
        return window
    yield build
    for window in built:
        if window.window.winfo_exists():
            window.window.destroy()


def type_in(window, text):
    window.search.insert(0, text)
    window._run_search()
    window.window.update()


# --------------------------------------------------------------------------- #
# Pages and navigation
# --------------------------------------------------------------------------- #
class TestPagesAndNavigation:
    def test_it_opens_on_general_and_builds_only_that_page(self, settings):
        window = settings()
        assert window.current == 'general' and set(window.pages) == {'general'}
        assert window.title.cget('text') == 'General'

    def test_the_navigation_lists_every_page_in_order(self, settings):
        window = settings()
        assert list(window.navigation.buttons) == [page.key for page in index.PAGES]
        assert [str(b.cget('text')).strip() for b in window.navigation.buttons.values()] == \
            [page.title for page in index.PAGES]

    def test_a_page_is_built_the_first_time_and_only_then(self, settings):
        window = settings()
        window.open_page('appearance')
        first = window.pages['appearance']
        window.open_page('general')
        window.open_page('appearance')
        assert window.pages['appearance'] is first
        assert set(window.pages) == {'general', 'appearance'}

    def test_only_the_current_page_is_shown(self, settings):
        window = settings()
        window.open_page('appearance')
        window.window.update()
        assert window._frames['appearance'].winfo_ismapped()
        assert not window._frames['general'].winfo_ismapped()
        assert window.title.cget('text') == 'Appearance'

    def test_clicking_the_navigation_opens_the_page(self, settings):
        window = settings()
        window.navigation.buttons['shortcuts'].invoke()
        assert window.current == 'shortcuts' and window.title.cget('text') == 'Shortcuts'

    def test_open_page_moves_the_navigation_too(self, settings):
        window = settings()
        window.open_page('translation')
        assert window.navigation.value == 'translation'

    @pytest.mark.parametrize('name,expected', [('Hotkeys', 'shortcuts'), ('API Key', 'providers'),
                                               ('Dictionary', 'dictionary'), ('Guide', 'help'),
                                               ('General', 'general'), ('Translation', 'translation')])
    def test_the_applications_old_names_still_open_the_right_page(self, settings, name, expected):
        window = settings()
        assert window.open_tab(name) is True
        assert window.current == expected

    def test_open_tab_passes_the_row_on(self, settings):
        window = settings()
        assert window.open_tab('Translation', highlight='replace_mode') is True
        assert window.current == 'translation'
        assert window.pages['translation'].row_for('replace_mode').highlighted

    def test_an_unknown_name_changes_nothing(self, settings):
        window = settings()
        assert window.open_tab('Nonsense') is False and window.open_page('') is False
        assert window.current == 'general'

    def test_a_page_that_cannot_be_built_says_so_and_does_not_break_the_window(self, settings, monkeypatch):
        window = settings()
        monkeypatch.setattr(window_module.AppearancePage, 'populate',
                            lambda self: (_ for _ in ()).throw(RuntimeError('boom')))
        window.open_page('appearance')
        assert window.current == 'appearance'
        window.open_page('general')
        assert window.current == 'general'

    def test_refresh_pages_reaches_every_built_page_even_when_one_fails(self, settings):
        window = settings()
        window.open_page('appearance')
        general, appearance = window.pages['general'], window.pages['appearance']
        general.refresh = Mock(side_effect=RuntimeError('boom'))
        appearance.refresh = Mock()
        window.refresh_pages()
        general.refresh.assert_called_once()
        appearance.refresh.assert_called_once()

    def test_rebuild_page_makes_a_fresh_one_and_shows_it_when_current(self, settings):
        window = settings()
        window.open_page('appearance')
        old = window.pages['appearance']
        window.rebuild_page('appearance')
        assert window.pages['appearance'] is not old and window.current == 'appearance'
        assert window._frames['appearance'].winfo_exists()

    def test_rebuilding_a_page_nobody_is_on_leaves_it_for_next_time(self, settings):
        window = settings()
        window.open_page('appearance')
        window.open_page('general')
        window.rebuild_page('appearance')
        assert 'appearance' not in window.pages and window.current == 'general'


class TestTheDictionaryPage:
    def test_it_is_the_packs_page_and_fills_its_frame(self, settings, monkeypatch):
        from src.ui.settings import page_dictionary
        from src.ui.settings.page_dictionary import DictionaryPage
        monkeypatch.setattr(page_dictionary, 'load_nlp', lambda: (None, {}))       # no real pack manager
        window = settings()
        window.open_page('dictionary')
        window.window.update()
        page = window.pages['dictionary']
        assert isinstance(page, DictionaryPage)
        assert page.body is not None and page.body.winfo_ismapped()


class TestTheProvidersPage:
    def test_it_is_the_cards_page_not_a_hosted_tab(self, settings):
        from src.ui.settings.page_providers import ProvidersPage
        window = settings()
        window.open_page('providers')
        window.window.update()
        page = window.pages['providers']
        assert isinstance(page, ProvidersPage) and len(page.cards) == 1
        assert page.body.winfo_ismapped()

    def test_a_change_to_the_keys_reaches_the_application_once(self, settings):
        callback = Mock()
        window = settings(on_api_change_callback=callback)
        window.api_changed()
        callback.assert_called_once_with()

    def test_without_an_application_callback_it_is_a_no_op(self, settings):
        window = settings()
        window.api_changed()

    def test_an_application_that_fails_does_not_break_the_page(self, settings, caplog):
        window = settings(on_api_change_callback=Mock(side_effect=RuntimeError('boom')))
        with caplog.at_level(logging.ERROR):
            window.api_changed()
        assert 'could not apply the API change' in caplog.text

    def test_a_page_can_scroll_to_a_widget_it_added(self, settings):
        window = settings()
        window.open_page('providers')
        window.window.update()
        page = window.pages['providers']
        for _ in range(3):
            page.add_backup()
        window.window.update()
        window.reveal('providers', page.cards[-1])
        window.reveal('nowhere', page.cards[-1])                # a page that was never built: nothing happens

    def test_choosing_a_provider_marks_saved_and_tells_the_application(self, settings):
        callback = Mock()
        window = settings(on_api_change_callback=callback)
        window.open_page('providers')
        page = window.pages['providers']
        page.cards[0].provider.choose(1)
        assert window.changes == ['api_keys'] and callback.call_count == 1


# --------------------------------------------------------------------------- #
# Saved
# --------------------------------------------------------------------------- #
class TestSaved:
    def test_a_change_shows_saved_and_tells_the_application(self, settings):
        window = settings()
        assert not window.saved.winfo_ismapped()
        window.changed('furigana')
        window.window.update()
        assert window.saved.winfo_ismapped() and window.changes == ['furigana']

    def test_it_goes_away_by_itself(self, settings, monkeypatch):
        monkeypatch.setattr(window_module, 'SETTINGS_SAVED_MS', 60)
        window = settings()
        window.changed('furigana')
        assert wait_for(window.window, lambda: not window.saved.winfo_ismapped(), 1)

    def test_a_second_change_keeps_it_for_a_full_time_again(self, settings, monkeypatch):
        monkeypatch.setattr(window_module, 'SETTINGS_SAVED_MS', 300)
        window = settings()
        window.changed('a')
        pump(window.window, 0.2)
        window.changed('b')
        pump(window.window, 0.2)
        assert window.saved.winfo_ismapped()                      # 0.4 s after the first, 0.2 after the second
        assert wait_for(window.window, lambda: not window.saved.winfo_ismapped(), 1)

    def test_an_application_that_fails_does_not_break_the_page(self, settings, caplog):
        window = settings()
        window.on_changed = Mock(side_effect=RuntimeError('boom'))
        with caplog.at_level(logging.ERROR):
            window.changed('furigana')
        assert window.saved.winfo_exists() and 'could not apply a change to furigana' in caplog.text

    def test_a_switch_on_a_page_reaches_the_application(self, settings):
        window = settings()
        window.pages['general'].switches['auto_update'].invoke()
        assert window.changes == ['auto_update']
        assert window.config.get_auto_check_updates() is False
        window.update_manager.auto_check_changed.assert_called_once()

    def test_without_a_listener_a_change_is_still_saved(self, settings):
        window = settings()
        window.on_changed = None
        window.changed('furigana')                               # no exception


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
class TestSearch:
    def titles(self, window):
        return [row.title_label.cget('text') for group in self.groups(window) for row in group.rows]

    def groups(self, window):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == 'TFrame' and hasattr(child, 'rows'):
                    found.append(child)
                walk(child)
        walk(window._results)
        return found

    def test_typing_lists_the_settings_that_answer(self, settings):
        window = settings()
        type_in(window, 'dark')
        assert self.titles(window)[0] == 'Theme'
        assert window.title.cget('text') == 'Search results' and window._results.winfo_ismapped()
        assert not window._frames['general'].winfo_ismapped()

    def test_a_result_names_its_page(self, settings):
        window = settings()
        type_in(window, 'dark')
        texts = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == 'TLabel':
                    texts.append(str(child.cget('text')))
                walk(child)
        walk(window._results)
        assert 'Appearance' in texts

    def test_show_opens_the_page_and_marks_the_row(self, settings):
        window = settings()
        type_in(window, 'dark')
        button = self.buttons(window)[0]
        button.invoke()
        window.window.update()
        assert window.current == 'appearance' and window.title.cget('text') == 'Appearance'
        assert window.pages['appearance'].row_for('theme').highlighted
        assert window.search.get() == '' and window._results is None

    def buttons(self, window):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == 'TButton' and str(child.cget('text')) == 'Show':
                    found.append(child)
                walk(child)
        walk(window._results)
        return found

    def test_the_mark_goes_away_by_itself(self, settings, monkeypatch):
        monkeypatch.setattr(window_module, 'SETTINGS_HIGHLIGHT_MS', 60)
        window = settings()
        window.open_page('appearance', highlight='theme')
        row = window.pages['appearance'].row_for('theme')
        assert row.highlighted
        assert wait_for(window.window, lambda: not row.highlighted, 1)

    def test_going_elsewhere_clears_the_mark(self, settings):
        window = settings()
        window.open_page('appearance', highlight='theme')
        row = window.pages['appearance'].row_for('theme')
        window.open_page('general')
        assert not row.highlighted

    def test_a_search_with_no_answer_says_so(self, settings):
        window = settings()
        type_in(window, 'zzzzqqq')
        texts = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == 'TLabel':
                    texts.append(str(child.cget('text')))
                walk(child)
        walk(window._results)
        assert any('Nothing matches' in text for text in texts)
        assert self.buttons(window) == []

    def test_a_long_list_is_cut_and_says_so(self, settings, monkeypatch):
        monkeypatch.setattr(window_module, 'SETTINGS_RESULTS_MAX', 2)
        window = settings()
        type_in(window, 'shortcut')
        assert len(self.buttons(window)) == 2
        texts = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == 'TLabel':
                    texts.append(str(child.cget('text')))
                walk(child)
        walk(window._results)
        assert any('Showing the first 2' in text for text in texts)

    def test_clearing_the_field_brings_the_page_back(self, settings):
        window = settings()
        type_in(window, 'dark')
        window.search.clear()
        window._run_search()
        window.window.update()
        assert window._results is None and window._frames['general'].winfo_ismapped()
        assert window.title.cget('text') == 'General'

    def test_enter_opens_the_best_result(self, settings):
        window = settings()
        window.search.insert(0, 'dark')
        assert window._search_enter() == 'break'
        assert window.current == 'appearance'
        assert window.pages['appearance'].row_for('theme').highlighted

    def test_enter_opens_the_best_of_several_results(self, settings):
        window = settings()
        window.search.insert(0, 'shortcut')
        assert len(index.search('shortcut')) > 1
        window._search_enter()
        assert window.current == 'shortcuts'
        assert window.pages['shortcuts'].row_for(index.search('shortcut')[0].key).highlighted
        assert not window.pages['shortcuts'].row_for('fix_grammar_shortcut').highlighted

    def test_enter_with_nothing_found_does_nothing(self, settings):
        window = settings()
        window.search.insert(0, 'zzzzqqq')
        window._search_enter()
        assert window.current == 'general'

    def test_escape_in_the_field_clears_it_then_closes(self, settings):
        window = settings()
        type_in(window, 'dark')
        assert window._search_escape() == 'break'
        window.window.update()
        assert window.search.get() == '' and window._results is None and window.window.winfo_exists()
        window._search_escape()
        assert not window.window.winfo_exists()

    def test_typing_waits_for_a_pause(self, settings, monkeypatch):
        monkeypatch.setattr(window_module, 'SEARCH_DELAY_MS', 60)
        window = settings()
        window.search.insert(0, 'dark')
        window._search_typed()
        assert window._results is None
        assert wait_for(window.window, lambda: window._results is not None, 1)

    def test_the_navigation_leaves_the_results(self, settings):
        window = settings()
        type_in(window, 'dark')
        window.navigation.buttons['translation'].invoke()
        window.window.update()
        assert window.current == 'translation' and window.search.get() == '' and window._results is None


class TestRevealingARow:
    def test_a_row_below_the_fold_is_scrolled_into_view(self, settings):
        window = settings()
        window.window.geometry('900x420+0+0')
        window.window.update()
        window.open_page('translation')
        window.window.update()
        row = window.pages['translation'].row_for('fix_grammar_button')
        area = window._areas['translation']
        assert row.winfo_rooty() + row.winfo_height() > area.canvas.winfo_rooty() + area.canvas.winfo_height()
        window.open_page('translation', highlight='fix_grammar_button')
        window.window.update()
        assert row.winfo_rooty() >= area.canvas.winfo_rooty()
        assert row.winfo_rooty() + row.winfo_height() <= area.canvas.winfo_rooty() + area.canvas.winfo_height()
        assert row.highlighted


# --------------------------------------------------------------------------- #
# Keys and closing
# --------------------------------------------------------------------------- #
class TestKeysAndClosing:
    def test_escape_closes_the_window(self, settings):
        window = settings()
        assert window._escape() == 'break'
        assert not window.window.winfo_exists()

    def test_escape_does_not_close_while_a_shortcut_is_being_recorded(self, settings):
        window = settings()
        window.set_recording(True)
        assert window._escape() is None
        assert window.window.winfo_exists()

    def test_escape_with_search_text_clears_it_first(self, settings):
        window = settings()
        window.search.insert(0, 'dark')
        window._escape()
        assert window.search.get() == '' and window.window.winfo_exists()

    def test_the_close_button_of_the_title_bar_destroys_it(self, settings):
        window = settings()
        window.close()
        assert not window.window.winfo_exists()
        window.close()                                           # a second time is harmless


class TestRecording:
    def test_the_application_is_told_once_per_change(self, settings):
        window = settings()
        window.set_recording(True)
        window.set_recording(True)
        window.set_recording(False)
        window.set_recording(False)
        assert window.recordings == [True, False]

    def test_a_failing_listener_does_not_break_recording(self, settings, caplog):
        window = settings()
        window.on_shortcut_recording = Mock(side_effect=RuntimeError('boom'))
        with caplog.at_level(logging.ERROR):
            window.set_recording(True)
        assert window.recording is True and 'could not follow the recording' in caplog.text

    def test_closing_during_a_recording_lets_the_application_answer_hotkeys_again(self, settings):
        source = FakeSource()
        window = settings(key_source_factory=lambda: source)
        window.open_page('shortcuts')
        window.pages['shortcuts'].editors['English'].start_recording()
        assert window.recordings == [True] and window.recording
        window.window.destroy()
        assert window.recordings == [True, False] and source.stopped

    def test_the_shortcut_page_reports_to_the_window(self, settings):
        source = FakeSource()
        window = settings(key_source_factory=lambda: source)
        window.open_page('shortcuts')
        page = window.pages['shortcuts']
        page.editors['English'].start_recording()
        source.press('ctrl', 'alt', 'k')
        assert wait_for(window.window, lambda: not page.editors['English'].recording)
        assert window.recordings == [True, False]
        assert window.changes == ['language_shortcuts']
        assert window.config.get_all_hotkeys()['English'] == 'ctrl+alt+k'

    def test_how_the_hotkeys_registered_is_shown_on_the_page(self, settings):
        status = {}
        window = settings(shortcut_status=lambda: status)
        window.open_page('shortcuts')
        status['Japanese'] = 'taken'
        window.refresh_shortcut_status()
        assert 'Another app' in window.pages['shortcuts'].editors['Japanese'].message_text

    def test_the_status_is_read_when_the_page_is_first_built(self, settings):
        window = settings(shortcut_status=lambda: {'English': 'taken'})
        window.open_page('shortcuts')
        assert 'Another app' in window.pages['shortcuts'].editors['English'].message_text

    def test_refreshing_the_status_before_the_page_exists_is_harmless(self, settings):
        window = settings()
        window.refresh_shortcut_status()

    def test_audio_shortcut_status_and_recording_cancel_on_providers_page(self, settings):
        status = {'__audio__': 'taken'}
        window = settings(shortcut_status=lambda: status, key_source_factory=FakeSource)
        window.open_page('providers')
        editor = window.pages['providers'].audio_section.shortcut_editor
        assert 'Another app' in editor.message_text
        status['__audio__'] = 'ok'
        window.refresh_shortcut_status()
        assert editor.message_text == ''
        editor.toggle_recording()
        assert editor.recording and window.recording
        window.open_page('general')
        assert not editor.recording and not window.recording
        assert window.recordings == [True, False]


# --------------------------------------------------------------------------- #
# Size and width
# --------------------------------------------------------------------------- #
class TestSizeAndWidth:
    def test_it_is_sized_by_the_rule_for_the_work_area(self, settings):
        from src.constants import SETTINGS_DEFAULT_SIZE, SETTINGS_MIN_SIZE
        from src.ui import translator_layout as layout
        from src.ui.components.dialog import work_area
        window = settings()
        ctx = C.get()
        expected = layout.window_size(work_area(), None, ctx.scale, default=SETTINGS_DEFAULT_SIZE,
                                      minimum=SETTINGS_MIN_SIZE)
        assert window.window.minsize() == (expected.min_width, expected.min_height)
        assert window.window.winfo_width() <= expected.width + 2

    def test_the_size_the_user_left_is_remembered(self, settings):
        window = settings()
        window.window.geometry('905x611+0+0')
        window.window.update()
        config = window.config
        window.window.destroy()
        assert config.get_settings_window_size() == (905, 611)

    def test_it_opens_at_the_remembered_size(self, settings):
        config = SettingsConfig()
        config.set_settings_window_size(870, 590)
        window = settings(config)
        window.window.update()
        assert (window.window.winfo_width(), window.window.winfo_height()) == (870, 590)

    def test_an_unchanged_size_is_not_saved_again(self, settings):
        window = settings()
        window.window.update()
        window.config.set_settings_window_size(window.window.winfo_width(), window.window.winfo_height())
        saves = window.config.saves
        window.window.destroy()
        assert window.config.saves == saves

    def test_a_minimised_window_does_not_remember_its_size(self, settings):
        window = settings()
        window.window.update()
        window.window.iconify()
        window.window.update()
        config = window.config
        window.window.destroy()
        assert config.get_settings_window_size() == (None, None)

    def test_it_is_a_resizable_toplevel_titled_settings(self, settings):
        window = settings()
        assert window.window.title() == 'Settings - CrossTrans'
        assert window.window.resizable() == (True, True)

    def test_a_page_uses_available_content_width_in_a_wide_window(self, settings):
        window = settings()
        window.window.geometry('1700x700+0+0')
        pump(window.window, 0.2)
        body = window.pages['general'].body
        content = window._frames['general']
        assert body.winfo_width() > C.get().px(1100)
        expected = content.canvas.winfo_width() - 2 * C.get().px(window_module.SIDE_PADDING)
        assert abs(body.winfo_width() - expected) <= 2

    def test_a_narrow_window_gives_the_page_all_it_has(self, settings):
        window = settings()
        window.window.geometry('720x600+0+0')
        pump(window.window, 0.2)
        body = window.pages['general'].body
        content = window._frames['general']
        assert body.winfo_width() >= content.winfo_width() - 2 * C.get().px(window_module.SIDE_PADDING) - 20

    def test_the_navigation_keeps_its_width(self, settings):
        from src.constants import SETTINGS_NAV_WIDTH
        window = settings()
        window.window.geometry('1200x700+0+0')
        pump(window.window, 0.2)
        assert window.nav.winfo_width() >= C.get().px(SETTINGS_NAV_WIDTH) - 2


# --------------------------------------------------------------------------- #
# Updates
# --------------------------------------------------------------------------- #
class TestUpdates:
    def test_an_offered_update_opens_the_about_page_first(self, settings):
        window = settings()
        window.offer_update('9.9.9', None)
        assert window.current == 'about'
        window.update_manager.request_check.assert_called_once()

    def test_the_about_page_is_where_the_update_widgets_live(self, settings):
        window = settings()
        window.open_page('about')
        assert window.update_offer_frame.winfo_exists() and window.check_update_btn.winfo_exists()

    def test_an_available_update_is_shown_when_the_about_page_is_first_opened(self, settings):
        manager = update_manager()
        manager.available_version = '9.9.9'
        window = settings(update_manager=manager)
        window.open_page('about')
        window.window.update()
        assert '9.9.9' in window.update_offer_label.cget('text')

    def test_the_status_uses_the_themes_colours(self, settings, light):
        window = settings()
        window.open_page('about')
        window._update_status('Error: nothing', 'red')
        assert str(window.update_status_label.cget('foreground')).lower() == light.palette.danger.lower()
        window._update_status('fine', 'green')
        assert str(window.update_status_label.cget('foreground')).lower() == light.palette.success.lower()
        window._update_status('waiting', 'gray')
        assert str(window.update_status_label.cget('foreground')).lower() == light.palette.muted.lower()


# --------------------------------------------------------------------------- #
# Destroying
# --------------------------------------------------------------------------- #
class TestDestroy:
    def test_destroying_cancels_the_pending_jobs(self, settings):
        window = settings()
        window.changed('a')
        window.open_page('appearance', highlight='theme')
        window.search.insert(0, 'dark')
        window._search_typed()
        assert window._saved_job and window._search_job and window._highlight_job
        window.window.destroy()
        assert window._saved_job is None and window._search_job is None and window._highlight_job is None

    def test_the_theme_subscription_is_released(self, settings, light):
        window = settings()
        assert window._retheme in light.manager._subscribers
        window.window.destroy()
        assert window._retheme not in light.manager._subscribers

    def test_an_owned_update_manager_is_stopped_with_the_window(self, tk_root, light, monkeypatch):
        manager = update_manager()
        created = []
        import src.core.update_ui_manager as module
        import src.ui.toast as toast_module
        monkeypatch.setattr(module, 'UpdateUIManager', lambda *a, **k: created.append(manager) or manager)
        manager.configure_callbacks = Mock()
        monkeypatch.setattr(toast_module, 'ToastManager', lambda root: Mock())
        window = SettingsWindow(tk_root, SettingsConfig())
        assert window._owns_update_manager and created
        window.window.destroy()
        manager.stop.assert_called_once()
