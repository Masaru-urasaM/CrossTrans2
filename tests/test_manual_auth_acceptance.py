"""Private-desktop fake-verifier coverage for the human acceptance recorder."""
import ctypes
import json
from types import SimpleNamespace
import tkinter as tk

import pytest

from tests.final_ui_helpers import design  # noqa: F401
from tests.settings_support import SettingsConfig
from tools.auth_acceptance_evidence import (AcceptanceView, FIXTURE_KEYS, PasswordBackendObserver,
                                            Verification, account_format)


@pytest.fixture
def harness(design):
    window = tk.Toplevel(design.root)
    window.geometry('1100x850')
    outcomes = [Verification(True, method='native-password-dialog', attempts=1),
                Verification(False, cancelled=True)]
    config = SettingsConfig(api_keys=[dict(api_key=key, provider='Auto', model_name='')
                                     for key in sorted(FIXTURE_KEYS)])
    view = AcceptanceView(window, config, lambda parent: outcomes.pop(0))
    window.update()
    yield window, view, outcomes
    view.close()
    window.destroy()


def complete_actions(view):
    view.page.show_all_btn.invoke()
    view.page.show_all_btn.invoke()
    view.page.cards[0].eye.invoke()
    view.reset()
    view.page.show_all_btn.invoke()


def test_real_controls_automatically_record_masks_and_cached_single_reveal_without_native_claim(harness):
    window, view, outcomes = harness
    complete_actions(view)
    report = view.recorder.report()
    assert all(report['checks'].values()) and report['functional_checks_passed']
    assert len(report['verifications']) == 2 and not outcomes
    assert not report['native_password_verified'] and report['status'] == 'incomplete'
    assert report['actions'][0]['after']['unmasked'] == [0, 1]
    assert report['actions'][1]['after']['unmasked'] == []
    assert report['actions'][2]['after']['unmasked'] == [0]
    assert all(key not in json.dumps(report) for key in FIXTURE_KEYS)


def test_mask_mismatch_cannot_be_reported_as_a_successful_ui(harness):
    _, view, _ = harness
    view.page.show_all_btn.invoke()
    view.page.cards[0].field.configure(show='*')
    view.run('inspect', lambda: None)
    report = view.recorder.report()
    assert not report['actions'][-1]['after']['consistent']
    assert not report['functional_checks_passed']


def test_reset_does_not_count_as_a_successful_hide_action(harness):
    _, view, _ = harness
    view.page.show_all_btn.invoke()
    view.reset()
    assert not view.recorder.report()['checks']['hide_without_reauthentication']


def test_complete_actions_with_no_cancellation_remain_incomplete(harness):
    _, view, outcomes = harness
    outcomes[-1] = Verification(False)
    complete_actions(view)
    report = view.recorder.report()
    assert not report['checks']['cancel_keeps_all_masked'] and report['status'] == 'incomplete'


def test_destroyed_cards_and_foreign_field_values_are_not_valid_evidence(harness):
    _, view, _ = harness
    view.page.cards[0].field.configure(state='normal')
    view.page.cards[0].field.set('private-fixture-content')
    view.page.cards[0].field.configure(state='readonly')
    view.run('inspect', lambda: None)
    assert not view.recorder.actions[-1]['after']['fixtures']
    assert 'private-fixture-content' not in json.dumps(view.recorder.report())
    view.page.body.destroy()
    assert not view.recorder.snapshot()['alive']


def test_verifier_error_is_fail_closed_and_report_never_contains_error_text(harness):
    _, view, _ = harness
    def failure(parent):
        raise RuntimeError('sensitive-fixture-details')
    view.recorder.verifier = failure
    view.page.show_all_btn.invoke()
    report = view.recorder.report()
    assert report['errors'] == ['RuntimeError']
    assert not report['actions'][0]['after']['unmasked']
    assert 'sensitive-fixture-details' not in json.dumps(report)


def test_a_native_label_without_successful_os_logon_and_sid_events_is_not_native_proof(harness):
    _, view, _ = harness
    view.recorder.native = True
    complete_actions(view)
    report = view.recorder.report()
    assert report['functional_checks_passed'] and not report['native_password_verified']


def test_close_restores_the_production_verifier(harness):
    from src.ui.settings import page_providers
    _, view, _ = harness
    previous = view.original_verifier
    assert page_providers.require_auth == view.recorder.verify
    view.close()
    assert page_providers.require_auth is previous


@pytest.mark.parametrize('accepted', [True, False])
def test_authentication_dialog_cannot_start_a_second_verification_from_the_parent(harness, accepted):
    _, view, _ = harness
    calls = []
    def verifier(parent):
        calls.append(parent)
        assert view.page._auth_pending
        assert str(view.page.show_all_btn.cget('state')) == 'disabled'
        assert all(str(card.eye.cget('state')) == 'disabled' for card in view.page.cards)
        view.page.toggle_all_keys()
        view.page.reveal_clicked(view.page.cards[0])
        assert not any(card.revealed for card in view.page.cards)
        return Verification(accepted)
    view.recorder.verifier = verifier
    view.page.show_all_btn.invoke()
    assert len(calls) == 1 and not view.page._auth_pending
    assert all(card.revealed == accepted for card in view.page.cards)
    assert str(view.page.show_all_btn.cget('state')) == 'normal'
    assert all(str(card.eye.cget('state')) == 'normal' for card in view.page.cards)


def test_pending_authentication_state_is_released_on_an_unexpected_verifier_exception(harness, monkeypatch):
    from src.ui.settings import page_providers
    _, view, _ = harness
    def failure(parent):
        raise RuntimeError('fixture')
    monkeypatch.setattr(page_providers, 'require_auth', failure)
    with pytest.raises(RuntimeError):
        view.page.toggle_all_keys()
    assert not view.page._auth_pending and not view.page._authenticated
    assert not any(card.revealed for card in view.page.cards)
    assert str(view.page.show_all_btn.cget('state')) == 'normal'


def test_fixture_edit_controls_stay_disabled_while_production_authentication_updates_them(harness):
    _, view, _ = harness
    def verifier(parent):
        assert str(view.page.add_btn.cget('state')) == 'disabled'
        assert str(view.page.delete_all_btn.cget('state')) == 'disabled'
        assert all(str(card.field.cget('state')) == 'readonly' for card in view.page.cards)
        return Verification(True)
    view.recorder.verifier = verifier
    view.page.show_all_btn.invoke()
    assert all(view.recorder.report()['actions'][0]['after'][key] for key in ('consistent', 'fixtures'))


@pytest.mark.parametrize('value,expected', [('HOST\\fixture','SAM'), ('fixture@example.invalid','UPN'),
    ('MicrosoftAccount\\fixture@example.invalid','MicrosoftAccount'), ('fixture','unqualified')])
def test_account_format_retains_only_the_shape(value, expected):
    assert account_format(value) == expected


def test_native_backend_observer_passes_arguments_unchanged_and_records_only_outcomes(monkeypatch):
    from src.core import windows_identity
    calls = []
    class Backend:
        def __init__(self):
            def rejected(*args):
                calls.append(args)
                ctypes.set_last_error(1326)
                return 0
            self.names = SimpleNamespace(GetUserNameExW=lambda *args: 1)
            self.security = SimpleNamespace(OpenProcessToken=lambda *args: 1, LogonUserW=rejected,
                                            EqualSid=lambda *args: 1)
    monkeypatch.setattr(windows_identity, 'WindowsAccounts', Backend)
    with PasswordBackendObserver() as observer:
        client = windows_identity.WindowsAccounts()
        client.names.GetUserNameExW(2, 'private-name-buffer', 1)
        assert client.security.LogonUserW('private-account','private-domain','private-password') == 0
        assert client.security.EqualSid('private-sid-1','private-sid-2') == 1
    events = observer.drain()
    assert calls == [('private-account','private-domain','private-password')]
    assert events[1] == dict(operation='LogonUserW', accepted=False, error_code=1326)
    assert 'private-' not in json.dumps(events)
    assert windows_identity.WindowsAccounts is Backend


def test_cli_without_interactive_flag_does_not_create_a_window():
    from tools.manual_auth_acceptance import main
    with pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 2
