"""Home translation swap, source tools and real editor focus regressions."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.core.translation_alignment import AlignedText, make_metadata, reverse_reading_result, validate_metadata
from src.ui import components as ui, translator_layout
from tests.final_ui_helpers import design
from tests.test_source_choice import main_window
from tests.test_translator_window import geometry, pump


def test_swap_is_an_exact_reversible_local_exchange(main_window, tk_root):
    original = '  私は日本語を勉強しています。😀\n'
    translated = '  I study Japanese. 😀\r\nA second line.\n'
    app = main_window(original, translated)
    app._do_retranslate = Mock(side_effect=AssertionError('Swap submitted a translation'))
    app._start_translation_task = Mock(side_effect=AssertionError('Swap started a request'))
    app._translation_task_runner = Mock()
    app._view.swap_btn.invoke()
    pump(tk_root, .03)
    assert app.original_text.get('1.0', 'end-1c') == translated
    assert app.trans_text.get_plain() == original
    assert app._view.source_chip.value == 'English'
    assert app.selected_language == app._view.picker.value == 'Japanese'
    app._translation_task_runner.cancel.assert_called_once_with(owner=app.popup)
    app._view.swap_btn.invoke()
    pump(tk_root, .03)
    assert app.original_text.get('1.0', 'end-1c') == original
    assert app.trans_text.get_plain() == translated
    assert app._view.source_chip.value == 'Japanese'
    assert app.selected_language == 'English'
    app._do_retranslate.assert_not_called()
    app._start_translation_task.assert_not_called()
    app.config.set_default_target_language.assert_not_called()


def test_unknown_source_offers_selection_then_allows_a_local_swap(main_window, tk_root, monkeypatch):
    from src.core.nlp_manager import nlp_manager
    monkeypatch.setattr(nlp_manager, 'detect_language', lambda text: ('Unknown', 0.0))
    app = main_window('Bonjour.', 'Hello.')
    app._do_retranslate = Mock()
    assert str(app._view.swap_btn.cget('state')) == 'normal'
    app._view.swap_btn.invoke()
    pump(tk_root, .03)
    picker = app._view.source_chip
    assert picker.window is not None
    assert app.original_text.get('1.0', 'end-1c') == 'Bonjour.'
    assert app.trans_text.get_plain() == 'Hello.'
    picker.set('French')
    picker.close()
    app._view.swap_btn.invoke()
    assert app.original_text.get('1.0', 'end-1c') == 'Hello.'
    assert app.trans_text.get_plain() == 'Bonjour.'
    assert app.selected_language == 'French' and picker.value == 'English'
    app._do_retranslate.assert_not_called()


def test_auto_swap_uses_the_dictionary_detector_on_click_only(main_window, monkeypatch):
    from src.core.nlp_manager import nlp_manager
    original, translated = "i'm supperman", 'A translated result.'
    app = main_window(original, translated)
    app._select_language_in_list('Vietnamese')
    app._do_retranslate = Mock()
    app._start_translation_task = Mock()
    monkeypatch.setattr(nlp_manager, 'is_installed', lambda language: language == 'English')
    detector = Mock(wraps=nlp_manager.detect_language)
    monkeypatch.setattr(nlp_manager, 'detect_language', detector)
    app._refresh_source_state()
    detector.assert_not_called()
    assert app._view.source_chip.value == 'Auto'
    app._view.swap_btn.invoke()
    detector.assert_called_once_with(original)
    assert app._view.source_chip.window is None
    assert app.selected_language == 'English' and app._view.source_chip.value == 'Vietnamese'
    assert app.original_text.get('1.0', 'end-1c') == translated
    assert app.trans_text.get_plain() == original
    app._do_retranslate.assert_not_called()
    app._start_translation_task.assert_not_called()


@pytest.mark.parametrize('detected,expected', [('French', 'French'), ('vi', 'Vietnamese'),
                                              ('Chinese (Traditional)', 'Chinese Traditional')])
def test_auto_swap_accepts_supported_dictionary_names_at_the_threshold(main_window, monkeypatch, detected, expected):
    from src.core.nlp_manager import nlp_manager
    from src.ui.dictionary_popup import DETECTION_THRESHOLD
    app = main_window('An unresolved passage.', 'A result.')
    monkeypatch.setattr(nlp_manager, 'detect_language', lambda text: (detected, DETECTION_THRESHOLD))
    app._view.swap_btn.invoke()
    assert app.selected_language == expected
    assert app._view.source_chip.window is None


@pytest.mark.parametrize('detected,confidence', [('Unknown', 1.0), ('English', .69),
                                               ('Unlisted test language', 1.0), (None, 0.0)])
def test_auto_swap_asks_only_when_detection_cannot_supply_a_supported_target(main_window, monkeypatch, detected, confidence):
    from src.core.nlp_manager import nlp_manager
    app = main_window('An unresolved passage.', 'A result.')
    before = app.selected_language
    monkeypatch.setattr(nlp_manager, 'detect_language', lambda text: (detected, confidence))
    app._view.swap_btn.invoke()
    assert app.selected_language == before
    assert app._view.source_chip.window is not None
    assert app.original_text.get('1.0', 'end-1c') == 'An unresolved passage.'
    assert app.trans_text.get_plain() == 'A result.'


def test_auto_swap_detection_failure_falls_back_to_source_choice(main_window, monkeypatch):
    from src.core.nlp_manager import nlp_manager
    app = main_window('An unresolved passage.', 'A result.')
    monkeypatch.setattr(nlp_manager, 'detect_language', Mock(side_effect=RuntimeError('Fake detector failure')))
    app._view.swap_btn.invoke()
    assert app._view.source_chip.window is not None
    assert app.original_text.get('1.0', 'end-1c') == 'An unresolved passage.'


@pytest.mark.parametrize('result', ['', 'Error: API key invalid or quota exceeded (401)'])
def test_auto_swap_does_not_detect_without_a_valid_result(main_window, monkeypatch, result):
    from src.core.nlp_manager import nlp_manager
    app = main_window('An unresolved passage.', result)
    detector = Mock(side_effect=AssertionError('There is no result to swap'))
    monkeypatch.setattr(nlp_manager, 'detect_language', detector)
    app._swap_languages()
    detector.assert_not_called()
    assert app.original_text.get('1.0', 'end-1c') == 'An unresolved passage.'


@pytest.mark.parametrize('chosen', [None, 'French', 'Unlisted test language'])
def test_known_or_explicit_source_does_not_run_dictionary_detection(main_window, monkeypatch, chosen):
    from src.core.nlp_manager import nlp_manager
    app = main_window('Hello.', 'A result.', verdicts={'Hello.': 'English'} if chosen is None else {})
    if chosen is not None:
        app._view.source_chip.set(chosen)
    detector = Mock(side_effect=AssertionError('An existing language was redetected'))
    monkeypatch.setattr(nlp_manager, 'detect_language', detector)
    app._view.swap_btn.invoke()
    detector.assert_not_called()
    if chosen == 'Unlisted test language':
        assert app._view.source_chip.window is not None
    else:
        assert app.selected_language == (chosen or 'English')
        assert app._view.source_chip.window is None


def test_swap_reverses_valid_links_without_inventing_a_new_alignment(main_window):
    original, translated = '日本語です。', 'This is Japanese.'
    data = make_metadata(original, translated, 'English', 'text', [
        {'source': [0, len(original)], 'translated': [0, len(translated)]}])
    app = main_window(original, AlignedText(translated, data))
    app._view.swap_btn.invoke()
    result = app._main_linked.result
    assert validate_metadata(result.alignment, translated, original, 'Japanese') is not None
    assert app._main_linked.controller is not None
    assert result.alignment['groups'][0]['source'] == [0, len(translated)]
    assert result.alignment['groups'][0]['translated'] == [0, len(original)]


@pytest.mark.parametrize('stale', [False, True])
def test_reverse_links_preserves_input_and_drops_untrusted_metadata(stale):
    data = make_metadata('First. Second.', 'One. Two.', 'English', 'text', [
        {'source': [0, 7], 'translated': [0, 3]},
        {'source': [7, 14], 'translated': [3, 9]}])
    before = deepcopy(data)
    result = reverse_reading_result(AlignedText('One. Two.', data),
        'Changed.' if stale else 'First. Second.', 'French', 'English')
    assert type(result) is str
    assert result == ('Changed.' if stale else 'First. Second.')
    assert data == before


@pytest.mark.parametrize('via', ['mouse', 'focus'])
def test_placeholder_yields_to_empty_editor_focus_and_returns_on_blur(main_window, tk_root, via):
    app = main_window('', '', furigana=False)
    view = app._view
    assert view.input_placeholder.winfo_manager() == 'place'
    if via == 'mouse':
        view.input_placeholder.event_generate('<Button-1>')
    else:
        view.original_text.focus_force()
    pump(tk_root, .03)
    assert not view.input_placeholder.winfo_manager()
    assert view.original_text.focus_get() == view.original_text
    assert view.original_text.get('1.0', 'end-1c') == ''
    view.original_text.insert('1.0', 'x')
    view.original_text.delete('1.0', 'end')
    app._sync_main_empty()
    assert not view.input_placeholder.winfo_manager()
    view.picker.button.focus_set()
    pump(tk_root, .03)
    assert view.input_placeholder.winfo_manager() == 'place'
    assert view.original_text.get('1.0', 'end-1c') == ''


@pytest.mark.parametrize('width,expected', [(1300, translator_layout.SIDE), (720, translator_layout.STACKED)])
@pytest.mark.parametrize('attached', [False, True])
def test_swap_and_source_tools_follow_the_actual_panes(main_window, tk_root, tmp_path, width, expected, attached):
    app = main_window('日本語です。', 'A translated result.')
    app.config.has_any_file_capable.return_value = True
    app.config.get.side_effect = lambda name, default=None: {'file_processing_enabled': True}.get(name, default)
    app._refresh_attachment_area()
    if attached:
        path = tmp_path / 'a_long_document_filename_for_the_source_pane.txt'
        path.write_text('Fake document.', encoding='utf-8')
        assert app.attachment_area.add_file(str(path), show_warning=False)
    geometry(app, width, 820)
    view = app._view
    assert view.mode == expected
    source, output, swap = view.source_card, view.translation_card, view.swap_btn
    right = lambda widget: widget.winfo_rootx() + widget.winfo_width()
    bottom = lambda widget: widget.winfo_rooty() + widget.winfo_height()
    assert isinstance(swap, ui.IconButton)
    assert swap.winfo_width() == swap.winfo_height()
    assert abs(right(view.source_chip) - right(source)) <= 1
    assert app.attachment_area.winfo_rootx() >= source.winfo_rootx()
    assert right(app.attachment_area) <= view.source_chip.winfo_rootx()
    assert right(app.attachment_area.add_btn) <= right(app.attachment_area)
    center = lambda widget: widget.winfo_rooty() + widget.winfo_height()/2
    assert abs(center(view.source_chip) - center(app.attachment_area.add_btn)) <= 3
    if expected == translator_layout.SIDE:
        assert right(source) <= swap.winfo_rootx() < right(swap) <= output.winfo_rootx()
        assert abs(center(source) - center(swap)) <= 1
        assert abs(center(view.source_chip) - center(view.picker)) <= 3
    else:
        assert bottom(view.source_pane) <= swap.winfo_rooty()
        assert bottom(swap) <= output.winfo_rooty()
        assert abs(swap.winfo_rootx() + swap.winfo_width()/2 - (source.winfo_rootx() + source.winfo_width()/2)) <= 1
    assert app.original_text.get('1.0', 'end-1c') == '日本語です。'
    assert app.trans_text.get_plain() == 'A translated result.'
    app.config.has_any_file_capable.return_value = False
    app._refresh_attachment_area()
    assert not view.attach_host.winfo_manager()


def test_wrapped_actions_keep_their_real_parent_through_swap_resize_and_reopen(main_window, tk_root):
    original, translated = '日本語です。', 'This is Japanese.'
    data = make_metadata(original, translated, 'English', 'text', [
        {'source': [0, len(original)], 'translated': [0, len(translated)]}])
    app = main_window(original, AlignedText(translated, data))
    for _ in range(3):
        for width in (1300, 720):
            geometry(app, width, 820)
            app._view.swap_btn.invoke()
            pump(tk_root, .05)
            view = app._view
            for button in (view.options_btn, view.fix_grammar_btn, view.history_group, view.translate_btn):
                assert button.master is view.actions
                assert str(button.place_info()['in']) == str(view.actions)
            assert view._action_rows == []
        app.show_popup(original, AlignedText(translated, data), 'English', force_new=True)
        pump(tk_root, .05)
        assert app._main_linked.controller is not None


@pytest.mark.parametrize('ruby', [False, True])
def test_long_linked_quick_results_reopen_without_touching_canonical_text(tk_root, ruby):
    from src.ui.quick_translate import QuickTranslateManager
    from tests.test_popup_alignment import FakeConfig
    from tools.popup_gallery import settle
    original = '\n'.join(f'{i}:日本語を勉強しています。' for i in range(80))
    translated = '\n'.join(f'{i}: I study Japanese every day.' for i in range(80))
    data = make_metadata(original, translated, 'English', 'text', [
        {'source': [0, len(original)], 'translated': [0, len(translated)]}])
    manager = QuickTranslateManager(tk_root, FakeConfig(ruby=ruby))
    manager._last_mouse_x, manager._last_mouse_y = 400, 200
    try:
        for _ in range(3):
            manager.show(AlignedText(translated, data), 'English', original=original, source_lang='Japanese')
            settle(manager, tk_root, compose_s=.02)
            assert manager.popup_text.get_plain() == translated
            assert manager.popup_furigana.get_plain() == original
            assert manager._linked_controller is not None
            manager.close()
            tk_root.update_idletasks()
            assert manager.popup is None
    finally:
        manager.close()
