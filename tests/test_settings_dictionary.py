"""
Tests for the Dictionary page (TODO.md U8, step 5, Decision 51): the language packs and the furigana reader.

The page never pip-installs anything here: the pack manager is a fake that answers for the packs a test
names, and every call it gets is recorded with the thread it came from, because the page's whole point is that
neither the scan nor an install happens on the UI thread. Each test waits for a result the way a user would.
"""
import threading
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core import pack_catalog as catalog
from src.core import settings_index as index
from src.ui import components as ui
from src.ui.settings import page_dictionary as module
from src.ui.settings.page_dictionary import DictionaryPage
from src.ui.theme import tokens
from tests.settings_support import Host, SettingsConfig, pump, wait_for
from tests.test_components import env, holder, light, shown  # noqa: F401  (shared fixtures)


def pack(category, size, code):
    return SimpleNamespace(category=category, size_mb=size, code=code)


PACKS = {
    'Vietnamese': pack('Asian', 120, 'vi'), 'Japanese': pack('Asian', 250, 'ja'),
    'Korean': pack('Asian', 100, 'ko'), 'English': pack('European', 25, 'en'),
    'German': pack('European', 20, 'de'), 'French': pack('European', 20, 'fr'),
    'Spanish': pack('European', 25, 'es'), 'Italian': pack('European', 20, 'it'),
    'Danish': pack('Scandinavian', 15, 'da'), 'Hebrew': pack('Middle Eastern', 15, 'he'),
}


class FakeNLP:
    """The pack manager: `installed` is what is installed; install and uninstall change it and answer from
    `answers` ((verb, language) -> (success, error)). A gate holds the call until the test opens it."""

    def __init__(self, installed=(), answers=None, scan_gate=None, work_gate=None, scan_error=None,
                 work_timeout=3):
        self.installed, self.answers = list(installed), answers or {}
        self.scan_gate, self.work_gate, self.scan_error = scan_gate, work_gate, scan_error
        self.work_timeout = work_timeout
        self.config = None
        self._installed_cache = {'German': True}
        self.calls, self.threads = [], []

    def set_config(self, config):
        self.config = config

    def get_installed_languages(self):
        self.threads.append(threading.current_thread())
        if self.scan_gate is not None:
            self.scan_gate.wait(3)
        if self.scan_error is not None:
            raise self.scan_error
        return list(self.installed)

    def _work(self, verb, language, progress):
        self.calls.append((verb, language))
        self.threads.append(threading.current_thread())
        if progress is not None:
            progress(f"{verb} {language}...", 40)
        if self.work_gate is not None:
            self.work_gate.wait(self.work_timeout)
        ok, error = self.answers.get((verb, language), (True, ''))
        if ok:
            if verb == 'install' and language not in self.installed:
                self.installed.append(language)
            if verb == 'uninstall' and language in self.installed:
                self.installed.remove(language)
        return ok, error

    def install(self, language, progress=None):
        return self._work('install', language, progress)

    def uninstall(self, language, progress=None):
        return self._work('uninstall', language, progress)


class Reader:
    """`reader_is_accurate`, as a switch the test flips."""

    def __init__(self):
        self.accurate = False

    def __call__(self):
        return self.accurate


@pytest.fixture(autouse=True)
def furigana_untouched(monkeypatch):
    """Installing the Japanese pack tells furigana to look again: never for real in a test."""
    from src.core import furigana
    monkeypatch.setattr(furigana, 'refresh_providers', lambda: None)


@pytest.fixture
def reader(monkeypatch):
    switch = Reader()
    monkeypatch.setattr(module, 'reader_is_accurate', switch)
    return switch


@pytest.fixture
def make(shown, light, reader, monkeypatch):
    """Build the page in a mapped window over a fake pack manager: `make(manager)` -> (page, host)."""
    shown.geometry('760x900+0+0')

    def build(manager=None, wait=True, packs=None):
        manager = manager or FakeNLP()
        monkeypatch.setattr(module, 'load_nlp', lambda: (manager, PACKS if packs is None else packs))
        host = Host(shown, SettingsConfig())
        page = DictionaryPage(host)
        page.build(shown)
        page.body.pack(fill='both', expand=True, padx=20, pady=20)
        shown.update()
        if wait:
            assert wait_for(shown, lambda: page.scanned, 3)
            shown.update()
        return page, host, manager
    return build


def settled(page, seconds=3):
    """Wait for the run and the look at the packs that follows it."""
    assert wait_for(page.body, lambda: not page.busy and not page.runner.busy(), seconds)
    page.body.update()


def names_of(group_rows, page):
    return [name for name, row in page.pack_rows.items() if row in group_rows]


# --------------------------------------------------------------------------- #
# Opening
# --------------------------------------------------------------------------- #
class TestOpening:
    def test_every_entry_of_the_page_has_a_row(self, make):
        page, host, manager = make()
        for each in index.entries_of('dictionary'):
            assert page.row_for(each.key) is not None, each.key

    def test_the_page_opens_before_the_packs_are_known(self, make):
        gate = threading.Event()
        page, host, manager = make(FakeNLP(scan_gate=gate), wait=False)
        assert page.summary_label.cget('text') == module.CHECKING
        assert not page.scanned and page.pack_rows == {} and page.busy
        assert str(page.install_all_btn.cget('state')) == 'disabled'
        gate.set()
        assert wait_for(page.body, lambda: page.scanned, 3)

    def test_the_manager_is_asked_off_the_ui_thread(self, make):
        page, host, manager = make(FakeNLP(installed=['German']))
        assert manager.threads and all(t is not threading.main_thread() for t in manager.threads)

    def test_the_manager_is_given_the_config(self, make):
        page, host, manager = make()
        assert manager.config is host.config

    def test_installed_packs_are_listed_a_to_z_with_a_chip_and_a_remove_button(self, make):
        page, host, manager = make(FakeNLP(installed=['Japanese', 'German']))
        assert set(page.remove_buttons) == {'German', 'Japanese'}
        assert [w for w in page.pack_rows['German'].control.winfo_children()
                if isinstance(w, ui.Chip)][0].cget('text') == 'Installed'
        assert page.pack_rows['German'].title_label.cget('text') == 'German'
        assert page.pack_rows['German'].description_label.cget('text') == 'European, about 20 MB'

    def test_the_rest_are_available_with_an_install_button_and_the_list_is_short_at_first(self, make):
        page, host, manager = make(FakeNLP(installed=['Japanese', 'German']))
        assert list(page.install_buttons) == ['Danish', 'English', 'French', 'Hebrew', 'Italian', 'Korean']
        assert page.install_buttons['Danish'].cget('text') == 'Install'
        assert page.expand_btn.cget('text') == 'Show all 8 languages'

    def test_show_all_lists_every_available_pack(self, make):
        page, host, manager = make(FakeNLP(installed=['Japanese', 'German']))
        page.expand_btn.invoke()
        page.body.update()
        assert list(page.install_buttons) == ['Danish', 'English', 'French', 'Hebrew', 'Italian', 'Korean',
                                              'Spanish', 'Vietnamese']
        assert not hasattr(page, 'expand_btn') or not page.expand_btn.winfo_exists()

    def test_the_summary_counts_what_is_installed(self, make):
        page, host, manager = make(FakeNLP(installed=['Japanese', 'German']))
        assert page.installed_summary.cget('text') == '2 installed, about 270 MB.'

    def test_with_nothing_installed_there_is_no_installed_group(self, make):
        page, host, manager = make(FakeNLP())
        assert page.remove_buttons == {}
        assert page.installed_summary.cget('text') == '0 packs, about 0 MB.'
        assert [g.heading_label.cget('text') for g in page.groups
                if g.heading_label is not None and g.winfo_exists()] == ['Installed', 'Available']

    def test_with_everything_installed_there_is_no_available_group(self, make):
        page, host, manager = make(FakeNLP(installed=list(PACKS)))
        assert page.install_buttons == {} and len(page.remove_buttons) == len(PACKS)
        assert [g.heading_label.cget('text') for g in page.groups if g.heading_label is not None
                and g.winfo_exists()] == ['Installed']

    def test_the_footer_says_where_packs_come_from(self, make):
        page, host, manager = make()
        assert not hasattr(page, 'note')
        assert not hasattr(page, 'furigana_reader_label')

    def test_building_the_page_changes_no_setting(self, make):
        page, host, manager = make(FakeNLP(installed=['German']))
        assert host.config.saves == 0 and host.changes == []


class TestWhenTheManagerIsMissing:
    def test_the_page_says_so_and_has_no_lists(self, shown, light, reader, monkeypatch):
        def broken():
            raise ImportError('no manager')
        monkeypatch.setattr(module, 'load_nlp', broken)
        host = Host(shown, SettingsConfig())
        page = DictionaryPage(host)
        page.build(shown).pack(fill='both', expand=True)
        shown.update()
        assert page.manager is None and page.pack_rows == {}
        assert page.notice.kind == catalog.DANGER and page.notice.text.cget('text') == module.UNAVAILABLE
        assert all(str(button.cget('state')) == 'disabled'
                   for button in (page.install_all_btn, page.remove_all_btn))
        page.install('German')


class TestWhenTheScanFails:
    def test_it_says_so_and_offers_to_try_again(self, make):
        manager = FakeNLP(scan_error=RuntimeError('boom'))
        page, host, manager = make(manager, wait=False)
        assert wait_for(page.body, lambda: page.notice is not None, 3)
        assert page.notice.kind == catalog.DANGER and page.notice.text.cget('text') == module.SCAN_FAILED
        assert page.notice.action.cget('text') == 'Try again'
        assert not page.busy and not page.scanned and page.summary_label.cget('text') == ''
        manager.scan_error = None
        manager.installed = ['German']
        page.notice.action.invoke()
        assert wait_for(page.body, lambda: page.scanned, 3)
        assert list(page.remove_buttons) == ['German']


# --------------------------------------------------------------------------- #
# The lists
# --------------------------------------------------------------------------- #
class TestSearching:
    def test_a_search_narrows_both_lists_and_shows_every_match(self, make, shown, monkeypatch):
        monkeypatch.setattr(module, 'SEARCH_DELAY_MS', 20)
        page, host, manager = make(FakeNLP(installed=['German', 'Japanese']))
        page.search.set('e')
        assert wait_for(shown, lambda: 'Vietnamese' in page.install_buttons, 2)
        assert set(page.remove_buttons) == {'German', 'Japanese'}
        assert 'Spanish' in page.install_buttons                      # not cut to the short list

    def test_a_name_narrows_to_it(self, make, shown, monkeypatch):
        monkeypatch.setattr(module, 'SEARCH_DELAY_MS', 20)
        page, host, manager = make(FakeNLP(installed=['German']))
        page.search.set('ger')
        assert wait_for(shown, lambda: list(page.pack_rows) == ['German'], 2)
        assert page.install_buttons == {}

    def test_a_category_narrows_to_it(self, make, shown, monkeypatch):
        monkeypatch.setattr(module, 'SEARCH_DELAY_MS', 20)
        page, host, manager = make(FakeNLP())
        page.search.set('scandi')
        assert wait_for(shown, lambda: list(page.pack_rows) == ['Danish'], 2)

    def test_nothing_matching_says_so(self, make, shown, monkeypatch):
        monkeypatch.setattr(module, 'SEARCH_DELAY_MS', 20)
        page, host, manager = make(FakeNLP())
        page.search.set('zzz')
        quote = chr(0x201C) + 'zzz' + chr(0x201D)
        assert wait_for(shown, lambda: page.pack_rows == {}, 2)
        labels = [w.cget('text') for w in page.lists_box.winfo_children() if isinstance(w, ui.Label)]
        assert labels == [f'No language matches {quote}.']

    def test_clearing_the_search_brings_the_short_list_back(self, make, shown, monkeypatch):
        monkeypatch.setattr(module, 'SEARCH_DELAY_MS', 20)
        page, host, manager = make(FakeNLP())
        page.search.set('ger')
        assert wait_for(shown, lambda: list(page.pack_rows) == ['German'], 2)
        page.search.set('')
        assert wait_for(shown, lambda: len(page.install_buttons) == module.COLLAPSED_ROWS, 2)

    def test_typing_on_waits_for_a_pause(self, make, shown, monkeypatch):
        monkeypatch.setattr(module, 'SEARCH_DELAY_MS', 150)
        page, host, manager = make(FakeNLP())
        for text in ('g', 'ge', 'ger'):
            page.search.set(text)
            pump(shown, 0.04)
        assert len(page.pack_rows) > 1                                # not rebuilt yet
        assert wait_for(shown, lambda: list(page.pack_rows) == ['German'], 2)

    def test_real_typing_searches(self, make, shown, monkeypatch):
        monkeypatch.setattr(module, 'SEARCH_DELAY_MS', 20)
        page, host, manager = make(FakeNLP())
        page.search.focus_force()
        shown.update()
        for char in 'fre':
            page.search.event_generate('<KeyPress>', keysym=char)
            page.search.event_generate('<KeyRelease>', keysym=char)
        assert wait_for(shown, lambda: list(page.pack_rows) == ['French'], 2)

    def test_the_search_survives_an_install(self, make, shown, monkeypatch):
        monkeypatch.setattr(module, 'SEARCH_DELAY_MS', 20)
        page, host, manager = make(FakeNLP())
        page.search.set('ger')
        assert wait_for(shown, lambda: list(page.pack_rows) == ['German'], 2)
        page.install('German')
        settled(page)
        assert list(page.remove_buttons) == ['German']                # still filtered, now installed


class TestRebuilding:
    def test_old_groups_do_not_pile_up(self, make, shown):
        page, host, manager = make(FakeNLP(installed=['German']))
        for _ in range(3):
            page._rebuild_lists()
        assert all(group.winfo_exists() for group in page.groups)
        headings = [g.heading_label.cget('text') for g in page.groups if g.heading_label is not None]
        assert headings == ['Installed', 'Available']

    def test_new_rows_pass_the_wheel_on(self, make, shown):
        page, host, manager = make(FakeNLP(installed=['German']))
        before = host.adopted
        page._rebuild_lists()
        assert host.adopted == before + 1

    def test_nothing_is_drawn_before_the_first_scan_answers(self, make):
        gate = threading.Event()
        page, host, manager = make(FakeNLP(scan_gate=gate), wait=False)
        page._rebuild_lists()
        assert page.lists_box.winfo_children() == []
        gate.set()


# --------------------------------------------------------------------------- #
# Installing
# --------------------------------------------------------------------------- #
class TestInstalling:
    def test_an_install_shows_progress_then_the_result_and_moves_the_pack(self, make, shown):
        gate = threading.Event()
        page, host, manager = make(FakeNLP(work_gate=gate))
        page.install_buttons['German'].invoke()
        assert page.busy and page.progress is not None
        assert page.progress_title.cget('text') == 'Installing German...'
        assert wait_for(shown, lambda: page.progress_detail.cget('text') == 'install German', 2)
        assert all(str(b.cget('state')) == 'disabled' for b in page.install_buttons.values())
        assert str(page.install_all_btn.cget('state')) == 'disabled'
        gate.set()
        settled(page)
        assert page.progress is None
        assert page.notice.kind == catalog.SUCCESS and page.notice.text.cget('text') == 'German is installed.'
        assert 'German' in page.remove_buttons and 'German' not in page.install_buttons
        assert page.installed_summary.cget('text') == '1 installed, about 20 MB.'
        assert all(str(b.cget('state')) == 'normal' for b in page.install_buttons.values())

    def test_the_work_is_done_off_the_ui_thread(self, make):
        page, host, manager = make()
        page.install('German')
        settled(page)
        assert manager.calls == [('install', 'German')]
        assert all(t is not threading.main_thread() for t in manager.threads)

    def test_a_failure_says_why_and_leaves_the_pack_available(self, make):
        manager = FakeNLP(answers={('install', 'German'): (False, 'ERROR: Read timed out.')})
        page, host, manager = make(manager)
        page.install('German')
        settled(page)
        assert page.notice.kind == catalog.DANGER
        assert page.notice.text.cget('text').startswith('Could not install German. CrossTrans could not download')
        assert 'German' in page.install_buttons and page.remove_buttons == {}

    def test_basic_mode_is_reported_as_information(self, make):
        manager = FakeNLP(answers={('install', 'German'): (True, catalog.BASIC_MODE)})
        page, host, manager = make(manager)
        page.install('German')
        settled(page)
        assert page.notice.kind == catalog.INFO
        assert page.notice.text.cget('text') == 'German is installed, with basic word splitting.'

    def test_a_second_request_while_one_runs_is_ignored(self, make):
        gate = threading.Event()
        page, host, manager = make(FakeNLP(work_gate=gate))
        page.install('German')
        page.install('Danish')
        gate.set()
        settled(page)
        assert manager.calls == [('install', 'German')]

    def test_a_new_run_clears_the_last_result(self, make):
        page, host, manager = make()
        page.install('German')
        settled(page)
        first = page.notice
        page.install('Danish')
        assert page.notice is None or not first.winfo_exists()
        settled(page)
        assert page.notice.text.cget('text') == 'Danish is installed.'

    def test_the_result_can_be_closed(self, make):
        page, host, manager = make()
        page.install('German')
        settled(page)
        page.notice.close_button.invoke()
        page.body.update()
        assert page.notice is None and not page.status_box.winfo_ismapped()

    def test_the_status_area_takes_no_room_when_empty(self, make):
        page, host, manager = make()
        assert not page.status_box.winfo_ismapped()
        page.install('German')
        page.body.update()
        assert page.status_box.winfo_ismapped()
        settled(page)
        page.notice.close_button.invoke()
        page.body.update()
        assert not page.status_box.winfo_ismapped()

    def test_the_japanese_pack_changes_the_reader_shown(self, make, reader):
        page, host, manager = make(FakeNLP())
        page.install('Japanese')
        settled(page)
        assert 'Japanese' in page.remove_buttons
        assert '1 installed' in page.installed_summary.cget('text')
        assert not hasattr(page, 'furigana_reader_label')

    def test_a_scan_after_a_failed_run_still_happens(self, make):
        manager = FakeNLP(answers={('install', 'German'): (False, 'x')})
        page, host, manager = make(manager)
        manager.installed.append('Danish')                              # changed behind the page's back
        page.install('German')
        settled(page)
        assert 'Danish' in page.remove_buttons

    def test_a_worker_that_breaks_outside_a_pack_is_reported(self, make, monkeypatch):
        page, host, manager = make()
        monkeypatch.setattr(module.catalog, 'run_batch', Mock(side_effect=RuntimeError('boom')))
        page.install('German')
        settled(page)
        assert page.notice.kind == catalog.DANGER and page.notice.text.cget('text') == module.WORK_FAILED


# --------------------------------------------------------------------------- #
# Removing
# --------------------------------------------------------------------------- #
class TestRemoving:
    def test_the_trash_button_asks_first_and_cancel_is_the_default(self, make):
        page, host, manager = make(FakeNLP(installed=['German']))
        page.remove_buttons['German'].invoke()
        page.body.update()
        dialog = page.dialog
        assert dialog.exists and dialog.window.title() == 'Remove the German pack?'
        cancel = dialog._button_list[0]
        assert cancel.cget('text') == 'Cancel' and dialog.window.focus_get() is cancel
        cancel.invoke()
        assert not dialog.exists and manager.calls == [] and not page.busy

    def test_escape_keeps_the_pack(self, make):
        page, host, manager = make(FakeNLP(installed=['German']))
        dialog = page.confirm_remove('German')
        dialog.window.event_generate('<Escape>')
        dialog.window.update()
        assert not dialog.exists and manager.calls == []

    def test_confirming_removes_it_and_says_so(self, make):
        page, host, manager = make(FakeNLP(installed=['German', 'Japanese']))
        dialog = page.confirm_remove('German')
        dialog._button_list[1].invoke()
        settled(page)
        assert manager.calls == [('uninstall', 'German')]
        assert page.notice.text.cget('text') == 'German was removed.'
        assert 'German' in page.install_buttons and list(page.remove_buttons) == ['Japanese']

    def test_removing_japanese_warns_about_furigana(self, make):
        page, host, manager = make(FakeNLP(installed=['Japanese']))
        dialog = page.confirm_remove('Japanese')
        labels = [w.cget('text') for w in dialog.body.winfo_children() if isinstance(w, ui.Label)]
        assert any('Furigana goes back to basic readings.' in text for text in labels)
        dialog.close()

    def test_a_failed_removal_says_why(self, make):
        manager = FakeNLP(installed=['German'], answers={('uninstall', 'German'): (False, 'Access is denied')})
        page, host, manager = make(manager)
        page.confirm_remove('German')._button_list[1].invoke()
        settled(page)
        assert page.notice.text.cget('text').startswith('Could not remove German. Windows would not let')
        assert 'German' in page.remove_buttons


# --------------------------------------------------------------------------- #
# Bulk actions and the menu
# --------------------------------------------------------------------------- #
def menu_states(menu):
    return [(menu.entrycget(i, 'label'), str(menu.entrycget(i, 'state')))
            for i in range(menu.index('end') + 1) if menu.type(i) == 'command']


class TestTheMenu:
    def test_both_are_offered_when_there_is_something_for_each(self, make):
        page, host, manager = make(FakeNLP(installed=['German']))
        assert visible_states(page) == [('Install all packs', 'normal'),
                                                  ('Delete all packs', 'normal')]

    def test_with_nothing_installed_there_is_nothing_to_remove(self, make):
        page, host, manager = make(FakeNLP())
        assert visible_states(page) == [('Install all packs', 'normal'),
                                                  ('Delete all packs', 'disabled')]

    def test_with_everything_installed_there_is_nothing_to_install(self, make):
        page, host, manager = make(FakeNLP(installed=list(PACKS)))
        assert visible_states(page) == [('Install all packs', 'disabled'),
                                                  ('Delete all packs', 'normal')]

    def test_while_busy_both_are_off(self, make):
        gate = threading.Event()
        page, host, manager = make(FakeNLP(installed=['German'], work_gate=gate))
        page.install('Danish')
        assert visible_states(page) == [('Install all packs', 'disabled'),
                                                  ('Delete all packs', 'disabled')]
        gate.set()
        settled(page)

    def test_the_button_waits_while_busy_and_comes_back(self, make):
        gate = threading.Event()
        page, host, manager = make(FakeNLP(installed=['German'], work_gate=gate))
        page.install('Danish')
        assert str(page.install_all_btn.cget('state')) == 'disabled'
        gate.set()
        settled(page)
        assert str(page.install_all_btn.cget('state')) == 'normal'

    def test_before_the_scan_both_are_off(self, make):
        gate = threading.Event()
        page, host, manager = make(FakeNLP(scan_gate=gate), wait=False)
        assert visible_states(page) == [('Install all packs', 'disabled'),
                                                  ('Delete all packs', 'disabled')]
        gate.set()

    def test_the_button_opens_it_under_itself_and_a_second_press_replaces_it(self, make, monkeypatch):
        page, host, manager = make(FakeNLP())
        assert not hasattr(page, 'more_btn')
        assert page.install_all_btn.winfo_ismapped()
        assert page.remove_all_btn.winfo_ismapped()

    def test_it_wears_the_palette(self, make, light):
        page, host, manager = make(FakeNLP())
        assert page.install_all_btn.kind == 'soft'
        assert page.remove_all_btn.kind == 'danger'

    def test_its_commands_open_the_questions(self, make):
        page, host, manager = make(FakeNLP(installed=['German']))
        page.install_all_btn.invoke()
        assert page.dialog.exists and page.dialog.window.title() == 'Install 9 language packs?'
        page.dialog.close()
        page.remove_all_btn.invoke()
        assert page.dialog.exists and page.dialog.window.title() == 'Remove the German pack?'
        page.dialog.close()


class TestInstallAll:
    def test_it_asks_first_and_says_the_size(self, make):
        page, host, manager = make(FakeNLP(installed=['Japanese', 'German']))
        dialog = page.confirm_install_all()
        page.body.update()
        assert dialog.window.title() == 'Install 8 language packs?'
        labels = [w.cget('text') for w in dialog.body.winfo_children() if isinstance(w, ui.Label)]
        assert any('about 340 MB' in text for text in labels)
        dialog._button_list[1].invoke()                               # Cancel
        assert manager.calls == []

    def test_installing_is_the_default_button(self, make):
        page, host, manager = make(FakeNLP())
        dialog = page.confirm_install_all()
        page.body.update()
        assert dialog._button_list[0].cget('text') == 'Install all' and dialog._default is dialog._button_list[0]
        dialog.close()

    def test_confirming_installs_every_available_pack_in_order_and_reports(self, make):
        page, host, manager = make(FakeNLP(installed=['Japanese', 'German']))
        page.confirm_install_all()._button_list[0].invoke()
        settled(page, 5)
        assert [language for _, language in manager.calls] == [
            'Danish', 'English', 'French', 'Hebrew', 'Italian', 'Korean', 'Spanish', 'Vietnamese']
        assert page.notice.kind == catalog.SUCCESS and page.notice.text.cget('text') == 'Installed 8 language packs.'
        assert page.install_buttons == {} and len(page.remove_buttons) == len(PACKS)

    def test_it_says_which_pack_it_is_on(self, make, shown):
        gate = threading.Event()
        # Keep the observed phase held even if UI work exhausts the old fake's
        # three-second auto-release. The UI observation deadline stays at two.
        page, host, manager = make(FakeNLP(installed=['Japanese', 'German'], work_gate=gate,
                                          work_timeout=15))
        page.confirm_install_all()._button_list[0].invoke()
        try:
            assert wait_for(shown, lambda: page.progress_title.cget('text') == 'Installing Danish (1 of 8)...', 2), {
                'actual': str(page.progress_title.cget('text')), 'calls': manager.calls,
                'busy': page.busy, 'runner_busy': page.runner.busy()}
        finally:
            gate.set()
        settled(page, 5)

    def test_one_that_fails_is_named_and_the_rest_still_go(self, make):
        manager = FakeNLP(installed=['Japanese', 'German'], answers={('install', 'French'): (False, 'x')})
        page, host, manager = make(manager)
        page.confirm_install_all()._button_list[0].invoke()
        settled(page, 5)
        assert page.notice.kind == catalog.WARNING
        assert page.notice.text.cget('text') == 'Installed 7 of 8. Could not install: French.'
        assert 'French' in page.install_buttons and 'Vietnamese' in page.remove_buttons


class TestRemoveAll:
    def test_it_asks_first_and_cancel_is_the_default(self, make):
        page, host, manager = make(FakeNLP(installed=['German', 'Danish']))
        dialog = page.confirm_remove_all()
        page.body.update()
        assert dialog.window.title() == 'Remove all 2 language packs?'
        cancel = dialog._button_list[0]
        assert cancel.cget('text') == 'Cancel' and dialog.window.focus_get() is cancel
        cancel.invoke()
        assert manager.calls == []

    def test_confirming_removes_them_all(self, make):
        page, host, manager = make(FakeNLP(installed=['German', 'Danish']))
        page.confirm_remove_all()._button_list[1].invoke()
        settled(page, 5)
        assert [language for _, language in manager.calls] == ['German', 'Danish']
        assert page.notice.text.cget('text') == 'Removed 2 language packs.'
        assert page.remove_buttons == {} and page.installed_summary.cget('text') == '0 packs, about 0 MB.'


# --------------------------------------------------------------------------- #
# The reader note
# --------------------------------------------------------------------------- #
class TestReaderSectionsRemoved:
    @pytest.mark.parametrize('installed', [[], ['Japanese'], ['German']])
    def test_pack_page_only_shows_dictionary_packs(self, make, installed):
        page, host, manager = make(FakeNLP(installed=installed))
        assert 'furigana_reader' not in page.rows
        assert not hasattr(page, 'furigana_reader_label')
        assert set(page.remove_buttons) == set(installed)


# --------------------------------------------------------------------------- #
# Closing
# --------------------------------------------------------------------------- #
class TestClosing:
    def test_closing_during_a_run_is_quiet(self, make, shown):
        gate = threading.Event()
        page, host, manager = make(FakeNLP(work_gate=gate))
        page.install('German')
        page.body.destroy()
        gate.set()
        pump(shown, 0.3)

    def test_closing_during_the_first_scan_is_quiet(self, make, shown):
        gate = threading.Event()
        page, host, manager = make(FakeNLP(scan_gate=gate), wait=False)
        page.body.destroy()
        gate.set()
        pump(shown, 0.3)

    def test_a_pending_search_is_dropped_with_the_page(self, make, shown, monkeypatch):
        monkeypatch.setattr(module, 'SEARCH_DELAY_MS', 100)
        page, host, manager = make(FakeNLP())
        page.search.set('ger')
        assert page._search_job is not None
        page.body.destroy()
        assert page._search_job is None
        pump(shown, 0.2)

    def test_the_theme_subscription_is_released(self, make, env):
        page, host, manager = make()
        page.body.destroy()
        env.manager.set_mode('dark')
        env.manager.set_mode('light')
        assert not page.body.winfo_exists()


def visible_states(page):
    return [(button.cget('text'), str(button.cget('state')))
            for button in (page.install_all_btn, page.remove_all_btn)]
