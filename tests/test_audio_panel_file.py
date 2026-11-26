"""An audio or video file translated from the audio panel (CHANGELOG Phase 31, Decision 88): decoded locally like
an attachment, recognized and translated like a recording, with the result in the quick popup under the panel.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.ui import audio_panel as module
from src.ui.audio_panel import AudioPanel
from src.ui.i18n import tr
from tests.final_ui_helpers import design, pump  # noqa: F401  (fixture)
from tests.test_audio_workflow import controller, rows, signal, track  # noqa: F401  (fixture)

WORK = (0, 0, 1600, 900)


@pytest.fixture
def file_run(controller, monkeypatch):
    seen = []
    recognizer = SimpleNamespace(rows=rows(), cache={}, transcribe=lambda *args: 'Recognized file')
    monkeypatch.setattr(controller, '_recognizer', lambda: recognizer)
    monkeypatch.setattr(controller, '_translate', lambda *args: ('Translated file', 'fixture (Groq)'))
    controller.watchers.append(lambda phase, info: seen.append((phase, dict(info))))
    controller.result_anchor = lambda: (100, 80, 420, 380)
    return SimpleNamespace(controller=controller, seen=seen, recognizer=recognizer)


class TestTheController:
    def test_the_result_opens_under_the_panel_and_the_file_is_untouched(self, file_run, tmp_path):
        controller = file_run.controller
        source = track(tmp_path, seconds=2)
        before = Path(source.path).read_bytes()
        controller.translate_file_from_panel(source.path)
        assert controller.phase == 'processing' and controller.trigger == 'panel'
        quick = controller.app.quick_translate_manager
        pump(controller.root, lambda: not controller.busy and quick.popup is not None)
        assert controller.transcript == 'Recognized file'
        assert quick._anchor_rect == (100, 80, 420, 380), 'under the panel, like a recording'
        assert controller.files is None, 'the decoded audio is not kept: no Play, no save question'
        assert Path(source.path).read_bytes() == before
        stages = [info.get('stage') for phase, info in file_run.seen if phase == 'processing']
        assert stages[0] == 'decoding' and file_run.seen[-1] == ('idle', {'outcome': 'ok', 'message': ''})
        assert controller.app.translation_service.history_manager.add_entry.call_count == 1

    def test_it_waits_for_a_running_translation(self, file_run, tmp_path):
        controller = file_run.controller
        controller.busy = True
        controller.translate_file_from_panel(track(tmp_path).path)
        controller.app.toast.show_warning.assert_called_with('Audio translation is already running.')
        controller.busy = False

    def test_a_file_that_is_gone_is_said_so(self, file_run, tmp_path):
        file_run.controller.translate_file_from_panel(str(tmp_path / 'gone.mp3'))
        file_run.controller.app.toast.show_warning.assert_called_with('File no longer exists.')
        assert file_run.controller.phase == 'idle'

    def test_nothing_runs_while_audio_translation_is_off(self, file_run, tmp_path):
        controller = file_run.controller
        controller.config.set('audio_enabled', False)
        controller.translate_file_from_panel(track(tmp_path).path)
        controller.app.toast.show_warning.assert_called_with(
            'Audio translation is disabled. Enable it in Settings > API keys.')
        assert controller.phase == 'idle'


class TestTheOneStepTarget:
    def test_a_recording_translates_into_the_language_chosen_now(self, controller, monkeypatch):
        """The recognizer is made with the target: it used to get the previous recording's."""
        targets = []
        controller.target = 'Japanese'                             # the previous recording's
        controller.config.set('audio_target_language', 'Korean')

        def recognizer():
            targets.append(controller.target)
            return SimpleNamespace(rows=[])                        # no key: stops right after
        monkeypatch.setattr(controller, '_recognizer', recognizer)
        controller.start_recording('panel')
        controller.translate_file_from_panel(__file__)
        assert targets == ['Korean', 'Korean']


class TestThePanel:
    @pytest.fixture
    def panel(self, controller):
        instance = AudioPanel(controller.root, controller, controller.config, work_area=lambda x, y: WORK)
        instance.open((200, 150))
        instance.window.update()
        yield instance
        if instance.is_open():
            controller.busy = False
            instance.window.destroy()

    def test_the_file_button_translates_the_chosen_file(self, panel, controller, monkeypatch):
        chosen = []
        monkeypatch.setattr(module.filedialog, 'askopenfilename', lambda **kw: chosen.append(kw) or 'C:/a/talk.mp4')
        monkeypatch.setattr(controller, 'translate_file_from_panel', Mock())
        panel.file_button.invoke()
        controller.translate_file_from_panel.assert_called_once_with('C:/a/talk.mp4')
        assert chosen[0]['parent'] is panel.window and '*.mp4' in chosen[0]['filetypes'][0][1]

    def test_a_cancelled_choice_does_nothing(self, panel, controller, monkeypatch):
        monkeypatch.setattr(module.filedialog, 'askopenfilename', lambda **kw: '')
        monkeypatch.setattr(controller, 'translate_file_from_panel', Mock())
        panel.file_button.invoke()
        controller.translate_file_from_panel.assert_not_called()

    def test_reading_a_file_shows_how_much_has_been_read(self, panel, controller):
        controller.busy, controller.capture, controller.state = True, None, 'decoding'
        controller._notify('processing', stage='decoding', done=95, total=600)
        assert str(panel.status.cget('text')) == tr('Reading the file…')
        assert str(panel.timer.cget('text')) == '01:35 / 10:00'
        assert str(panel.file_button.cget('state')) == 'disabled'
        controller._notify('processing', stage='transcribing', done=1, total=4)
        assert str(panel.timer.cget('text')) == '01:35 / 10:00', 'the readout stays while it is recognized'
        controller.busy = False
        controller._notify('idle', outcome='ok', message='')
        assert str(panel.file_button.cget('state')) == 'normal'
        assert str(panel.timer.cget('text')) == '00:00 / 03:00'


class TestQuestionsAboveThePanel:
    """The panel is always on top: the save question, and the save-as box after it, must open above it."""

    @staticmethod
    def recording(controller, tmp_path):
        from src.core import audio_media as media
        files = media.AudioFiles()
        media.write_pcm(files.directory / 'pc.wav', signal())
        files.tracks = [media.AudioTrack(str(files.directory / 'pc.wav'), 'PC')]
        controller.files = files
        return files

    def test_the_save_question_stays_on_top_centred_on_the_panel(self, controller, tmp_path):
        self.recording(controller, tmp_path)
        controller.result_anchor = lambda: (300, 200, 500, 420)
        controller.request_close('replace')
        dialog = controller.dialog
        settle_for(controller.root, 0.4)                        # past the 150 ms a dialog normally stays in front
        assert dialog.window.attributes('-topmost') in (1, True, '1')
        x, y = dialog.window.winfo_x(), dialog.window.winfo_y()
        width, height = dialog.window.winfo_width(), dialog.window.winfo_height()
        assert (abs((x + width // 2) - 550), abs((y + height // 2) - 410)) <= (2, 2), (x, y, width, height)
        assert dialog._front_job is None, 'nothing is waiting to drop it out of the topmost band'
        dialog._default.invoke()
        assert controller.files is None

    def test_without_the_panel_it_leaves_the_topmost_band_as_before(self, controller, tmp_path):
        self.recording(controller, tmp_path)
        controller.result_anchor = lambda: None
        controller.request_close('replace')
        settle_for(controller.root, 0.4)
        assert controller.dialog.window.attributes('-topmost') in (0, False, '0')
        controller.dialog._default.invoke()

    def test_the_save_as_box_belongs_to_the_panel(self, controller, tmp_path, monkeypatch):
        from src.ui import audio_controller as controller_module
        self.recording(controller, tmp_path)
        owner = []
        monkeypatch.setattr(controller_module.filedialog, 'asksaveasfilename',
                            lambda **kw: owner.append(kw['parent']) or '')
        panel_window = Mock()
        controller.front_window = lambda: panel_window
        controller._save_then_close(None)
        assert owner == [panel_window] and controller.files is None


def settle_for(root, seconds):
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.005)
