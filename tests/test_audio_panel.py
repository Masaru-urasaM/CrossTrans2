"""The audio panel window (CHANGELOG Phase 29, Decision 88): it opens at the cursor, takes the focus, mirrors the
controller's phases, writes the options to the configuration, and answers Space, Enter, Esc and the shortcut.
"""
from types import SimpleNamespace

import pytest

from src.core import audio_service
from src.ui import audio_panel as module, components as ui
from src.ui.audio_panel import AudioPanel, KEYS_TAG
from src.ui.i18n import tr
from tests.final_ui_helpers import design, pump  # noqa: F401  (fixture)
from tests.test_audio_panel_controller import Capture, panel_run  # noqa: F401  (fixture)
from tests.test_audio_workflow import controller  # noqa: F401  (fixture)

WORK = (0, 0, 1600, 900)
WHISPER = [dict(provider='Groq', model_name='whisper-large-v3', api_key='fixture-whisper', is_audio_key=True)]
GEMINI = [dict(provider='Google', model_name='gemini-2.5-flash', api_key='fixture-gemini', is_audio_key=True)]
IDLE_CAPTURE = SimpleNamespace(stop=lambda: None, cancel=lambda: None)   # a recording that records nothing


@pytest.fixture
def panel(controller):
    instance = AudioPanel(controller.root, controller, controller.config, work_area=lambda x, y: WORK)
    yield instance
    if instance.is_open():
        controller.busy = False
        instance.window.destroy()


def opened(panel, at=(200, 150)):
    panel.open(at)
    panel.window.update()
    return panel


def text(widget):
    return str(widget.cget('text'))


class TestOpening:
    def test_it_opens_beside_the_cursor_on_top_and_inside_the_work_area(self, panel):
        opened(panel, (1550, 880))
        left, top, width, height = panel.rect()
        assert panel.window.attributes('-topmost') in (1, True, '1')
        assert 0 <= left and left + width <= WORK[2] and 0 <= top and top + height <= WORK[3]
        assert width >= ui.get().px(module.PANEL_MIN_WIDTH)

    def test_a_second_open_keeps_the_place_the_user_dragged_it_to(self, panel):
        opened(panel)
        panel._drag_start(SimpleNamespace(x_root=210, y_root=160))
        panel._drag_move(SimpleNamespace(x_root=610, y_root=360))
        panel.window.update()
        moved = panel.rect()[:2]
        panel.open((50, 50))
        panel.window.update()
        assert panel.rect()[:2] == moved

    def test_dragging_stops_at_the_work_area_edge(self, panel):
        opened(panel)
        panel._drag_start(SimpleNamespace(x_root=210, y_root=160))
        panel._drag_move(SimpleNamespace(x_root=-500, y_root=5000))
        panel.window.update()
        left, top, width, height = panel.rect()
        assert left == 0 and top + height == WORK[3]

    def test_the_options_show_and_store_the_configuration(self, panel, controller):
        controller.config.set('audio_source', 'pc')
        controller.config.set('audio_target_language', 'Korean')
        opened(panel)
        assert panel.source.value == 'pc' and panel.language.value == 'Korean'
        panel.source.buttons[1].invoke()
        assert controller.config.get('audio_source') == 'microphone'
        assert tuple(panel.meters) == ('Microphone',), 'one meter per device the source records'
        panel.mode.buttons[0].invoke()
        assert controller.config.get('audio_mode') == 'two_step'

    def test_closing_stops_following_the_controller(self, panel, controller):
        opened(panel)
        assert panel._watch in controller.watchers
        assert panel.close() and not panel.is_open()
        assert panel._watch not in controller.watchers


class TestKeys:
    def test_space_records_or_stops_from_any_control_and_never_presses_it(self, panel, controller, monkeypatch):
        toggles = []
        monkeypatch.setattr(controller, 'toggle_recording', lambda: toggles.append(True))
        controller.config.set('audio_source', 'both')
        opened(panel)
        pill = panel.source.buttons[0]
        assert pill.bindtags()[1] == KEYS_TAG, 'after the widget\'s own bindings, before its class'
        pill.focus_force()
        panel.window.update()
        pill.event_generate('<space>')
        assert toggles == [True] and controller.config.get('audio_source') == 'both'
        panel.record.focus_force()
        panel.window.update()
        panel.record.event_generate('<space>')
        assert toggles == [True, True], 'Space on Record toggles once, not twice'

    def test_enter_presses_the_focused_control(self, panel, controller):
        controller.config.set('audio_source', 'both')
        opened(panel)
        pill = panel.source.buttons[0]
        pill.focus_force()
        panel.window.update()
        pill.event_generate('<Return>')
        assert controller.config.get('audio_source') == 'pc'

    def test_escape_closes_only_while_idle(self, panel, controller):
        opened(panel)
        controller.busy, controller.capture, controller.state = True, IDLE_CAPTURE, 'recording'
        panel.window.focus_force()
        panel.window.update()
        panel.window.event_generate('<Escape>')
        assert panel.is_open(), 'a running recording keeps the panel'
        controller.busy, controller.capture, controller.state = False, None, 'idle'
        panel.window.event_generate('<Escape>')
        assert not panel.is_open()

    def test_the_shortcut_while_open_records_or_stops(self, panel, controller, monkeypatch):
        toggles = []
        monkeypatch.setattr(controller, 'toggle_recording', lambda: toggles.append(True))
        opened(panel)
        panel.hotkey()
        assert toggles == [True]


class TestPhases:
    def test_recording_shows_stop_the_time_and_a_level_per_device(self, panel, controller):
        opened(panel)
        controller.busy, controller.capture, controller.state = True, IDLE_CAPTURE, 'recording'
        controller._notify('recording', elapsed=12.4, limit=180, levels={'PC': .6, 'Microphone': .2})
        assert text(panel.record) == tr('Stop') and panel.record.kind == 'danger'
        assert text(panel.timer) == '00:12 / 03:00'
        assert {role: float(meter.cget('value')) for role, meter in panel.meters.items()} == {'PC': .6,
                                                                                               'Microphone': .2}
        assert text(panel.status) == tr('Recording')
        assert str(panel.source.buttons[0].cget('state')) == 'disabled'
        assert str(panel.language.button.cget('state')) == 'disabled'
        assert str(panel.close_button.cget('state')) == 'disabled'
        assert str(panel.cancel_button.cget('state')) == 'normal'
        controller.busy, controller.capture = False, None

    def test_processing_disables_record_and_counts_the_chunks(self, panel, controller):
        opened(panel)
        controller.busy, controller.capture, controller.state = True, None, 'transcribing'
        controller._notify('processing', stage='transcribing', done=1, total=2)
        assert str(panel.record.cget('state')) == 'disabled' and text(panel.record) == tr('Record')
        assert text(panel.status) == tr('Recognizing speech: {done}/{total}', done=1, total=2)
        controller.busy = False

    def test_an_error_is_shown_with_its_message_and_record_is_offered_again(self, panel, controller):
        opened(panel)
        controller._notify('idle', outcome='error', message='Audio capture lost samples. Check the device and try again.')
        assert text(panel.status) == tr('Audio capture lost samples. Check the device and try again.')
        assert str(panel.record.cget('state')) == 'normal' and str(panel.cancel_button.cget('state')) == 'disabled'
        assert str(panel.status_icon.cget('image'))

    def test_record_and_stop_on_the_panel_run_a_whole_recording(self, panel_run):
        controller = panel_run.controller
        instance = AudioPanel(controller.root, controller, controller.config, work_area=lambda x, y: WORK)
        opened(instance)
        try:
            instance.record.invoke()                               # Record, from the panel
            pump(controller.root, lambda: controller.state == 'recording')
            assert text(instance.record) == tr('Stop') and instance._phase == 'recording'
            instance.record.invoke()                               # Stop
            pump(controller.root, lambda: controller.state == 'result')
            assert text(instance.status) == tr('Done') and text(instance.record) == tr('Record')
        finally:
            instance.window.destroy()


class TestOneStep:
    def keys(self, monkeypatch, controller, rows):
        monkeypatch.setattr(audio_service, 'has_manual_audio_keys', lambda config: True)
        monkeypatch.setattr(controller.config, 'get_audio_keys', lambda: rows, raising=False)

    def test_a_key_that_only_transcribes_disables_one_step_and_says_why(self, panel, controller, monkeypatch):
        self.keys(monkeypatch, controller, WHISPER)
        opened(panel)
        assert str(panel.mode.buttons[1].cget('state')) == 'disabled'
        assert text(panel.mode_note) == tr(module.ONE_STEP_NEEDS_GEMINI) and panel.mode_note.winfo_ismapped()

    def test_pc_and_microphone_say_when_one_step_runs_as_two(self, panel, controller, monkeypatch):
        self.keys(monkeypatch, controller, GEMINI)
        controller.config.set('audio_mode', 'one_step')
        controller.config.set('audio_source', 'both')
        opened(panel)
        assert str(panel.mode.buttons[1].cget('state')) == 'normal'
        assert text(panel.mode_note) == tr(module.ONE_STEP_WITH_BOTH) and panel.mode_note.winfo_ismapped()
        panel.source.buttons[0].invoke()                           # PC audio only
        panel.window.update()
        assert not panel.mode_note.winfo_ismapped()


class TestApplication:
    @staticmethod
    def app(action, panel, controller):
        from src.app import TranslatorApp
        app = SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: action
                                                     if key == 'audio_hotkey_action' else default),
                              audio_controller=controller, audio_panel=panel)
        app._audio_panel = lambda: TranslatorApp._audio_panel(app)
        return app, TranslatorApp

    def test_the_shortcut_opens_the_panel_then_records_or_stops(self, panel, controller, monkeypatch):
        toggles = []
        monkeypatch.setattr(controller, 'toggle_recording', lambda: toggles.append(True))
        original = panel.open
        monkeypatch.setattr(panel, 'open', lambda pointer=None: original((200, 150)))
        app, cls = self.app('panel', panel, controller)
        cls._on_audio_hotkey(app)
        assert panel.is_open() and toggles == []
        cls._on_audio_hotkey(app)
        assert toggles == [True]

    def test_hold_to_record_keeps_the_old_shortcut(self, panel, controller, monkeypatch):
        started = []
        monkeypatch.setattr(controller, 'start_recording', lambda trigger='hold': started.append(trigger))
        app, cls = self.app('hold', panel, controller)
        cls._on_audio_hotkey(app)
        assert started == ['hold'] and not panel.is_open()

    def test_the_tray_offers_the_panel_while_audio_translation_is_on(self):
        from src.ui.tray import TrayManager
        values = {'audio_enabled': True}
        config = SimpleNamespace(get=lambda key, default=None: values.get(key, default),
                                 get_shortcuts_enabled=lambda: False, get_all_hotkeys=lambda: {},
                                 has_any_vision_capable=lambda: False, get_fix_grammar_hotkey_enabled=lambda: False)
        tray = TrayManager(config)
        tray.configure_callbacks(on_audio_panel=lambda: None)
        texts = [getattr(item, 'text', None) for item in tray._build_menu_items()]
        assert texts[:3] == [tr('Open Translator'), tr('Audio panel'), tr('Settings')]
        values['audio_enabled'] = False
        assert tr('Audio panel') not in [getattr(item, 'text', None) for item in tray._build_menu_items()]


class TestPreferences:
    def test_a_theme_change_redraws_the_panel_where_it_is(self, panel, controller):
        opened(panel)
        before = panel.rect()[:2]
        old_body = panel.body
        panel._rebuild()
        panel.window.update()
        assert panel.body is not old_body and not old_body.winfo_exists()
        assert panel.rect()[:2] == before and panel.source.value == controller.config.get('audio_source', 'both')
