"""Explicit private update access must not leak credentials or inherit CLI login."""
import hashlib
import io
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.error
import urllib.request

import pytest
import src.core
from config import Config
from src.core.crypto import SecureStorage
from src.utils import github_updates as access, updates
from tests.settings_support import SettingsConfig
from tests.test_update_notifications import Root

TOKEN = 'test_update_credential'
ASSET = access.API_ROOT + '/releases/assets/123'


class Response(io.BytesIO):
    status = 200
    headers = {}


def memory_config():
    config = Config.__new__(Config)
    config._config = {}
    config.save = Mock()
    return config


def test_token_is_encrypted_separately_and_removed_without_changing_ai_keys():
    config = memory_config()
    config._config['api_keys'] = [{'api_key': 'existing-ai'}]
    config.set_github_update_token(TOKEN)
    stored = config._config['github_update_token_encrypted']
    assert TOKEN not in stored and config.get_github_update_token() == TOKEN
    assert TOKEN not in json.dumps(config._config)
    config.set_github_update_token('')
    assert 'github_update_token_encrypted' not in config._config
    assert config.get_github_update_token() == ''
    assert config._config['api_keys'] == [{'api_key': 'existing-ai'}]


def test_encryption_failure_keeps_previous_token_and_never_saves_plaintext(monkeypatch):
    config = memory_config()
    config.set_github_update_token(TOKEN)
    before = dict(config._config)
    config.save.reset_mock()
    monkeypatch.setattr(SecureStorage, 'encrypt', lambda value: None)
    with pytest.raises(ValueError, match='encrypt'):
        config.set_github_update_token('replacement')
    assert config._config == before
    config.save.assert_not_called()


def test_write_failure_restores_memory_value():
    config = memory_config()
    config.set_github_update_token(TOKEN)
    before = dict(config._config)
    config.save.side_effect = OSError('denied')
    with pytest.raises(OSError):
        config.set_github_update_token('replacement')
    assert config._config == before


@pytest.mark.parametrize('token', ['secret\r\nAuthorization: bad', 'a b', '秘密'])
def test_bad_header_values_are_rejected_without_echo(token):
    with pytest.raises(ValueError) as error:
        access.validate_token(token)
    assert token not in str(error.value)


def test_missing_token_makes_no_request_and_does_not_use_environment(monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', TOKEN)
    monkeypatch.setenv('GH_TOKEN', TOKEN)
    opener = Mock()
    monkeypatch.setattr(updates, 'open_request', opener)
    result = updates.AutoUpdater(require_auth=True).check_update()
    assert result['requires_auth'] and result['error'] == access.TOKEN_REQUIRED
    opener.assert_not_called()


def test_private_metadata_and_binary_download_use_the_api_token_and_digest(monkeypatch, tmp_path):
    binary = b'verified-installer-bytes'
    metadata = {'tag_name': 'v9.9.9', 'body': '', 'assets': [{
        'name': 'CrossTrans2Setup_v9.9.9.exe', 'url': ASSET,
        'browser_download_url': 'https://github.com/not-authorized-browser-path',
        'digest': 'sha256:' + hashlib.sha256(binary).hexdigest()}]}
    seen = []
    def open_request(url, **kwargs):
        seen.append((url, kwargs))
        return Response(binary if kwargs.get('binary') else json.dumps(metadata).encode())
    monkeypatch.setattr(updates, 'open_request', open_request)
    monkeypatch.setattr(updates.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(updates.tempfile, 'mkdtemp', lambda **kwargs: str(tmp_path))
    updater = updates.AutoUpdater(access_token=TOKEN, require_auth=True)
    assert updater.check_update(max_retries=1)['has_update']
    assert updater.exe_url == ASSET
    assert updater.download()['success']
    assert seen[0][1]['token'] == seen[1][1]['token'] == TOKEN
    assert seen[1][1]['binary'] is True
    assert (tmp_path / 'CrossTrans2Setup_v9.9.9.exe').read_bytes() == binary


@pytest.mark.parametrize('code', [401, 403, 404])
def test_private_errors_are_actionable_and_never_log_response_secrets(monkeypatch, caplog, code):
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(ASSET, code, 'denied', {}, io.BytesIO(TOKEN.encode()))
    monkeypatch.setattr(updates, 'open_request', fail)
    updater = updates.AutoUpdater(access_token=TOKEN, require_auth=True)
    result = updater.check_update(max_retries=1)
    assert result['error'] == (access.NO_RELEASE if code == 404 else access.ACCESS_DENIED)
    assert TOKEN not in caplog.text and TOKEN not in str(result)


def test_token_is_only_attached_to_selected_repository_api(monkeypatch):
    opener = Mock()
    monkeypatch.setattr(access.urllib.request, 'build_opener', Mock(return_value=opener))
    access.open_request(ASSET, token=TOKEN, binary=True)
    request = opener.open.call_args.args[0]
    assert request.get_header('Authorization') == 'Bearer ' + TOKEN
    assert request.get_header('Accept') == 'application/octet-stream'
    with pytest.raises(ValueError):
        access.open_request('https://evil.invalid/asset', token=TOKEN)
    with pytest.raises(ValueError):
        access.open_request('https://api.github.com/repos/other/repo/asset', token=TOKEN)


@pytest.mark.parametrize('target,retains', [
    (ASSET + '?next=1', True),
    ('https://release-assets.githubusercontent.com/signed-binary', False),
    ('https://evil.invalid/asset', False),
])
def test_redirect_keeps_auth_only_at_the_original_origin(target, retains):
    request = urllib.request.Request(ASSET, headers={'Authorization': 'Bearer ' + TOKEN})
    redirected = access.SafeRedirect().redirect_request(request, None, 302, 'found', {}, target)
    assert bool(redirected.get_header('Authorization')) is retains


@pytest.mark.parametrize('target', ['http://api.github.com/asset', 'https://user:password@api.github.com/asset'])
def test_insecure_redirects_are_rejected(target):
    request = urllib.request.Request(ASSET, headers={'Authorization': 'Bearer ' + TOKEN})
    with pytest.raises(ValueError):
        access.SafeRedirect().redirect_request(request, None, 302, 'found', {}, target)


def test_controller_reads_token_on_ui_thread_and_invalidates_old_result(monkeypatch):
    from src.core import update_ui_manager as ui_updates
    root, config = Root(), SettingsConfig(auto_check_updates=False)
    owner = threading.get_ident()
    def read_token():
        assert threading.get_ident() == owner
        return TOKEN
    config.get_github_update_token = read_token
    updater = Mock()
    updater.check_update.return_value = {'has_update': True, 'version': '9.9.9'}
    factory = Mock(return_value=updater)
    monkeypatch.setattr(ui_updates, 'AutoUpdater', factory)
    manager = ui_updates.UpdateUIManager(root, config, Mock())
    callback = Mock()
    manager.request_check(callback)
    # An old result is already queued, but has not been delivered to Tk.
    deadline = time.monotonic() + 2
    while manager._answers.empty() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not manager._answers.empty()
    manager.credentials_changed()
    root.tick()
    factory.assert_called_once_with(access_token=TOKEN, require_auth=False)
    assert manager.available_version is None
    callback.assert_not_called()
    manager.stop()


def test_public_metadata_and_binary_download_work_without_a_token(monkeypatch, tmp_path):
    binary = b'public-verified-installer-bytes'
    public_url = 'https://github.com/Masaru-urasaM/CrossTrans2/releases/download/v9.9.9/CrossTrans2Setup_v9.9.9.exe'
    metadata = {'tag_name': 'v9.9.9', 'body': '', 'assets': [{
        'name': 'CrossTrans2Setup_v9.9.9.exe', 'url': ASSET,
        'browser_download_url': public_url,
        'digest': 'sha256:' + hashlib.sha256(binary).hexdigest()}]}
    seen = []
    def open_request(url, **kwargs):
        seen.append((url, kwargs))
        return Response(binary if kwargs.get('binary') else json.dumps(metadata).encode())
    monkeypatch.setattr(updates, 'open_request', open_request)
    monkeypatch.setattr(updates.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(updates.tempfile, 'mkdtemp', lambda **kwargs: str(tmp_path))
    updater = updates.AutoUpdater(access_token='', require_auth=False)
    result = updater.check_update(max_retries=1)
    assert result['has_update'] is True and result['version'] == '9.9.9'
    assert updater.exe_url == public_url
    assert updater.download()['success'] is True
    assert seen[0][1]['token'] == '' and seen[1][1]['token'] == ''
    assert seen[1][0] == public_url and seen[1][1]['binary'] is True
    assert (tmp_path / 'CrossTrans2Setup_v9.9.9.exe').read_bytes() == binary


def test_controller_uses_public_updates_when_no_token_is_saved(monkeypatch):
    from src.core import update_ui_manager as ui_updates
    root, config = Root(), SettingsConfig(auto_check_updates=False)
    updater = Mock()
    updater.check_update.return_value = {'has_update': False, 'version': '2.0.0'}
    factory = Mock(return_value=updater)
    monkeypatch.setattr(ui_updates, 'AutoUpdater', factory)
    manager = ui_updates.UpdateUIManager(root, config, Mock())
    seen = []
    manager.request_check(lambda result, inst: seen.append((result, inst)))
    deadline = time.monotonic() + 2
    while not seen and time.monotonic() < deadline:
        root.tick()
        time.sleep(0.005)
    factory.assert_called_once_with(access_token='', require_auth=False)
    assert seen and seen[0][0]['has_update'] is False
    manager.stop()
