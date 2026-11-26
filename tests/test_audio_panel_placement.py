"""The audio panel's result opens directly under the panel, and the panel and Settings show each other's changes
(CHANGELOG Phase 30, Decision 88).
"""
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.ui import popup_layout
from src.ui.audio_panel import AudioPanel
from tests.final_ui_helpers import design, pump  # noqa: F401  (fixture)
from tests.test_audio_panel_controller import Capture, panel_run  # noqa: F401  (fixture)
from tests.test_audio_workflow import controller  # noqa: F401  (fixture)

WORK = popup_layout.WorkArea(0, 0, 1600, 900)


def settle(root, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.005)

MARGIN, GAP = popup_layout.SCREEN_MARGIN, popup_layout.ANCHOR_GAP


class TestPlaceBelow:
    def test_directly_below_with_the_left_edges_aligned(self):
        assert popup_layout.place_below((200, 100, 500, 400), 600, 300, WORK, min_height=150) == (200, 508, 300)

    def test_above_when_only_that_side_holds_it(self):
        x, y, height = popup_layout.place_below((200, 600, 500, 250), 600, 300, WORK, min_height=150)
        assert (x, height) == (200, 300) and y + height == 600 - GAP

    def test_the_side_with_more_room_with_the_height_cut(self):
        x, y, height = popup_layout.place_below((200, 300, 500, 300), 600, 700, WORK, min_height=150)
        assert y == 600 + GAP and height == WORK.bottom - MARGIN - y, 'below has more room: cut to it'

    def test_pinned_inside_the_work_area_when_no_side_holds_the_minimum(self):
        x, y, height = popup_layout.place_below((200, 60, 500, 800), 600, 700, WORK, min_height=400)
        assert WORK.top + MARGIN <= y and y + height <= WORK.bottom - MARGIN

    def test_kept_inside_the_work_area_sideways(self):
        x, _, _ = popup_layout.place_below((1300, 100, 300, 200), 600, 300, WORK, min_height=150)
        assert x + 600 == WORK.right - MARGIN


class TestTheResultUnderThePanel:
    def test_a_panel_recording_opens_its_result_under_the_panel(self, panel_run):
        controller = panel_run.controller
        quick = controller.app.quick_translate_manager
        quick._last_mouse_x, quick._last_mouse_y = 1400, 800          # the cursor is elsewhere
        controller.result_anchor = lambda: (120, 90, 420, 360)
        controller.start_recording('panel')
        pump(controller.root, lambda: controller.state == 'recording')
        controller.stop_recording()
        pump(controller.root, lambda: controller.state == 'result' and quick.popup is not None
             and quick.popup.winfo_ismapped())
        settle(controller.root, 0.6)                                     # the measured layout settles
        assert quick._anchor_rect == (120, 90, 420, 360)
        top = quick.popup.winfo_y()
        assert top >= 90 + 360, 'under the panel, never over it'
        assert quick.popup.winfo_x() <= 120 + 420

    def test_the_next_hotkey_opens_at_the_cursor_again(self, controller):
        quick = controller.app.quick_translate_manager
        quick.anchor_below((120, 90, 420, 360))
        quick.capture_mouse_position()
        assert quick._anchor_rect is None

    def test_with_the_panel_gone_the_result_opens_at_the_cursor(self, controller):
        quick = controller.app.quick_translate_manager
        quick.anchor_below((120, 90, 420, 360))
        controller.result_anchor = lambda: None
        controller._anchor_result()
        assert quick._anchor_rect is None

    def test_a_held_recording_does_not_move_under_the_panel(self, panel_run):
        controller = panel_run.controller
        calls = []
        controller.result_anchor = lambda: calls.append(True) or (120, 90, 420, 360)
        controller.start_recording()
        pump(controller.root, lambda: controller.state == 'recording')
        controller.stop_recording()
        pump(controller.root, lambda: controller.state == 'result')
        assert calls == [] and controller.app.quick_translate_manager._anchor_rect is None


class TestSettings:
    def test_the_audio_section_offers_the_shortcut_action_and_stores_it(self, design):
        import tkinter as tk
        from tests.settings_support import Host, SettingsConfig
        from src.ui.settings.audio_settings import HOTKEY_ACTIONS
        from src.ui.settings.page_providers import ProvidersPage
        config = SettingsConfig()
        window = tk.Toplevel(design.root)
        host = Host(window, config)
        page = ProvidersPage(host)
        page.build(window).pack(fill='both', expand=True)
        try:
            assert 'audio_hotkey_action' in page.rows
            dropdown = page.audio_hotkey_action
            assert dropdown.value == 'panel'
            dropdown.choose([value for value, _ in HOTKEY_ACTIONS].index('hold'))
            assert config.get('audio_hotkey_action') == 'hold' and 'audio_hotkey_action' in host.changes
            config.set('audio_hotkey_action', 'panel')
            page.audio_section.refresh()
            assert dropdown.value == 'panel'
        finally:
            window.destroy()

    def test_the_app_tells_the_open_panel_about_a_settings_change(self):
        from src.app import TranslatorApp
        panel = SimpleNamespace(is_open=lambda: True, sync_from_config=Mock())
        app = SimpleNamespace(audio_panel=panel, AUDIO_PANEL_SETTINGS=TranslatorApp.AUDIO_PANEL_SETTINGS)
        app._sync_audio_panel = lambda: TranslatorApp._sync_audio_panel(app)
        for key in ('audio_source', 'audio_mode', 'audio_language', 'api_keys'):
            assert key in TranslatorApp.AUDIO_PANEL_SETTINGS
        TranslatorApp._sync_audio_panel(app)
        panel.sync_from_config.assert_called_once()

    def test_a_panel_change_refreshes_an_open_settings_window(self):
        from src.app import TranslatorApp
        section = SimpleNamespace(refresh=Mock())
        settings = SimpleNamespace(window=SimpleNamespace(winfo_exists=lambda: True),
                                   pages={'providers': SimpleNamespace(audio_section=section)})
        TranslatorApp._audio_panel_changed(SimpleNamespace(settings_window=settings), 'audio_source')
        section.refresh.assert_called_once()
        TranslatorApp._audio_panel_changed(SimpleNamespace(settings_window=None), 'audio_source')

    def test_the_panel_reports_each_option_by_its_settings_key(self, controller):
        changes = []
        panel = AudioPanel(controller.root, controller, controller.config, on_change=changes.append,
                           work_area=lambda x, y: (0, 0, 1600, 900))
        panel.open((200, 150))
        try:
            panel.source.buttons[0].invoke()
            panel.mode.buttons[0].invoke()
            panel._language_chosen('Korean')
            assert changes == ['audio_source', 'audio_mode', 'audio_language']
            controller.config.set('audio_source', 'microphone')
            panel.sync_from_config()
            assert panel.source.value == 'microphone'
        finally:
            panel.window.destroy()
