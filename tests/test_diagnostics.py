"""Tests for privacy-safe diagnostics, bug-report export, startup error surfacing, and Settings UI."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest

import main
from src.app import TranslatorApp
from src.core import settings_index as index
from src.ui.diagnostics_dialog import DiagnosticsDialog
from src.ui.settings import page_about
from src.ui.tray import TrayManager
from src.utils import diagnostics, logging_setup
from tests.final_ui_helpers import design  # noqa: F401
from tests.settings_support import Host, SettingsConfig


def test_redact_text_strips_api_keys_tokens_and_auth_headers():
    sample = (
        'Header Authorization: Bearer super-secret-jwt-value-12345\n'
        'x-goog-api-key: AIzaSyDummyKeyValue1234567890abcdef\n'
        'openai=sk-proj-secret1234567890abcdef\n'
        'groq=gsk_1234567890abcdefghijklmnop\n'
        'github=ghp_1234567890abcdefghijklmnop\n'
    )
    cleaned = diagnostics.redact_text(sample)
    for leaked in ('super-secret-jwt-value', 'AIzaSy', 'sk-proj-', 'gsk_1234', 'ghp_1234'):
        assert leaked not in cleaned
    assert '[REDACTED]' in cleaned


def test_sanitize_config_snapshot_never_exposes_secrets_or_history():
    cfg = SettingsConfig(
        api_keys=[{'provider': 'Groq', 'model_name': 'llama-3.3', 'api_key': 'gsk_real_secret_key_999'}],
        audio_keys=[{'provider': 'OpenAI', 'model_name': 'whisper-1', 'api_key_encrypted': 'enc_blob_xyz'}],
        history=[{'original': 'private user text', 'translated': 'secret output'}],
        github_update_token_encrypted='gh_enc_123',
        debug_logging=True,
    )
    snap = diagnostics.sanitize_config_snapshot(cfg)
    raw_dump = json.dumps(snap)
    assert 'gsk_real_secret_key_999' not in raw_dump
    assert 'enc_blob_xyz' not in raw_dump
    assert 'gh_enc_123' not in raw_dump
    assert 'private user text' not in raw_dump
    assert snap['api_keys_count'] == 1
    assert snap['api_keys'][0]['configured'] is True
    assert snap['has_github_update_token'] is True
    assert snap['debug_logging'] is True


def test_export_diagnostic_bundle_creates_zip_with_sanitized_logs(tmp_path, monkeypatch):
    log_dir = tmp_path / 'logs'
    log_dir.mkdir()
    (log_dir / 'crash_report.txt').write_text(
        'Stage: startup\ntoken=ghp_secretvalue1234567890\n', encoding='utf-8'
    )
    (log_dir / 'translator_20261110.log').write_text(
        json.dumps({'ts': '2026-11-10T10:00:00+09:00', 'level': 'ERROR', 'tag': 'API', 'msg': 'sk-secretkeyvalue123456'}) + '\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(diagnostics, 'resolve_log_dir', lambda: str(log_dir))
    monkeypatch.setattr(logging_setup, 'resolve_log_dir', lambda: str(log_dir))

    cfg = SettingsConfig(api_keys=[{'provider': 'OpenAI', 'model_name': 'gpt-4o', 'api_key': 'sk-dontleakme'}])
    out_zip = tmp_path / 'out' / 'bug_report.zip'
    result = diagnostics.export_diagnostic_bundle(out_zip, config=cfg, shortcut_status={'English': 'ok'})

    assert result == out_zip and out_zip.is_file()
    with zipfile.ZipFile(out_zip, 'r') as zf:
        names = set(zf.namelist())
        assert {'summary.txt', 'environment.json', 'logs/crash_report.txt', 'logs/translator_20261110.log'} <= names
        summary = zf.read('summary.txt').decode('utf-8')
        assert 'Shortcut registration status:' in summary
        assert 'English: ok' in summary
        for member in names:
            body = zf.read(member).decode('utf-8')
            assert 'ghp_secretvalue' not in body
            assert 'sk-secretkeyvalue' not in body
            assert 'sk-dontleakme' not in body


def test_build_github_issue_url_is_bounded_and_redacted():
    url = diagnostics.build_github_issue_url('key=sk-1234567890abcdefghijklmnop\n' + ('line\n' * 500))
    assert url.startswith('https://github.com/Masaru-urasaM/CrossTrans2/issues/new?')
    assert 'sk-1234567890' not in url
    assert len(url) < 4000


def test_diagnostics_dialog_actions_work_without_touching_session(design, tmp_path, monkeypatch):
    log_dir = tmp_path / 'logs'
    log_dir.mkdir()
    monkeypatch.setattr(diagnostics, 'resolve_log_dir', lambda: str(log_dir))
    monkeypatch.setattr(logging_setup, 'resolve_log_dir', lambda: str(log_dir))

    started = []
    opened_urls = []
    monkeypatch.setattr('os.startfile', lambda p: started.append(p))
    monkeypatch.setattr('webbrowser.open', lambda u: opened_urls.append(u))
    export_target = tmp_path / 'bundle.zip'
    monkeypatch.setattr('src.ui.diagnostics_dialog.filedialog.asksaveasfilename', lambda **_kw: str(export_target))

    cfg = SettingsConfig()
    dlg = DiagnosticsDialog(design.root, config=cfg, shortcut_status={'English': 'ok'})
    try:
        design.manager.set_ui_language('ja')
        design.manager.set_mode('dark')
        design.root.update()

        dlg.copy_report()
        assert 'CrossTrans2 Diagnostic Report' in dlg.window.clipboard_get()

        saved = dlg.export_bundle()
        assert saved == str(export_target) and export_target.is_file()

        dlg.open_logs()
        assert started == [str(log_dir)]

        dlg.report_on_github()
        assert len(opened_urls) == 1 and 'Diagnostic' in opened_urls[0]
    finally:
        dlg.view.close()


def test_about_page_exposes_diagnostics_dialog_and_debug_toggle(design, tmp_path, monkeypatch):
    monkeypatch.setattr(logging_setup, 'resolve_log_dir', lambda: str(tmp_path / 'logs'))
    host = Host(design.root, SettingsConfig())
    page = page_about.AboutPage(host)
    page.build(design.root)
    page.body.pack(fill='both', expand=True)
    design.root.update()

    assert page.row_for('diagnostics') is not None
    assert page.row_for('debug_logging') is not None
    assert 'diagnostics' in index.entry('diagnostics').key

    page.switches['debug_logging'].invoke()
    design.root.update()
    assert host.config.get('debug_logging') is True
    assert host.changes[-1] == 'debug_logging'
    assert (tmp_path / 'logs' / logging_setup.DEBUG_FLAG_FILENAME).is_file()

    dlg = page.open_diagnostics()
    try:
        design.root.update()
        assert dlg.window.winfo_exists()
    finally:
        dlg.view.close()


def test_main_startup_failure_writes_crash_report_and_notifies_user(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'setup_logging', lambda **_kw: str(tmp_path / 't.log'))
    monkeypatch.setattr(main, 'is_already_running', lambda: (False, None))
    monkeypatch.setattr(main, 'write_crash_report', lambda *a, **kw: str(tmp_path / 'crash_report.txt'))
    monkeypatch.setattr(main, 'build_crash_report_text', lambda *a, **kw: 'REPORT BODY')

    notified = []
    monkeypatch.setattr(diagnostics, 'notify_fatal_error', lambda *a, **kw: notified.append((a, kw)))

    class BrokenApp:
        def __init__(self):
            raise RuntimeError('Unable to load tkdnd library.')

    monkeypatch.setattr('src.app.TranslatorApp', BrokenApp)
    assert main.main([]) == 1
    assert len(notified) == 1
    assert 'Unable to load tkdnd library.' in notified[0][0][1]
    assert notified[0][1]['report_text'] == 'REPORT BODY'


def test_frozen_version_upgrade_updates_version_without_restarting(monkeypatch):
    app = TranslatorApp.__new__(TranslatorApp)
    saved = []
    app.config = SimpleNamespace(
        get_last_run_version=lambda: '1.9.21',
        set_last_run_version=lambda v: saved.append(v),
    )
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(app, '_clear_caches_and_restart', lambda: pytest.fail('must not restart when frozen'))

    assert app._check_version_upgrade() is False
    assert len(saved) == 1


def test_tray_menu_includes_open_logs_with_two_separators():
    from pystray import Menu
    cfg = SettingsConfig()
    tray = TrayManager(cfg)
    opened = []
    tray.configure_callbacks(on_open_logs=lambda: opened.append(1))
    items = tray._build_menu_items()
    assert items.count(Menu.SEPARATOR) == 2
    logs_item = next(i for i in items if getattr(i, 'text', None) == 'Open Logs')
    logs_item._action(tray.tray_icon, logs_item)
    assert opened == [1]
