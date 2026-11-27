"""The model registry in front of the Worker (Decision 110): the fallback chain of `RemoteConfigManager`.

Order of tiers: the registry, then the Worker's `/v1/config`, then the table the app holds (cache file or hardcoded).
The registry's table is the same v2 payload and goes through the same validation and cache file. A loopback server
plays both the registry and the Worker (tests/registry_support.py), so every request really goes through urllib and
`server.paths()` says which of the two the app contacted. Nothing here touches the singleton or the real
%APPDATA%\\CrossTrans2: the cache folder is a tmp_path.
"""
import json
import logging
import os
import threading
import time
import urllib.request
from types import SimpleNamespace

import pytest

import src.core  # noqa: F401 - imported before config, like the other tests (CLAUDE.md Known Issues)
import src.constants as constants
from src.core import registry_source, remote_config
from src.core.registry_source import registry_to_config_v2
from src.core.remote_config import REGISTRY_CACHE_DIRNAME, RemoteConfigManager
from tests.registry_support import CATALOG_PATH, WORKER_PATH, RegistryServer, load_catalog, load_legacy

HOUR = 3600


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A loopback registry + Worker, and a cache folder of its own, wired in where the app reads its settings."""
    monkeypatch.setattr(urllib.request, 'getproxies', lambda: {})
    monkeypatch.setattr(urllib.request, '_opener', urllib.request.build_opener(urllib.request.ProxyHandler({})))
    server = RegistryServer().start()
    cache_dir = tmp_path / 'CrossTrans2'
    monkeypatch.setattr(remote_config, 'CACHE_DIR', str(cache_dir))
    monkeypatch.setattr(remote_config, 'CACHE_FILE', str(cache_dir / 'models_config.json'))
    monkeypatch.setattr(constants, 'MODEL_REGISTRY_URL', server.registry_url)
    monkeypatch.setattr(constants, 'REMOTE_CONFIG_URL', server.worker_url)
    monkeypatch.setattr(constants, 'MODEL_REGISTRY_ENABLED', True)
    yield SimpleNamespace(server=server, cache_dir=cache_dir, cache_file=cache_dir / 'models_config.json',
                          tmp_path=tmp_path)
    server.stop()


def held_table(age_hours=30):
    """The table of an installed app: the Worker's payload as the cache file holds it, `age_hours` old."""
    table = load_legacy()
    table['_cached_at'] = time.time() - age_hours * HOUR
    table['_source'] = 'remote'
    return table


def make_manager(held=None):
    """A manager outside the singleton that holds `held` (the hardcoded table when None)."""
    manager = object.__new__(RemoteConfigManager)
    manager._initialized = True
    manager._config_lock = threading.RLock()
    manager._update_callbacks = []
    manager._fetching = False
    manager._last_refresh_attempt = 0.0
    manager._config = held if held is not None else manager._build_hardcoded_defaults()
    return manager


def without_meta(table):
    return {key: value for key, value in table.items() if not key.startswith('_')}


def cached(env):
    return json.loads(env.cache_file.read_text(encoding='utf-8'))


# ------------------------------------------------------------------------------------------------ the registry answers

def test_a_registry_that_answers_is_the_source_and_the_worker_is_not_contacted(env):
    manager = make_manager(held_table())
    manager._fetch_remote_thread()
    assert env.server.paths() == [CATALOG_PATH]                       # the Worker was never asked
    assert manager.config_source == 'registry'
    assert manager._fetching is False


def test_the_table_is_the_registrys_with_defaults_and_trial_limit_from_the_held_worker_table(env):
    held = held_table()
    manager = make_manager(held)
    manager._fetch_remote_thread()
    expected = registry_to_config_v2(load_catalog(), base=held)
    assert manager.model_provider_map == expected['model_provider_map']
    assert manager.vision_models == expected['vision_models']
    assert manager.api_key_patterns == expected['api_key_patterns']
    assert manager.provider_api_urls == expected['provider_api_urls']
    assert manager.providers_list == expected['providers_list'] and manager.providers_list[0] == 'Auto'
    assert manager.default_models_by_provider == held['default_models_by_provider']
    assert manager._config['trial_daily_limit'] == held['trial_daily_limit'] == 20
    assert manager.config_updated_at == load_catalog()['build']['generated_at']


def test_the_table_is_cached_in_the_usual_file_and_loads_back_as_a_registry_table(env):
    manager = make_manager(held_table())
    manager._fetch_remote_thread()
    saved = cached(env)
    assert saved['_source'] == 'registry' and saved['version'] == 2 and saved['_cached_at'] > time.time() - 60
    assert without_meta(saved) == without_meta(manager._config)
    reborn = object.__new__(RemoteConfigManager)
    reborn._config_lock = threading.RLock()
    reborn._load_cached_or_defaults()                                 # what the next start-up does
    assert reborn.config_source == 'registry' and reborn.model_provider_map == manager.model_provider_map
    assert reborn._is_cache_fresh()


def test_a_first_run_with_no_cache_takes_the_hardcoded_defaults_and_invents_no_trial_limit(env):
    manager = make_manager()                                          # the hardcoded tier: nothing cached yet
    manager._fetch_remote_thread()
    assert manager.config_source == 'registry' and env.server.paths() == [CATALOG_PATH]
    assert manager.default_models_by_provider == manager._build_hardcoded_defaults()['default_models_by_provider']
    assert 'trial_daily_limit' not in manager._config
    assert manager._validate_config(manager._config) is True


def test_the_registry_copy_lives_inside_the_apps_cache_folder_and_nowhere_else(env):
    make_manager(held_table())._fetch_remote_thread()
    created = sorted(os.path.relpath(os.path.join(root, name), env.tmp_path)
                     for root, _dirs, files in os.walk(env.tmp_path) for name in files)
    assert created == sorted([os.path.join('CrossTrans2', 'models_config.json'),
                              os.path.join('CrossTrans2', REGISTRY_CACHE_DIRNAME, 'catalog-v1.json')])


def test_the_registry_is_asked_again_at_every_refresh_with_a_conditional_request(env):
    """The app decides when to fetch (start-up after a day, then every 6 h); the client's 24 h cache must not hold it back."""
    manager = make_manager(held_table())
    manager._fetch_remote_thread()
    manager._fetch_remote_thread()
    assert env.server.paths() == [CATALOG_PATH, CATALOG_PATH]
    assert env.server.hits[1][1].get('if-none-match')
    assert manager.config_source == 'registry'


def test_the_registry_user_agent_is_not_urllibs_default(env):
    make_manager(held_table())._fetch_remote_thread()
    agent = env.server.hits[0][1]['user-agent']
    assert agent and not agent.lower().startswith('python-urllib')


def test_the_update_callbacks_hear_of_a_registry_table_like_any_other(env):
    manager = make_manager(held_table())
    heard = []
    manager.register_update_callback(lambda: heard.append(1))
    manager._fetch_remote_thread()
    assert heard == [1]


def test_the_real_background_thread_takes_the_same_road(env):
    manager = make_manager(held_table())
    manager.fetch_remote_async(force=True)
    for thread in [t for t in threading.enumerate() if t.name == 'remote-config']:
        thread.join(timeout=20)
    assert manager._fetching is False and manager.config_source == 'registry'
    assert env.server.paths() == [CATALOG_PATH]


# --------------------------------------------------------------------------- the registry does not answer: as it was before

REGISTRY_FAILURES = [
    ((500, b'{"error":"down"}'), 'http-500'), ((403, b'error code: 1010'), 'http-403'), ((200, b'<html>'), 'garbage'),
    ((200, b'{"schema_version": 2, "providers": {}, "models": []}'), 'other-schema'), (None, 'dropped'),
]


def failing_catalog():
    """A well-formed catalog the adapter must refuse: three providers only."""
    catalog = load_catalog()
    keep = {'google', 'openai', 'groq'}
    catalog['models'] = [m for m in catalog['models'] if m['kind'] != 'chat' or m['provider'] in keep]
    return (200, json.dumps(catalog).encode('utf-8'))


@pytest.mark.parametrize('answer', [a for a, _ in REGISTRY_FAILURES] + [failing_catalog()],
                         ids=[i for _, i in REGISTRY_FAILURES] + ['implausible-catalog'])
def test_a_registry_that_fails_hands_over_to_the_worker_unchanged(env, answer):
    env.server.registry = answer
    manager = make_manager(held_table())
    manager._fetch_remote_thread()
    assert env.server.paths() == [CATALOG_PATH, WORKER_PATH]
    assert manager.config_source == 'remote'
    legacy = load_legacy()
    for key in ('providers_list', 'model_provider_map', 'api_key_patterns', 'vision_models',
                'default_models_by_provider', 'provider_api_urls', 'trial_daily_limit'):
        assert manager._config[key] == legacy[key], key               # the Worker's table, untouched
    assert cached(env)['_source'] == 'remote'


def test_the_worker_path_gives_the_same_table_with_the_registry_on_or_off(env, monkeypatch):
    env.server.registry = (500, b'{}')
    on = make_manager(held_table())
    on._fetch_remote_thread()
    monkeypatch.setattr(constants, 'MODEL_REGISTRY_ENABLED', False)
    off = make_manager(held_table())
    off._fetch_remote_thread()
    assert without_meta(on._config) == without_meta(off._config)
    assert on.config_source == off.config_source == 'remote'


def test_a_registry_that_is_down_after_it_answered_is_not_served_from_its_stale_copy(env):
    manager = make_manager(held_table())
    manager._fetch_remote_thread()
    assert manager.config_source == 'registry'
    env.server.registry = (503, b'{}')
    manager._fetch_remote_thread()
    assert manager.config_source == 'remote'                          # the Worker was asked; the old copy did not stand in
    assert env.server.paths()[-2:] == [CATALOG_PATH, WORKER_PATH]


def test_the_worker_can_serve_again_after_a_registry_table(env):
    manager = make_manager(held_table())
    manager._fetch_remote_thread()
    assert manager.model_provider_map['Anthropic'] != load_legacy()['model_provider_map']['Anthropic']
    env.server.registry = (500, b'{}')
    manager._fetch_remote_thread()
    assert manager.model_provider_map['Anthropic'] == load_legacy()['model_provider_map']['Anthropic']


def test_when_neither_answers_the_app_keeps_the_table_it_has(env):
    env.server.registry = (500, b'{}')
    env.server.worker = (500, b'{}')
    held = held_table()
    manager = make_manager(held)
    before = dict(manager._config)
    manager._fetch_remote_thread()
    assert manager._config == before and manager._fetching is False
    assert env.server.paths() == [CATALOG_PATH, WORKER_PATH]
    assert not env.cache_file.exists()                                # nothing stamped as new


def test_a_worker_payload_that_fails_validation_is_still_refused(env):
    env.server.registry = (500, b'{}')
    env.server.worker = (200, json.dumps({'version': 3}).encode('utf-8'))
    manager = make_manager(held_table())
    before = dict(manager._config)
    manager._fetch_remote_thread()
    assert manager._config == before and not env.cache_file.exists()


# ----------------------------------------------------------------------------------------------------- the feature switch

def test_with_the_switch_off_only_the_worker_is_asked_and_no_registry_folder_appears(env, monkeypatch):
    monkeypatch.setattr(constants, 'MODEL_REGISTRY_ENABLED', False)
    manager = make_manager(held_table())
    manager._fetch_remote_thread()
    assert env.server.paths() == [WORKER_PATH]
    assert manager.config_source == 'remote'
    assert not (env.cache_dir / REGISTRY_CACHE_DIRNAME).exists()


@pytest.mark.parametrize('value, expected', [
    (None, True), ('', True), ('   ', True), ('1', True), ('true', True), ('yes', True), ('on', True), ('anything', True),
    ('0', False), ('false', False), ('False', False), ('NO', False), ('off', False), (' Off ', False),
])
def test_the_environment_variable_turns_the_registry_off(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv('CROSSTRANS_MODEL_REGISTRY', raising=False)
    else:
        monkeypatch.setenv('CROSSTRANS_MODEL_REGISTRY', value)
    assert constants._env_switch('CROSSTRANS_MODEL_REGISTRY', True) is expected


def test_the_switch_is_on_by_default_and_the_registry_address_is_the_public_one():
    assert constants._env_switch('CROSSTRANS_NO_SUCH_SWITCH', True) is True
    assert constants._env_switch('CROSSTRANS_NO_SUCH_SWITCH', False) is False
    assert constants.MODEL_REGISTRY_URL == 'https://models.masaru.uk'
    assert isinstance(constants.MODEL_REGISTRY_ENABLED, bool)


# ------------------------------------------------------------------------------------------- the seams cannot break the app

def test_a_registry_step_that_raises_hands_over_to_the_worker(env, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError('fixture')

    monkeypatch.setattr(registry_source, 'fetch_registry_config', boom)
    manager = make_manager(held_table())
    manager._fetch_remote_thread()
    assert env.server.paths() == [WORKER_PATH] and manager.config_source == 'remote'


def test_a_registry_table_that_fails_the_apps_validation_hands_over_to_the_worker(env, monkeypatch):
    monkeypatch.setattr(registry_source, 'fetch_registry_config', lambda **kwargs: {'version': 99})
    manager = make_manager(held_table())
    manager._fetch_remote_thread()
    assert env.server.paths() == [WORKER_PATH] and manager.config_source == 'remote'


def test_the_registry_step_is_given_the_held_table_and_the_cache_folder_of_the_app(env, monkeypatch):
    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(registry_source, 'fetch_registry_config', spy)
    held = held_table()
    make_manager(held)._fetch_remote_thread()
    assert seen['cache_dir'] == os.path.join(str(env.cache_dir), REGISTRY_CACHE_DIRNAME)
    assert seen['base_url'] == env.server.registry_url
    assert seen['base']['default_models_by_provider'] == held['default_models_by_provider']
    assert seen['base']['trial_daily_limit'] == 20


def test_the_legacy_save_path_is_unchanged(env):
    manager = make_manager(held_table())
    manager._save_cache(load_legacy())                                # no source given: what the Worker path always wrote
    assert cached(env)['_source'] == 'remote'
    manager._save_cache(load_legacy(), 'registry')
    assert cached(env)['_source'] == 'registry'


def test_clearing_the_cache_removes_the_registry_copy_too(env):
    make_manager(held_table())._fetch_remote_thread()
    assert env.cache_file.exists() and (env.cache_dir / REGISTRY_CACHE_DIRNAME).exists()
    make_manager().clear_cache()
    assert not env.cache_file.exists() and not (env.cache_dir / REGISTRY_CACHE_DIRNAME).exists()
    make_manager().clear_cache()                                      # nothing left to clear: no error


def test_the_refresh_cadence_still_asks_for_a_forced_fetch(env):
    """Decision 103 is unchanged: a table older than the interval is fetched again, whatever the source."""
    manager = make_manager(held_table(age_hours=7))
    asked = []
    manager.fetch_remote_async = lambda force=False: asked.append(force)
    assert manager.refresh_if_stale() is True and asked == [True]
    assert env.server.hits == []                                      # nothing is fetched by merely deciding to


def test_the_frozen_app_is_told_about_the_registry_modules_and_bundles_no_snapshot():
    """remote_config imports them inside a function and swallows an ImportError: a bundle without them would silently
    fall back to the Worker. The hardcoded tier is the offline floor, so no catalog snapshot is bundled either."""
    from pathlib import Path
    from tools import verify_ui_bundle as bundle
    root = Path(__file__).resolve().parents[1]
    spec = (root / 'CrossTrans.spec').read_text(encoding='utf-8')
    for module in ('src.core.registry_source', 'src.core.model_registry_client'):
        assert f"'{module}'" in spec and module in bundle.ESSENTIAL_MODULES
    assert 'snapshot' not in spec.lower() and not list((root / 'src').rglob('*snapshot*'))


def test_the_logs_hold_measurements_only(env, caplog):
    catalog = load_catalog()
    with caplog.at_level(logging.DEBUG):
        make_manager(held_table())._fetch_remote_thread()
        env.server.registry = (500, b'{}')
        make_manager(held_table())._fetch_remote_thread()
    text = '\n'.join(record.getMessage() for record in caplog.records)
    assert 'Updated config from the model registry' in text and 'Updated config from remote' in text
    assert 'providers=14' in text
    assert not [m['id'] for m in catalog['models'] if m['id'] in text]
    assert not [token for token in ('sk-', 'AIza', 'Bearer', 'gsk_') if token in text]
