"""System preferences, readable native pairs, live text scaling and locale/data separation."""
import ctypes
from dataclasses import replace
import tkinter as tk
from tkinter import font

import pytest

from src.constants import LANGUAGES
from src.core import settings_index, key_testing, pack_catalog
from src.ui import components as ui, i18n
from src.ui.ruby_text import RubyText
from src.ui.theme import accessibility as A, fonts, tokens
from tests.final_ui_helpers import design  # noqa: F401


class WindowsPreferences:
    def __init__(self, contrast=True, animation=False, seconds=17):
        self.contrast, self.animation, self.seconds = contrast, animation, seconds
        self.actions = []

    def SystemParametersInfoW(self, action, size, destination, flags):
        self.actions.append(action)
        if action == A.SPI_GETHIGHCONTRAST:
            value = ctypes.cast(destination, ctypes.POINTER(A.HIGHCONTRAST)).contents
            assert size == value.cbSize == ctypes.sizeof(A.HIGHCONTRAST)
            value.dwFlags = A.HCF_HIGHCONTRASTON if self.contrast else 0
        elif action == A.SPI_GETCLIENTAREAANIMATION:
            ctypes.cast(destination, ctypes.POINTER(ctypes.c_int)).contents.value = self.animation
        elif action == A.SPI_GETMESSAGEDURATION:
            ctypes.cast(destination, ctypes.POINTER(ctypes.c_ulong)).contents.value = self.seconds
        return True

    def GetSysColor(self, index):
        return {A.COLOR_WINDOW: 0, A.COLOR_WINDOWTEXT: 0xFFFFFF, A.COLOR_HIGHLIGHT: 0xFFFF00,
                A.COLOR_HIGHLIGHTTEXT: 0, A.COLOR_HOTLIGHT: 0x00FFFF}[index]


def test_windows_preferences_use_documented_read_actions_and_duration_units():
    windows = WindowsPreferences()
    prefs = A.read_preferences(windows)
    assert windows.actions == [0x0042, 0x1042, 0x2016]
    assert prefs.high_contrast and not prefs.animations
    assert prefs.notification_ms == 17000
    assert prefs.colours == ('#000000', '#FFFFFF', '#00FFFF', '#000000', '#FFFF00')


def test_unsupported_preferences_have_safe_defaults():
    assert A.read_preferences(object()) == A.Preferences()


@pytest.mark.parametrize('value,expected', [(0x112233, '#332211'), (0, '#000000'), (0xFFFFFF, '#FFFFFF')])
def test_windows_colorref_byte_order(value, expected):
    assert A.colour_from_colorref(value) == expected


@pytest.mark.parametrize('value,expected', [(None, 1), ('bad', 1), (0.5, 1), (2, 1.5), ('1.3', 1.3)])
def test_text_scale_never_reduces_readability_or_exceeds_supported_range(value, expected):
    assert A.text_scale(value) == expected


def test_high_contrast_keeps_system_selected_text_pair_together():
    prefs = A.read_preferences(WindowsPreferences())
    palette = A.contrast_palette(prefs)
    assert palette.bg == palette.surface == palette.alt == prefs.colours[0]
    assert palette.text == palette.muted == prefs.colours[1]
    assert (palette.selection, palette.selection_text) == prefs.colours[2:4]
    assert palette.ruby == prefs.colours[4]


def test_preferences_refresh_notifies_only_after_a_real_change(design):
    notified = []
    design.manager.subscribe(lambda manager: notified.append(manager.high_contrast))
    assert not design.manager.refresh_accessibility()
    design.preferences[0] = A.read_preferences(WindowsPreferences())
    assert design.manager.refresh_accessibility()
    assert notified == [True]
    assert not design.manager.animations_enabled
    assert design.manager.notification_duration_ms == 17000
    assert not design.manager.refresh_accessibility()


def test_live_text_scale_resizes_ruby_without_changing_the_text(design):
    box = RubyText(design.root, base_font=design.ctx.font('reading'),
                    ruby_font=design.ctx.font('caption', 'japanese'), bg=design.ctx.palette.surface)
    box.insert_notation('1.0', '{猫|ねこ} and Copy')
    before = abs(box.base_font[1])
    design.manager.set_text_scale(1.5)
    design.root.update_idletasks()
    assert abs(box.base_font[1]) >= round(before * 1.5)
    assert box.get_plain() == '猫 and Copy'
    assert box.ruby_pairs == 1
    assert design.store.get('text_scale') == 1.5


def test_high_contrast_round_trip_preserves_ruby_content_and_semantic_colours(design):
    p = design.ctx.palette
    box = RubyText(design.root, bg=p.surface, base_fg=p.text, kanji_fg=p.text, ruby_fg=p.ruby)
    box.insert_notation('1.0', '{猫|ねこ} Copy')
    box.highlight_plain_range((0, 1))
    design.preferences[0] = A.read_preferences(WindowsPreferences())
    design.manager.refresh_accessibility()
    box.highlight_plain_range((0, 1))
    assert box.get_plain() == '猫 Copy'
    assert str(box.cget('bg')).upper() == '#000000'
    assert box._ruby_fg.upper() == '#FFFF00'
    design.preferences[0] = A.Preferences(notification_ms=0)
    design.manager.refresh_accessibility()
    assert str(box.cget('bg')).upper() == p.surface.upper()
    assert box._ruby_fg.upper() == p.ruby.upper()
    assert box.get_plain() == '猫 Copy'


def test_linked_reading_has_a_noncolour_cue_in_high_contrast(design):
    box = RubyText(design.root)
    box.set_plain('Original sentence.')
    design.preferences[0] = A.read_preferences(WindowsPreferences())
    design.manager.refresh_accessibility()
    box.highlight_plain_range((0, len('Original sentence.')), animate=True)
    assert int(box.tag_cget('linked_reading', 'underline')) == 1
    assert box._linked_animation is None


def test_reduced_motion_progress_retains_working_state(design):
    progress = ui.Progress(design.root, mode='indeterminate')
    design.preferences[0] = replace(design.preferences[0], animations=False)
    design.manager.refresh_accessibility()
    progress.start()
    assert progress._running
    assert not design.manager.animations_enabled
    progress.stop()
    assert not progress._running


def test_language_changes_only_presentation_labels_and_keeps_typed_text(design):
    label = ui.Label(design.root, 'Copy')
    data = ui.Label(design.root, 'Copy', localize=False)
    variable = tk.StringVar(design.root, '')
    field = ui.TextField(design.root, placeholder='Search settings', textvariable=variable)
    design.manager.set_ui_language('ja')
    assert label.cget('text') == 'コピー'
    assert data.cget('text') == 'Copy'
    assert field.get() == variable.get() == ''
    assert field.showing_placeholder and field._placeholder == '設定を検索'
    field.set('Copy {customer}')
    design.manager.set_ui_language('en')
    assert label.cget('text') == 'Copy'
    assert field.get() == variable.get() == 'Copy {customer}'
    assert design.store.get('ui_language') == 'en'


def test_japanese_style_uses_the_installed_japanese_font_chain(design):
    design.manager.set_ui_language('ja')
    expected = fonts.resolve_family(fonts.CHAINS[('japanese', 'regular')], design.ctx.families)
    assert design.ctx.font('body')[0] == expected
    label = ui.Label(design.root, 'Settings')
    actual = design.ctx.style.lookup(label.cget('style'), 'font')
    assert font.Font(design.root, font=actual).actual('family').lower() == expected.lower()


def test_model_ids_that_look_like_ui_words_remain_exact(design):
    picker = ui.ModelPicker(design.root, ['Auto', 'Natural', 'Copy'], 'Natural', lambda value: None)
    design.manager.set_ui_language('ja')
    assert picker.value == picker.button.cget('text') == 'Natural'
    picker.set('Auto')
    assert picker.value == 'Auto' and picker.button.cget('text') == '自動'
    picker.set('Copy')
    design.manager.set_ui_language('en')
    assert picker.value == picker.button.cget('text') == 'Copy'


def test_dropdown_translates_labels_and_preserves_command_values(design):
    chosen = []
    dropdown = ui.Dropdown(design.root, [('light', 'Light'), ('dark', 'Dark')], 'light', chosen.append)
    design.manager.set_ui_language('ja')
    assert dropdown.button.cget('text') == 'ライト'
    dropdown.choose(1)
    assert chosen == ['dark'] and dropdown.value == 'dark'
    assert dropdown.button.cget('text') == 'ダーク'


def test_native_menu_changes_language_without_rebinding_commands(design):
    invoked = []
    menu = ui.Menu(design.root, tearoff=False)
    menu.add_command(label='Copy', command=lambda: invoked.append('same callback'))
    design.manager.set_ui_language('ja')
    assert menu.entrycget(0, 'label') == 'コピー'
    menu.invoke(0)
    assert invoked == ['same callback']


def test_open_window_title_switches_both_ways(design):
    from src.ui.theme import engine
    window = tk.Toplevel(design.root)
    window.title('Settings - CrossTrans')
    engine.attach(window)
    design.manager.set_ui_language('ja')
    assert window.title() == '設定 - CrossTrans'
    design.manager.set_ui_language('en')
    assert window.title() == 'Settings - CrossTrans'


@pytest.mark.parametrize('query,key', [('文字サイズ', 'text_size'), ('表示言語', 'ui_language'),
                                      ('ふりがな', 'furigana'), ('ダーク', 'theme')])
def test_settings_search_finds_japanese_terms_without_changing_ids(query, key):
    assert key in [entry.key for entry in settings_index.search(query)]


def test_every_translation_language_has_a_japanese_display_name():
    assert all(name in i18n.catalog() and i18n.catalog()[name]['ja'] for name, _, _ in LANGUAGES)


@pytest.mark.parametrize('source,expected', [
    ('Japanese → English', '日本語 → 英語'),
    ('Translated with Copy-model', '翻訳済み：Copy-model'),
    ('Installing Japanese (2 of 3)...', '日本語をインストール中（2 / 3）…'),
    ('Works with Copy-model. It can read files.', 'Copy-modelで利用できます。 ファイルに対応しています。'),
])
def test_composed_status_messages_preserve_data_and_translate_ui(source, expected, design):
    design.manager.set_ui_language('ja')
    assert i18n.tr(source) == expected


def test_catalog_coverage_and_voice_lint():
    from tools import ui_strings
    sources, findings = ui_strings.audit()
    assert len(sources) >= 300
    assert not findings, findings


def test_repeated_text_size_changes_return_to_the_exact_original_metrics(design):
    # Odd pixel sizes expose cumulative rounding that even token sizes can conceal.
    box = RubyText(design.root, base_font=(design.ctx.font('reading')[0], -17),
                    ruby_font=(design.ctx.font('caption', 'japanese')[0], -11))
    box.insert_notation('1.0', '{猫|ねこ} and Copy')
    original = box.base_font, box.ruby_font
    for scale in [1.15, 1.3, 1.5, 1.0] * 3:
        design.manager.set_text_scale(scale)
    assert (box.base_font, box.ruby_font) == original
    assert box.get_plain() == '猫 and Copy'


def test_recolour_preserves_ruby_widgets_custom_tags_and_selection(design):
    box = RubyText(design.root)
    box.insert_notation('1.0', '{猫|ねこ} and Copy')
    box.tag_add('custom_mark', '1.0', '1.3')
    box.tag_add('sel', '1.0', '1.3')
    frames = list(box._frames)
    selected, custom = box.tag_ranges('sel'), box.tag_ranges('custom_mark')
    box.recolour(bg=tokens.DARK.surface, base_fg=tokens.DARK.text, ruby_fg=tokens.DARK.ruby)
    assert list(box._frames) == frames
    assert box.tag_ranges('sel') == selected and box.tag_ranges('custom_mark') == custom
    assert box.get_plain() == '猫 and Copy'


def test_unsubscribing_releases_the_bound_receiver_without_waiting_for_gc(design):
    import weakref

    class Receiver:
        def changed(self, manager):
            pass

    receiver = Receiver()
    reference = weakref.ref(receiver)
    stop = design.manager.subscribe(receiver.changed)
    stop()
    del receiver
    assert reference() is None


def test_scroll_timer_is_root_owned_and_cancelled_on_destroy(design):
    area = ui.ScrollArea(design.root)
    area._on_scroll('0', '0.5')
    job = area._bar_job
    command = design.root.tk.splitlist(design.root.tk.call('after', 'info', job))[0]
    assert command in design.root._tclCommands
    area.destroy()
    assert job not in design.root.tk.splitlist(design.root.tk.call('after', 'info'))


def test_unreachable_ui_cycles_are_collected_before_background_work(design):
    import gc
    import threading
    from src.ui.settings.background import BackgroundRunner
    from tests.final_ui_helpers import pump
    collected = []
    main_thread = threading.get_ident()

    class Cycle:
        def __del__(self):
            collected.append(threading.get_ident())

    enabled = gc.isenabled()
    gc.disable()
    try:
        marker = Cycle()
        marker.self = marker
        del marker
        runner = BackgroundRunner(design.root)
        finished = []
        runner.start('gc', lambda progress, cancelled: gc.collect(),
                      on_done=lambda result, error: finished.append(error))
        pump(design.root, lambda: bool(finished))
    finally:
        if enabled:
            gc.enable()
    assert collected == [main_thread]
    assert finished == [None]


def test_notification_duration_respects_the_windows_minimum(design, monkeypatch):
    from src.ui.toast import ToastManager
    design.preferences[0] = replace(design.preferences[0], notification_ms=12000)
    design.manager.refresh_accessibility()
    manager = ToastManager(design.root)
    manager.FADE_IN_STEP = 1
    durations = []
    monkeypatch.setattr(manager, '_schedule_dismiss', lambda toast, duration: durations.append(duration))
    manager.show('Notice', duration=10)
    assert durations == [12000]
    manager.dismiss_all()


def test_segmented_choices_wrap_and_keep_the_selected_value(design):
    window = tk.Toplevel(design.root)
    window.geometry('310x300+0+0')
    design.manager.set_ui_language('ja')
    design.manager.set_text_scale(1.5)
    host = ui.Frame(window)
    host.pack(fill='x')
    choices = ui.SegmentedControl(host, [('system', 'Same as Windows'), ('light', 'Light'), ('dark', 'Dark')], 'dark')
    choices.pack(anchor='w')
    window.update()
    assert len({button.grid_info()['row'] for button in choices.buttons}) > 1
    assert all(button.winfo_width() >= button.winfo_reqwidth() for button in choices.buttons)
    assert choices.value == 'dark'


def test_model_label_elides_in_pixels_without_changing_the_model(design):
    from tkinter import font as tkfont
    window = tk.Toplevel(design.root)
    window.geometry('240x160+0+0')
    model = 'provider/' + 'a-long-model-name-' * 5
    picker = ui.ModelPicker(window, [model], model, lambda name: None)
    picker.pack(fill='x')
    window.update()
    assert picker.value == model
    assert str(picker.button.cget('text')) != model
    font = tkfont.Font(root=window, font=design.ctx.font('body'))
    assert font.measure(picker.button.cget('text')) < picker.button.winfo_width()


def test_short_translator_scrolls_with_three_text_rows_and_pinned_actions(design):
    from tkinter import font as tkfont
    from src.ui.translator_window import TranslatorWindow
    from tests.settings_support import SettingsConfig
    design.manager.set_ui_language('ja')
    design.manager.set_text_scale(1.5)
    view = TranslatorWindow(design.root, SettingsConfig(), LANGUAGES)
    window = view.build('Some source text', 'English', recent=lambda: [], pinned=lambda: [])
    window.minsize(1, 1)
    window.geometry('590x340+0+0')
    view.show()
    window.update()
    view.show_error('The key was refused or its quota is used up. Check it in Settings or try another key.')
    window.update()
    line = tkfont.Font(root=window, font=design.ctx.font('reading')).metrics('linespace')
    assert view.original_text.winfo_height() >= 3 * line
    assert view.output_host.winfo_height() >= 3 * line
    assert view.body_area.canvas.yview()[1] < 1
    assert view.translate_btn.winfo_rooty() + view.translate_btn.winfo_height() <= window.winfo_rooty() + window.winfo_height()
    view.body_area.reveal(view.copy_btn)
    window.update()
    assert view.copy_btn.winfo_rooty() + view.copy_btn.winfo_height() <= view.body_area.canvas.winfo_rooty() + view.body_area.canvas.winfo_height()


@pytest.mark.parametrize('case_key', ['K', 'L'])
def test_large_japanese_chrome_is_measured_before_reveal(design, monkeypatch, case_key):
    from src.ui import popup_layout, quick_translate
    from src.ui.quick_translate import QuickTranslateManager
    from tests import popup_cases
    from tests.test_popup_geometry import put_up
    design.manager.set_ui_language('ja')
    design.manager.set_text_scale(1.5)
    work = popup_layout.WorkArea(0, 0, 680, 480)
    manager = QuickTranslateManager(design.root, popup_cases.FakeConfig())
    monkeypatch.setattr(manager, '_work_area', lambda: work)
    manager._last_mouse_x, manager._last_mouse_y = 200, work.height // 2
    try:
        put_up(manager, design.root, popup_cases.case(case_key))
        assert manager._layout['done'] and 'heights' in manager._layout
        assert manager.popup_text.winfo_width() > 1
        assert manager.popup_text.winfo_height() > 1
        assert manager.popup.winfo_height() <= work.height
        for button in manager._btn_frame.winfo_children():
            if button is manager.popup_linked_btn and not manager._linked_enabled():
                assert not button.winfo_manager()
                continue
            assert button.winfo_height() >= button.winfo_reqheight()
    finally:
        manager.close()


@pytest.mark.parametrize('anchor,area,expected', [
    ((-1500, 800, 80, 30), (-1920, 0, 0, 900), (-1560, 716)),
    ((-1910, -980, 50, 30), (-1920, -1000, 0, 0), (-1920, -944)),
    ((1250, 660, 80, 30), (0, 0, 1366, 728), (1166, 576)),
])
def test_tooltip_placement_respects_negative_monitor_origins(anchor, area, expected):
    from src.ui.components.feedback import tooltip_position
    assert tooltip_position(anchor, (200, 78), area, 6) == expected


def test_monitor_query_uses_the_nearest_monitor_and_has_a_fallback(monkeypatch):
    import sys
    from types import SimpleNamespace
    from src.ui.theme import windows
    calls = []
    api = SimpleNamespace(MonitorFromPoint=lambda point, flags: calls.append((point, flags)) or 7,
                          GetMonitorInfo=lambda handle: {'Work': (-1920, -1000, 0, 0)})
    monkeypatch.setitem(sys.modules, 'win32api', api)
    assert windows.monitor_work_area(-400, -50) == (-1920, -1000, 0, 0)
    assert calls == [((-400, -50), 2)]
    api.GetMonitorInfo = lambda handle: (_ for _ in ()).throw(OSError('unavailable'))
    assert windows.monitor_work_area(0, 0) is None


def test_tooltip_timer_belongs_to_the_root_and_is_cancelled(design):
    from src.ui.components.feedback import Tooltip
    button = ui.Button(design.root, 'Copy')
    tooltip = Tooltip(button, 'Copy')
    tooltip._schedule()
    job = tooltip._job
    command = design.root.tk.splitlist(design.root.tk.call('after', 'info', job))[0]
    assert command in design.root._tclCommands
    button.destroy()
    assert job not in design.root.tk.splitlist(design.root.tk.call('after', 'info'))


@pytest.mark.parametrize('room,translation,original,expected', [
    (400, 100, 200, (100, 200)), (200, 100, 200, (66, 134)),
    (80, 100, 0, (80, 0)), (0, 100, 200, (0, 0)),
])
def test_popup_row_floors_fit_the_physical_screen_budget(room, translation, original, expected):
    from src.ui.popup_layout import bounded_minimums
    assert bounded_minimums(room, translation, original) == expected
