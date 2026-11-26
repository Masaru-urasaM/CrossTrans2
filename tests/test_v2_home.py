"""Home navigation reuses existing Dictionary/History logic without extra windows or requests."""
import tkinter as tk
from unittest.mock import Mock
import pytest

from tests.final_ui_helpers import design, pump
from tests.settings_support import SettingsConfig
from src.core.history import HistoryManager
from src.ui import components as ui
from src.ui.main_workspace import MainWorkspace
from src.ui.history_dialog import HistoryDialog
from src.ui.home_dictionary import HomeDictionary


ENTRY = dict(id='fixture-entry', timestamp=1000, original='A complete source sentence.',
             translated='A complete translated sentence.', target_lang='English')
RESULT = '## learn\n1. Translation: study\n2. Source Language: English\n3. Definition: To acquire knowledge.\n4. Word Type: verb\n5. Pronunciation: /lɜːn/\n6. Synonyms: study\n7. Antonyms: forget\n8. Examples: I learn each day.'


def make(design, with_key=False):
    window = tk.Toplevel(design.root)
    window.geometry('1100x720')
    config = SettingsConfig(history=[dict(ENTRY)], api_keys=[dict(api_key='fixture-key', provider='Google', model_name='fixture')] if with_key else [])
    translation = ui.Frame(window)
    translation.pack(fill='both', expand=True)
    text = tk.Text(translation, height=4)
    text.insert('1.0', 'Unfinished draft')
    text.pack(fill='both', expand=True)
    shell = MainWorkspace(window, translation, Mock())
    service = Mock()
    service.dictionary_lookup.return_value = RESULT
    service.get_trial_info.return_value = None
    settings, opened = Mock(), Mock()
    shell.configure_home_pages(
        dictionary=lambda host: HomeDictionary(host, design.root, config, service, Mock(), Mock(), settings, opened),
        history=lambda host: HistoryDialog(window, HistoryManager(config), opened, container=host,
                                            on_close=lambda: shell.select_home('translate')))
    window.update_idletasks()
    return window, shell, text, service, opened


def test_sidebar_has_three_working_destinations_and_keeps_page_state(design):
    window, shell, text, service, opened = make(design)
    assert list(shell.home_navigation.buttons) == ['translate', 'dictionary', 'history']
    dictionary = shell.select_home('dictionary')
    dictionary.input.insert('1.0', 'Lookup draft')
    history = shell.select_home('history')
    history.search_entry.set('complete')
    shell.select_home('translate')
    assert text.get('1.0', 'end-1c') == 'Unfinished draft'
    assert shell.select_home('dictionary') is dictionary
    assert dictionary.text() == 'Lookup draft'
    assert shell.select_home('history') is history
    assert history.search_var.get() == 'complete'
    service.dictionary_lookup.assert_not_called()
    assert len([child for child in design.root.winfo_children() if isinstance(child, tk.Toplevel)]) == 1


def test_history_detail_contains_full_canonical_text_in_the_same_window(design):
    window, shell, text, service, opened = make(design)
    history = shell.select_home('history')
    history.select_item(ENTRY)
    window.update_idletasks()
    assert history.window.winfo_toplevel() is window
    assert [box.get_plain() for box in history.detail_boxes] == [ENTRY['original'], ENTRY['translated']]
    history._load_item(ENTRY)
    opened.assert_called_once_with(ENTRY)
    assert history.window.winfo_exists()
    history.show_list()
    assert history.search_entry.winfo_exists()


def test_direct_lookup_is_explicit_and_result_is_embedded(design):
    window, shell, text, service, opened = make(design, with_key=True)
    page = shell.select_home('dictionary')
    page.input.insert('1.0', 'learn')
    service.dictionary_lookup.assert_not_called()
    page.lookup_phrase()
    pump(window, lambda: page.result_view is not None)
    assert page.result_view.window.winfo_toplevel() is window
    assert page.result_view.result == RESULT
    service.dictionary_lookup.assert_called_once()
    page.show_input()
    assert page.text() == 'learn'


def test_no_key_lookup_routes_to_setup_without_a_request(design):
    window, shell, text, service, opened = make(design)
    page = shell.select_home('dictionary')
    page.input.insert('1.0', 'learn')
    page.lookup_phrase()
    assert 'API key' in page.status.cget('text')
    service.dictionary_lookup.assert_not_called()


def test_late_result_for_changed_text_is_discarded(design):
    window, shell, text, service, opened = make(design)
    page = shell.select_home('dictionary')
    page.input.insert('1.0', 'new text')
    page.receive_result(RESULT, 'English', ['old text'], None, 'old text')
    assert page.result_view is None


def test_dictionary_handoff_does_not_overwrite_an_existing_lookup_draft_silently(design):
    window, shell, text, service, opened = make(design)
    page = shell.select_home('dictionary')
    page.input.insert('1.0', 'Existing lookup')
    question = page.receive_text('Incoming phrase')
    assert page.text() == 'Existing lookup'
    question._button_list[0].invoke()
    assert page.text() == 'Existing lookup'


def test_open_in_translate_protects_an_existing_result_even_with_identical_source(design):
    from types import SimpleNamespace
    from src.app import TranslatorApp
    owner = SimpleNamespace(original_text=Mock(), trans_text=Mock(), attachment_area=None,
                            _main_translation_pending=lambda: False, _load_history_item=Mock(),
                            popup=design.root, _view=SimpleNamespace(workspace=Mock()))
    owner.original_text.get.return_value = 'Same source'
    owner.trans_text.get_plain.return_value = 'Work already done'
    question = TranslatorApp._request_open_in_translate(owner, dict(original='Same source', translated='Other result'))
    owner._load_history_item.assert_not_called()
    question._button_list[0].invoke()
    owner._load_history_item.assert_not_called()


def test_dictionary_return_with_same_source_preserves_existing_translation(design):
    from types import SimpleNamespace
    from src.app import TranslatorApp
    owner = SimpleNamespace(original_text=Mock(), trans_text=Mock(), attachment_area=None,
                            _main_translation_pending=lambda: False, _load_history_item=Mock(),
                            popup=design.root, _view=SimpleNamespace(workspace=Mock()))
    owner.original_text.get.return_value = 'Same source'
    owner.trans_text.get_plain.return_value = 'Keep this result'
    TranslatorApp._request_open_in_translate(owner, dict(original='Same source', translated='', dictionary_source_only=True))
    owner._load_history_item.assert_not_called()
    owner._view.workspace.select_home.assert_called_once_with('translate')


def test_embedded_history_detail_recolours_without_losing_canonical_text(design):
    window, shell, text, service, opened = make(design)
    history = shell.select_home('history')
    history.select_item(ENTRY)
    design.manager.set_mode('green')
    assert [box.get_plain() for box in history.detail_boxes] == [ENTRY['original'], ENTRY['translated']]
    assert all(box.cget('bg') == design.manager.palette.surface for box in history.detail_boxes)


@pytest.mark.parametrize('mode', ['pink', 'blue', 'yellow', 'green', 'light', 'dark'])
def test_new_history_detail_uses_current_palette_before_any_theme_switch(design, mode):
    design.manager.set_mode(mode)
    window, shell, text, service, opened = make(design)
    history = shell.select_home('history')
    history.select_item(ENTRY)
    window.update_idletasks()
    for box in history.detail_boxes:
        assert box.cget('bg').lower() == design.manager.palette.surface.lower()
        assert box.cget('fg').lower() == design.manager.palette.text.lower()
    design.manager.set_mode('dark' if mode != 'dark' else 'pink')
    for box in history.detail_boxes:
        assert box.cget('bg').lower() == design.manager.palette.surface.lower()
        assert box.get_plain()

@pytest.mark.parametrize('locale', ['en', 'ja'])
def test_history_detail_shares_limited_height_instead_of_squeezing_the_second_box(design, locale):
    import math
    from tkinter import font as tkfont
    design.manager.set_ui_language(locale)
    design.manager.set_text_scale(1.5)
    window, shell, text, service, opened = make(design)
    history = shell.select_home('history')
    history.select_item(ENTRY)
    design.root.update()
    boxes = history.detail_boxes
    line = max(tkfont.Font(font=box.cget('font')).metrics('linespace') for box in boxes)
    pads = [2 * sum(int(box.cget(key)) for key in ('pady', 'borderwidth', 'highlightthickness'))
            for box in boxes]
    chrome = window.winfo_height() - sum(box.winfo_height() for box in boxes)
    window.geometry(f'1100x{chrome+sum(pads)+math.ceil(4.5*line)}')
    design.root.update()
    assert all(box.winfo_height() >= 2*line+pad for box, pad in zip(boxes, pads))
    assert abs(boxes[0].winfo_height()-boxes[1].winfo_height()) <= 2
