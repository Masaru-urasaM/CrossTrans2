"""First-run user journey, nonmodal notices, authentication delivery and screenshot selection."""
from pathlib import Path
import threading
from types import SimpleNamespace
import tkinter as tk

from PIL import Image
import pytest

from src.core.onboarding import OnboardingState, SAMPLE
from src.core import screenshot
from src.ui import components as ui
from src.ui.welcome import WelcomeManager
from src.ui.dialogs import APIErrorDialog
from tests.settings_support import SettingsConfig
from tests.final_ui_helpers import Store, design, pump  # noqa: F401


def test_welcome_is_shown_once_but_can_be_reopened():
    store = Store()
    state = OnboardingState(store)
    assert state.should_show
    state.finish()
    assert not OnboardingState(store).should_show
    assert not store.get('first_translation_done', False)


@pytest.mark.parametrize('text,failed', [('', False), ('   ', False), ('Error', True)])
def test_failed_or_empty_results_do_not_consume_the_success_moment(text, failed):
    store = Store()
    assert not OnboardingState(store).first_success(text, failed=failed)
    assert not store.get('first_translation_done', False)


def test_first_success_is_persisted_and_only_announced_once():
    store = Store()
    assert OnboardingState(store).first_success('First result')
    assert not OnboardingState(store).first_success('Another result')


def welcome(design):
    tried, settings, celebrations = [], [], []
    view = WelcomeManager(design.root, SettingsConfig(default_target_language='English',
                           api_keys=[{'api_key': 'fixture-setup-key', 'provider': 'Google', 'model_name': 'fixture'}]), on_try=lambda *args: tried.append(args),
                           on_settings=settings.append, toast=SimpleNamespace(show_success=celebrations.append))
    return view, tried, settings, celebrations


def test_setup_does_not_submit_sample_until_the_explicit_try_action(design):
    view, tried, _, _ = welcome(design)
    view.show()
    assert tried == []
    view.next()
    view._choose_target('Japanese')
    view.next()
    view.next()
    view.next()
    assert view.step == 4 and tried == []
    pump(design.root, lambda: not view.host.pages['dictionary'].busy)
    view.try_sample()
    assert tried == [(SAMPLE, 'Japanese')]
    assert view.config.get_default_target_language() == 'Japanese'
    assert view.config.get('welcome_seen')
    assert view.config.autostart_calls == []


@pytest.mark.parametrize('language,scale',[('en',1.0),('ja',1.5)])
def test_setup_can_reach_and_save_the_last_palette_with_localized_previews(design,language,scale):
    from src.ui import i18n
    from src.ui.theme import tokens
    design.manager.set_ui_language(language)
    design.manager.set_text_scale(scale)
    view, tried, _, _ = welcome(design)
    view.show()
    picker = view.theme_picker
    assert set(picker.tiles) == set(tokens.PALETTES)
    for mode, source in tokens.THEME_OPTIONS[1:]:
        captions = [str(child.cget('text')) for child in picker.tiles[mode].winfo_children()
                    if child.winfo_class() == 'TLabel']
        assert i18n.tr(source) in captions
    tile = picker.tiles['graphite']
    view.scroll.reveal(tile,8)
    design.root.update()
    assert tile.winfo_rooty() >= view.scroll.canvas.winfo_rooty()-1
    assert tile.winfo_rooty()+tile.winfo_height() <= (
        view.scroll.canvas.winfo_rooty()+view.scroll.canvas.winfo_height()+1)
    picker.choose('graphite')
    assert view.config.get_theme_mode() == design.manager.mode == 'graphite'
    assert design.manager.is_dark and tried == []
    view.skip()


def test_skip_reopen_uses_current_preferences_without_overwriting_them(design):
    view, tried, _, _ = welcome(design)
    view.show()
    view.skip()
    view.config.set_default_target_language('Japanese')
    view.config._config['autostart'] = True
    view.show()
    assert view.target == 'Japanese' and view.autostart.get()
    view.skip()
    assert tried == [] and view.config.autostart_calls == []


def test_welcome_add_key_opens_the_embedded_setup_step(design):
    view, tried, settings, _ = welcome(design)
    view.show()
    view.add_key()
    assert view.step == 1 and 'providers' in view.host.pages
    assert settings == [] and tried == []
    assert view.state.should_show


def test_first_success_feedback_uses_the_shared_toast_once(design):
    view, _, _, celebrations = welcome(design)
    view.note_success('result', failed=True)
    view.note_success('result')
    view.note_success('another result')
    assert len(celebrations) == 1


@pytest.mark.parametrize('dialog_type', [APIErrorDialog])
def test_notices_are_mapped_on_a_hidden_root_without_a_grab(design, dialog_type):
    opened = []
    notice = dialog_type(design.root, on_open_settings_tab=opened.append)
    design.root.update_idletasks()
    assert notice.window.winfo_viewable()
    assert design.root.grab_current() is None
    assert not notice.window.transient()
    notice._open_settings()
    assert opened == ['API Key']
    assert not notice.window.winfo_exists()




def test_long_dialog_keeps_every_action_reachable_on_a_short_screen(design, monkeypatch):
    from src.ui.components import dialog as module
    monkeypatch.setattr(module, 'work_area', lambda: (0, 0, 700, 480))
    view = ui.Dialog(design.root, 'A long explanation', 'A sentence that wraps. ' * 80)
    for label in ['Cancel', 'Start free trial', 'Open API keys']:
        view.add_button(label)
    view.show()
    pump(design.root, lambda: view.window.winfo_viewable())
    assert view.window.winfo_height() <= 432
    assert all(button.winfo_width() >= button.winfo_reqwidth() for button in view._button_list)
    assert all(button.winfo_viewable() for button in view._button_list), {
        'window': view.window.geometry(),
        'footer': (view.buttons.winfo_width(), view.buttons.winfo_height(), view.buttons.winfo_reqheight()),
        'root': (view.root.winfo_width(), view.root.winfo_height()),
        'scroll': (view.scroll.winfo_height(), view.scroll.winfo_reqheight()),
        'buttons': [(button.winfo_manager(), button.winfo_width(), button.winfo_height(),
                     button.winfo_viewable()) for button in view._button_list]}
    assert view.scroll.canvas.yview()[1] < 1


@pytest.mark.parametrize('accepted', [False, True])
def test_password_verification_runs_off_ui_thread_and_returns_on_ui_thread(design, monkeypatch, accepted):
    from src.core import auth
    monkeypatch.setattr(tk.Toplevel, 'wait_window', lambda self: None)
    threads = []
    entered = []
    main_thread = threading.get_ident()

    def verify(value):
        entered.append(value)
        threads.append(threading.get_ident())
        return accepted

    monkeypatch.setattr(auth, 'verify_password', verify)
    view = auth.PasswordDialog(design.root)
    view.password_var.set('test-only password')
    view._verify()
    assert view.password_var.get() == ''
    pump(design.root, lambda: not view.runner.busy())
    assert entered == ['test-only password'] and threads[0] != main_thread
    assert view.result is accepted
    assert not view.dialog.winfo_exists() if accepted else view.failed_attempts == 1


@pytest.mark.parametrize('start,end,origin,size,expected', [
    ((-100, 30), (50, 130), (-200, 0), (400, 200), (100, 30, 250, 130)),
    ((50, 130), (-100, 30), (-200, 0), (400, 200), (100, 30, 250, 130)),
    ((-500, -50), (500, 500), (-200, 0), (400, 200), (0, 0, 400, 200)),
    ((-50, -80), (60, 30), (-100, -100), (200, 200), (50, 20, 160, 130)),
])
def test_selection_coordinates_across_negative_and_multiple_monitors(start, end, origin, size, expected):
    assert screenshot.selection_box(start, end, origin, size) == expected


def test_capture_uses_release_coordinates_and_completes_exactly_once():
    capture = screenshot.ScreenshotCapture()
    capture.original_image = Image.new('RGB', (200, 100), 'red')
    capture._vscreen_left = -100
    capture.start_x, capture.start_y = -90, 10
    capture.cur_x, capture.cur_y = -80, 20
    capture._dragging = True
    completed = []
    capture.callback = completed.append
    capture._on_release(SimpleNamespace(x_root=50, y_root=90))
    capture._close(call_callback=True)
    assert len(completed) == 1 and completed[0]
    path = Path(completed[0])
    try:
        with Image.open(path) as cropped:
            assert cropped.size == (140, 80)
    finally:
        path.unlink()


def test_cancel_does_not_deliver_duplicate_screenshot_results():
    capture = screenshot.ScreenshotCapture()
    completed = []
    capture.callback = completed.append
    capture._close(call_callback=True)
    capture._close(call_callback=True)
    assert completed == [None]


@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('background', ['#000000', '#FFFFFF', '#777777', '#236BAC', '#EDB742'])
def test_selection_outline_keeps_a_contrasting_edge_over_capture_colours(mode, background):
    from src.ui.theme.tokens import PALETTES
    from src.ui.theme.contrast import contrast_ratio
    strokes = screenshot.selection_strokes(PALETTES[mode])
    assert max(contrast_ratio(colour, background) for colour, _ in strokes) >= 3


def test_no_key_notice_only_offers_user_key_setup(design):
    opened = []
    notice = APIErrorDialog(design.root, on_open_settings_tab=opened.append)
    labels = [button.cget('text') for button in notice.view._button_list]
    assert not any('trial' in label.lower() for label in labels)
    button = next(button for button in notice.view._button_list if button.cget('text') == 'Open API keys')
    button.invoke()
    assert opened == ['API Key']
