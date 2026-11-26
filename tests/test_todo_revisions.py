"""Regression contracts for the current Home, Settings and quick-popup revisions."""
import tkinter as tk
from unittest.mock import Mock

import pytest
import src.core
from config import Config
from src.ui import components as ui
from src.ui.main_linked_reading import MainLinkedReading
from src.ui.quick_translate import QuickTranslateManager
from src.ui.ruby_text import RubyText
from src.ui.settings.page_shortcuts import ShortcutsPage
from src.ui.settings.pages_basic import TranslationPage
from tests.final_ui_helpers import design, pump
from tests.settings_support import Host, SettingsConfig
from tests.test_hotkey_manager import make as make_hotkeys
from tests.test_popup_alignment import result
from tests.test_source_choice import main_window
from tests.test_alignment_service import make_service, SOURCE


def test_new_settings_and_cleared_bindings_survive_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, 'CONFIG_DIR', str(tmp_path))
    monkeypatch.setattr(Config, 'CONFIG_FILE', str(tmp_path / 'config.json'))
    config = Config()
    assert config.get_shortcuts_enabled()
    assert list(config.DEFAULT_HOTKEYS) == ['English', 'Japanese', 'Chinese Simplified', 'Vietnamese']
    config.set_language_hotkeys({name: '' for name in config.DEFAULT_LANGUAGES})
    config.set_shortcuts_enabled(False)
    config.set_linked_reading_enabled(False)
    config.set_show_linked_reading(True)
    restarted = Config()
    assert not restarted.get_shortcuts_enabled()
    assert not restarted.get_linked_reading_enabled()
    assert restarted.get_show_linked_reading()
    assert restarted.get_hotkeys() == {name: '' for name in config.DEFAULT_LANGUAGES}
    restarted.set_shortcuts_enabled(True)
    assert Config().get_hotkeys() == restarted.get_hotkeys()


def test_disabled_shortcuts_unregister_and_ignore_queued_events():
    config = SettingsConfig()
    callback = Mock()
    manager = make_hotkeys(config, callback)
    manager.register_hotkeys()
    old_ids = set(manager._registered_ids)
    assert old_ids
    config.set_shortcuts_enabled(False)
    manager.register_hotkeys()
    assert set(manager.user32.unregistered) == old_ids
    assert not manager._registered_ids and not manager.registration
    manager._on_hotkey('English')
    callback.assert_not_called()
    config.set_shortcuts_enabled(True)
    manager.register_hotkeys()
    assert len(manager._registered_ids) == len(old_ids)


def test_empty_and_disabled_bindings_are_not_advertised_in_pins_or_tray():
    from src.core.language_prefs import pinned_names
    from src.ui.tray import TrayManager
    config = SettingsConfig(hotkeys={'English': '', 'Japanese': 'win+alt+j'},
                            fix_grammar_hotkey_enabled=True)
    config.has_any_vision_capable = lambda: True
    assert pinned_names(config.get_all_hotkeys()) == ['Japanese']
    tray = TrayManager(config)
    hints = [str(item.text) for item in tray._build_menu_items() if '→' in str(item.text)]
    assert len(hints) == 3 and not any('English' in hint for hint in hints)
    config.set_shortcuts_enabled(False)
    assert not any('→' in str(item.text) for item in tray._build_menu_items())
    assert config.get_all_hotkeys() == {'English': '', 'Japanese': 'win+alt+j'}


def test_shortcut_page_collapses_without_losing_cleared_bindings(design):
    # Use the bounded viewport the product supplies. A previously mapped,
    # auto-sized session root is shared with unrelated legacy widget tests.
    window = tk.Toplevel(design.root)
    window.geometry('900x850+0+0')
    host = Host(design.root)
    page = ShortcutsPage(host)
    page.build(window).pack(fill='both', expand=True)
    for name in host.config.DEFAULT_LANGUAGES:
        page.editors[name].clear_btn.invoke()
        page.editors[name].clear_dialog._button_list[1].invoke()
        assert page.editors[name].value == ''
    page.switches['shortcuts_enabled'].invoke()
    assert not page.content.winfo_manager()
    assert not host.config.get_shortcuts_enabled()
    page.switches['shortcuts_enabled'].invoke()
    assert page.content.winfo_manager()
    assert all(not editor.value for name, editor in page.editors.items()
               if name in host.config.DEFAULT_LANGUAGES)
    assert host.changes[-2:] == ['shortcuts_enabled', 'shortcuts_enabled']


def test_link_settings_are_independent_on_the_real_settings_page(design):
    host = Host(design.root)
    page = TranslationPage(host)
    page.build(design.root).pack(fill='both', expand=True)
    page.switches['show_linked_reading'].invoke()
    assert not host.config.get_show_linked_reading()
    assert host.config.get_linked_reading_enabled()
    page.switches['linked_reading'].invoke()
    assert not host.config.get_linked_reading_enabled()
    assert not host.config.get_show_linked_reading()
    page.switches['show_linked_reading'].invoke()
    assert host.config.get_show_linked_reading()
    assert not host.config.get_linked_reading_enabled()


@pytest.mark.parametrize('kind', ['text', 'merged'])
@pytest.mark.parametrize('generate', [False, True])
@pytest.mark.parametrize('show', [False, True])
def test_requests_follow_generation_setting_independently_of_button_visibility(kind, generate, show):
    service = make_service(enabled=generate)
    service.config.data['show_linked_reading'] = show
    if not generate:
        service._call_model.return_value = 'Ordinary answer.'
    if kind == 'text':
        answer = service.translate_text(SOURCE, 'English', allow_linked=True, skip_cache=True)
    else:
        answer = service.translate_or_fix(SOURCE, 'English', skip_cache=True)
    prompt = service._call_model.call_args.args[0]
    assert ('SOURCE UNITS:' in prompt) == generate
    assert ('source_ids' in prompt) == generate
    assert bool(getattr(answer, 'alignment', None)) == generate
    assert service._call_model.call_count == 1


@pytest.mark.parametrize('kind', ['text', 'merged'])
@pytest.mark.parametrize('metadata', [None, {'version': 99}])
def test_old_cache_never_creates_or_persists_missing_links(kind, metadata):
    from copy import deepcopy
    service = make_service()
    service.history_manager.add_entry(SOURCE, 'One. Two.', 'English', source_type=kind)
    entry = service.history_manager.get_history()[0]
    if metadata is not None:
        entry['alignment'] = metadata
    saved = deepcopy(entry)
    for enabled in (True, False, True):
        service.config.data['linked_reading_enabled'] = enabled
        restored = service.history_manager.reading_result(entry)
        cached = (service.translate_text(SOURCE, 'English', allow_linked=True) if kind == 'text'
                  else service.translate_or_fix(SOURCE, 'English'))
        assert type(restored) is type(cached) is str
        assert restored == cached == 'One. Two.'
        assert entry == saved
    service._call_model.assert_not_called()


@pytest.mark.parametrize('initially_enabled', [False, True])
@pytest.mark.parametrize('corrupt', [False, True])
def test_opening_or_toggling_old_results_cannot_invent_ui_links(design, initially_enabled, corrupt):
    from src.core.translation_alignment import AlignedText
    config = SettingsConfig(linked_reading_enabled=initially_enabled, show_linked_reading=True)
    value = AlignedText('One. Two.', {'version': 99}) if corrupt else 'One. Two.'
    window = tk.Toplevel(design.root)
    source = tk.Text(window)
    source.insert('1.0', SOURCE)
    translated = RubyText(window)
    translated.insert_plain('1.0', value)
    toolbar = ui.Frame(window)
    reading = MainLinkedReading(design.root, window, source, translated, config, toolbar)
    reading.set_result(value, SOURCE, 'English')
    manager = QuickTranslateManager(design.root, config)
    try:
        manager.show(value, 'English', original=SOURCE)
        pump(design.root, lambda: bool(manager._layout and manager._layout.get('revealed')))
        for enabled in (True, False, True):
            config.set_linked_reading_enabled(enabled)
            reading.sync()
            manager._sync_linked_reading()
            assert reading.controller is manager._linked_controller is None
            assert not getattr(reading.result, 'alignment', None)
            assert manager._current_alignment is None
            assert str(reading.button.cget('state')) == 'disabled'
            assert str(manager.popup_linked_btn.cget('state')) == 'disabled'
            assert source.get('1.0', 'end-1c') == SOURCE
            assert translated.get_plain() == manager.popup_text.get_plain() == 'One. Two.'
    finally:
        manager.close()
        window.destroy()


@pytest.mark.parametrize('generate', [False, True])
@pytest.mark.parametrize('show', [False, True])
def test_main_link_button_visibility_and_highlights_follow_both_flags(design, generate, show):
    window = tk.Toplevel(design.root)
    source = tk.Text(window)
    source.insert('1.0', 'First. Second.')
    translated = RubyText(window)
    value = result()
    translated.insert_plain('1.0', value)
    toolbar = ui.Frame(window)
    config = SettingsConfig(linked_reading_enabled=generate, show_linked_reading=show)
    linked = MainLinkedReading(design.root, window, source, translated, config, toolbar)
    linked.set_result(value, 'First. Second.', 'English')
    assert bool(linked.button.winfo_manager()) == (generate and show)
    assert bool(linked.controller) == (generate and show)
    if generate and show:
        linked.toggle()
        assert linked.controller is None
        assert config.get_linked_reading_enabled()
    window.destroy()


@pytest.mark.parametrize('generate', [False, True])
@pytest.mark.parametrize('show', [False, True])
def test_quick_link_button_visibility_and_highlights_follow_both_flags(design, generate, show):
    config = SettingsConfig(linked_reading_enabled=generate, show_linked_reading=show)
    manager = QuickTranslateManager(design.root, config)
    try:
        manager.show(result(), 'English', original='First. Second.')
        pump(design.root, lambda: bool(manager._layout and manager._layout.get('revealed')))
        assert bool(manager.popup_linked_btn.winfo_manager()) == (generate and show)
        assert bool(manager._linked_controller) == (generate and show)
        if generate and show:
            manager._toggle_linked_reading()
            assert manager._linked_controller is None
            assert config.get_linked_reading_enabled()
    finally:
        manager.close()


@pytest.mark.parametrize('grammar', [False, True])
def test_replace_cancel_keeps_the_same_popup_and_canonical_documents(design, grammar):
    config = SettingsConfig(quick_replace=False, linked_reading_enabled=False)
    manager = QuickTranslateManager(design.root, config)
    replace = Mock()
    manager.configure_callbacks(on_copy_and_replace=replace)
    try:
        manager.show('A translated sentence.', 'English', original='日本語の文です。',
                     model_info='Fixture model (Provider)', is_grammar=grammar)
        pump(design.root, lambda: bool(manager._layout and manager._layout.get('revealed')))
        window = manager.popup
        credit = manager.popup_attribution.cget('text')
        for _ in range(3):
            manager._handle_copy_and_replace()
            assert manager.popup_furigana.get_plain() == '日本語の文です。'
            assert manager.popup_furigana.tag_ranges('replace_preview')
            assert manager.popup_text.get_plain() == 'A translated sentence.'
            manager._handle_replace_cancel()
            design.root.update()
            assert manager.popup is window and window.winfo_ismapped()
            if grammar:
                assert manager.popup_furigana is None
            else:
                assert manager.popup_furigana.get_plain() == '日本語の文です。'
                assert not manager.popup_furigana.tag_ranges('replace_preview')
            assert manager.popup_text.get_plain() == 'A translated sentence.'
            assert manager.popup_attribution.cget('text') == credit
        replace.assert_not_called()
        manager._handle_copy_and_replace()
        manager._handle_replace_agree()
        replace.assert_called_once_with()
    finally:
        manager.close()


def test_home_actions_belong_below_each_content_card(design, main_window):
    app = main_window('Source', 'Result')
    view = app._view
    assert view.dict_btn.master is view.source_footer
    assert view.copy_btn.master is view.translation_footer
    assert view.expand_btn.master is view.translation_footer
    assert view.attach_host.master is view.source_bar
    assert not hasattr(view, 'input_empty') and not hasattr(view, 'output_empty')
    assert isinstance(view.source_chip.button, ui.Button)
    assert view.source_chip.button.icon_name == view.picker.button.icon_name == 'chevron-down'


def test_key_header_badges_usage_and_drag_survive_live_theme_changes(design):
    import time
    from tests.test_v2_providers import make
    page, host = make(design)
    entries = host.config.get_api_keys()
    entries[0].update(vision_capable=True, file_capable=False)
    host.config.set_api_keys(entries)
    page._draw_capabilities()
    first = page.cards[0]
    now = time.time()
    page.usage.apply(dict(id=first.key_id, at=now, kind='success', seconds=1.5))
    page.usage.apply(dict(id=first.key_id, at=now + 1, kind='test_success', seconds=0))
    for theme in ('pink', 'blue', 'yellow', 'green', 'light', 'dark', 'pink'):
        design.manager.set_mode(theme)
        page.refresh_usage(now + 1)
        design.root.update()
        heading = page.capability_row.title_label
        assert heading.master is page.api_heading and heading.winfo_ismapped()
        assert heading.cget('text') == 'API keys'
        assert page.capability_box.master is page.api_heading
        expected = (design.ctx.icon('check-lg', 16, design.manager.palette.success),
                    design.ctx.icon('x-lg', 16, design.manager.palette.danger))
        for chip, icon in zip(page.capability_chips, expected):
            assert str(icon) in str(chip.cget('image'))
        assert first.last_used_label.cget('text') == '← Last used'
        assert first.last_used_label.winfo_rootx() >= first.model.winfo_rootx() + first.model.winfo_width()
        assert 'Test OK' in first.usage_label.cget('text')
        assert str(first.usage_label.cget('foreground')) == design.manager.palette.success
        assert first.usage_label.master is first.test_btn.master
        assert not first.status_label.winfo_manager()
        assert not hasattr(first, 'drag_handle')
    first.field.event_generate('<ButtonPress-1>', x=3, y=3)
    first.field.event_generate('<ButtonRelease-1>', x=3, y=3)
    assert page._drag_card is None
    first.meta_line.event_generate('<ButtonPress-1>', x=3, y=3)
    assert page._drag_pending is not None
    first.meta_line.event_generate('<B1-Motion>', x=13, y=13)
    assert page._drag_card is first
    page._finish_drag(False)


def test_large_text_keeps_two_key_rows_when_the_controls_fit(design):
    from tests.test_v2_providers import make
    design.manager.set_ui_language('ja')
    design.manager.set_text_scale(1.5)
    page, host = make(design, count=1)
    design.root.update()
    first = page.cards[0]
    first._wrap()
    assert not first._compact
    assert first.provider.grid_info()['row'] == first.model.grid_info()['row'] == 0
    assert first.field.grid_info()['row'] == first.test_btn.grid_info()['row'] == 0


def test_provider_and_model_columns_stay_aligned_across_provider_values(design):
    from tests.test_v2_providers import make
    page, host = make(design, count=3)
    for card, provider in zip(page.cards, ('Google', 'Groq', 'Auto')):
        card.provider.set(provider)
    design.root.update()
    widths = [card.provider.winfo_width() for card in page.cards]
    origins = [card.model.winfo_rootx() for card in page.cards]
    assert max(widths) - min(widths) <= 2
    assert max(origins) - min(origins) <= 2


def test_live_theme_changes_after_building_shortcuts_repaint_later_widgets(design, caplog):
    import logging
    from src.ui.components.styles import name
    from src.ui.theme.compat import NO_AUTOSTYLE
    window = tk.Toplevel(design.root)
    page = ShortcutsPage(Host(window))
    page.build(window).pack(fill='x')
    text = tk.Text(window, height=1, bg=design.manager.palette.surface,
                   fg=design.manager.palette.text, **NO_AUTOSTYLE)
    text.pack()
    destructive = ui.Button(window, 'Delete all packs', kind='danger', icon='trash')
    destructive.pack()
    with caplog.at_level(logging.ERROR):
        for theme in ('pink', 'blue', 'yellow', 'green', 'light', 'dark', 'pink'):
            design.manager.set_mode(theme)
            design.root.update()
            assert all(callable(editor.keys) for editor in page.editors.values())
            assert str(text.cget('background')) == design.manager.palette.surface
            assert str(text.cget('foreground')) == design.manager.palette.text
            assert destructive.cget('style') == name('danger', destructive.plane, 'TButton')
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    window.destroy()


@pytest.mark.parametrize('language', ['en', 'ja'])
def test_embedded_settings_resize_the_visible_page_without_switching_tabs(design, language):
    from tests.test_v2_workspace import workspace
    design.manager.set_ui_language(language)
    window, shell, settings, _ = workspace(design)
    shell.show_primary('settings')
    window.update()
    page = settings.pages['general']
    for width in (1700, 640, 1120, 640):
        window.geometry(f'{width}x600')
        for _ in range(3):
            window.update()
        edge = window.winfo_rootx() + window.winfo_width()
        area = settings._areas['general']
        assert area.canvas.winfo_rootx() + area.canvas.winfo_width() <= edge
        for switch in page.switches.values():
            assert switch.winfo_ismapped(), {
                'requested_width': width, 'window': (window.winfo_width(), window.winfo_height()),
                'switch': str(switch), 'control': (switch.master.winfo_width(), switch.master.winfo_height()),
                'canvas': (area.canvas.winfo_width(), area.canvas.winfo_height()),
                'content': (area.content.winfo_width(), area.content.winfo_height()),
                'canvas_window_width': area.canvas.itemcget(area._window, 'width'),
                'control_manager': switch.master.winfo_manager(),
            }
            assert switch.winfo_rootx() + switch.winfo_width() <= edge
        assert page.restore_btn.winfo_rootx() + page.restore_btn.winfo_width() <= edge
    window.destroy()


@pytest.mark.parametrize('language', ['en', 'ja'])
@pytest.mark.parametrize('text_scale', [1.0, 1.5])
def test_settings_remap_after_resizing_a_different_page(design, language, text_scale):
    import _tkinter
    import time
    from tests.test_v2_workspace import workspace

    design.manager.set_ui_language(language)
    design.manager.set_text_scale(text_scale)
    window, shell, settings, _ = workspace(design)
    shell.show_primary('settings')
    window.geometry('1700x900')
    window.update()
    page = settings.pages['general']
    for width in (640, 1700, 760):
        settings.open_page('about')
        window.geometry(f'{width}x650')
        window.update_idletasks()
        settings.open_page('general')
        area = settings._areas['general']
        deadline = time.monotonic() + 4
        stable_since = None
        while time.monotonic() < deadline:
            if not window.tk.dooneevent(_tkinter.DONT_WAIT):
                time.sleep(.001)
            edge = area.winfo_rootx() + area.winfo_width()
            settled = (area.canvas.winfo_width() == area.content.winfo_width()
                       and area._layout_job is None and area._bar_job is None
                       and all(switch.winfo_rootx() + switch.winfo_width() <= edge
                               for switch in page.switches.values()))
            if settled:
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= .12:
                    break
            else:
                stable_since = None
        edge = area.winfo_rootx() + area.winfo_width()
        assert area.canvas.winfo_rootx() + area.canvas.winfo_width() <= edge
        assert area.canvas.winfo_height() == area.winfo_height()
        for switch in page.switches.values():
            assert switch.winfo_ismapped(), {
                'requested_width': width, 'window': (window.winfo_width(), window.winfo_height()),
                'switch': str(switch), 'control': (switch.master.winfo_width(), switch.master.winfo_height()),
                'canvas': (area.canvas.winfo_width(), area.canvas.winfo_height()),
                'content': (area.content.winfo_width(), area.content.winfo_height()),
                'canvas_window_width': area.canvas.itemcget(area._window, 'width'),
                'control_manager': switch.master.winfo_manager(),
            }
            assert switch.winfo_rootx() + switch.winfo_width() <= edge
        assert page.restore_btn.winfo_rootx() + page.restore_btn.winfo_width() <= edge
    window.destroy()


@pytest.mark.parametrize('language', ['en', 'ja'])
def test_large_text_home_actions_wrap_without_squeezing_controls(design, language):
    from src.constants import LANGUAGES
    from src.ui.translator_window import TranslatorWindow
    from tests.test_v2_home import make
    design.manager.set_ui_language(language)
    design.manager.set_text_scale(1.5)
    view = TranslatorWindow(design.root, SettingsConfig(), LANGUAGES)
    view.build('Source', 'English', recent=lambda: [], pinned=lambda: [])
    view.window.minsize(1, 1)
    view.show()
    for width in (650, 750, 960, 1230):
        view.window.geometry(f'{width}x900')
        for _ in range(3):
            view.window.update()
        for button in (view.options_btn, view.fix_grammar_btn, view.translate_btn):
            assert button.winfo_width() >= button.winfo_reqwidth()
    view.window.destroy()
    window, shell, _, _, _ = make(design)
    dictionary = shell.select_home('dictionary')
    for width in (650, 960, 1230):
        window.geometry(f'{width}x700')
        for _ in range(3):
            window.update()
        for button in (dictionary.lookup_button, dictionary.select_button, dictionary.pack_button):
            assert button.winfo_width() >= button.winfo_reqwidth()
    window.destroy()


@pytest.mark.parametrize('language', ['en', 'ja'])
def test_history_detail_keeps_two_readable_lines_with_a_short_viewport(design, language):
    from tkinter import font as tkfont
    from tests.test_v2_home import make, ENTRY
    design.manager.set_ui_language(language)
    design.manager.set_text_scale(1.5)
    window, shell, _, _, _ = make(design)
    history = shell.select_home('history')
    history.select_item(ENTRY)
    window.geometry('900x390')
    for _ in range(3):
        window.update()
    for box in history.detail_boxes:
        line = tkfont.Font(font=box.cget('font')).metrics('linespace')
        padding = 2 * sum(int(box.cget(key)) for key in ('pady', 'borderwidth', 'highlightthickness'))
        assert box.winfo_height() >= 2 * line + padding
    assert [box.get_plain() for box in history.detail_boxes] == [ENTRY['original'], ENTRY['translated']]
    assert history.detail_area.canvas.yview() != (0.0, 1.0)
    window.destroy()
