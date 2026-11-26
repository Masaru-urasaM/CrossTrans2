"""History links, embedded quick tools, left-drag and actionable provider failures."""
import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.error

import pytest
from PIL import Image

from src.core.api_manager import AIAPIManager, ProviderRequestError
from src.core.multimodal import MultimodalProcessor
from src.ui import popup_errors
from src.ui.dictionary_mode import WordButtonFrame
from tests.test_source_choice import main_window
from tests.test_dictionary_design import picker, env
from tests.final_ui_helpers import design


@pytest.mark.parametrize('saved', [True, False])
def test_history_detail_links_are_lossless_and_owned_by_the_detail(main_window, saved):
    from src.core.translation_alignment import structural_alignment
    app = main_window('', '', furigana=False)
    source, translated = 'First. Second.', 'One. Two.'
    item = dict(original=source, translated=translated, target_lang='English', source_type='text')
    if saved:
        item['alignment'] = structural_alignment(source, translated, 'English')
    before = json.dumps(item, sort_keys=True)
    page = app._view.workspace.select_home('history')
    page.select_item(item)
    app.root.update()
    links = page.detail_links
    assert links.controller is not None
    assert [box.get_plain() for box in page.detail_boxes] == [source, translated]
    links.controller.activate(0, 1)
    assert all(box.tag_ranges('linked_reading') for box in page.detail_boxes)
    links.toggle()
    assert links.controller is None
    links.toggle()
    controller = links.controller
    page.show_list()
    assert controller.closed and page.detail_links is None
    assert json.dumps(item, sort_keys=True) == before


def test_history_quick_pick_loads_translate_without_changing_home_tab(main_window):
    app = main_window('', '', furigana=False)
    app.show_main_window = Mock()
    app.translation_service.history_manager.add_entry('Good morning', 'Bonjour', 'French')
    page = app._open_history()
    app.root.update()
    panel = app._home_quick_panel
    assert panel.frame.winfo_toplevel() is app.popup
    assert app._view.workspace.home_page == 'translate'
    row = page.scrollable_frame.winfo_children()[0]
    row.event_generate('<Button-1>')
    app.root.update()
    assert panel.closed
    assert app.original_text.get('1.0', 'end-1c') == 'Good morning'
    assert app.trans_text.get_plain() == 'Bonjour'
    assert app.selected_language == 'French'


def test_quick_panel_escape_and_navigation_preserve_draft_and_release_bindings(main_window):
    app = main_window('Keep this draft', 'Keep this result', furigana=False)
    app.show_main_window = Mock()
    before = app.popup.bind('<ButtonPress-1>')
    app._open_history()
    panel = app._home_quick_panel
    app._on_translator_escape()
    assert panel.closed and app.popup.winfo_exists()
    assert app.popup.bind('<ButtonPress-1>') == before
    app._open_history()
    panel = app._home_quick_panel
    app._view.workspace.select_home('history')
    app.root.update()
    assert panel.closed
    assert app.original_text.get('1.0', 'end-1c') == 'Keep this draft'
    assert app.trans_text.get_plain() == 'Keep this result'


def test_translate_dictionary_uses_the_same_word_composer_inside_home(main_window, monkeypatch):
    from src.core.nlp_manager import nlp_manager
    app = main_window('one two three', '', furigana=False)
    app.show_main_window = Mock()
    app.quick_translate_manager = Mock()
    app.config.get_default_target_language.return_value = 'English'
    app._view.source_chip.set('English')
    monkeypatch.setattr(nlp_manager, 'is_installed', lambda language: True)
    monkeypatch.setattr(WordButtonFrame, '_tokenize_text', lambda self, text: text.split())
    page = app._open_home_dictionary()
    app.root.update()
    assert page.word_frame is not None and page.word_frame.frame.winfo_ismapped()
    assert page.word_frame.frame.winfo_toplevel() is app.popup
    assert page.word_frame._drop_target is page.custom_boxes
    assert page.word_frame.get_selected_words() == []
    assert app._view.workspace.home_page == 'translate'
    page.word_frame.exit_btn.invoke()
    assert app._home_quick_panel.closed
    assert app.original_text.get('1.0', 'end-1c') == 'one two three'


def test_left_click_left_drag_and_shift_range_remain_distinct(picker):
    top, frame, boxes = picker
    word = frame.word_labels[0].label
    x, y = word.winfo_rootx() + 3, word.winfo_rooty() + 3
    word.event_generate('<ButtonPress-1>', x=3, y=3, rootx=x, rooty=y)
    word.event_generate('<ButtonRelease-1>', x=3, y=3, rootx=x, rooty=y)
    assert frame.get_selected_words() == ['one']
    assert not boxes._boxes[0]._tags
    word.event_generate('<ButtonPress-1>', x=3, y=3, rootx=x, rooty=y)
    box = boxes._boxes[0]
    drop = SimpleNamespace(x_root=box.text_widget.winfo_rootx() + 4,
                           y_root=box.text_widget.winfo_rooty() + 4)
    frame._drag_motion(drop)
    frame._end_drag(drop)
    assert box.get_content() == 'one'
    frame._on_word_click(0, SimpleNamespace(state=1, num=1))
    frame._on_word_drag_enter(2)
    frame._release_selection()
    assert frame.get_selected_words() == ['one', 'two', 'three']
    assert box.get_content() == 'one'


def test_phrase_square_actions_work_by_keyboard_and_keep_their_theme(picker):
    top, frame, boxes = picker
    add = boxes._boxes[0].add_btn
    add.focus_force()
    top.update()
    add.event_generate('<Return>')
    top.update()
    assert len(boxes._boxes) == 2
    boxes._boxes[1].remove_btn.invoke()
    assert len(boxes._boxes) == 1
    frame.ctx.manager.set_mode('dark')
    top.update()
    assert add.winfo_width() == add.winfo_height() >= frame.ctx.px(40)


@pytest.mark.parametrize('kind', ['word', 'tag'])
def test_escape_cancels_the_drag_before_the_windows_close_binding(picker, kind):
    top, frame, boxes = picker
    top.bind('<Escape>', lambda event: top.destroy())
    if kind == 'word':
        focus = frame.word_labels[0].widget
        frame._start_drag_to_box('one', SimpleNamespace(x_root=100, y_root=100))
        frame._drag_motion(SimpleNamespace(x_root=120, y_root=120))
    else:
        box = boxes._boxes[0]
        box.add_word_tag('one')
        tag = box._tags[0]
        focus = tag.frame
        boxes._on_tag_drag_start(tag, SimpleNamespace(x_root=100, y_root=100))
        boxes._on_tag_drag_motion(SimpleNamespace(x_root=120, y_root=120))
    focus.focus_force()
    top.update()
    focus.event_generate('<Escape>')
    top.update()
    assert top.winfo_exists()
    assert frame._drag_ghost is boxes._drag_ghost is None
    if kind == 'tag':
        assert boxes._boxes[0].get_content() == 'one'
    focus.event_generate('<Escape>')
    assert not top.winfo_exists()


def test_dragging_a_fresh_word_does_not_also_select_it_for_duplicate_lookup(picker):
    top, frame, boxes = picker
    frame._on_word_click(0, SimpleNamespace(state=0, num=1, x_root=100, y_root=100))
    box = boxes._boxes[0]
    point = SimpleNamespace(x_root=box.text_widget.winfo_rootx() + 4,
                            y_root=box.text_widget.winfo_rooty() + 4)
    frame._drag_motion(point)
    frame._end_drag(point)
    assert frame.get_selected_words() == []
    assert box.get_content() == 'one'


def test_compact_dictionary_no_key_notice_is_visible_without_losing_phrases(design, monkeypatch):
    import tkinter as tk
    from src.ui.home_dictionary import HomeDictionary
    top = tk.Toplevel(design.root)
    top.geometry('760x570')
    config = Mock()
    config.get_default_target_language.return_value = 'English'
    config.get_furigana_enabled.return_value = False
    config.get_api_keys.return_value = []
    settings = Mock()
    monkeypatch.setattr(WordButtonFrame, '_tokenize_text', lambda self, text: text.split())
    page = HomeDictionary(top, design.root, config, Mock(), Mock(), Mock(), settings, Mock(), compact=True)
    page.receive_text('one two', source_language='English', target_language='English')
    page.show_selection('one two', 'English', True, 'English')
    page.custom_boxes._boxes[0].add_word_tag('keep')
    design.root.update()
    page.lookup_words(['one'], 'English', 'English', context='one two')
    design.root.update()
    assert page.selection_notice.winfo_ismapped()
    assert page.selection_notice.winfo_height() > page.selection_notice.text.winfo_reqheight()
    assert page.custom_boxes.get_all_phrases() == ['keep']
    page.selection_notice.action.invoke()
    settings.assert_called_once_with('API Key')
    top.destroy()


def test_overlay_contrast_audit_keeps_visible_and_partly_covered_controls(design):
    import tkinter as tk
    from src.ui import components as ui
    from tools.ui_gallery import covered_by_overlay, pixel_background
    window = tk.Toplevel(design.root)
    window.geometry('440x300')
    host = ui.Frame(window)
    host.pack(fill='both', expand=True)
    hidden = ui.Button(host, 'Translate')
    hidden.place(x=10, y=10, width=120, height=40)
    partial = ui.Button(host, 'Copy')
    partial.place(x=40, y=150, width=130, height=40)
    panel = ui.Card(host)
    panel.occludes_underlay = True
    panel.place(x=0, y=0, width=220, height=180)
    visible = ui.Button(panel, 'Close')
    visible.pack()
    design.root.update()
    try:
        assert covered_by_overlay(hidden)
        assert not covered_by_overlay(partial)
        assert covered_by_overlay(partial, partial=True)
        assert not covered_by_overlay(visible)
        from PIL import ImageDraw
        origin = window.winfo_rootx(), window.winfo_rooty()
        bitmap = Image.new('RGB', (window.winfo_width(), window.winfo_height()), 'white')
        draw = ImageDraw.Draw(bitmap)
        for widget, colour in ((partial, (25, 65, 95)), (panel, (255, 255, 255))):
            x, y = widget.winfo_rootx() - origin[0], widget.winfo_rooty() - origin[1]
            draw.rectangle((x, y, x + widget.winfo_width() - 1, y + widget.winfo_height() - 1), fill=colour)
        assert pixel_background(bitmap, partial, origin) == (25, 65, 95)
    finally:
        window.destroy()


@pytest.mark.parametrize('raw,fragment,fix', [
    ('Error: HTTP 401: invalid key', 'authentication failed', popup_errors.FIX_SETTINGS),
    ('Error: HTTP 403: access denied', 'permission', popup_errors.FIX_SETTINGS),
    ('Error: HTTP 429: rate_limit_exceeded', 'limiting', popup_errors.FIX_RETRY),
    ('Error: HTTP 429: insufficient_quota', 'quota or credit', popup_errors.FIX_SETTINGS),
    ('Error: HTTP 400: image input not supported', 'image input', popup_errors.FIX_SETTINGS),
    ('Error: HTTP 400: invalid image dimensions', 'request format', popup_errors.FIX_NONE),
    ('Error: HTTP 404: model not found', 'model ID', popup_errors.FIX_SETTINGS),
    ('Error: HTTP 413: image too large', 'limit', popup_errors.FIX_NONE),
    ('No text received after three Ctrl+C attempts.', 'active app', popup_errors.FIX_NONE),
    ('Error: NewProviderReason', 'could not be identified', popup_errors.FIX_RETRY),
])
def test_popup_explains_the_specific_failure(raw, fragment, fix):
    help_ = popup_errors.explain(raw)
    assert fragment in help_.message and help_.fix == fix


def test_real_http_error_body_survives_transport_and_redacts_credentials(monkeypatch):
    key = 'gsk_fixture_only'
    payload = {'error': {'message': f'Image input not supported for this route. Key {key}.'}}
    error = urllib.error.HTTPError('https://api.groq.com', 400, 'Bad Request', {},
                                   io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr('urllib.request.urlopen', Mock(side_effect=error))
    manager = AIAPIManager()
    with pytest.raises(ProviderRequestError) as caught:
        manager._make_request_with_retry('https://api.groq.com/openai/v1/chat/completions', {},
            {'Authorization': 'Bearer ' + key}, lambda result: result, max_retries=1)
    assert caught.value.code == 400
    assert 'Image input not supported' in str(caught.value)
    assert key not in str(caught.value)
    assert popup_errors.explain('Error: ' + str(caught.value)).fix == popup_errors.FIX_SETTINGS


@pytest.mark.parametrize('multimodal', [True, False])
def test_groq_qwen_legacy_negative_reaches_actual_image_request(monkeypatch, tmp_path, multimodal):
    image = tmp_path / 'image.png'
    Image.new('RGB', (128, 128), 'red').save(image)
    manager = AIAPIManager()
    manager.configure([dict(api_key='gsk_fixture_only', provider='Groq',
                            model_name='qwen/qwen3.8-27b', vision_capable=False)])
    requests = []
    def transport(**kwargs):
        requests.append(kwargs)
        return 'red'
    monkeypatch.setattr(manager, '_make_request_with_retry', transport)
    value = (manager.translate_multimodal('Read image', [str(image)]) if multimodal
             else manager.translate_image('Read image', str(image)))
    assert value == 'red' and len(requests) == 1
    data = requests[0]['data']
    assert data['model'] == 'qwen/qwen3.8-27b'
    assert data['reasoning_effort'] == 'none'
    assert data['max_completion_tokens'] == 16384 and requests[0]['timeout'] <= 20
    content = data['messages'][0]['content']
    uri = next(part['image_url']['url'] for part in content if part['type'] == 'image_url')
    assert base64.b64decode(uri.split(',')[1]) == image.read_bytes()


def test_current_unsupported_probe_is_respected_and_other_qwen_models_stay_text_only():
    entry = dict(provider='Groq', model_name='qwen/qwen3.8-27b', vision_capable=False,
                 vision_probe_version=MultimodalProcessor.VISION_PROBE_VERSION)
    assert not MultimodalProcessor.config_accepts_images(entry, 'Groq')
    assert not MultimodalProcessor.config_accepts_images(
        dict(model_name='qwen/qwen3-32b', vision_capable=False), 'Groq')


def test_groq_capability_recovery_and_fresh_probe_persist_across_restart(tmp_path, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, 'CONFIG_DIR', str(tmp_path))
    monkeypatch.setattr(Config, 'CONFIG_FILE', str(tmp_path / 'config.json'))
    monkeypatch.setattr('config.SecureStorage.is_available', lambda: False)
    config = Config()
    row = dict(api_key='gsk_fixture_only', provider='Groq', model_name='qwen/qwen3.8-27b', vision_capable=False)
    config._config['api_keys'] = [row]
    config.save()
    loaded = Config()
    assert loaded.get('vision_enabled') and loaded.get_api_keys()[0]['vision_capable']
    loaded.update_api_capabilities(row['api_key'], row['model_name'], False, True, provider='Groq')
    restarted = Config()
    assert restarted.get_api_keys()[0]['vision_capable'] is False
    assert restarted.get('vision_enabled') is False


def test_large_text_quick_tools_gallery_settles_and_keeps_word_rows_visible():
    import os
    from pathlib import Path
    import subprocess
    import sys
    import quiet_session
    if not quiet_session.DESKTOP_NAME:
        pytest.skip(quiet_session.skip_reason())
    repo = Path(__file__).resolve().parents[1]
    label = 'test-history-quick-large-rows'
    process = subprocess.run([sys.executable, str(repo / 'tools/ui_gallery.py'), '--label', label,
        '--surfaces', 'home_quick_tools', '--scales', '100', '--theme', 'dark', '--ui-language', 'ja',
        '--text-scale', '1.5', '--no-shots', '--strict'], cwd=repo, env=dict(os.environ),
        capture_output=True, text=True, encoding='utf-8', timeout=240)
    assert process.returncode == 0, process.stdout[-3000:] + process.stderr[-3000:]
    report = json.loads((repo / 'logs/ui_gallery' / (label + '.json')).read_text(encoding='utf-8'))
    from src.ui.theme.tokens import MODES
    assert report['captures'] == 11 + len(MODES)
    detail = json.loads((repo / 'logs/ui_gallery' / label / 's100.json').read_text(encoding='utf-8'))
    assert {row['state'] for row in detail['captures'] if row['state'].startswith('dictionary-live-')} == {
        'dictionary-live-' + mode for mode in MODES}
    assert not report['errors'] and not report['findings_by_kind'] and not report['blocked']
