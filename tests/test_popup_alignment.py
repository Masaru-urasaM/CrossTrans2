"""Exercise the real popup's linked-reading lifecycle on the private desktop."""
import json
from types import SimpleNamespace
import time
from unittest.mock import Mock

import pytest

from src.core.translation_alignment import AlignedText, make_metadata, parse_answer, segment_source
from src.ui.quick_translate import QuickTranslateManager
from src.ui import linked_reading


class FakeConfig:
    def __init__(self, enabled=True, ruby=False):
        self.enabled = enabled
        self.ruby = ruby

    def get_linked_reading_enabled(self):
        return self.enabled

    def set_linked_reading_enabled(self, enabled):
        self.enabled = enabled

    def get_furigana_enabled(self):
        return self.ruby

    def get_quick_replace(self):
        return False


def result(source='First. Second.', translated=None, target='English'):
    units = segment_source(source)
    rows = [{'source_ids': [u.id], 'text': translated or f'Result {u.id}.'} for u in units]
    parsed = parse_answer(json.dumps({'version': 1, 'segments': rows}), source, target)
    return AlignedText(parsed.text, parsed.metadata)


def settle(root):
    root.update()
    time.sleep(0.015)
    root.update()


@pytest.fixture
def manager(tk_root, monkeypatch):
    monkeypatch.setattr(linked_reading, 'LINKED_READING_DELAY_MS', 1)
    mgr = QuickTranslateManager(tk_root, FakeConfig())
    yield mgr
    mgr.close()
    settle(tk_root)


def show(manager, root, source='First. Second.', translated=None, target='English'):
    value = result(source, translated, target)
    manager.show(value, target, original=source)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        root.update()
        layout = manager._layout or {}
        if layout.get('done') and layout.get('revealed') and layout.get('size'):
            break
        time.sleep(.005)
    assert manager._layout.get('size') and manager._layout.get('revealed')
    return value, manager._linked_controller


def hit(box, offset):
    starts, *_ = box.prepare_plain_map()
    x, y, width, height = box.bbox(starts[offset])
    return box.winfo_rootx() + x + 1, box.winfo_rooty() + y + height // 2


def test_original_without_furigana_and_hover_both_directions(manager, tk_root):
    value, controller = show(manager, tk_root)
    assert manager.popup_furigana.get_plain() == 'First. Second.'
    controller.motion(0, *hit(manager.popup_furigana, 0))
    settle(tk_root)
    assert controller.active == (0, 0)
    assert manager.popup_text.tag_ranges('linked_reading')
    group = value.alignment['groups'][1]
    controller.motion(1, *hit(manager.popup_text, group['translated'][0]))
    settle(tk_root)
    assert controller.active == (1, 1)
    assert manager.popup_furigana.tag_ranges('linked_reading')
    assert manager.popup_furigana.get_plain(*manager.popup_furigana.tag_ranges('linked_reading')) == 'Second.'
    assert manager.popup_text.get_plain() == value


@pytest.mark.parametrize('ruby', [True, False])
def test_original_is_visible_with_linking_off_and_japanese_output(manager, tk_root, ruby):
    manager.config.enabled = False
    manager.config.ruby = ruby
    source = 'Select Quit to close the old version.\nRun the test EXE.'
    translated = '古いバージョンを終了してください。\nテスト版を実行してください。'
    manager.show(translated, 'Japanese', original=source, source_lang='English')
    settle(tk_root)
    assert manager.popup_furigana is not None
    assert manager.popup_furigana.winfo_ismapped()
    assert manager.popup_furigana.get_plain() == source
    assert not manager.popup_furigana.has_ruby
    assert manager.popup_text.get_plain() == translated
    assert manager.popup_text.has_ruby is ruby
    assert getattr(manager, '_linked_controller', None) is None
    assert manager.config.enabled is False


def test_debounce_and_close_cancel_pending_work(manager, tk_root):
    value, controller = show(manager, tk_root)
    controller.motion(0, *hit(manager.popup_furigana, 0))
    first_timer = controller.timer
    controller.motion(0, *hit(manager.popup_furigana, 7))
    assert controller.timer != first_timer
    manager.close()
    settle(tk_root)
    assert controller.closed and controller.timer is None


def test_external_destroy_before_timer_does_not_raise(manager, tk_root):
    value, controller = show(manager, tk_root)
    controller.motion(0, *hit(manager.popup_furigana, 0))
    manager.popup.destroy()
    settle(tk_root)
    assert controller.closed and controller.timer is None


def test_pointer_recovers_after_release_outside_the_box(manager, tk_root):
    value, controller = show(manager, tk_root)
    controller.press()
    controller.motion(0, *hit(manager.popup_furigana, 0), state=0)
    settle(tk_root)
    assert not controller.dragging and controller.active == (0, 0)


def test_error_and_unsupported_results_never_reuse_old_map(manager, tk_root):
    value, controller = show(manager, tk_root)
    manager.show('Error: failed', 'English', original='First. Second.')
    settle(tk_root)
    assert controller.closed and manager._linked_controller is None
    manager.show('Plain answer.', 'English', original='Other source.')
    settle(tk_root)
    assert manager._linked_controller is None
    assert manager._current_alignment is None
    assert str(manager.popup_linked_btn.cget('state')) == 'disabled'


def test_corrupt_metadata_and_off_setting_do_not_link(manager, tk_root):
    value = result()
    value.alignment['target_hash'] = 'bad'
    manager.show(value, 'English', original='First. Second.')
    assert manager._current_alignment is None
    assert manager._linked_controller is None
    manager.config.enabled = False
    manager.show(result(), 'English', original='First. Second.')
    assert manager.popup_furigana is not None
    assert manager.popup_furigana.get_plain() == 'First. Second.'
    assert getattr(manager, '_linked_controller', None) is None


def settings_page(tk_root, config):
    """The Translation page of the Settings window, built against a stand-in window."""
    import tkinter as tk
    from src.ui.settings.pages_basic import TranslationPage
    from tests.settings_support import Host
    window = tk.Toplevel(tk_root)
    window.geometry('760x760+0+0')
    host = Host(window, config)
    page = TranslationPage(host)
    page.build(window)
    page.body.pack(fill='both', expand=True)
    window.update()
    return window, page


def test_settings_checkbox_starts_enabled_and_persists_both_toggles(tk_root):
    from tests.settings_support import SettingsConfig
    cfg = SettingsConfig()
    del cfg._config['linked_reading_enabled']          # a file written before the key existed
    window, page = settings_page(tk_root, cfg)
    try:
        button = page.switches['linked_reading']
        assert cfg.get_linked_reading_enabled() is True and page.variables['linked_reading'].get() is True
        button.invoke()
        assert cfg.get_linked_reading_enabled() is False
        button.invoke()
        assert cfg.get_linked_reading_enabled() is True
        assert cfg.saves == 2
    finally:
        window.destroy()


def test_toolbar_toggle_restores_existing_links_without_retranslation(manager, tk_root):
    value, controller = show(manager, tk_root)
    manager.config.set_linked_reading_enabled = Mock(
        side_effect=manager.config.set_linked_reading_enabled)
    manager._on_re_translate = Mock(side_effect=AssertionError('Toggle called the API path'))
    button = manager.popup_linked_btn
    assert button.winfo_ismapped() and button.action_name == 'Linked reading'
    assert manager._linked_tooltip.text.startswith('Linked reading: On.')
    button.invoke()
    settle(tk_root)
    assert controller.closed and manager._linked_controller is None
    assert manager._linked_tooltip.text.startswith('Linked reading: Off.')
    button.invoke()
    settle(tk_root)
    assert manager._linked_controller is not None
    assert manager._linked_controller is not controller
    manager.config.set_linked_reading_enabled.assert_not_called()
    assert manager.popup_text.get_plain() == value
    manager._on_re_translate.assert_not_called()


def test_enabling_generation_does_not_invent_links_for_an_existing_plain_result(manager, tk_root):
    manager.config.enabled = False
    manager._on_re_translate = Mock()
    manager.show('Plain result.', 'English', original='Plain source.')
    settle(tk_root)
    manager.config.enabled = True
    manager._sync_linked_reading()
    settle(tk_root)
    assert manager._linked_controller is None and manager._current_alignment is None
    assert str(manager.popup_linked_btn.cget('state')) == 'disabled'
    manager._on_re_translate.assert_not_called()


def test_popup_reading_switch_is_local_while_settings_controls_generation(manager, tk_root):
    from tests.settings_support import SettingsConfig

    class SharedConfig(SettingsConfig):
        """Settings' configuration, with the setting the popup also reads and writes held by the popup's."""
        def get_linked_reading_enabled(self):
            return manager.config.get_linked_reading_enabled()

        def set_linked_reading_enabled(self, enabled):
            manager.config.set_linked_reading_enabled(enabled)

    value, controller = show(manager, tk_root)
    window, page = settings_page(tk_root, SharedConfig())
    try:
        checkbox = page.switches['linked_reading']
        manager.popup_linked_btn.invoke()
        settle(tk_root)
        assert page.variables['linked_reading'].get() is True
        checkbox.invoke()
        settle(tk_root)
        assert manager._linked_controller is None
        assert not manager.popup_linked_btn.winfo_manager()
        checkbox.invoke()
        settle(tk_root)
        assert manager.popup_linked_btn.winfo_manager()
        manager.popup_linked_btn.invoke()
        settle(tk_root)
        assert manager._linked_controller is not None
    finally:
        window.destroy()


@pytest.mark.parametrize('mode', ['edit', 'preview'])
def test_settings_changes_do_not_restart_links_in_edit_or_preview(manager, tk_root, mode):
    show(manager, tk_root)
    if mode == 'edit':
        manager._enter_custom_prompt_mode()
    else:
        manager._show_replace_preview()
    manager.config.enabled = False
    manager.root.event_generate('<<LinkedReadingChanged>>', when='tail')
    settle(tk_root)
    manager.config.enabled = True
    manager.root.event_generate('<<LinkedReadingChanged>>', when='tail')
    settle(tk_root)
    assert manager._linked_controller is None


def test_external_popup_destroy_releases_setting_binding(manager, tk_root):
    show(manager, tk_root)
    assert manager._linked_setting_binding is not None
    manager.popup.destroy()
    settle(tk_root)
    assert manager._linked_setting_binding is None
    manager.root.event_generate('<<LinkedReadingChanged>>', when='tail')
    settle(tk_root)


def test_custom_edit_flattens_and_cancel_restores_exact_pair(manager, tk_root):
    value, controller = show(manager, tk_root)
    manager._enter_custom_prompt_mode()
    assert controller.closed and manager._linked_controller is None
    manager.popup_text.insert('end', ' Changed.')
    manager._handle_custom_prompt_cancel()
    settle(tk_root)
    assert manager._linked_controller is not None
    assert manager.popup_text.get_plain() == value


def test_replace_preview_suspends_links_and_preserves_text(manager, tk_root):
    value, controller = show(manager, tk_root)
    manager._show_replace_preview()
    assert controller.closed and manager._linked_controller is None
    assert value in manager.popup_text.get_plain()
    assert 'First. Second.' == manager.popup_furigana.get_plain()
    assert manager.popup_furigana.tag_ranges('replace_preview')


def test_selection_and_resizing_suspend_hover(manager, tk_root):
    value, controller = show(manager, tk_root)
    controller.press()
    controller.motion(0, *hit(manager.popup_furigana, 0))
    assert controller.timer is None and controller.active is None
    controller.release()
    manager._grip_start = {'pointer': (0, 0)}
    controller.motion(0, *hit(manager.popup_furigana, 0))
    assert controller.timer is None
    manager._grip_start = None
    controller.motion(0, *hit(manager.popup_furigana, 0))
    settle(tk_root)
    assert controller.active == (0, 0)


def test_furigana_labels_have_live_hover_bindings(manager, tk_root):
    manager.config.ruby = True
    value, controller = show(manager, tk_root, '私は毎日日本語を勉強します。次です。')
    assert manager.popup_furigana.has_ruby
    for part in manager.popup_furigana.linked_event_parts():
        assert part.bind('<Motion>')
    frame = manager.popup_furigana._frames[0]
    controller.motion(0, frame.winfo_rootx() + 2, frame.winfo_rooty() + 2)
    settle(tk_root)
    assert controller.active == (0, 0)


def test_scrollbar_hooks_survive_refit_and_restore_on_close(manager, tk_root):
    source = '\n'.join(f'Line {i}.' for i in range(60))
    value, controller = show(manager, tk_root, source)
    manager._toggle_original()
    settle(tk_root)
    for box in controller.boxes:
        assert str(box.cget('yscrollcommand')) == controller.scrolls[box]['command']
    controller.close()
    assert controller.closed


def test_counterpart_scroll_does_not_scroll_reading_box_or_expand_original(manager, tk_root):
    source = '\n'.join(f'Line {i}.' for i in range(60))
    value, controller = show(manager, tk_root, source)
    before = manager.popup_text.yview()
    controller.activate(1, 50)
    settle(tk_root)
    assert manager.popup_text.yview() == before
    assert manager.popup_furigana.yview()[0] > 0
    assert manager._original_collapsed
    assert controller.active == (1, 50)


def test_wheel_over_stationary_pointer_refreshes_owner_not_counterpart(manager, tk_root, monkeypatch):
    source = '\n'.join(f'Line {i}.' for i in range(60))
    value, controller = show(manager, tk_root, source)
    point = hit(manager.popup_furigana, 0)
    monkeypatch.setattr(tk_root, 'winfo_pointerxy', lambda: point)
    controller.motion(0, *point)
    settle(tk_root)
    manager.popup_furigana.yview_scroll(5, 'units')
    settle(tk_root)
    settle(tk_root)
    assert controller.active is not None and controller.active[1] > 0


def test_resize_does_not_revive_hover_after_pointer_left_for_window_edge(manager, tk_root, monkeypatch):
    _value, controller = show(manager, tk_root)
    point = hit(manager.popup_furigana, 0)
    monkeypatch.setattr(tk_root, 'winfo_pointerxy', lambda: point)
    controller.motion(0, *point)
    settle(tk_root)
    assert controller.active == (0, 0)
    counterpart = Mock(wraps=manager.popup_text.see_plain_range)
    monkeypatch.setattr(manager.popup_text, 'see_plain_range', counterpart)
    monkeypatch.setattr(tk_root, 'winfo_pointerxy', lambda: (-5000, -5000))
    controller.geometry_changed(side=0)
    settle(tk_root)
    assert controller.active is None and controller.timer is None
    assert not manager.popup_text.tag_ranges('linked_reading')
    counterpart.assert_not_called()


@pytest.mark.parametrize('length', [3000, 10000, 50000])
def test_large_display_pair_uses_cached_map_without_layout_on_hover(manager, tk_root, length):
    paragraph = 'Alpha sentence. ' * 8 + '\n\n'
    target_paragraph = 'Beta sentence. ' * 8 + '\n\n'
    source = (paragraph * (length // len(paragraph) + 1))[:length]
    translated = (target_paragraph * (length // len(target_paragraph) + 1))[:length]
    data = make_metadata(source, translated, 'English', 'text', [
        {'source': [0, length], 'translated': [0, length]}])
    manager.show(AlignedText(translated, data), 'English', original=source)
    settle(tk_root)
    controller = manager._linked_controller
    manager._fit_boxes = Mock(side_effect=AssertionError('Hover re-laid out popup'))
    controller.activate(0, 0)
    settle(tk_root)
    assert manager.popup_text.get_plain() == translated
    manager._fit_boxes.assert_not_called()

def test_provisional_reveal_does_not_seal_layout_before_text_is_measurable(manager, tk_root, monkeypatch):
    value, controller = show(manager, tk_root, '\n'.join(f'Line {index}.' for index in range(60)))
    manager._layout['done'] = False
    manager._layout.pop('size', None)
    width = manager.popup_text.winfo_width
    monkeypatch.setattr(manager.popup_text, 'winfo_width', lambda: 1)
    manager._finish_layout(manager._generation, forced=True)
    assert not manager._layout['done']
    assert manager._layout.get('revealed')
    monkeypatch.setattr(manager.popup_text, 'winfo_width', width)
    manager._finish_layout(manager._generation)
    settle(tk_root)
    assert manager._layout['done'] and manager._layout.get('size')
    assert manager.popup_furigana.winfo_height() > 1
