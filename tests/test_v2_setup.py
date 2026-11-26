"""The five-step setup must be explicit, skippable and recoverable without Trial."""
from types import SimpleNamespace
from unittest.mock import Mock

from tests.final_ui_helpers import design
from tests.settings_support import SettingsConfig
from src.ui.welcome import WelcomeManager
from src.core.onboarding import OnboardingState, STEPS
from src.ui.theme import tokens


def setup(design, keys=()):
    config = SettingsConfig(default_target_language='English', api_keys=list(keys))
    complete, tried = Mock(), Mock()
    view = WelcomeManager(design.root, config, on_try=tried, on_settings=Mock(),
                           toast=SimpleNamespace(show_success=Mock()), on_complete=complete)
    view.show()
    return view, config, complete, tried


def accept_warning(view):
    view.warning.view._button_list[1].invoke() if hasattr(view.warning, 'view') else view.warning._button_list[1].invoke()


def test_no_key_skip_requires_acknowledgement_but_really_opens_home(design):
    view, config, complete, tried = setup(design)
    assert set(view.theme_picker.previews) == set(tokens.PALETTES)
    view.skip()
    assert view.state.should_show and not complete.called
    assert view.warning.window.winfo_viewable()
    accept_warning(view)
    complete.assert_called_once_with()
    tried.assert_not_called()
    assert config.get_theme_mode() == 'system'
    assert not view.state.should_show
    assert config.get_api_keys() == []
    assert not config.get('first_translation_done')


def test_partial_theme_and_language_choices_survive_skipping(design):
    view, config, complete, tried = setup(design)
    view.theme_picker.choose('pink')
    view._choose_ui_language('ja')
    view.skip()
    accept_warning(view)
    assert config.get_theme_mode() == 'pink' and config.get('ui_language') == 'ja'
    assert design.manager.palette is tokens.PINK
    complete.assert_called_once()
    tried.assert_not_called()


def test_key_entered_before_next_is_flushed_and_no_test_is_automatic(design, monkeypatch):
    factory = Mock(side_effect=AssertionError('No automatic API request'))
    monkeypatch.setattr('src.ui.settings.page_providers.AIAPIManager', factory)
    view, config, complete, tried = setup(design)
    view.next()
    assert view.step == 1
    card = view.api_page.cards[0]
    card.field.set('fixture-new-key')
    card._typed()
    view.next()
    assert view.step == 2
    assert config.get_api_keys()[0]['api_key'] == 'fixture-new-key'
    factory.assert_not_called()
    tried.assert_not_called()


def test_no_key_normal_next_can_explicitly_continue_to_optional_packs(design):
    view, config, complete, tried = setup(design)
    view.next()
    view.next()
    assert view.step == 1 and view.warning.window.winfo_exists()
    accept_warning(view)
    assert view.step == 2
    assert config.get_api_keys() == [] and not complete.called


def test_interrupted_setup_resumes_from_saved_step():
    config = SettingsConfig(setup_step=3)
    assert STEPS == 5 and OnboardingState(config).resume_step == 3
    config.set('setup_step', 1000)
    assert OnboardingState(config).resume_step == 4
    config.set('setup_step', 'invalid')
    assert OnboardingState(config).resume_step == 0


def test_finishing_setup_waits_for_an_in_flight_pack_operation(design):
    view, config, complete, tried = setup(design, keys=[dict(api_key='fixture-key')])
    view.host.pages['dictionary'] = SimpleNamespace(busy=True, show_notice=Mock())
    view.step = 4
    view._render = Mock()
    view.skip()
    assert view.step == 2 and view.window.winfo_exists()
    assert not complete.called and not config.get('welcome_seen')
    view.host.pages['dictionary'].show_notice.assert_called_once()


def test_setup_progress_follows_the_interface_language_and_current_step(design):
    view, config, complete, tried = setup(design)
    assert view.progress.cget('text') == 'Step 1 of 5'
    view._choose_ui_language('ja')
    assert view.progress.cget('text') == 'ステップ 1 / 5'
    view.next()
    assert view.progress.cget('text') == 'ステップ 2 / 5'
    view._choose_ui_language('en')
    assert view.progress.cget('text') == 'Step 2 of 5'
