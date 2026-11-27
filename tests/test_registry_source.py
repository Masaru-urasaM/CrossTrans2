"""The model registry as the source of the model/provider table (Decision 110): the adapter and the client around it.

`registry_to_config_v2()` rebuilds the v2 payload of the Worker's `/v1/config` from the registry catalog, so nothing
downstream changes. The recorded data (tests/fixtures, see tests/registry_support.py) is the registry's real catalog
trimmed to 8 models a provider and the Worker's real payload of the same day. The network is refused in tests except
loopback: `fetch_registry_config()` talks to a loopback server through the real urllib, so the `User-Agent` that goes
on the wire is the one that is checked.
"""
import copy
import logging
import tempfile
import types

import pytest

import src.core  # noqa: F401 - imported before config, like the other tests (CLAUDE.md Known Issues)
import src.core.api_manager as api_manager_module
import src.core.multimodal as multimodal_module
from src.constants import PROVIDERS_LIST
from src.core import key_testing as kt
from src.core import model_registry_client, registry_source
from src.core import remote_config
from src.core.api_manager import AIAPIManager, DEFAULT_MODELS_BY_PROVIDER
from src.core.model_registry_client import query_catalog
from src.core.multimodal import MultimodalProcessor
from src.core.registry_source import (
    PROVIDER_DISPLAY, RegistryDataError, build_api_urls, build_key_patterns, fetch_registry_config,
    registry_to_config_v2,
)
from src.core.speech_engines import resolve_audio_provider
from tests.registry_support import CATALOG_PATH, ETAG, RegistryServer, load_catalog, load_legacy

CONFIG_KEYS = {'version', 'updated_at', 'providers_list', 'model_provider_map', 'api_key_patterns', 'vision_models',
               'default_models_by_provider', 'provider_api_urls', 'trial_daily_limit'}
INFORMATIONAL = {'_auto_generated', '_openrouter_total_models'}      # the Worker's own bookkeeping, not part of the table
DISPLAY = dict(PROVIDER_DISPLAY)                                      # registry id -> display name
STANDARD_KEY = 'AIzaSyFixtureKey0123456789abcdefghijk'
AUTH_KEY = 'AQ.Ab8RN6FixtureKey0123456789abcdefghijk'
PROJECT_KEY = 'sk-proj-Fixture0123456789abcdefghijk'
SERVICE_KEY = 'sk-svcacct-Fixture0123456789abcdefghijk'
ANTHROPIC_KEY = 'sk-ant-api03-Fixture0123456789abcdefghijk'
OPENROUTER_KEY = 'sk-or-v1-Fixture0123456789abcdefghijk'
BARE_KEY = 'sk-' + 'a' * 48                                           # OpenAI legacy, DeepSeek, SiliconFlow: ambiguous
UUID_KEY = '0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b'                     # SambaNova's shape: no prefix


@pytest.fixture
def catalog():
    return load_catalog()


@pytest.fixture
def legacy():
    return load_legacy()


@pytest.fixture
def config(catalog, legacy):
    return registry_to_config_v2(catalog, base=legacy)


@pytest.fixture
def server(monkeypatch):
    """A loopback server, with urllib kept off any proxy the machine has configured."""
    import urllib.request
    monkeypatch.setattr(urllib.request, 'getproxies', lambda: {})
    monkeypatch.setattr(urllib.request, '_opener', urllib.request.build_opener(urllib.request.ProxyHandler({})))
    running = RegistryServer().start()
    yield running
    running.stop()


def shape_of(value):
    """The type structure of a payload: what keys and elements are made of, not what they say."""
    if isinstance(value, dict):
        return ('dict', frozenset(type(k).__name__ for k in value), frozenset(shape_of(v) for v in value.values()))
    if isinstance(value, list):
        return ('list', frozenset(shape_of(v) for v in value))
    return type(value).__name__


def ids_of(result):
    return [m['id'] for m in result['data']]


def provider_entry(prefixes=(), base_url='https://api.example.test/v1', compatible=None):
    return {'key_prefixes': list(prefixes), 'api': {'base_url': base_url, 'openai_compatible_base_url': compatible}}


# ------------------------------------------------------------------------------------------------------- the vendored client

def test_the_vendored_client_says_where_it_comes_from_and_identifies_itself():
    from pathlib import Path
    source = Path(model_registry_client.__file__).read_text(encoding='utf-8')
    header = source.split('\"\"\"', 1)[0]
    assert header.startswith('# Vendored, unmodified copy of clients/python/model_registry_client.py')
    assert 'model-registry project' in header and 'Do not edit' in header
    assert model_registry_client.SCHEMA_VERSION == 1 and model_registry_client.DEFAULT_BASE_URL == 'https://models.masaru.uk'
    assert model_registry_client.USER_AGENT.startswith('model-registry-client-python')
    assert not model_registry_client.USER_AGENT.lower().startswith('python-urllib')


# ----------------------------------------------------------------------------------------------- the shape of the payload

def test_the_payload_has_the_keys_and_types_of_the_workers_v2_payload(config, legacy):
    assert set(config) == CONFIG_KEYS
    assert {key for key in legacy if key not in INFORMATIONAL} == CONFIG_KEYS
    for key in CONFIG_KEYS:
        assert shape_of(config[key]) == shape_of(legacy[key]), key
    assert config['version'] == 2


def test_the_payload_passes_the_apps_own_validation(config):
    manager = object.__new__(remote_config.RemoteConfigManager)
    assert manager._validate_config(config) is True


def test_the_payload_is_made_of_plain_json_types(config):
    import json
    assert json.loads(json.dumps(config)) == config


def test_the_registry_build_time_becomes_updated_at(catalog, legacy):
    assert registry_to_config_v2(catalog, legacy)['updated_at'] == catalog['build']['generated_at']
    del catalog['build']
    assert registry_to_config_v2(catalog, legacy)['updated_at'] == ''


# --------------------------------------------------------------------------------------------------------- display names

def test_the_display_name_table_is_the_documented_one():
    assert dict(PROVIDER_DISPLAY) == {
        'google': 'Google', 'openai': 'OpenAI', 'anthropic': 'Anthropic', 'deepseek': 'DeepSeek', 'groq': 'Groq',
        'xai': 'xAI', 'mistral': 'Mistral', 'perplexity': 'Perplexity', 'cerebras': 'Cerebras',
        'sambanova': 'SambaNova', 'together': 'Together', 'siliconflow': 'SiliconFlow', 'openrouter': 'OpenRouter',
        'huggingface': 'HuggingFace'}


def test_providers_list_keeps_auto_first_and_the_apps_order(config, legacy):
    assert config['providers_list'][0] == 'Auto'
    assert config['providers_list'] == PROVIDERS_LIST == legacy['providers_list']
    assert [name for _, name in PROVIDER_DISPLAY] == PROVIDERS_LIST[1:]


def test_the_model_map_is_keyed_by_display_name_in_the_apps_order(config):
    assert list(config['model_provider_map']) == PROVIDERS_LIST[1:]


def test_providers_the_app_has_no_name_for_are_ignored(catalog, legacy):
    catalog['providers']['cohere'] = provider_entry(['co-'])
    catalog['models'].append(dict(catalog['models'][0], provider='cohere', id='command-x'))
    config = registry_to_config_v2(catalog, legacy)
    assert 'command-x' not in str(config) and 'Cohere' not in config['providers_list']


# ------------------------------------------------------------------------------------------------------ model_provider_map

@pytest.mark.parametrize('provider_id', [pid for pid, _ in PROVIDER_DISPLAY if pid != 'perplexity'])
def test_the_model_map_is_the_active_and_preview_chat_models_of_each_provider(config, catalog, provider_id):
    expected = ids_of(query_catalog(catalog, 'models', {'provider': provider_id}))     # the registry's own defaults
    assert expected, provider_id
    assert config['model_provider_map'][DISPLAY[provider_id]] == expected


def test_deprecated_and_non_chat_models_are_not_in_the_model_map(catalog):
    config = registry_to_config_v2(catalog)                       # the registry alone: no held table to carry a list from
    offered = {m for ids in config['model_provider_map'].values() for m in ids}
    deprecated = [m for m in catalog['models'] if m['kind'] == 'chat' and m['lifecycle']['status'] == 'deprecated']
    other = [m for m in catalog['models'] if m['kind'] != 'chat']
    assert deprecated and other                                   # the fixture holds both kinds on purpose
    assert 'gemini-2.5-flash' in {m['id'] for m in deprecated}
    for model in deprecated + other:
        assert model['id'] not in config['model_provider_map'][DISPLAY[model['provider']]], model['id']
    assert 'aqa' not in offered and 'mistral-embed' not in offered and 'chatgpt-image-latest' not in offered


def test_preview_models_are_offered(config, catalog):
    previews = [m for m in catalog['models'] if m['kind'] == 'chat' and m['lifecycle']['status'] == 'preview']
    assert previews
    for model in previews:
        assert model['id'] in config['model_provider_map'][DISPLAY[model['provider']]]


def test_a_provider_whose_models_the_registry_lists_only_as_deprecated_keeps_its_list(catalog, legacy):
    """Perplexity has no model list endpoint; the registry knows four sonar models, all marked deprecated."""
    perplexity = [m for m in catalog['models'] if m['provider'] == 'perplexity']
    assert perplexity and {m['lifecycle']['status'] for m in perplexity} == {'deprecated'}
    with_base = registry_to_config_v2(catalog, base=legacy)
    assert with_base['model_provider_map']['Perplexity'] == legacy['model_provider_map']['Perplexity']
    without_base = registry_to_config_v2(catalog)
    assert without_base['model_provider_map']['Perplexity'] == []
    assert 'Perplexity' in without_base['providers_list']        # empty, but still a provider of the app


def test_a_provider_missing_from_the_catalog_keeps_its_list_too(catalog, legacy):
    del catalog['providers']['sambanova']
    catalog['models'] = [m for m in catalog['models'] if m['provider'] != 'sambanova']
    with pytest.raises(RegistryDataError, match='SambaNova'):     # it is one of the 12 the app calls by URL
        registry_to_config_v2(catalog, legacy)
    catalog['providers']['sambanova'] = provider_entry(base_url='https://api.sambanova.ai/v1')
    config = registry_to_config_v2(catalog, legacy)
    assert config['model_provider_map']['SambaNova'] == legacy['model_provider_map']['SambaNova']


def test_ids_are_unique_and_junk_entries_are_skipped(catalog, legacy):
    google = next(m for m in catalog['models'] if m['provider'] == 'google' and m['kind'] == 'chat'
                  and m['lifecycle']['status'] == 'active')
    catalog['models'] += [dict(google), 'junk', None, {'provider': 'google', 'kind': 'chat'},
                          dict(google, id='  '), dict(google, id=7)]
    ids = registry_to_config_v2(catalog, legacy)['model_provider_map']['Google']
    assert ids.count(google['id']) == 1 and all(isinstance(i, str) and i.strip() for i in ids)


# ----------------------------------------------------------------------------------------------------------- vision_models

@pytest.mark.parametrize('provider_id', [pid for pid, _ in PROVIDER_DISPLAY])
def test_vision_models_are_the_explicit_chat_ids_that_take_images(config, catalog, provider_id):
    expected = ids_of(query_catalog(catalog, 'providerModels', {'kind': 'chat', 'input': 'image'}, provider=provider_id))
    assert config['vision_models'].get(provider_id, []) == expected


def test_vision_models_use_lowercase_provider_keys_and_no_patterns(config, legacy):
    assert set(config['vision_models']) <= {pid for pid, _ in PROVIDER_DISPLAY}
    assert all(key == key.lower() for key in config['vision_models'])
    assert not [i for ids in config['vision_models'].values() for i in ids if '*' in i]
    assert any('*' in i for ids in legacy['vision_models'].values() for i in ids)     # what the Worker still serves


def test_a_deprecated_model_that_reads_images_stays_vision_capable(config, catalog):
    """A saved key may still name gemini-2.5-flash. The `gemini-*` pattern is gone, the answer must not be."""
    old = next(m for m in catalog['models'] if m['id'] == 'gemini-2.5-flash')
    assert old['lifecycle']['status'] == 'deprecated' and 'image' in old['input']
    assert 'gemini-2.5-flash' in config['vision_models']['google']
    assert 'gemini-2.5-flash' not in config['model_provider_map']['Google']


def test_a_text_only_model_is_not_listed_as_vision(config, catalog):
    text_only = [m for m in catalog['models'] if m['kind'] == 'chat' and 'image' not in m['input']]
    assert text_only
    for model in text_only:
        assert model['id'] not in config['vision_models'].get(model['provider'], [])


def test_the_apps_vision_check_answers_from_the_registry_table(config, catalog, monkeypatch):
    monkeypatch.setattr(multimodal_module, 'get_config', lambda: types.SimpleNamespace(vision_models=config['vision_models']))
    reads = next(m['id'] for m in catalog['models'] if m['provider'] == 'google' and m['kind'] == 'chat'
                 and m['lifecycle']['status'] == 'active' and 'image' in m['input'])
    text_only = next(m['id'] for m in catalog['models'] if m['provider'] == 'sambanova' and 'image' not in m['input'])
    assert MultimodalProcessor.is_vision_capable(reads, 'google') is True
    assert MultimodalProcessor.is_vision_capable('gemini-2.5-flash', 'google') is True
    assert MultimodalProcessor.is_vision_capable(text_only, 'sambanova') is False


# ------------------------------------------------------------------------------------------------------- provider_api_urls

def test_the_chat_urls_reproduce_the_workers_twelve(config, legacy):
    assert config['provider_api_urls'] == legacy['provider_api_urls']
    assert len(config['provider_api_urls']) == 12


def test_google_and_anthropic_are_not_given_a_generic_chat_url(config, catalog):
    google = catalog['providers']['google']['api']
    assert google['openai_compatible_base_url']                    # the registry has one: it must stay unused
    assert 'Google' not in config['provider_api_urls'] and 'Anthropic' not in config['provider_api_urls']


def test_the_openai_compatible_address_wins_over_the_base_address_and_a_slash_is_dropped():
    providers = {pid: provider_entry(base_url=f'https://{pid}.example.test/v1/') for pid, _ in PROVIDER_DISPLAY}
    providers['groq']['api']['openai_compatible_base_url'] = 'https://groq.example.test/openai/v1'
    urls = build_api_urls(providers)
    assert urls['Groq'] == 'https://groq.example.test/openai/v1/chat/completions'
    assert urls['OpenAI'] == 'https://openai.example.test/v1/chat/completions'


@pytest.mark.parametrize('address', ['http://api.example.test/v1', 'ftp://x', '', None, 'https://a b.test', 7])
def test_an_address_that_is_not_plain_https_is_not_used(catalog, legacy, address):
    catalog['providers']['groq']['api'] = {'base_url': address, 'openai_compatible_base_url': None}
    with pytest.raises(RegistryDataError, match='Groq'):
        registry_to_config_v2(catalog, legacy)


# ------------------------------------------------------------------------------------------------------- api_key_patterns

def test_the_registry_lists_the_ambiguous_sk_prefix_for_three_providers(catalog):
    owners = sorted(pid for pid, p in catalog['providers'].items() if 'sk-' in p['key_prefixes'])
    assert owners == ['deepseek', 'openai', 'siliconflow']


def test_the_key_table_is_exactly_the_apps_own_and_has_no_bare_sk(config, legacy):
    assert config['api_key_patterns'] == legacy['api_key_patterns']
    assert 'sk-' not in config['api_key_patterns']
    assert len(config['api_key_patterns']) == 11


def test_prefixes_are_ordered_longest_first_so_none_hides_a_longer_one(config):
    prefixes = list(config['api_key_patterns'])
    assert [len(p) for p in prefixes] == sorted((len(p) for p in prefixes), reverse=True)
    for index, shorter in enumerate(prefixes):
        assert not [p for p in prefixes[:index] if shorter.startswith(p) and
                    config['api_key_patterns'][p] != config['api_key_patterns'][shorter]]


def test_a_prefix_several_providers_list_is_dropped():
    providers = {'google': provider_entry(['zz-', 'AIza']), 'openai': provider_entry(['zz-', 'sk-proj-'])}
    assert build_key_patterns(providers) == {'sk-proj-': 'OpenAI', 'AIza': 'Google'}


def test_a_prefix_that_begins_another_providers_prefix_is_dropped():
    """`sk-` alone for OpenAI (the registry changes its mind about DeepSeek) would still swallow `sk-ant-`."""
    providers = {'openai': provider_entry(['sk-']), 'anthropic': provider_entry(['sk-ant-']),
                 'openrouter': provider_entry(['sk-or-v1-'])}
    assert build_key_patterns(providers) == {'sk-or-v1-': 'OpenRouter', 'sk-ant-': 'Anthropic'}


def test_a_provider_may_keep_a_short_and_a_long_prefix_of_its_own():
    providers = {'openai': provider_entry(['sk-', 'sk-proj-'])}
    table = build_key_patterns(providers)
    assert table == {'sk-proj-': 'OpenAI', 'sk-': 'OpenAI'} and list(table) == ['sk-proj-', 'sk-']


def test_junk_prefixes_are_ignored():
    providers = {'groq': provider_entry(['gsk_', '', None, 7]), 'xai': {'key_prefixes': 'xai-'}, 'openai': 'x'}
    assert build_key_patterns(providers) == {'gsk_': 'Groq'}


class _Config:
    """What `get_config()` hands the app's key detection: only the key table."""

    def __init__(self, table, models=None):
        self.api_key_patterns = table
        self.model_provider_map = models or {}


@pytest.mark.parametrize('key, provider', [
    (STANDARD_KEY, 'Google'), (AUTH_KEY, 'Google'), (PROJECT_KEY, 'OpenAI'), (SERVICE_KEY, 'OpenAI'),
    (ANTHROPIC_KEY, 'Anthropic'), (OPENROUTER_KEY, 'OpenRouter'), ('gsk_' + 'x' * 20, 'Groq'),
    ('xai-' + 'x' * 20, 'xAI'), ('pplx-' + 'x' * 20, 'Perplexity'), ('hf_' + 'x' * 20, 'HuggingFace'),
    ('csk-' + 'x' * 20, 'Cerebras'),
    (BARE_KEY, ''), (UUID_KEY, ''), ('x' * 32, ''),          # Decision 106: a key that cannot be placed is refused
])
def test_key_detection_with_the_registry_table_matches_the_workers_table(config, legacy, monkeypatch, key, provider):
    for table in (config['api_key_patterns'], legacy['api_key_patterns']):
        monkeypatch.setattr(api_manager_module, 'get_config', lambda table=table: _Config(table))
        assert AIAPIManager()._detect_provider_from_key(key) == provider
        assert kt.detect_provider(key, table) == provider


def test_a_bare_sk_key_still_refuses_to_guess_google_under_auto(config, monkeypatch):
    monkeypatch.setattr(api_manager_module, 'get_config', lambda: _Config(config['api_key_patterns']))
    manager = AIAPIManager()
    manager.configure([{'api_key': BARE_KEY, 'provider': 'Auto', 'model_name': ''}])
    with pytest.raises(Exception, match='Cannot auto-detect provider'):
        manager.translate('Hello')


@pytest.mark.parametrize('claimants', [['OpenAI', 'DeepSeek'], ['Groq', 'HuggingFace', 'OpenRouter'],
                                       ['Cerebras', 'SambaNova'], ['Google', 'SambaNova']])
@pytest.mark.parametrize('key', [STANDARD_KEY, PROJECT_KEY, OPENROUTER_KEY, BARE_KEY, UUID_KEY, 'csk-' + 'x' * 20])
def test_the_tie_break_between_providers_that_list_one_model_follows_the_same_prefixes(config, legacy, monkeypatch,
                                                                                       claimants, key):
    picked = []
    for table in (config['api_key_patterns'], legacy['api_key_patterns']):
        monkeypatch.setattr(api_manager_module, 'get_config', lambda table=table: _Config(table))
        picked.append(AIAPIManager._claimant_for_key(claimants, key))
    assert picked[0] == picked[1]


@pytest.mark.parametrize('key, provider', [(ANTHROPIC_KEY, 'Anthropic'), (PROJECT_KEY, 'OpenAI'),
                                           (OPENROUTER_KEY, 'OpenRouter'), (AUTH_KEY, 'Google'), (BARE_KEY, 'Auto')])
def test_the_audio_card_places_keys_with_the_registry_table(config, monkeypatch, key, provider):
    monkeypatch.setattr(remote_config, 'get_config', lambda: _Config(config['api_key_patterns'], config['model_provider_map']))
    assert resolve_audio_provider('Auto', '', key) == provider


def test_the_apps_default_models_still_route_back_to_their_provider(config, monkeypatch):
    """The defaults the app tries first for a provider must identify as that provider (tests/test_default_model_routing)."""
    holder = types.SimpleNamespace(model_provider_map=config['model_provider_map'],
                                   api_key_patterns=config['api_key_patterns'],
                                   default_models_by_provider=config['default_models_by_provider'])
    monkeypatch.setattr(api_manager_module, 'get_config', lambda: holder)
    manager = AIAPIManager()
    for provider, models in config['default_models_by_provider'].items():
        key = next((prefix + 'x' * 8 for prefix, name in config['api_key_patterns'].items() if name == provider), 'x' * 8)
        for model in models:
            assert manager._identify_provider(model, key) == provider, (provider, model)


# --------------------------------------------------------------------------- what the registry cannot give: carried forward

def test_defaults_and_the_trial_limit_come_from_the_table_the_app_holds(catalog, legacy):
    config = registry_to_config_v2(catalog, base=legacy)
    assert config['default_models_by_provider'] == legacy['default_models_by_provider']
    assert config['trial_daily_limit'] == legacy['trial_daily_limit'] == 20
    config['default_models_by_provider']['Google'].append('mutated')
    assert 'mutated' not in legacy['default_models_by_provider']['Google']        # a copy, not the base's own lists


def test_without_a_held_table_the_defaults_are_the_hardcoded_ones_and_nothing_is_invented(catalog):
    config = registry_to_config_v2(catalog)
    assert config['default_models_by_provider'] == {k: list(v) for k, v in DEFAULT_MODELS_BY_PROVIDER.items()}
    assert 'trial_daily_limit' not in config                                      # no hardcoded value exists: left out


@pytest.mark.parametrize('base', [{}, {'default_models_by_provider': {}}, {'default_models_by_provider': 'x'},
                                  {'default_models_by_provider': {'Google': None}}, 'junk', None])
def test_an_unusable_held_table_falls_back_to_the_hardcoded_defaults(catalog, base):
    config = registry_to_config_v2(catalog, base=base)
    assert config['default_models_by_provider'] == {k: list(v) for k, v in DEFAULT_MODELS_BY_PROVIDER.items()}


@pytest.mark.parametrize('limit', [None, 'twenty', 2.5, True, [20]])
def test_a_trial_limit_that_is_not_a_number_is_left_out(catalog, legacy, limit):
    legacy['trial_daily_limit'] = limit
    assert 'trial_daily_limit' not in registry_to_config_v2(catalog, base=legacy)


# --------------------------------------------------------------------------------------------- bad registry data is refused

def thin_out(catalog, keep):
    """Only `keep` providers keep their chat models."""
    catalog['models'] = [m for m in catalog['models'] if m['kind'] != 'chat' or m['provider'] in keep]


@pytest.mark.parametrize('bad', [None, [], 'catalog', 7, {}])
def test_a_catalog_that_is_not_a_catalog_is_refused(bad):
    with pytest.raises(RegistryDataError):
        registry_to_config_v2(bad)


@pytest.mark.parametrize('version', [None, 0, 2, '1'])
def test_another_schema_version_is_refused(catalog, version):
    catalog['schema_version'] = version
    with pytest.raises(RegistryDataError, match='schema_version'):
        registry_to_config_v2(catalog)


def test_a_catalog_without_providers_or_models_is_refused(catalog):
    for field in ('providers', 'models'):
        broken = copy.deepcopy(catalog)
        broken[field] = {} if field == 'providers' else []
        with pytest.raises(RegistryDataError, match=field):
            registry_to_config_v2(broken)
        del broken[field]
        with pytest.raises(RegistryDataError, match=field):
            registry_to_config_v2(broken)


def test_fewer_than_five_providers_with_models_is_implausible(catalog, legacy):
    thin_out(catalog, {'google', 'openai', 'anthropic', 'openrouter'})
    with pytest.raises(RegistryDataError, match='only 4 providers'):
        registry_to_config_v2(catalog, legacy)
    catalog['models'] += [m for m in load_catalog()['models'] if m['provider'] == 'groq' and m['kind'] == 'chat']
    assert registry_to_config_v2(catalog, legacy)['model_provider_map']['Groq']          # five: plausible again


def test_an_empty_model_map_is_refused_whatever_the_held_table_says(catalog, legacy):
    catalog['models'] = [m for m in catalog['models'] if m['kind'] != 'chat']
    with pytest.raises(RegistryDataError, match='only 0 providers'):
        registry_to_config_v2(catalog, legacy)         # the held table's lists must not make an empty catalog look fine


def test_a_catalog_without_key_prefixes_is_refused(catalog, legacy):
    for provider in catalog['providers'].values():
        provider['key_prefixes'] = []
    with pytest.raises(RegistryDataError, match='key prefixes'):
        registry_to_config_v2(catalog, legacy)


def test_a_catalog_that_leaves_one_of_the_twelve_urls_out_is_refused(catalog, legacy):
    del catalog['providers']['together']['api']
    with pytest.raises(RegistryDataError, match='Together'):
        registry_to_config_v2(catalog, legacy)


def test_a_catalog_in_which_no_model_reads_images_is_refused(catalog, legacy):
    for model in catalog['models']:
        model['input'] = ['text']
    with pytest.raises(RegistryDataError, match='images'):
        registry_to_config_v2(catalog, legacy)


# -------------------------------------------------------------------------------------------- the client around the adapter

def test_a_working_registry_gives_the_table_and_keeps_its_copy_in_the_given_folder(server, tmp_path, legacy):
    cache = tmp_path / 'cache'
    config = fetch_registry_config(str(cache), server.registry_url, base=legacy)
    assert config == registry_to_config_v2(load_catalog(), base=legacy)
    assert sorted(p.name for p in cache.iterdir()) == ['catalog-v1.json']
    assert server.paths() == [CATALOG_PATH]


def test_the_default_urllib_user_agent_never_goes_out(server, tmp_path):
    """Cloudflare answers HTTP 403 / error 1010 to `Python-urllib/x`: what urllib really sent is what counts."""
    assert fetch_registry_config(str(tmp_path / 'cache'), server.registry_url) is not None
    (path, headers), = server.hits
    assert path == CATALOG_PATH
    assert headers['user-agent'] == model_registry_client.USER_AGENT
    assert not headers['user-agent'].lower().startswith('python-urllib') and headers['user-agent']
    assert headers['accept'] == 'application/json'


def test_a_custom_transport_is_given_the_clients_own_headers(tmp_path, catalog):
    import json
    seen = []

    def transport(url, headers, timeout):
        seen.append((url, headers, timeout))
        return 200, {'etag': ETAG}, json.dumps(catalog).encode('utf-8')

    assert fetch_registry_config(str(tmp_path / 'c'), 'https://registry.test', transport=transport, timeout=3) is not None
    (url, headers, timeout), = seen
    assert url == 'https://registry.test/v1/catalog' and timeout == 3
    assert headers['user-agent'] == model_registry_client.USER_AGENT and 'python-urllib' not in headers['user-agent'].lower()


def test_every_call_asks_the_registry_and_the_second_is_a_conditional_request(server, tmp_path):
    cache = str(tmp_path / 'cache')
    assert fetch_registry_config(cache, server.registry_url) is not None
    assert fetch_registry_config(cache, server.registry_url) is not None      # the client's 24 h cache does not answer it
    assert server.paths() == [CATALOG_PATH, CATALOG_PATH]
    assert 'if-none-match' not in server.hits[0][1] and server.hits[1][1]['if-none-match'] == ETAG


def test_the_registry_client_never_touches_the_temp_folder(server, tmp_path, monkeypatch):
    """Without cache_dir the client stores in <tempdir>/model-registry-client: that must not be reachable from here."""
    def refused():
        raise AssertionError('the registry client asked for the temp folder')

    monkeypatch.setattr(model_registry_client, 'tempfile',
                        types.SimpleNamespace(gettempdir=refused, mkstemp=tempfile.mkstemp))
    assert fetch_registry_config(str(tmp_path / 'cache'), server.registry_url) is not None
    with pytest.raises(ValueError, match='cache_dir'):
        fetch_registry_config('', server.registry_url)
    with pytest.raises(ValueError, match='cache_dir'):
        fetch_registry_config(None, server.registry_url)


def test_the_client_is_created_without_a_bundled_snapshot(server, tmp_path, monkeypatch):
    made = []
    real = registry_source.ModelRegistryClient
    monkeypatch.setattr(registry_source, 'ModelRegistryClient', lambda **kw: (made.append(kw), real(**kw))[1])
    fetch_registry_config(str(tmp_path / 'cache'), server.registry_url)
    (kwargs,) = made
    assert kwargs['snapshot'] is None and kwargs['cache_dir'] == str(tmp_path / 'cache')


@pytest.mark.parametrize('answer', [
    (500, b'{"error":"down"}'), (403, b'error code: 1010'), (404, b'{}'), (200, b'not json'), (200, b''),
    (200, b'[]'), (200, b'{"schema_version": 2, "providers": {}, "models": []}'), None,
], ids=['http-500', 'http-403-cloudflare', 'http-404', 'garbage', 'empty-body', 'array', 'other-schema', 'dropped'])
def test_a_registry_that_does_not_answer_properly_gives_nothing(server, tmp_path, answer):
    server.registry = answer
    assert fetch_registry_config(str(tmp_path / 'cache'), server.registry_url) is None


def test_a_registry_nobody_listens_on_gives_nothing(server, tmp_path):
    url = server.registry_url
    server.stop()
    assert fetch_registry_config(str(tmp_path / 'cache'), url) is None


def test_an_implausible_catalog_gives_nothing_and_leaves_no_copy_to_fall_back_on(server, tmp_path, catalog):
    thin_out(catalog, {'google'})
    server.serve_catalog(catalog)
    assert fetch_registry_config(str(tmp_path / 'cache'), server.registry_url) is None


def test_a_stale_copy_of_the_catalog_is_not_taken_for_an_answer(server, tmp_path):
    """The client serves its cached copy of any age when the network fails. The app would stamp it as new: refused."""
    cache = str(tmp_path / 'cache')
    assert fetch_registry_config(cache, server.registry_url) is not None
    server.registry = (503, b'{}')
    assert (tmp_path / 'cache' / 'catalog-v1.json').exists()
    assert fetch_registry_config(cache, server.registry_url) is None


def test_logs_hold_measurements_only(server, tmp_path, caplog, catalog):
    names = {m['id'] for m in catalog['models']}
    with caplog.at_level(logging.DEBUG, logger='src.core.registry_source'):
        fetch_registry_config(str(tmp_path / 'ok'), server.registry_url)
        server.registry = (500, b'{}')
        fetch_registry_config(str(tmp_path / 'down'), server.registry_url)
        server.serve_catalog(dict(catalog, models=[]))
        fetch_registry_config(str(tmp_path / 'bad'), server.registry_url)
    text = '\n'.join(record.getMessage() for record in caplog.records)
    assert 'providers=14' in text and 'duration_ms=' in text and 'HTTP 500' in text
    assert not [name for name in names if name in text]
    assert not [token for token in ('sk-', 'AIza', 'Bearer', 'gsk_') if token in text]
