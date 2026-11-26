"""Authentication orchestration uses fake OS outcomes; no real password or Hello prompt."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core import auth, windows_identity


@pytest.mark.parametrize('empty,available,hello,password,expected', [
    (True, True, False, False, True),
    (False, True, True, False, True),
    (False, True, False, True, True),
    (False, True, False, False, False),
    (False, False, False, True, True),
    (False, False, False, False, False),
])
def test_hello_password_fallback_and_cancel_cannot_grant_access(monkeypatch, empty, available, hello, password, expected):
    parent = object()
    verify = Mock(return_value=empty)
    consent = Mock(return_value=hello)
    dialog = Mock(return_value=SimpleNamespace(result=password))
    monkeypatch.setattr(auth, 'verify_password', verify)
    monkeypatch.setattr(auth.WindowsHelloAuth, 'is_available', lambda: available)
    monkeypatch.setattr(auth, '_run_windows_hello', consent)
    monkeypatch.setattr(auth, 'PasswordDialog', dialog)
    assert auth.require_auth(parent) is expected
    verify.assert_called_once_with('')
    assert consent.call_count == int(not empty and available)
    assert dialog.call_count == int(not empty and not (available and hello))


def test_password_is_checked_against_the_selected_account_without_logging(monkeypatch, caplog):
    verifier = Mock(return_value=True)
    monkeypatch.setattr(auth, 'verify_current_password', verifier)
    password, account = 'Synthetic secret, never a real password', 'LOCAL\\fixture'
    assert auth.verify_password(password, account)
    verifier.assert_called_once_with(password, account)
    assert password not in caplog.text


def test_failed_os_identity_lookup_is_not_success(monkeypatch):
    def unavailable():
        raise OSError('Synthetic account lookup failure')
    monkeypatch.setattr(windows_identity.sys, 'platform', 'win32')
    monkeypatch.setattr(windows_identity, 'WindowsAccounts', unavailable)
    assert not windows_identity.verify_current_password('Synthetic fixture')
