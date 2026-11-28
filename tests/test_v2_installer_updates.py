"""Only verified 2.x installer assets may replace the installed application."""
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.utils import updates
from src.core.update_ui_manager import UpdateUIManager


def test_release_selects_the_installer_instead_of_a_raw_executable(monkeypatch):
    metadata = dict(tag_name='v9.9.9', body='', assets=[
        dict(name='CrossTrans2.exe', browser_download_url='https://example.com/raw.exe', digest='sha256:' + 'a' * 64),
        dict(name='CrossTrans2Setup_v9.9.9.exe', browser_download_url='https://example.com/setup.exe', digest='sha256:' + 'b' * 64)])
    class Response(io.BytesIO):
        status = 200
        headers = {}
    monkeypatch.setattr(updates.urllib.request, 'urlopen', lambda *a, **k: Response(json.dumps(metadata).encode()))
    updater = updates.AutoUpdater()
    assert updater.check_update(max_retries=1)['has_update']
    assert updater.exe_url == 'https://example.com/setup.exe'
    assert updater.expected_sha256 == 'b' * 64


def test_install_rechecks_digest_then_launches_the_installer_without_a_batch(monkeypatch, tmp_path):
    file = tmp_path / 'CrossTrans2Setup_v9.9.9.exe'
    file.write_bytes(b'fixture-installer')
    updater = updates.AutoUpdater()
    updater.download_path = str(file)
    updater.expected_sha256 = hashlib.sha256(file.read_bytes()).hexdigest()
    launch, exit_app = Mock(), Mock()
    monkeypatch.setattr(updates, 'launch_installer', launch)
    monkeypatch.setattr(updates.os, '_exit', exit_app)
    monkeypatch.setattr(updates.logging, 'shutdown', Mock())
    monkeypatch.setattr(updates.subprocess, 'Popen', Mock(side_effect=AssertionError('No batch subprocess')))
    assert updater.install_and_restart() == {'success': True, 'pending': True}
    exit_app.assert_not_called()
    assert not updater.confirm_install_and_restart()
    updater._handoff_file.write_text(updater._handoff_token, encoding='utf-8')
    assert updater.confirm_install_and_restart()
    assert launch.call_args.args[0] == str(file)
    assert '/UPDATE=1' in launch.call_args.args[1] and '/NORESTART' in launch.call_args.args[1]
    assert '/WAITPID=' in launch.call_args.args[1]
    exit_app.assert_called_once_with(0)
    assert list(tmp_path.iterdir()) == [file]


def test_modified_or_unverified_installer_is_never_started(monkeypatch, tmp_path):
    file = tmp_path / 'setup.exe'
    file.write_bytes(b'changed fixture')
    updater = updates.AutoUpdater()
    updater.download_path = str(file)
    launch = Mock()
    monkeypatch.setattr(updates, 'launch_installer', launch)
    assert not updater.install_and_restart()['success']
    updater.expected_sha256 = '0' * 64
    assert not updater.install_and_restart()['success']
    launch.assert_not_called()


def test_v2_never_consumes_v1_update_status_files(monkeypatch, tmp_path):
    old = tmp_path / 'crosstrans_update_error.txt'
    old.write_text('fixture-v1-marker', encoding='utf-8')
    monkeypatch.setenv('TEMP', str(tmp_path))
    manager = UpdateUIManager(Mock(), Mock(), Mock())
    manager.check_update_status()
    assert old.read_text(encoding='utf-8') == 'fixture-v1-marker'
    assert manager._pending_update_error is None
    assert not hasattr(updates.AutoUpdater, 'schedule_update_on_reboot')


def test_installer_uses_all_users_identity_and_preserves_personal_data():
    source = (Path(__file__).resolve().parents[1] / 'installer' / 'CrossTrans2.iss').read_text(encoding='utf-8')
    assert 'PrivilegesRequired=admin' in source
    assert 'DefaultDirName={autopf}' in source
    assert 'CrossTrans2.exe' in source and 'runasoriginaluser' in source
    assert '[UninstallDelete]' not in source
    assert '{userappdata}' not in source and '{localappdata}' not in source


def test_cancelling_restart_removes_readiness_without_exiting(monkeypatch, tmp_path):
    updater = updates.AutoUpdater()
    updater._handoff_file = tmp_path / 'ready'
    updater._handoff_token = 'fixture-token'
    updater._handoff_file.write_text(updater._handoff_token)
    exit_app = Mock()
    monkeypatch.setattr(updates.os, '_exit', exit_app)
    updater.cancel_install_handoff()
    assert not updater.confirm_install_and_restart()
    exit_app.assert_not_called()
    assert tmp_path.exists()


def test_cancel_and_failed_launch_remove_empty_handoff_temp_dir(monkeypatch, tmp_path):
    handoff_dir = tmp_path / 'crosstrans2_handoff_abc'
    handoff_dir.mkdir()
    updater = updates.AutoUpdater()
    updater._handoff_file = handoff_dir / 'ready'
    updater._handoff_token = 'fixture-token'
    updater._handoff_file.write_text(updater._handoff_token)
    updater.cancel_install_handoff()
    assert not handoff_dir.exists()

    installer = tmp_path / 'CrossTrans2Setup_v9.9.9.exe'
    installer.write_bytes(b'fixture-installer')
    updater.download_path = str(installer)
    updater.expected_sha256 = hashlib.sha256(installer.read_bytes()).hexdigest()
    monkeypatch.setattr(updates.tempfile, 'mkdtemp', lambda prefix='': str(tmp_path / f'{prefix}fail'))
    (tmp_path / 'crosstrans2_handoff_fail').mkdir()
    monkeypatch.setattr(updates, 'launch_installer', Mock(side_effect=OSError('UAC cancelled')))
    result = updater.install_and_restart()
    assert not result['success']
    assert not (tmp_path / 'crosstrans2_handoff_fail').exists()


@pytest.mark.parametrize('scenario', ['mismatch', 'missing_digest', 'network_error'])
def test_failed_download_removes_empty_update_temp_dir(monkeypatch, tmp_path, scenario):
    update_dir = tmp_path / f'crosstrans2_update_{scenario}'
    update_dir.mkdir()
    updater = updates.AutoUpdater()
    updater.exe_url = 'https://example.com/setup.exe'
    updater.latest_version = '9.9.9'
    updater.expected_sha256 = '' if scenario == 'missing_digest' else '0' * 64
    monkeypatch.setattr(updates.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(updates.tempfile, 'mkdtemp', lambda prefix='': str(update_dir))
    monkeypatch.setattr(updates, 'get_ssl_context_for_url', lambda _url: None)

    class Response(io.BytesIO):
        headers = {'Content-Length': '12'}
        def __enter__(self): return self
        def __exit__(self, *args): return False

    if scenario == 'network_error':
        monkeypatch.setattr(updates, 'open_request', Mock(side_effect=OSError('connection reset')))
    else:
        monkeypatch.setattr(updates, 'open_request', lambda *a, **k: Response(b'corrupt-data'))

    result = updater.download()
    assert not result['success']
    assert updater.download_path is None
    assert not update_dir.exists()


def test_release_version_is_consistent_across_app_installer_and_workflow():
    from src.constants import VERSION
    root = Path(__file__).resolve().parents[1]
    major, minor, patch = (int(part) for part in VERSION.split('.'))
    iss = (root / 'installer' / 'CrossTrans2.iss').read_text(encoding='utf-8')
    ver_info = (root / 'installer' / 'version_info.txt').read_text(encoding='utf-8')
    build_ps1 = (root / 'tools' / 'build_installer.ps1').read_text(encoding='utf-8')
    workflow = (root / '.github' / 'workflows' / 'installer.yml').read_text(encoding='utf-8')
    assert f'#define AppVersion "{VERSION}"' in iss
    assert f'filevers=({major}, {minor}, {patch}, 0), prodvers=({major}, {minor}, {patch}, 0)' in ver_info
    assert f"StringStruct('FileVersion', '{VERSION}.0')" in ver_info
    assert f"StringStruct('ProductVersion', '{VERSION}')" in ver_info
    assert f'$AppVersion = "{VERSION}"' in build_ps1
    assert f'CrossTrans2Setup_v{VERSION}.exe' in workflow
    assert f"'candidate-{VERSION}-'" in workflow
