"""User keys are required and stale Trial preferences can never activate a proxy."""
import importlib.util
from unittest.mock import Mock

import pytest
import src.core
from config import Config
from src.core.translation import TranslationService


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, 'CONFIG_DIR', str(tmp_path))
    monkeypatch.setattr(Config, 'CONFIG_FILE', str(tmp_path / 'config.json'))
    return Config()


def test_service_without_keys_never_constructs_a_quota_or_proxy(config):
    config.set('trial_mode_forced', True)
    service = TranslationService(config)
    assert service._configure_api() is False
    assert service.is_trial_mode() is False
    assert service.get_trial_info() is None
    assert not hasattr(service, 'trial_client')
    assert not hasattr(service, 'quota_manager')
    for module in ('trial_api', 'trial_manager', 'quota_manager'):
        assert importlib.util.find_spec('src.core.' + module) is None


def test_a_failed_user_key_never_falls_back_to_a_proxy(config):
    service = TranslationService(config)
    service.api_manager = Mock()
    service.api_manager.translate.side_effect = RuntimeError('fixture key rejected')
    result = service.translate_text('Hello', 'English', skip_cache=True)
    assert result == 'Error: fixture key rejected'
    service.api_manager.translate.assert_called_once()
    assert service.get_trial_info() is None


def test_legacy_trial_attributes_cannot_redirect_model_dispatch(config):
    service = TranslationService(config)
    service._is_trial_mode = True
    service.trial_client = Mock()
    service.api_manager = Mock(last_attribution='Fixture model (Fixture provider)')
    service.api_manager.translate.return_value = 'Translated'
    assert service._call_model('Fixture prompt') == 'Translated'
    service.trial_client.translate.assert_not_called()
    assert service.last_attribution == 'Fixture model (Fixture provider)'
