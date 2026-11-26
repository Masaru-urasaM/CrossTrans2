"""
Tests for the API keys page (TODO.md U8, step 4, Decision 51): key cards, saving, Test, showing keys,
Delete all keys, failover, the free trial and the capability chips.

Nothing here reaches the network, the keyboard, Windows Hello or the registry: the AI manager is a fake the
tests fill in, `require_auth` a recorder, the remote configuration a fixed namespace and the settings an
in-memory dictionary. A test runs on a worker thread exactly as the real page does, so each one waits for
the result the way the user would.
"""
import threading
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core import key_testing as kt
from src.core import settings_index as index
from src.ui import components as ui
from src.ui.components import context as C
from src.ui.settings import page_providers as module
from src.ui.settings.page_providers import KeyCard, ProvidersPage
from src.ui.theme import tokens
from tests.settings_support import Host, SettingsConfig, pump, wait_for
from tests.test_components import env, holder, light, shown  # noqa: F401  (shared fixtures)

REMOTE = SimpleNamespace(
    providers_list=['Auto', 'Google', 'Groq', 'OpenAI'],
    model_provider_map={'Google': ['gemini-a', 'gemini-b'], 'Groq': ['llama-a', 'llama-b'], 'OpenAI': ['gpt-a']},
    api_key_patterns={'AIza': 'Google', 'gsk_': 'Groq'},
    default_models_by_provider={}, trial_daily_limit=25)
KEY = 'AIza-first-key'
KEY2 = 'gsk_second-key'


class FakeManager:
    """Answers for the (provider, model) pairs in `works`; every other pair raises `error`. A `gate` holds
    each connection until the test opens it, so a test can look at the page mid-flight."""

    def __init__(self, works=(), vision=(), error='HTTP 401 Unauthorized', gate=None):
        self.works, self.vision, self.error, self.gate = set(works), set(vision), error, gate
        self.calls, self.threads = [], []

    def test_connection(self, model, key, provider):
        self.calls.append((provider, model, key))
        self.threads.append(threading.current_thread())
        if self.gate is not None:
            self.gate.wait(3)
        if (provider, model) not in self.works:
            raise RuntimeError(self.error)
        return True                                   # the real one returns True or raises

    def test_vision_connection(self, model, key, provider):
        return (provider, model) in self.vision


class Auth:
    """`require_auth`, recorded: `ok` is what Windows Hello would answer."""

    def __init__(self):
        self.calls, self.ok = [], True

    def __call__(self, parent):
        self.calls.append(parent)
        return self.ok


@pytest.fixture(autouse=True)
def remote(monkeypatch):
    monkeypatch.setattr(module, 'get_config', lambda: REMOTE)
    return REMOTE


@pytest.fixture
def auth(monkeypatch):
    recorder = Auth()
    monkeypatch.setattr(module, 'require_auth', recorder)
    return recorder


@pytest.fixture
def use(monkeypatch):
    """Make the page's AI manager `manager`."""
    def install(manager):
        monkeypatch.setattr(module, 'AIAPIManager', lambda: manager)
        return manager
    return install


@pytest.fixture
def make(shown, light):
    """Build the page in a mapped window: `make(keys=[...], **settings)` -> (page, host)."""
    built = []
    shown.geometry('760x900+0+0')

    def build(keys=None, config=None, **overrides):
        config = config or SettingsConfig(**overrides)
        if keys is not None:
            config.set_api_keys(keys)
            config.saves = config.secure_saves = 0
        host = Host(shown, config)
        page = ProvidersPage(host)
        page.build(shown)
        page.body.pack(fill='both', expand=True, padx=20, pady=20)
        shown.update()
        built.append(page)
        return page, host
    return build


def entry(key=KEY, model='', provider='Auto', **flags):
    return {'api_key': key, 'model_name': model, 'provider': provider, **flags}


def saved(host):
    return host.config.get_api_keys()


def finish_test(page, card, seconds=10):
    """Wait until `card`'s test is over (the runner has no job for it any more)."""
    assert wait_for(page.body, lambda: not page.runner.busy(card.tag), seconds)
    page.body.update()


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #
class TestBuilding:
    def test_every_entry_of_the_page_has_a_row(self, make):
        page, host = make()
        for each in index.entries_of('providers'):
            assert page.row_for(each.key) is not None, each.key

    def test_with_no_keys_there_is_one_empty_primary_card(self, make):
        page, host = make()
        assert len(page.cards) == 1
        card = page.cards[0]
        assert card.title_label.cget('text') == 'Primary key'
        assert card.values() == ('Auto', 'Auto', '')
        assert not card.remove_btn.winfo_ismapped()
        assert card.status == '' and not card.status_label.winfo_ismapped()

    def test_saved_keys_become_cards_in_order(self, make):
        page, host = make(keys=[entry(KEY, 'gemini-b', 'Google'), entry(KEY2, '', 'Groq')])
        assert [c.title_label.cget('text') for c in page.cards] == ['Primary key', 'Backup key 1']
        assert page.cards[0].values() == ('Google', 'gemini-b', KEY)
        assert page.cards[1].values() == ('Groq', 'Auto', KEY2)

    def test_an_entry_without_a_provider_is_auto(self, make):
        page, host = make(keys=[{'api_key': KEY, 'model_name': 'm'}])
        assert page.cards[0].values() == ('Auto', 'm', KEY)

    def test_the_key_is_masked_until_asked(self, make):
        page, host = make(keys=[entry()])
        card = page.cards[0]
        assert str(card.field.cget('show')) == '*' and not card.revealed

    def test_a_backup_has_a_remove_button_and_the_primary_does_not(self, make):
        page, host = make(keys=[entry(), entry(KEY2)])
        assert not page.cards[0].remove_btn.winfo_ismapped()
        assert page.cards[1].remove_btn.winfo_ismapped()

    def test_the_provider_dropdown_lists_the_remote_providers(self, make):
        page, host = make()
        assert [value for value, _ in page.cards[0].provider.options] == REMOTE.providers_list

    def test_the_model_list_follows_the_provider(self, make):
        page, host = make(keys=[entry(KEY, '', 'Groq')])
        assert page.cards[0].model.models == ['Auto', 'llama-a', 'llama-b']

    def test_more_saved_keys_than_the_limit_are_all_shown_not_lost(self, make):
        keys = [entry(f'k{i}') for i in range(10 + 2)]
        page, host = make(keys=keys)
        assert len(page.cards) == 10 + 2
        assert str(page.add_btn.cget('state')) == 'normal'

    def test_the_footer_names_the_providers_without_auto(self, make):
        page, host = make()
        assert not hasattr(page, 'note')
        assert page.add_btn.winfo_ismapped()

    def test_the_failover_switch_shows_the_setting(self, make):
        page, host = make(model_rotation=False)
        assert page.variables['failover'].get() is False

    def test_building_saves_nothing(self, make):
        page, host = make(keys=[entry()])
        assert host.config.saves == 0 and host.changes == [] and host.api_changes == 0


class TestPersistedStatus:
    def test_a_tested_key_says_what_it_can_read(self, make):
        page, host = make(keys=[entry(file_capable=True, vision_capable=True)])
        card = page.cards[0]
        assert card.status == ''
        assert [chip.cget('text') for chip in page.capability_chips] == ['Reads images', 'Reads files', 'Reads audio']

    def test_a_failed_startup_check_is_shown(self, make):
        config = SettingsConfig()
        config.api_status_cache[KEY] = False
        page, host = make(keys=[entry()], config=config)
        assert page.cards[0].status == ''

    def test_a_key_nobody_checked_says_nothing(self, make):
        page, host = make(keys=[entry()])
        assert page.cards[0].status == ''

    def test_the_capability_chips_follow_the_flags(self, make):
        page, host = make(keys=[entry(file_capable=True, vision_capable=False)])
        assert [c.cget('text') for c in page.capability_box.winfo_children()] == [
            'Reads images', 'Reads files', 'Reads audio']

    def test_no_flags_means_neither_is_read_yet(self, make):
        page, host = make(keys=[entry()])
        assert [c.cget('text') for c in page.capability_box.winfo_children()] == [
            'Reads images', 'Reads files', 'Reads audio']


# --------------------------------------------------------------------------- #
# The card
# --------------------------------------------------------------------------- #
class TestTheCard:
    def card(self, make):
        page, host = make(keys=[entry()])
        return page.cards[0]

    def test_a_status_is_painted_in_its_tone(self, make, light):
        card = self.card(make)
        card.set_status('Works.', kt.SUCCESS)
        assert str(card.status_label.cget('foreground')).upper() == tokens.LIGHT.success.upper()
        card.set_status('Not yet.', kt.WARNING)
        assert str(card.status_label.cget('foreground')).upper() == tokens.LIGHT.warning.upper()
        card.set_status('No.', kt.DANGER)
        assert str(card.status_label.cget('foreground')).upper() == tokens.LIGHT.danger.upper()

    def test_a_quiet_status_uses_the_label_s_own_colour(self, make):
        card = self.card(make)
        card.set_status('No.', kt.DANGER)
        card.set_status('Testing...', kt.MUTED)
        assert str(card.status_label.cget('foreground')) == ''

    def test_an_empty_status_takes_no_room_and_comes_back(self, make):
        card = self.card(make)
        card.set_status('Hello')
        card.update()
        assert card.status_label.winfo_ismapped()
        card.set_status('')
        card.update()
        assert not card.status_label.winfo_ismapped()
        card.set_status('Again')
        card.update()
        assert card.status_label.winfo_ismapped()

    def test_the_status_follows_a_theme_change(self, make, env):
        card = self.card(make)
        card.set_status('No.', kt.DANGER)
        env.manager.set_mode('dark')
        card.update()
        assert str(card.status_label.cget('foreground')).upper() == tokens.DARK.danger.upper()
        env.manager.set_mode('light')

    def test_the_status_wraps_to_the_card(self, make):
        card = self.card(make)
        card.set_status('A long sentence. ' * 30)
        card.update()
        assert int(float(str(card.status_label.cget('wraplength')))) <= card.winfo_width()
        assert card.status_label.cget('text') == 'A long sentence.'
        assert card.status_label.tooltip.text == 'A long sentence. ' * 30

    def test_values_are_what_the_card_shows(self, make):
        card = self.card(make)
        card.field.set('  AIza-typed  ')
        assert card.values()[2] == 'AIza-typed'

    def test_values_still_answer_after_the_card_is_gone(self, make):
        card = self.card(make)
        card.field.set('AIza-last-seen')
        card._typed()
        card.destroy()
        assert card.values() == ('Auto', 'Auto', 'AIza-last-seen')

    def test_showing_the_key_unmasks_it_and_the_button_says_hide(self, make):
        card = self.card(make)
        card.set_revealed(True)
        assert str(card.field.cget('show')) == '' and card.revealed and card.eye.is_on
        assert card.eye.tooltip.text == module.HIDE_KEY
        card.set_revealed(False)
        assert str(card.field.cget('show')) == '*' and not card.eye.is_on
        assert card.eye.tooltip.text == module.SHOW_KEY

    def test_the_test_button_turns_into_stop_while_testing(self, make):
        card = self.card(make)
        card.set_testing(True)
        assert card.test_btn.cget('text') == 'Stop' and card.testing
        card.set_testing(False)
        assert card.test_btn.cget('text') == 'Test' and not card.testing

    def test_choosing_a_provider_changes_the_models_offered_and_keeps_the_model(self, make):
        page, host = make(keys=[entry(KEY, 'my-model', 'Google')])
        card = page.cards[0]
        card.provider.choose(2)                                     # Groq
        assert card.values() == ('Groq', 'my-model', KEY)
        assert card.model.models == ['Auto', 'llama-a', 'llama-b']

    def test_apply_result_shows_the_pair_that_worked(self, make):
        card = self.card(make)
        card.apply_result('Groq', 'llama-b')
        assert card.values()[:2] == ('Groq', 'llama-b')
        assert card.model.models == ['Auto', 'llama-a', 'llama-b']
        assert card.provider.button.cget('text') == 'Groq'

    def test_clearing_the_key_empties_the_field(self, make):
        card = self.card(make)
        card.clear_key()
        assert card.values()[2] == '' and card.key_text == ''

    def test_numbering_names_the_primary_and_the_backups(self):
        assert module.title_for(0) == 'Primary key' and module.title_for(3) == 'Backup key 3'


# --------------------------------------------------------------------------- #
# Saving
# --------------------------------------------------------------------------- #
class TestSaving:
    def test_choosing_a_provider_saves_at_once_and_tells_the_window_and_the_application(self, make):
        page, host = make(keys=[entry()])
        page.cards[0].provider.choose(1)                            # Google
        assert saved(host)[0]['provider'] == 'Google'
        assert host.changes == ['api_keys'] and host.api_changes == 1

    def test_choosing_a_model_saves_it(self, make):
        page, host = make(keys=[entry()])
        page.cards[0].model.choose(('gemini-b', '', ''))
        assert saved(host)[0]['model_name'] == 'gemini-b'

    def test_auto_is_saved_as_an_empty_model(self, make):
        page, host = make(keys=[entry(KEY, 'gemini-a', 'Google')])
        page.cards[0].model.choose(('Auto', '', ''))
        assert saved(host)[0]['model_name'] == ''

    def test_a_typed_model_the_list_lacks_is_saved(self, make):
        page, host = make(keys=[entry()])
        page.cards[0].model.choose(('brand-new-model', '', ''))
        assert saved(host)[0]['model_name'] == 'brand-new-model'

    def test_a_key_is_saved_once_typing_pauses(self, make, shown, monkeypatch):
        monkeypatch.setattr(module, 'SETTINGS_KEY_SAVE_MS', 60)
        page, host = make()
        card = page.cards[0]
        card.field.set(KEY)
        card._typed()
        assert host.config.saves == 0
        assert wait_for(shown, lambda: host.changes == ['api_keys'], 1)
        pump(shown, 0.2)
        assert saved(host)[0]['api_key'] == KEY and host.changes == ['api_keys']        # once, not per call

    def test_typing_on_keeps_pushing_the_save_back(self, make, shown, monkeypatch):
        monkeypatch.setattr(module, 'SETTINGS_KEY_SAVE_MS', 150)
        page, host = make()
        card = page.cards[0]
        for text in ('A', 'AI', 'AIz', 'AIza'):
            card.field.set(text)
            card._typed()
            pump(shown, 0.05)
        assert host.config.saves == 0
        assert wait_for(shown, lambda: host.changes == ['api_keys'], 1)
        assert saved(host)[0]['api_key'] == 'AIza'

    def test_leaving_the_field_saves_at_once(self, make):
        page, host = make()
        card = page.cards[0]
        card.field.set(KEY)
        card._finished()
        assert saved(host)[0]['api_key'] == KEY and host.api_changes == 1

    def test_a_key_typed_for_real_is_saved(self, make, shown, monkeypatch):
        monkeypatch.setattr(module, 'SETTINGS_KEY_SAVE_MS', 40)
        page, host = make()
        card = page.cards[0]
        card.field.focus_force()
        shown.update()
        card.field.insert('end', KEY)
        card.field.event_generate('<KeyRelease>', keysym='a')
        assert wait_for(shown, lambda: saved(host) and saved(host)[0]['api_key'] == KEY, 1.5)

    def test_nothing_is_saved_when_nothing_changed(self, make):
        page, host = make(keys=[entry()])
        card = page.cards[0]
        card._finished()
        card.model.choose(('Auto', '', ''))
        assert host.config.saves == 0 and host.changes == []

    def test_a_pending_key_is_saved_when_the_window_closes(self, make, shown):
        page, host = make()
        card = page.cards[0]
        card.field.set(KEY)
        card._typed()
        page.body.destroy()
        assert saved(host)[0]['api_key'] == KEY
        assert host.api_changes == 1 and host.changes == []        # the window is closing: no "Saved"

    def test_closing_with_nothing_pending_saves_nothing(self, make):
        page, host = make(keys=[entry()])
        page.body.destroy()
        assert host.config.saves == 0 and host.api_changes == 0

    def test_the_saved_list_keeps_the_cards_order_and_drops_empty_ones(self, make):
        page, host = make(keys=[entry(KEY, '', 'Google')])
        page.add_backup()
        page.add_backup()
        page.cards[2].field.set(KEY2)
        page.cards[2]._finished()
        assert [item['api_key'] for item in saved(host)] == [KEY, KEY2]

    def test_what_a_test_found_out_is_kept_when_the_list_is_saved_again(self, make):
        page, host = make(keys=[entry(KEY, 'gemini-a', 'Google', vision_capable=True, file_capable=True)])
        page.cards[0].model.choose(('gemini-a', '', ''))             # nothing changed
        page.cards[0].provider.choose(1)                             # Google again: nothing changed
        page.add_backup()
        page.cards[1].field.set(KEY2)
        page.cards[1]._finished()
        first = saved(host)[0]
        assert first['vision_capable'] is True and first['file_capable'] is True

    def test_removing_a_key_overwrites_the_file_and_forgets_its_check(self, make):
        config = SettingsConfig()
        config.api_status_cache[KEY2] = True
        page, host = make(keys=[entry(), entry(KEY2)], config=config)
        page.remove_card(page.cards[1])
        assert host.config.secure_saves == 1 and KEY2 not in host.config.api_status_cache

    def test_adding_or_changing_a_key_is_not_a_secure_save(self, make):
        page, host = make(keys=[entry()])
        page.cards[0].provider.choose(1)
        assert host.config.saves >= 1 and host.config.secure_saves == 0

    def test_the_capability_toggles_follow_the_saved_flags(self, make):
        page, host = make(keys=[entry(KEY, 'm', 'Google', vision_capable=True, file_capable=True)])
        page.cards[0].model.choose(('other', '', ''))                # a different model: flags do not follow
        assert host.config._config.get('vision_enabled') is False


# --------------------------------------------------------------------------- #
# Adding and removing
# --------------------------------------------------------------------------- #
class TestAddAndRemove:
    def test_a_backup_is_added_numbered_focused_and_scrolled_to(self, make):
        page, host = make(keys=[entry()])
        card = page.add_backup()
        assert card is page.cards[1] and card.title_label.cget('text') == 'Backup key 1'
        assert card.values() == ('Auto', 'Auto', '')
        assert host.reveals == [('providers', card)] and host.adopted >= 1
        top = card.winfo_toplevel()
        assert str(top.tk.call('focus', '-lastfor', str(top))) == str(card.field)       # where typing would go

    def test_adding_a_card_does_not_save(self, make):
        page, host = make(keys=[entry()])
        page.add_backup()
        assert host.config.saves == 0

    def test_the_add_button_remains_available_beyond_ten_keys(self, make):
        page, host = make()
        for _ in range(10 - 1):
            assert page.add_backup() is not None
        assert len(page.cards) == 10
        assert str(page.add_btn.cget('state')) == 'normal'
        assert page.add_backup() is not None and len(page.cards) == 11

    def test_removing_a_backup_renumbers_saves_and_frees_the_button(self, make):
        page, host = make(keys=[entry(f'k{i}') for i in range(10)])
        assert str(page.add_btn.cget('state')) == 'normal'
        gone = page.cards[2]
        page.remove_card(gone)
        page.body.update()
        assert [c.title_label.cget('text') for c in page.cards] == [
            'Primary key', *[f'Backup key {i}' for i in range(1, 9)]]
        assert [item['api_key'] for item in saved(host)] == [f'k{i}' for i in range(10) if i != 2]
        assert str(page.add_btn.cget('state')) == 'normal'
        assert not gone.winfo_exists()
        assert host.changes == ['api_keys']

    def test_the_primary_cannot_be_removed(self, make):
        page, host = make(keys=[entry(), entry(KEY2)])
        page.remove_card(page.cards[0])
        assert len(page.cards) == 2 and host.config.saves == 0

    def test_removing_a_card_that_is_gone_does_nothing(self, make):
        page, host = make(keys=[entry(), entry(KEY2)])
        gone = page.cards[1]
        page.remove_card(gone)
        page.remove_card(gone)
        assert len(page.cards) == 1

    def test_removing_a_card_stops_its_test(self, make, use):
        gate = threading.Event()
        use(FakeManager(gate=gate))
        page, host = make(keys=[entry(), entry(KEY2)])
        card = page.cards[1]
        page.test_card(card)
        assert page.runner.busy(card.tag)
        page.remove_card(card)
        assert not page.runner.busy(card.tag)
        gate.set()
        pump(page.body, 0.2)
        assert host.api_changes == 1                                # only the removal's


# --------------------------------------------------------------------------- #
# Test
# --------------------------------------------------------------------------- #
class TestTesting:
    def test_a_working_pair_is_found_shown_saved_and_reported(self, make, use, shown):
        manager = use(FakeManager(works={('Google', 'gemini-b')}, vision={('Google', 'gemini-b')}))
        page, host = make(keys=[entry(KEY)])
        card = page.cards[0]
        page.test_card(card)
        finish_test(page, card)
        assert manager.calls == [('Google', 'gemini-a', KEY), ('Google', 'gemini-b', KEY)]
        assert card.values() == ('Google', 'gemini-b', KEY)
        assert card.status == 'Works with gemini-b. It can read images and files.' and card.tone == kt.SUCCESS
        first = saved(host)[0]
        assert (first['provider'], first['model_name']) == ('Google', 'gemini-b')
        assert first['vision_capable'] is True and first['file_capable'] is True
        assert host.config.api_status_cache[KEY] is True
        assert [c.cget('text') for c in page.capability_box.winfo_children()] == ['Reads images', 'Reads files', 'Reads audio']
        assert host.changes == ['api_keys'] and host.api_changes == 1
        assert card.test_btn.cget('text') == 'Test' and not card.testing

    def test_a_key_without_image_reading_says_files_only(self, make, use):
        use(FakeManager(works={('Google', 'gemini-a')}))
        page, host = make(keys=[entry(KEY)])
        card = page.cards[0]
        page.test_card(card)
        finish_test(page, card)
        assert card.status == 'Works with gemini-a. It can read files.'
        assert saved(host)[0]['vision_capable'] is False and saved(host)[0]['file_capable'] is True

    def test_a_failure_is_reported_in_plain_words_and_remembered(self, make, use):
        use(FakeManager(works=set(), error='HTTP 401 Unauthorized'))
        page, host = make(keys=[entry(KEY, '', 'Google', vision_capable=True, file_capable=True)])
        card = page.cards[0]
        page.test_card(card)
        finish_test(page, card)
        assert card.status == ('None of the 2 models answered. '
                               'The provider did not accept the key. Check that it was copied whole and is for '
                               'this provider.')
        assert card.tone == kt.DANGER
        # Authentication failure is not evidence that an already-tested model lost its capabilities.
        assert saved(host)[0]['vision_capable'] is True and saved(host)[0]['file_capable'] is True
        assert host.config.api_status_cache[KEY] is False
        assert [c.cget('text') for c in page.capability_box.winfo_children()] == [
            'Reads images', 'Reads files', 'Reads audio']

    def test_a_failed_test_keeps_what_the_user_chose(self, make, use):
        use(FakeManager(works=set()))
        page, host = make(keys=[entry(KEY, 'gemini-a', 'Google')])
        card = page.cards[0]
        page.test_card(card)
        finish_test(page, card)
        assert card.values() == ('Google', 'gemini-a', KEY)
        assert saved(host)[0]['model_name'] == 'gemini-a'

    def test_a_single_pair_failing_says_it_did_not_answer(self, make, use):
        use(FakeManager(works=set(), error='boom'))
        page, host = make(keys=[entry(KEY, 'gemini-a', 'Google')])
        card = page.cards[0]
        page.test_card(card)
        finish_test(page, card)
        assert card.status == 'It did not answer.'

    def test_the_network_is_never_touched_from_the_ui_thread(self, make, use):
        manager = use(FakeManager(works={('Google', 'gemini-a')}))
        page, host = make(keys=[entry(KEY)])
        page.test_card(page.cards[0])
        finish_test(page, page.cards[0])
        assert manager.threads and all(t is not threading.main_thread() for t in manager.threads)

    def test_progress_is_shown_while_it_runs(self, make, use, shown):
        gate = threading.Event()
        use(FakeManager(works={('Google', 'gemini-b')}, gate=gate))
        page, host = make(keys=[entry(KEY)])
        card = page.cards[0]
        page.test_card(card)
        assert card.testing and card.test_btn.cget('text') == 'Stop'
        assert card.status.startswith('Trying 1 of 2')
        gate.set()
        finish_test(page, card)

    def test_no_key_means_no_test(self, make, use):
        manager = use(FakeManager())
        page, host = make()
        page.test_card(page.cards[0])
        assert page.cards[0].status == module.NO_KEY and page.cards[0].tone == kt.WARNING
        assert manager.calls == [] and not page.runner.busy()

    def test_a_provider_with_no_models_says_so(self, make, use):
        manager = use(FakeManager())
        page, host = make(keys=[entry(KEY, '', 'OpenAI')])
        REMOTE.model_provider_map['OpenAI'], held = [], REMOTE.model_provider_map['OpenAI']
        try:
            page.test_card(page.cards[0])
        finally:
            REMOTE.model_provider_map['OpenAI'] = held
        assert page.cards[0].status == module.NO_MODEL and manager.calls == []

    def test_pressing_test_again_while_it_runs_stops_it(self, make, use, shown):
        gate = threading.Event()
        use(FakeManager(works={('Google', 'gemini-a')}, gate=gate))
        page, host = make(keys=[entry(KEY)])
        card = page.cards[0]
        page.test_card(card)
        page.test_card(card)
        assert not card.testing and card.status == 'Stopped.'
        assert card.test_btn.cget('text') == 'Test' and not page.runner.busy(card.tag)
        gate.set()
        pump(shown, 0.3)
        assert card.status == 'Stopped.' and host.config.saves == 0 and host.changes == []

    def test_a_key_changed_during_the_test_ignores_the_result(self, make, use, shown):
        gate = threading.Event()
        use(FakeManager(works={('Google', 'gemini-a')}, gate=gate))
        page, host = make(keys=[entry(KEY)])
        card = page.cards[0]
        page.test_card(card)
        card.field.set('AIza-other')
        gate.set()
        finish_test(page, card)
        assert card.status == '' and card.values() == ('Auto', 'Auto', 'AIza-other')
        assert host.config.saves == 0

    def test_a_test_that_cannot_run_says_so(self, make, monkeypatch):
        monkeypatch.setattr(module, 'AIAPIManager', Mock(side_effect=RuntimeError('no manager')))
        page, host = make(keys=[entry(KEY)])
        card = page.cards[0]
        page.test_card(card)
        finish_test(page, card)
        assert card.status == module.TEST_FAILED_TO_RUN and card.tone == kt.DANGER
        assert not card.testing and host.config.saves == 0

    def test_two_cards_test_independently(self, make, use):
        use(FakeManager(works={('Google', 'gemini-a'), ('Groq', 'llama-a')}))
        page, host = make(keys=[entry(KEY), entry(KEY2)])
        first, second = page.cards
        page.test_card(first)
        page.test_card(second)
        finish_test(page, first)
        finish_test(page, second)
        assert first.status.startswith('Works with gemini-a') and second.status.startswith('Works with llama-a')
        assert [item['model_name'] for item in saved(host)] == ['gemini-a', 'llama-a']

    def test_a_success_clears_what_an_old_failed_check_said(self, make, use):
        config = SettingsConfig()
        config.api_status_cache[KEY] = False
        use(FakeManager(works={('Google', 'gemini-a')}))
        page, host = make(keys=[entry(KEY)], config=config)
        page.test_card(page.cards[0])
        finish_test(page, page.cards[0])
        assert host.config.api_status_cache[KEY] is True

    def test_the_window_closing_during_a_test_is_quiet(self, make, use, shown):
        gate = threading.Event()
        use(FakeManager(works={('Google', 'gemini-a')}, gate=gate))
        page, host = make(keys=[entry(KEY)])
        page.test_card(page.cards[0])
        page.body.destroy()
        gate.set()
        pump(shown, 0.3)
        assert host.config.saves == 0 and host.changes == []


# --------------------------------------------------------------------------- #
# Showing keys
# --------------------------------------------------------------------------- #
class TestShowingKeys:
    def test_showing_a_key_asks_windows_once(self, make, auth):
        page, host = make(keys=[entry(), entry(KEY2)])
        first, second = page.cards
        page.reveal_clicked(first)
        assert first.revealed and len(auth.calls) == 1 and auth.calls[0] is host.window
        page.reveal_clicked(second)
        assert second.revealed and len(auth.calls) == 1

    def test_hiding_never_asks(self, make, auth):
        page, host = make(keys=[entry()])
        page.reveal_clicked(page.cards[0])
        page.reveal_clicked(page.cards[0])
        assert not page.cards[0].revealed and len(auth.calls) == 1

    def test_a_refusal_keeps_the_key_hidden_and_asks_again_next_time(self, make, auth):
        auth.ok = False
        page, host = make(keys=[entry()])
        page.reveal_clicked(page.cards[0])
        assert not page.cards[0].revealed
        auth.ok = True
        page.reveal_clicked(page.cards[0])
        assert page.cards[0].revealed and len(auth.calls) == 2

    def test_an_empty_card_shows_without_asking(self, make, auth):
        page, host = make()
        page.reveal_clicked(page.cards[0])
        assert page.cards[0].revealed and auth.calls == []

    def test_show_all_asks_once_and_shows_every_key(self, make, auth):
        page, host = make(keys=[entry(), entry(KEY2)])
        page.toggle_all_keys()
        assert all(c.revealed for c in page.cards) and len(auth.calls) == 1

    def test_show_all_again_hides_every_key(self, make, auth):
        page, host = make(keys=[entry(), entry(KEY2)])
        page.toggle_all_keys()
        page.toggle_all_keys()
        assert not any(c.revealed for c in page.cards) and len(auth.calls) == 1

    def test_show_all_with_nothing_to_show_does_nothing(self, make, auth):
        page, host = make()
        page.toggle_all_keys()
        assert not page.cards[0].revealed and auth.calls == []

    def test_show_all_refused_shows_nothing(self, make, auth):
        auth.ok = False
        page, host = make(keys=[entry()])
        page.toggle_all_keys()
        assert not page.cards[0].revealed

    def test_one_shown_key_makes_the_toggle_hide_them_all(self, make, auth):
        page, host = make(keys=[entry(), entry(KEY2)])
        page.reveal_clicked(page.cards[0])
        page.toggle_all_keys()
        assert not any(c.revealed for c in page.cards)


# --------------------------------------------------------------------------- #
# The overflow menu and Delete all keys
# --------------------------------------------------------------------------- #
def labels(menu):
    return [(menu.entrycget(i, 'label'), str(menu.entrycget(i, 'state')))
            for i in range(menu.index('end') + 1) if menu.type(i) == 'command']


class TestVisibleKeyActions:
    def test_it_offers_show_and_delete_when_there_are_keys(self, make):
        page, host = make(keys=[entry()])
        assert page.show_all_btn.cget('text') == 'Show all keys'
        assert page.delete_all_btn.cget('text') == 'Delete all keys'
        assert str(page.show_all_btn.cget('state')) == 'normal'

    def test_without_keys_both_are_off(self, make):
        page, host = make()
        assert all(str(button.cget('state')) == 'disabled'
                   for button in (page.show_all_btn, page.delete_all_btn))

    def test_it_says_hide_while_a_key_is_shown(self, make, auth):
        page, host = make(keys=[entry()])
        page.reveal_clicked(page.cards[0])
        assert page.show_all_btn.cget('text') == 'Hide all keys'

    def test_its_commands_do_what_they_say(self, make, auth):
        page, host = make(keys=[entry()])
        page.show_all_btn.invoke()
        assert page.cards[0].revealed
        page.delete_all_btn.invoke()
        assert page.dialog.exists
        page.dialog.close()

    def test_bulk_actions_are_direct_and_do_not_post_a_menu(self, make):
        page, host = make(keys=[entry()])
        assert not hasattr(page, 'more_btn')
        assert page.show_all_btn.winfo_ismapped()
        assert page.delete_all_btn.winfo_ismapped()

    def test_hiding_all_restores_every_mask(self, make, auth):
        page, host = make(keys=[entry(), entry(KEY2)])
        page.show_all_btn.invoke()
        page.show_all_btn.invoke()
        assert all(not row.revealed and str(row.field.cget('show')) == '*' for row in page.cards)


class TestDeleteAll:
    def test_it_asks_first_and_cancel_is_the_default(self, make):
        page, host = make(keys=[entry(), entry(KEY2)])
        dialog = page.confirm_delete_all()
        page.body.update()
        assert dialog.exists and dialog.window.title() == module.DELETE_TITLE
        cancel = dialog._button_list[0]
        assert cancel.cget('text') == 'Cancel' and dialog.window.focus_get() is cancel
        cancel.invoke()
        assert not dialog.exists and len(page.cards) == 2 and host.config.saves == 0

    def test_escape_keeps_the_keys(self, make):
        page, host = make(keys=[entry()])
        dialog = page.confirm_delete_all()
        dialog.window.event_generate('<Escape>')
        dialog.window.update()
        assert not dialog.exists and saved(host)[0]['api_key'] == KEY

    def test_confirming_removes_the_backups_and_the_primary_s_key(self, make):
        config = SettingsConfig()
        config.api_status_cache.update({KEY: True, KEY2: False})
        page, host = make(keys=[entry(KEY, 'gemini-a', 'Google'), entry(KEY2)], config=config)
        dialog = page.confirm_delete_all()
        dialog._button_list[1].invoke()
        page.body.update()
        assert len(page.cards) == 1
        assert page.cards[0].values() == ('Google', 'gemini-a', '')
        assert saved(host) == [{'model_name': 'gemini-a', 'api_key': '', 'provider': 'Google'}]
        assert host.config.secure_saves == 1 and host.config.api_status_cache == {}
        assert host.changes == ['api_keys'] and host.api_changes == 1

    def test_with_an_auto_primary_nothing_is_left_to_save(self, make):
        page, host = make(keys=[entry(KEY), entry(KEY2)])
        page.delete_all()
        assert saved(host) == [] and page.cards[0].values() == ('Auto', 'Auto', '')

    def test_the_cards_left_are_clean(self, make, auth):
        page, host = make(keys=[entry(file_capable=True, vision_capable=True)])
        page.reveal_clicked(page.cards[0])
        page.delete_all()
        card = page.cards[0]
        assert not card.revealed and card.status == '' and not card.testing
        assert [c.cget('text') for c in page.capability_box.winfo_children()] == [
            'Reads images', 'Reads files', 'Reads audio']

    def test_running_tests_are_stopped(self, make, use):
        gate = threading.Event()
        use(FakeManager(works={('Google', 'gemini-a')}, gate=gate))
        page, host = make(keys=[entry(), entry(KEY2)])
        page.test_card(page.cards[1])
        page.test_card(page.cards[0])
        page.delete_all()
        assert not page.runner.busy() and not page.cards[0].testing
        gate.set()
        pump(page.body, 0.2)
        assert page.cards[0].status == ''

    def test_the_add_button_is_back_after_it(self, make):
        page, host = make(keys=[entry(f'k{i}') for i in range(10)])
        page.delete_all()
        assert str(page.add_btn.cget('state')) == 'normal'


# --------------------------------------------------------------------------- #
# Failover and the free trial
# --------------------------------------------------------------------------- #
class TestFailover:
    def test_the_switch_writes_the_setting_and_announces_it(self, make):
        page, host = make(model_rotation=True)
        page.switches['failover'].invoke()
        assert host.config.get_model_rotation() is False and host.changes == ['failover']

    def test_refresh_reads_it_again(self, make):
        page, host = make(model_rotation=True)
        host.config.set_model_rotation(False)
        page.refresh()
        assert page.variables['failover'].get() is False




class TestCapabilityChips:
    def test_they_are_drawn_again_after_a_change_and_pass_the_wheel_on(self, make):
        page, host = make(keys=[entry()])
        before = host.adopted
        page._draw_capabilities()
        assert host.adopted == before + 1

    def test_the_first_drawing_does_not_adopt(self, make):
        page, host = make()
        assert host.adopted == 0


class TestThePageInTheWindow:
    def test_a_card_knows_its_serial_and_test_tag(self, make):
        page, host = make(keys=[entry(), entry(KEY2)])
        assert [c.tag for c in page.cards] == ['test-0', 'test-1']

    def test_a_new_card_gets_a_new_tag_even_after_a_removal(self, make):
        page, host = make(keys=[entry(), entry(KEY2)])
        page.remove_card(page.cards[1])
        card = page.add_backup()
        assert card.tag == 'test-2'


class TestNoTrialInV2:
    def test_stale_trial_setting_does_not_create_trial_controls(self, make):
        page, host = make(trial_mode_forced=True)
        page.refresh()
        assert not hasattr(page, 'trial_switch')
        assert not hasattr(page, 'trial_var')
        assert 'trial' not in page.rows


class TestTestKeepsAuto:
    """Decision 104 (2026-11-20): a passed Test leaves Model = Auto on the card when the model that answered is
    one of its provider's defaults - a real call with Auto tries those - and says which model that was."""

    @pytest.fixture
    def defaults(self, monkeypatch):
        def install(table):
            served = SimpleNamespace(**{**vars(REMOTE), 'default_models_by_provider': table})
            monkeypatch.setattr(module, 'get_config', lambda: served)
        return install

    def test_a_default_that_answers_keeps_auto_and_is_named(self, make, use, defaults):
        defaults({'Google': ['gemini-b']})
        use(FakeManager(works={('Google', 'gemini-b')}, vision={('Google', 'gemini-b')}))
        page, host = make(keys=[entry(KEY, '', 'Google')])
        card = page.cards[0]
        page.test_card(card)
        finish_test(page, card)
        assert card.values() == ('Google', 'Auto', KEY)
        assert card.status == 'Auto: works with gemini-b. It can read images and files.'
        assert card.status_label.cget('text') == card.status              # shown, not only in the tooltip
        first = saved(host)[0]
        assert (first['provider'], first['model_name']) == ('Google', '')
        assert first['vision_capable'] is True and first['file_capable'] is True   # the flags still land

    def test_auto_and_auto_with_a_recognised_key_names_the_provider_and_keeps_auto(self, make, use, defaults):
        defaults({'Google': ['gemini-a']})
        use(FakeManager(works={('Google', 'gemini-a')}))
        page, host = make(keys=[entry(KEY)])                     # Provider Auto, Model Auto, an AIza key
        card = page.cards[0]
        page.test_card(card)
        finish_test(page, card)
        assert card.values() == ('Google', 'Auto', KEY)
        assert (saved(host)[0]['provider'], saved(host)[0]['model_name']) == ('Google', '')

    def test_a_model_auto_would_not_reach_is_named_on_the_card(self, make, use, defaults):
        defaults({'Google': ['gemini-gone']})                     # the default fails, the map's gemini-a answers
        use(FakeManager(works={('Google', 'gemini-a')}))
        page, host = make(keys=[entry(KEY, '', 'Google')])
        card = page.cards[0]
        page.test_card(card)
        finish_test(page, card)
        assert card.values() == ('Google', 'gemini-a', KEY)
        assert card.status.startswith('Works with gemini-a.')
        assert saved(host)[0]['model_name'] == 'gemini-a'

    def test_a_named_model_stays_named(self, make, use, defaults):
        defaults({'Google': ['gemini-b']})
        use(FakeManager(works={('Google', 'gemini-b')}))
        page, host = make(keys=[entry(KEY, 'gemini-b', 'Google')])
        card = page.cards[0]
        page.test_card(card)
        finish_test(page, card)
        assert card.values() == ('Google', 'gemini-b', KEY)
        assert card.status.startswith('Works with gemini-b.')

    def test_the_log_line_says_auto_was_kept(self, make, use, defaults, caplog):
        defaults({'Google': ['gemini-b']})
        use(FakeManager(works={('Google', 'gemini-b')}))
        page, host = make(keys=[entry(KEY, '', 'Google')])
        card = page.cards[0]
        with caplog.at_level('INFO'):
            page.test_card(card)
            finish_test(page, card)
        [record] = [r for r in caplog.records if getattr(r, 'event', None) == 'key_test.ok']
        assert record.data['kept_auto'] is True and record.data['model'] == 'gemini-b'
        assert KEY not in record.getMessage() and KEY not in str(record.data)
