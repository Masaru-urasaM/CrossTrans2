"""Translation-first Show all, including linked scrolling in a one-row source."""
import pytest

from src.ui import quick_translate
from tests.final_ui_helpers import design
from tests.test_popup_alignment import FakeConfig, result
from tools.popup_gallery import settle


@pytest.mark.parametrize('ruby', [False, True])
def test_show_all_keeps_minimal_source_linkable_and_resize_preserves_priority(
        tk_root, monkeypatch, design, ruby):
    monkeypatch.setattr(quick_translate, 'get_monitor_work_area', lambda *args: (0, 0, 1280, 760))
    source = '\n'.join(f'{i}. 漢字を確認してください。' for i in range(60))
    value = result(source)
    manager = quick_translate.QuickTranslateManager(tk_root, FakeConfig(ruby=ruby))
    manager._last_mouse_x, manager._last_mouse_y = 300, 100
    try:
        manager.show(value, 'English', original=source, source_lang='Japanese')
        settle(manager, tk_root, compose_s=.01)
        before = manager.popup.geometry()
        controller = manager._linked_controller
        manager.popup_original_toggle.invoke()
        settle(manager, tk_root, compose_s=.01)
        assert manager._linked_controller is controller
        assert manager.popup_original_toggle.cget('text') == 'Show less'

        def check_priority():
            metrics = manager._layout['resize_metrics']
            heights = manager._layout['heights']
            room = manager.popup.winfo_height() - manager._layout['chrome']
            assert heights.translation_capped
            assert heights.original == metrics['min_original']
            assert heights.translation == room - heights.original
            assert manager._original_scrollbar.winfo_ismapped()
            assert manager.popup_text.get_plain() == value
            assert manager.popup_furigana.get_plain() == source

        check_priority()
        target_view = manager.popup_text.yview()
        controller.activate(1, len(controller.groups) - 1)
        settle(manager, tk_root, compose_s=.01)
        assert manager.popup_text.yview() == target_view
        assert manager.popup_furigana.yview()[0] > 0
        assert manager.popup_furigana.tag_ranges('linked_reading')
        start = manager.popup_furigana.tag_ranges('linked_reading')[0]
        assert manager.popup_furigana.bbox(start) is not None
        assert manager.popup_furigana.has_ruby is ruby

        manager._fit_boxes(reposition=False, size=(800, 550))
        settle(manager, tk_root, compose_s=.01)
        check_priority()
        manager._live_user_layout(800, 520)
        tk_root.update_idletasks()
        heights = manager._layout['heights']
        assert heights.original == manager._layout['resize_metrics']['min_original']
        assert heights.translation == 520 - manager._layout['chrome'] - heights.original
        manager.popup_original_toggle.invoke()
        settle(manager, tk_root, compose_s=.01)
        assert manager.popup.geometry() == before
    finally:
        manager.close()


def test_show_all_does_not_widen_just_for_a_long_source(tk_root, monkeypatch, design):
    monkeypatch.setattr(quick_translate, 'get_monitor_work_area', lambda *args: (0, 0, 1280, 760))
    source = '\n'.join(f'Source paragraph {i} with additional original details.' for i in range(120))
    translated = '\n'.join(f'Result {i}.' for i in range(8))
    manager = quick_translate.QuickTranslateManager(tk_root, FakeConfig(enabled=False))
    try:
        manager.show(translated, 'English', original=source)
        settle(manager, tk_root, compose_s=.01)
        width = manager.popup.winfo_width()
        manager.popup_original_toggle.invoke()
        settle(manager, tk_root, compose_s=.01)
        assert manager.popup.winfo_width() == width
        assert not manager._layout['heights'].translation_capped
        assert manager._layout['heights'].original_capped
        assert manager._layout['heights'].original > manager._layout['resize_metrics']['min_original']
        assert manager.popup_text.get_plain() == translated
        assert manager.popup_furigana.get_plain() == source
        assert manager._linked_controller is None
        assert manager.popup_linked_btn.winfo_exists()
    finally:
        manager.close()
