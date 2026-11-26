"""Saved credential priority must govern actual calls in all request modalities."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from src.core import api_manager as module
from src.core.api_manager import AIAPIManager


def manager(monkeypatch, configs):
    result = AIAPIManager()
    health = Mock()
    health.get_priority_sorted_providers.side_effect = AssertionError('Health must not reorder keys')
    health.get_adaptive_timeout.return_value = 10
    result.configure(configs, health_manager=health)
    result._try_model_rotation = Mock(return_value=None)
    return result


def key(name, provider, model='fixture-model', vision=True):
    return dict(api_key=name, provider=provider, model_name=model, vision_capable=vision)


@pytest.mark.parametrize('kind', ['text', 'image', 'multimodal'])
def test_primary_is_used_even_when_health_would_prefer_a_backup(monkeypatch, kind):
    api = manager(monkeypatch, [key('a', 'Google'), key('b', 'OpenAI')])
    dispatch = Mock(return_value='Answer')
    api._generate_content = dispatch
    api._generate_content_multimodal = dispatch
    value = (api.translate('Prompt') if kind == 'text' else
             api.translate_image('Prompt', 'fixture.png') if kind == 'image' else
             api.translate_multimodal('Prompt', image_paths=['fixture.png']))
    assert value == 'Answer'
    assert dispatch.call_args.args[1] == 'a'
    assert dispatch.call_count == 1


@pytest.mark.parametrize('kind', ['text', 'image', 'multimodal'])
def test_fallback_keeps_saved_order_and_the_config_is_not_reordered(monkeypatch, kind):
    api = manager(monkeypatch, [key('a', 'Google'), key('b', 'OpenAI'), key('c', 'Groq')])
    calls = []
    def generate(provider, credential, *args):
        calls.append(credential)
        if credential == 'a':
            raise RuntimeError('fixture quota reached')
        return 'Answer'
    api._generate_content = api._generate_content_multimodal = generate
    if kind == 'text':
        assert api.translate('Prompt') == 'Answer'
    elif kind == 'image':
        assert api.translate_image('Prompt', 'fixture.png') == 'Answer'
    else:
        assert api.translate_multimodal('Prompt', image_paths=['fixture.png']) == 'Answer'
    assert calls == ['a', 'b']
    assert [item['api_key'] for item in api.api_configs] == ['a', 'b', 'c']


def test_a_later_auto_model_key_cannot_jump_ahead_of_the_primary(monkeypatch):
    api = manager(monkeypatch, [key('a', 'Google'), key('b', 'OpenAI', model='')])
    api._generate_content = Mock(return_value='Primary answer')
    api._try_auto_detect_model = Mock(return_value='Backup answer')
    assert api.translate('Prompt') == 'Primary answer'
    api._try_auto_detect_model.assert_not_called()


def test_auto_model_primary_runs_before_an_explicit_backup(monkeypatch):
    api = manager(monkeypatch, [key('a', 'Google', model=''), key('b', 'OpenAI')])
    api._try_auto_detect_model = Mock(return_value='Primary answer')
    api._generate_content = Mock(return_value='Backup answer')
    assert api.translate('Prompt') == 'Primary answer'
    api._generate_content.assert_not_called()


@pytest.mark.parametrize('kind', ['image', 'multimodal'])
def test_vision_skips_known_incompatible_keys_but_can_use_auto_models(monkeypatch, kind):
    monkeypatch.setattr(module, 'get_config', lambda: SimpleNamespace(
        default_models_by_provider={'Google': ['fixture-auto-vision']}))
    api = manager(monkeypatch, [key('a', 'Groq', vision=False), key('b', 'Google', model='')])
    dispatch = Mock(return_value='Answer')
    api._generate_content = api._generate_content_multimodal = dispatch
    if kind == 'image':
        assert api.translate_image('Prompt', 'fixture.png') == 'Answer'
    else:
        assert api.translate_multimodal('Prompt', image_paths=['fixture.png']) == 'Answer'
    assert dispatch.call_args.args[1:3] == ('b', 'fixture-auto-vision')


def test_real_translation_events_identify_keys_and_do_not_include_credentials(monkeypatch):
    from src.core.key_identity import normalize
    keys = normalize([key('fixture-secret-a', 'Google'), key('fixture-secret-b', 'OpenAI')])
    api = manager(monkeypatch, keys)
    events = []
    api.usage_callback = events.append
    def generate(provider, credential, *args):
        if credential == 'fixture-secret-a':
            raise RuntimeError('429 fixture quota')
        return 'Answer'
    api._generate_content = generate
    assert api.translate('Prompt') == 'Answer'
    assert [(event['id'], event['kind']) for event in events] == [(keys[0]['id'], 'quota'), (keys[1]['id'], 'success')]
    assert 'fixture-secret' not in str(events)
    assert all(event['seconds'] >= 0 for event in events)


def test_connection_tests_do_not_create_translation_usage(monkeypatch):
    api = manager(monkeypatch, [])
    events = []
    api.usage_callback = events.append
    api._generate_content = Mock(return_value='Connection works')
    api.test_connection('fixture-model', 'fixture-key', 'Google')
    assert events == []
