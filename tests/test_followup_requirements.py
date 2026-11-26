"""End-to-end contracts for the follow-up Home and Settings requirements."""
import base64
import ctypes
import io
import json
import os
from pathlib import Path
import platform
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.error

import pytest
from PIL import Image

from src.core.file_processor import FileProcessor
from src.core.text_files import TEXT_EXTENSIONS, read_text_file
from src.core.api_manager import AIAPIManager
from src.core import key_testing, windows_identity
from src.ui import components as ui
from src.ui.settings.pages_basic import TranslationPage
from tests.final_ui_helpers import design, pump
from tests.settings_support import Host, SettingsConfig
from tests.test_source_choice import main_window
from tests.test_v2_providers import make


def test_audit_permits_only_the_requested_last_used_typography():
    from tools.ui_audit import scan_text
    assert not scan_text("label = Label(parent, '← Last used')")['symbols']
    assert scan_text("label = Label(parent, '← Back')")['symbols']


@pytest.mark.parametrize('suffix', [*TEXT_EXTENSIONS, '.unknown', ''])
@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'utf-16', 'cp932'])
def test_text_content_is_supported_independently_of_suffix(tmp_path, suffix, encoding):
    path = tmp_path / ('日本語 notes' + suffix)
    source = '# Meeting\r\nこんにちは世界\r\nHello world\n'
    path.write_bytes(source.encode(encoding))
    assert FileProcessor(Mock()).extract_text(str(path)) == source


@pytest.mark.parametrize('suffix', ['.md', '.txt', '.unknown', ''])
def test_binary_never_becomes_an_attachment_text(tmp_path, suffix):
    path = tmp_path / ('binary' + suffix)
    path.write_bytes(b'\x89PNG\r\n\x1a\n\x00\xff\x00')
    with pytest.raises(ValueError):
        read_text_file(path)


def test_markdown_drop_and_browse_reach_extraction(design, tmp_path, monkeypatch):
    from src.ui import attachments
    from tkinter import messagebox
    paths = [tmp_path / name for name in ('notes.MD', 'README', 'unknown.payload')]
    for path in paths:
        path.write_bytes(b'# Meeting\nHello world')
    area = attachments.AttachmentArea(design.root, SettingsConfig(file_processing_enabled=True))
    area.pack(fill='x')
    monkeypatch.setattr(messagebox, 'showwarning', Mock())
    area._on_drop(SimpleNamespace(data=' '.join('{' + str(path) + '}' for path in paths)))
    assert [entry['path'] for entry in area.get_attachments()] == list(map(str, paths))
    monkeypatch.setattr(attachments.filedialog, 'askopenfilenames', lambda **kw: tuple(map(str, paths)))
    area.clear()
    area._browse_documents()
    assert len(area.get_attachments()) == 3
    assert all(FileProcessor(Mock()).extract_text(entry['path']) == '# Meeting\nHello world'
               for entry in area.get_attachments())


def test_placeholders_counts_and_option_wrapper_are_not_canonical_data(main_window):
    app = main_window('', '', furigana=False)
    view = app._view
    view.sync_empty('', '')
    assert view.original_text.get('1.0', 'end-1c') == app.trans_text.get_plain() == ''
    assert view.input_placeholder.winfo_manager() == view.output_placeholder.winfo_manager() == 'place'
    view.original_text.insert('1.0', 'Hello world')
    view.sync_empty('Hello world', '')
    view.set_count(11)
    assert not view.input_placeholder.winfo_manager()
    assert view.count_label.cget('text') == '11 characters · 2 words'
    for _ in range(10):
        view.show_options(True)
        assert view.options.winfo_manager() == 'pack'
        view.custom_prompt.set('Preserve names')
        view.show_options(False)
        assert not view.options.winfo_manager()
    assert view.custom_instruction() == 'Preserve names'
    assert view.original_text.get('1.0', 'end-1c') == 'Hello world'
    assert app.trans_text.get_plain() == ''


def test_linked_branch_hides_its_separator_and_preserves_preference(design):
    host = Host(design.root, SettingsConfig(linked_reading_enabled=True, show_linked_reading=True))
    page = TranslationPage(host)
    page.build(design.root).pack(fill='both', expand=True)
    assert page.rows['linked_reading'].title_label.cget('text') == 'Show Linked reading'
    assert page.linked_button_row.title_label.cget('text') == 'Show Linked reading button'
    page.switches['linked_reading'].invoke()
    assert not page.linked_button_row.visible
    assert not page.linked_button_row.separator.winfo_manager()
    assert host.config.get_show_linked_reading()
    assert page.row_for('show_linked_reading') is page.rows['linked_reading']
    page.switches['linked_reading'].invoke()
    assert page.linked_button_row.visible and page.variables['show_linked_reading'].get()


def test_shortcut_confirmation_no_yes_and_danger_live_theme(design):
    changes = []
    editor = ui.ShortcutEditor(design.root, 'ctrl+alt+j', owner='Japanese', on_change=changes.append)
    editor.pack()
    editor.clear_btn.invoke()
    editor.clear_dialog._button_list[0].invoke()
    assert editor.value == 'ctrl+alt+j' and changes == []
    editor.clear_btn.invoke()
    editor.clear_dialog.cancel()
    assert editor.value == 'ctrl+alt+j' and changes == []
    for theme in ('pink', 'blue', 'yellow', 'green', 'light', 'dark'):
        design.manager.set_mode(theme)
        assert str(design.ctx.icon('x-lg', editor.clear_btn.size, design.manager.palette.danger)) in str(
            editor.clear_btn.cget('image'))
    editor.clear_btn.invoke()
    editor.clear_dialog._button_list[1].invoke()
    assert editor.value == '' and changes == ['']


def test_status_lengths_do_not_move_key_fields_and_last_used_follows_model(design):
    page, host = make(design, count=3)
    page.body.winfo_toplevel().geometry('1250x800')
    design.root.update()
    for card, status in zip(page.cards, ('', 'OK 18:20', 'A long provider error that must wrap in its own slot.')):
        card.set_status(status)
        card.is_last_success = card is page.cards[0]
        card._wrap()
    design.root.update()
    origins = [tuple(widget.winfo_rootx() for widget in (card.field, card.eye, card.test_btn, card.status_slot))
               for card in page.cards]
    assert all(max(column) - min(column) <= 2 for column in zip(*origins))
    first = page.cards[0]
    assert first.last_used_label.winfo_rootx() >= first.model.winfo_rootx() + first.model.winfo_width()
    assert abs(first.last_used_label.winfo_rootx() - first.status_slot.winfo_rootx()) <= 2


def test_pointer_drag_requires_motion_and_has_masked_ghost_and_cleanup(design):
    page, host = make(design, count=3)
    card = page.cards[0]
    pump(design.root, lambda: bool(page.keys_box.winfo_ismapped()))
    card.set_revealed(True)
    x, y = card.winfo_rootx() + 5, card.winfo_rooty() + 5
    page._begin_drag(card, SimpleNamespace(x_root=x, y_root=y))
    assert page._drag_card is None
    page._drag_motion(SimpleNamespace(x_root=x + 20, y_root=y + 30))
    assert page._drag_card is card and page._drag_ghost is not None
    def texts(widget):
        result = []
        try:
            result.append(str(widget.cget('text')))
        except Exception:
            pass
        for child in widget.winfo_children():
            result.extend(texts(child))
        return result
    assert card.key_text not in '\n'.join(texts(page._drag_ghost))
    assert page._drop_marker.winfo_reqheight() >= card.winfo_height()
    before = [entry['id'] for entry in host.config.get_api_keys()]
    page._finish_drag(False)
    assert page._drag_ghost is page._drop_marker is page._drag_job is None
    assert [entry['id'] for entry in host.config.get_api_keys()] == before


def test_qwen_probe_sends_usable_png_through_actual_openrouter_adapter(monkeypatch):
    import src.core.api_manager as module
    monkeypatch.setattr(module, 'get_config', lambda: SimpleNamespace(
        provider_api_urls={'OpenRouter': 'https://openrouter.ai/api/v1/chat/completions'}))
    manager = AIAPIManager()
    requests = []
    def request(**kw):
        requests.append(kw)
        return 'A red square.'
    monkeypatch.setattr(manager, '_make_request_with_retry', request)
    assert manager.test_vision_connection('qwen/qwen3.8-27b', 'fake-fixture', 'OpenRouter') is True
    data = requests[0]['data']
    assert data['model'] == 'qwen/qwen3.8-27b'
    url = data['messages'][0]['content'][1]['image_url']['url']
    assert url.startswith('data:image/png;base64,')
    with Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1]))) as image:
        assert image.size == (128, 128)


@pytest.mark.parametrize('code,body,expected', [
    (429, 'Rate limit exceeded', None), (401, 'Unauthorized', None),
    (400, 'Image input is not supported', False), (400, 'Image dimensions too small', None),
    (503, 'Service unavailable', None),
])
def test_probe_distinguishes_unsupported_from_inconclusive(monkeypatch, code, body, expected):
    manager = AIAPIManager()
    error = urllib.error.HTTPError('https://fixture.invalid', code, 'Fixture error', {}, io.BytesIO(body.encode()))
    monkeypatch.setattr(manager, '_generate_content', Mock(side_effect=error))
    assert manager.test_vision_connection('qwen/qwen3.8-27b', 'fake-fixture', 'OpenRouter') is expected


def test_inconclusive_probe_does_not_erase_saved_capability_and_provider_change_does(design):
    config = SettingsConfig(api_keys=[dict(api_key='fixture', model_name='m', provider='OpenRouter',
                                          vision_capable=True, file_capable=True)])
    config.update_api_capabilities('fixture', 'm', None, True, provider='OpenRouter')
    assert config.get_api_keys()[0]['vision_capable'] is True
    row = key_testing.merge_rows([('Google', 'm', 'fixture')], config.get_api_keys())[0]
    assert 'vision_capable' not in row


@pytest.mark.parametrize('account,expected', [
    ('HOST\\fixture', ('fixture', 'HOST')), ('fixture@example.invalid', ('fixture@example.invalid', None)),
    ('MicrosoftAccount\\fixture@example.invalid', ('fixture@example.invalid', 'MicrosoftAccount')),
])
@pytest.mark.parametrize('password_ok,same_sid', [(True, True), (False, True), (True, False)])
def test_windows_password_verifies_current_sid_and_closes_handles(account, expected, password_ok, same_sid):
    from ctypes import wintypes as W
    backend = windows_identity.WindowsAccounts.__new__(windows_identity.WindowsAccounts)
    closed, credentials, buffers = [], [], []
    def open_token(process, access, output):
        ctypes.cast(output, ctypes.POINTER(W.HANDLE))[0] = W.HANDLE(10)
        return True
    def logon(username, domain, password, logon_type, provider, output):
        credentials.append((username, domain))
        buffers.append(password)
        assert password.value == 'fixture-password'
        if password_ok:
            ctypes.cast(output, ctypes.POINTER(W.HANDLE))[0] = W.HANDLE(20)
        return password_ok
    backend.security = SimpleNamespace(OpenProcessToken=open_token, LogonUserW=logon,
                                        EqualSid=lambda a, b: same_sid)
    backend.kernel = SimpleNamespace(GetCurrentProcess=lambda: 1,
                                      CloseHandle=lambda handle: closed.append(handle.value))
    backend._user = lambda token: ctypes.create_string_buffer(bytes(ctypes.c_void_p(token.value)))
    assert backend.verify('fixture-password', account) is (password_ok and same_sid)
    assert credentials == [expected]
    assert set(closed) == ({10, 20} if password_ok else {10})
    assert buffers[0].value == ''


def test_resize_probe_records_callback_cost_without_claiming_interactive_fps(main_window, tmp_path):
    """Measure the old callback body and the coalesced path on the same current UI and runtime."""
    from src.ui import translator_layout
    from src.ui.quick_translate import QuickTranslateManager
    from src.utils.ui_helpers import cancel_window_callback
    app = main_window('私は毎日日本語を勉強しています。' * 60,
                      'I study Japanese every day. ' * 60, furigana=True)
    view, root = app._view, app.root
    root.update()
    if view._arrange_job is not None:
        cancel_window_callback(root, view.window, view._arrange_job)
        view._arrange_job = None
    def old_callback():
        view.arrange(translator_layout.layout_mode(view.frame.winfo_width(), view.ctx.scale))
        view._call('on_resize', view.mode)
    def sample(operation, count=40):
        rows = []
        for _ in range(count):
            start = time.perf_counter()
            operation()
            rows.append((time.perf_counter() - start) * 1000)
        values = sorted(rows)
        return dict(samples=len(values), p50_ms=values[len(values) // 2],
                    p95_ms=values[int(len(values) * .95)], max_ms=max(values))
    view._last_size = None
    view._arrange_for_width()
    report = dict(runtime=sys.version, tcl=root.tk.call('info', 'patchlevel'), os=platform.platform(),
                  methodology='Private-desktop synthetic callbacks; identical settled geometry; not pointer-to-photon FPS.',
                  main_old_callback=sample(old_callback),
                  main_duplicate_geometry=sample(view._arrange_for_width))
    manager = QuickTranslateManager(root, SettingsConfig(furigana_enabled=True))
    manager._last_mouse_x, manager._last_mouse_y = 500, 300
    manager.show('日本語の翻訳結果です。' * 80, 'Japanese', original='A long source sentence. ' * 80)
    pump(root, lambda: bool(manager._layout and manager._layout.get('revealed')), timeout=6)
    rect = (manager.popup.winfo_x(), manager.popup.winfo_y(),
            manager.popup.winfo_width(), manager.popup.winfo_height())
    report['popup_cached_body_layout'] = sample(lambda: manager._live_user_layout(*rect[2:]))
    report['popup_full_reflow'] = sample(lambda: manager._fit_boxes(reposition=False, size=rect[2:],
                                                                    position=rect[:2]), count=12)
    assert manager.popup_text.get_plain() == '日本語の翻訳結果です。' * 80
    manager.close()
    path = Path(os.environ.get('CROSSTRANS_RESIZE_REPORT', str(tmp_path / 'resize.json')))
    if path.exists():
        path = path.with_stem(path.stem + '-' + str(time.time_ns()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
