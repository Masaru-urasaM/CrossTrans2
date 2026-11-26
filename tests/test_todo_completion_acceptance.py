"""Measured options recovery and all-page native Settings resize acceptance."""
import json
import tkinter as tk
import time
from types import SimpleNamespace

import pytest

from src.ui import components as ui
from src.core import settings_index
from tests.final_ui_helpers import design, pump  # noqa: F401
from tests.test_source_choice import main_window  # noqa: F401
from tests.test_v2_workspace import workspace
from tests.test_settings_dictionary import FakeNLP, PACKS


@pytest.mark.parametrize('language', ['en', 'ja'])
@pytest.mark.parametrize('text_scale', [1.0, 1.5])
@pytest.mark.parametrize('width', [760, 1400])
@pytest.mark.parametrize('attachment', [False, True])
def test_ten_options_cycles_restore_actual_editor_bounds_at_unchanged_window_geometry(
        main_window, design, tmp_path, language, text_scale, width, attachment):
    design.manager.set_ui_language(language)
    design.manager.set_text_scale(text_scale)
    app = main_window('Retain this source', 'Retain this result', furigana=False)
    view, root = app._view, app.root
    app.popup.geometry(f'{width}x1000+10+10')
    if attachment:
        app.config.has_any_file_capable.return_value = True
        app.config.get.side_effect = lambda key, default=None: True if key == 'file_processing_enabled' else default
        app._refresh_attachment_area()
        path = tmp_path / 'fixture.md'
        path.write_text('# Retained attachment\n', encoding='utf-8')
        assert app.attachment_area.add_file(str(path))
    view.show_options(False)
    def bounds():
        return [(widget.winfo_rootx(),widget.winfo_rooty(),widget.winfo_width(),widget.winfo_height())
                for widget in (view.original_text,app.trans_text,view.body_area)]
    def scroll_state():
        area=view.body_area
        return dict(canvas=(area.canvas.winfo_width(),area.canvas.winfo_height()),
                    content=(area.content.winfo_height(),area.content.winfo_reqheight()),
                    bounds=area.canvas.bbox('all'),view=area.canvas.yview(),
                    needed=area._bar_needed,bar=bool(area.scrollbar.winfo_ismapped()),
                    manager=area.scrollbar.winfo_manager(),cell=area.grid_bbox(1,0),
                    column=area.columnconfigure(1),layout_width=area._layout_width,
                    pending=(area._bar_job,area._layout_job),
                    canvas_grid=area.canvas.grid_info())
    last, changed = [None], [time.monotonic()]
    def settled():
        current = bounds()
        area=view.body_area
        gutter=area.scrollbar.winfo_reqwidth() if area.scrollbar.winfo_manager() else 0
        fits=(abs(area.canvas.winfo_width()+gutter-area.winfo_width())<=2
              and abs(area.canvas.winfo_height()-area.winfo_height())<=2)
        if (current != last[0] or not fits or view._arrange_job is not None
                or area._layout_job is not None):
            last[0], changed[0] = current, time.monotonic()
        return time.monotonic() - changed[0] >= .1
    pump(root, settled)
    baseline, window = bounds(), app.popup.geometry()
    initial_scroll=scroll_state()
    view.custom_prompt.set('Keep the names')
    for _ in range(10):
        view.show_options(True)
        root.update_idletasks()
        view.show_options(False)
        root.update_idletasks()
    pump(root, settled)
    assert app.popup.geometry() == window
    final = bounds()
    if not all(abs(old-new)<=2 for first,last in zip(baseline,final) for old,new in zip(first,last)):
        pytest.fail(json.dumps(dict(before=baseline,after=final,initial_scroll=initial_scroll,
                                    final_scroll=scroll_state()),default=str,indent=2),pytrace=False)
    assert view.custom_instruction() == 'Keep the names'
    assert app.original_text.get('1.0','end-1c') == 'Retain this source'
    assert app.trans_text.get_plain() == 'Retain this result'
    if attachment:
        assert len(app.attachment_area.get_attachments()) == 1


@pytest.mark.parametrize('language', ['en', 'ja'])
@pytest.mark.parametrize('text_scale', [1.0, 1.5])
def test_every_settings_page_uses_live_width_and_survives_native_maximize_restore(design, monkeypatch, language, text_scale):
    from src.ui.settings import page_dictionary
    fake = FakeNLP()
    monkeypatch.setattr(page_dictionary, 'load_nlp', lambda: (fake, PACKS))
    design.manager.set_ui_language(language)
    design.manager.set_text_scale(text_scale)
    window, shell, settings, _ = workspace(design)
    window.maxsize(4096,4096)
    shell.show_primary('settings')
    try:
        for spec in settings_index.PAGES:
            settings.open_page(spec.key)
            widths=[]
            for dimensions in ('800x760','1400x900'):
                window.geometry(dimensions)
                for _ in range(3):window.update()
                page=settings.pages[spec.key]
                frame=settings._frames[spec.key]
                assert frame.winfo_viewable() and page.body.winfo_viewable()
                assert frame.winfo_rootx()+frame.winfo_width() <= window.winfo_rootx()+window.winfo_width()
                widths.append(page.body.winfo_width())
            assert widths[1]>widths[0],spec.key
            before=window.geometry()
            window.state('zoomed')
            window.update()
            assert window.state()=='zoomed'
            window.state('normal')
            window.geometry(before)
            window.update()
            assert settings.pages[spec.key].body.winfo_viewable()
    finally:
        window.destroy()


def test_gallery_settles_visible_search_results_instead_of_the_hidden_general_page(design):
    from tools.ui_gallery import Context
    window,shell,settings,_=workspace(design)
    shell.show_primary('settings')
    settings.search.set('key')
    settings._run_search()
    ctx=Context(SimpleNamespace(settings_window=settings),design.root,100)
    try:
        ctx.settle_settings()
        assert settings._results.winfo_viewable()
        assert not settings.pages['general'].body.winfo_ismapped()
        assert not ctx.extra
    finally:
        window.destroy()


def test_leaving_settings_during_verification_rejects_a_late_success(design, monkeypatch):
    from src.ui.settings import page_providers
    window,shell,settings,_=workspace(design)
    shell.show_primary('settings')
    settings.open_page('providers')
    page=settings.pages['providers']
    page.cards[0].field.set('fixture-only-key')
    calls=[]
    def verify(parent):
        calls.append(parent)
        settings.deactivate()
        return True
    monkeypatch.setattr(page_providers,'require_auth',verify)
    try:
        page.toggle_all_keys()
        assert len(calls)==1
        assert not page._authenticated
        assert all(not card.revealed for card in page.cards)
    finally:
        window.destroy()


def test_destroying_providers_during_verification_never_touches_dead_widgets(design, monkeypatch):
    from src.ui.settings import page_providers
    window,shell,settings,_=workspace(design)
    settings.open_page('providers')
    page=settings.pages['providers']
    page.cards[0].field.set('fixture-only-key')
    calls=[]
    def verify(parent):
        calls.append(parent)
        page.body.destroy()
        return True
    monkeypatch.setattr(page_providers,'require_auth',verify)
    try:
        page.toggle_all_keys()
        assert len(calls)==1
        assert not page._authenticated
    finally:
        window.destroy()


def file_pipeline(main_window, monkeypatch):
    from src.core.api_manager import AIAPIManager
    from src.core.file_processor import FileProcessor
    from src.ui.attachments import AttachmentArea
    # Admission is exercised below; global native drop registration is unrelated.
    monkeypatch.setattr(AttachmentArea, 'accept_drops_on', lambda self, widget: None)
    app = main_window('', '', furigana=False)
    app.config.has_any_file_capable.return_value = True
    app.config.get.side_effect = lambda key, default=None: True if key == 'file_processing_enabled' else default
    app._refresh_attachment_area()
    manager = AIAPIManager()
    manager.configure([dict(api_key='gsk_fixture_only', model_name='fixture-text-model', provider='Groq',
                            file_capable=True, vision_capable=False)])
    requests = []
    def transport(**kwargs):
        requests.append(kwargs['data'])
        return '===ORIGINAL===\nFixture original\n===TRANSLATION===\nTranslated fixture text.'
    monkeypatch.setattr(manager, '_make_request_with_retry', transport)
    app.translation_service.api_manager = manager
    app.file_processor = FileProcessor(manager)
    return app, requests


def wire_text(request):
    parts = []
    for message in request['messages']:
        content = message['content']
        if isinstance(content, str):
            parts.append(content)
        else:
            assert all(block['type'] == 'text' for block in content)
            parts.extend(block['text'] for block in content)
    return '\n'.join(parts)


@pytest.mark.parametrize('admission', ['browse', 'drop'])
@pytest.mark.parametrize('suffix,encoding', [('.MD', 'cp932'), ('.payload', 'utf-16'), ('', 'utf-8-sig')])
def test_text_admission_reaches_the_actual_provider_payload_and_history(
        main_window, design, monkeypatch, tmp_path, admission, suffix, encoding):
    from src.ui import attachments
    app, requests = file_pipeline(main_window, monkeypatch)
    source = '# Meeting\r\nこんにちは世界\r\n**Keep this syntax.**\n'
    path = tmp_path / ('日本語 notes' + suffix)
    path.write_bytes(source.encode(encoding))
    if admission == 'browse':
        monkeypatch.setattr(attachments.filedialog, 'askopenfilenames', lambda **kwargs: (str(path),))
        app.attachment_area._browse_documents()
    else:
        app.attachment_area._on_drop(SimpleNamespace(data='{' + str(path) + '}'))
    assert len(app.attachment_area.get_attachments()) == 1
    app._do_retranslate()
    pump(app.root, lambda: bool(requests) and not app._main_translation_pending())
    assert len(requests) == 1
    assert source in wire_text(requests[0]) and path.name in wire_text(requests[0])
    assert requests[0]['model'] == 'fixture-text-model'
    assert app.trans_text.get_plain() == 'Translated fixture text.'
    saved = app.translation_service.history_manager.get_history()[0]
    assert saved['source_type'] == 'multimodal' and saved['translated'] == 'Translated fixture text.'


def test_extraction_failure_omits_only_the_bad_file_and_keeps_valid_attachments(
        main_window, design, monkeypatch, tmp_path):
    app, requests = file_pipeline(main_window, monkeypatch)
    valid, changed = tmp_path/'valid.md', tmp_path/'changed.md'
    valid.write_bytes(b'# Valid\nRetain this document.')
    changed.write_bytes(b'Previously readable.')
    for path in (valid, changed):
        assert app.attachment_area.add_file(str(path))
    changed.write_bytes(b'\x89PNG\x00\xff\x00')
    app._do_retranslate()
    pump(app.root, lambda: bool(requests) and not app._main_translation_pending())
    body = wire_text(requests[0])
    assert len(requests) == 1 and '# Valid\nRetain this document.' in body
    assert 'changed.md' not in body and 'Previously readable.' not in body
    assert any(item['path'] == str(valid) for item in app.attachment_area.get_attachments())
    app.toast.show_warning.assert_called_once()
    assert 'changed.md' in app.toast.show_warning.call_args.args[0]
