"""
The model registry as the primary source of the model/provider table (Decision 110).

`https://models.masaru.uk` (schema v1) lists what each provider offers right now. The app's table (`remote_config`)
has always been the v2 payload of the Worker's `/v1/config`; this module rebuilds that same payload from the registry's
catalog, so everything downstream (validation, the cache file, `get_config()`) stays as it was.

    fetch_registry_config()  ->  download the catalog, adapt it, or None  (never raises)
    registry_to_config_v2()  ->  the adapter: catalog dict in, v2 payload dict out; raises RegistryDataError

The mapping follows the registry's docs/INTEGRATION.md section 5. What the registry cannot give is taken from the table
the app already holds (`base`): `default_models_by_provider` and `trial_daily_limit` are product choices of the Worker,
not registry data, so they are carried forward and never invented.

Nothing here reaches Tk, and the logs hold measurements only (counts, durations, the registry's own error class).
"""
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from src.core.model_registry_client import ModelRegistryClient

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
AUTO = 'Auto'

# Registry provider id -> the display name the app uses, in the order of `providers_list` (src/constants.py).
PROVIDER_DISPLAY: Tuple[Tuple[str, str], ...] = (
    ('google', 'Google'), ('openai', 'OpenAI'), ('anthropic', 'Anthropic'), ('deepseek', 'DeepSeek'),
    ('groq', 'Groq'), ('xai', 'xAI'), ('mistral', 'Mistral'), ('perplexity', 'Perplexity'),
    ('cerebras', 'Cerebras'), ('sambanova', 'SambaNova'), ('together', 'Together'),
    ('siliconflow', 'SiliconFlow'), ('openrouter', 'OpenRouter'), ('huggingface', 'HuggingFace'),
)

# The providers `api_manager` calls through `provider_api_urls` (chat completions, OpenAI style). Google and Anthropic
# have their own clients and are deliberately not listed: a URL for them would route them through the generic path.
OPENAI_STYLE_PROVIDERS = (
    'OpenAI', 'Groq', 'DeepSeek', 'Mistral', 'xAI', 'Perplexity', 'Cerebras', 'SambaNova', 'Together',
    'SiliconFlow', 'OpenRouter', 'HuggingFace',
)

# A catalog that fails these is treated as bad data: the caller falls through to the Worker, as if the registry were down.
MIN_PROVIDERS_WITH_MODELS = 5     # providers of the table for which the registry lists at least one chat model
MIN_KEY_PREFIXES = 5              # the app's own table has 11; without prefixes key detection would silently stop

# The registry's own states in which a chat model is offered to the user (INTEGRATION.md: models() defaults).
OFFERED_STATUSES = ('active', 'preview')


class RegistryDataError(ValueError):
    """The catalog is not usable as the app's table (missing parts, implausibly small, unusable URLs)."""


def _providers_of(catalog: Any) -> Dict[str, Any]:
    if not isinstance(catalog, dict):
        raise RegistryDataError('catalog is not an object')
    if catalog.get('schema_version') != SCHEMA_VERSION:
        raise RegistryDataError(f"unsupported schema_version {catalog.get('schema_version')!r}")
    providers, models = catalog.get('providers'), catalog.get('models')
    if not isinstance(providers, dict) or not providers:
        raise RegistryDataError('catalog has no providers')
    if not isinstance(models, list) or not models:
        raise RegistryDataError('catalog has no models')
    return providers


def _chat_models(catalog: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Chat models of the catalog by provider id, in catalog order, ids that are real non-empty strings only."""
    by_provider: Dict[str, List[Dict[str, Any]]] = {}
    for model in catalog['models']:
        if not isinstance(model, dict) or model.get('kind') != 'chat':
            continue
        model_id, provider = model.get('id'), model.get('provider')
        if isinstance(model_id, str) and model_id.strip() and isinstance(provider, str):
            by_provider.setdefault(provider, []).append(model)
    return by_provider


def _status_of(model: Dict[str, Any]) -> Any:
    lifecycle = model.get('lifecycle')
    return lifecycle.get('status') if isinstance(lifecycle, dict) else None


def _reads_images(model: Dict[str, Any]) -> bool:
    modalities = model.get('input')
    return isinstance(modalities, (list, tuple)) and 'image' in modalities


def _unique(ids: List[str]) -> List[str]:
    return list(dict.fromkeys(ids))


def build_key_patterns(providers: Dict[str, Any]) -> Dict[str, str]:
    """`{prefix: display name}` for the prefixes that identify ONE provider.

    The app matches a key with `key.startswith(prefix)`, first hit wins, and keeps a bare `sk-` unknown on purpose
    (Decision 106: a key it cannot place is refused, never sent to Google). The registry also lists `sk-` for OpenAI,
    DeepSeek and SiliconFlow. A dict can hold it for one provider only, and it would also swallow `sk-ant-`, `sk-or-v1-`
    and `sk-proj-` wherever it came first, so a prefix is dropped when
      - more than one provider lists it (ambiguous), or
      - it is the beginning of another provider's prefix (generic).
    The rest is ordered longest first, so no prefix can hide a longer one.
    """
    claims: Dict[str, set] = {}
    for provider_id, display in PROVIDER_DISPLAY:
        entry = providers.get(provider_id)
        listed = entry.get('key_prefixes') if isinstance(entry, dict) else None
        for prefix in listed if isinstance(listed, (list, tuple)) else ():
            if isinstance(prefix, str) and prefix:
                claims.setdefault(prefix, set()).add(display)
    kept: Dict[str, str] = {}
    for prefix, owners in claims.items():
        if len(owners) != 1:
            continue
        if any(other != prefix and other.startswith(prefix) and claims[other] != owners for other in claims):
            continue
        kept[prefix] = next(iter(owners))
    return dict(sorted(kept.items(), key=lambda item: (-len(item[0]), item[0])))


def build_api_urls(providers: Dict[str, Any]) -> Dict[str, str]:
    """`{display name: chat-completions URL}` of the OpenAI-style providers.

    `(openai_compatible_base_url or base_url) + /chat/completions`, identical to the Worker's 12 URLs on 2026-11-27.
    Only `https://` counts: the app sends the user's key to this address."""
    ids = {display: provider_id for provider_id, display in PROVIDER_DISPLAY}
    urls: Dict[str, str] = {}
    for display in OPENAI_STYLE_PROVIDERS:
        entry = providers.get(ids[display])
        api = entry.get('api') if isinstance(entry, dict) else None
        if not isinstance(api, dict):
            continue
        base = api.get('openai_compatible_base_url') or api.get('base_url')
        if isinstance(base, str) and base.startswith('https://') and not any(ch.isspace() for ch in base):
            urls[display] = base.rstrip('/') + '/chat/completions'
    return urls


def _hardcoded_defaults() -> Dict[str, List[str]]:
    # Imported here, not at the top: api_manager imports remote_config, which reaches this module.
    from src.core.api_manager import DEFAULT_MODELS_BY_PROVIDER
    return {name: list(models) for name, models in DEFAULT_MODELS_BY_PROVIDER.items()}


def registry_to_config_v2(catalog: Any, base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The registry catalog as a v2 `/v1/config` payload.

    `base` is the table the app holds now (cache or hardcoded); it supplies what the registry has no equivalent for:
    `default_models_by_provider` (the app's hardcoded defaults when `base` has none), `trial_daily_limit` (left out
    when `base` has none) and, for a provider whose models the registry lists none as active or preview, that
    provider's list (the registry knows Perplexity's four models only as deprecated; its picker must not go empty).

    Raises RegistryDataError when the result is implausible, so a bad catalog never replaces a good table."""
    base = base if isinstance(base, dict) else {}
    providers = _providers_of(catalog)
    chat = _chat_models(catalog)

    model_map: Dict[str, List[str]] = {}
    vision: Dict[str, List[str]] = {}
    base_map = base.get('model_provider_map') if isinstance(base.get('model_provider_map'), dict) else {}
    listed = 0
    for provider_id, display in PROVIDER_DISPLAY:
        models = chat.get(provider_id, [])
        offered = _unique([m['id'] for m in models if _status_of(m) in OFFERED_STATUSES])
        if offered:
            listed += 1
        else:
            offered = _unique([i for i in base_map.get(display) or [] if isinstance(i, str) and i])
        model_map[display] = offered
        # Vision is a capability question, not an offer: a deprecated model that still answers keeps its answer, so a
        # key saved with `gemini-2.5-flash` stays vision-capable now that the `gemini-*` pattern is gone.
        images = _unique([m['id'] for m in models if _reads_images(m)])
        if images:
            vision[provider_id] = images

    if listed < MIN_PROVIDERS_WITH_MODELS:
        raise RegistryDataError(f'only {listed} providers list chat models (need {MIN_PROVIDERS_WITH_MODELS})')
    key_patterns = build_key_patterns(providers)
    if len(key_patterns) < MIN_KEY_PREFIXES:
        raise RegistryDataError(f'only {len(key_patterns)} usable key prefixes (need {MIN_KEY_PREFIXES})')
    api_urls = build_api_urls(providers)
    if len(api_urls) != len(OPENAI_STYLE_PROVIDERS):
        missing = [name for name in OPENAI_STYLE_PROVIDERS if name not in api_urls]
        raise RegistryDataError(f'no https chat URL for {len(missing)} provider(s): {", ".join(missing)}')
    if not vision:
        raise RegistryDataError('no model reads images')

    held = base.get('default_models_by_provider')
    defaults = {name: list(models) for name, models in held.items() if isinstance(models, (list, tuple))}         if isinstance(held, dict) else {}
    if not defaults:
        defaults = _hardcoded_defaults()

    build = catalog.get('build')
    generated = build.get('generated_at') if isinstance(build, dict) else None
    config: Dict[str, Any] = {
        'version': 2,
        'updated_at': generated if isinstance(generated, str) else '',
        'providers_list': [AUTO] + [display for _, display in PROVIDER_DISPLAY],
        'model_provider_map': model_map,
        'api_key_patterns': key_patterns,
        'vision_models': vision,
        'default_models_by_provider': defaults,
        'provider_api_urls': api_urls,
    }
    limit = base.get('trial_daily_limit')
    if isinstance(limit, int) and not isinstance(limit, bool):
        config['trial_daily_limit'] = limit
    return config


def fetch_registry_config(cache_dir: str, base_url: str, base: Optional[Dict[str, Any]] = None,
                          transport: Any = None, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Download the catalog and adapt it; None when that is not possible. Never raises.

    The registry client keeps its own copy under `cache_dir` (the app's cache folder, never the temp folder) and is
    created with no bundled snapshot: the hardcoded table is the app's offline floor.

    Every call asks the registry (a conditional request, `If-None-Match`; a 304 costs a few hundred bytes): when to ask
    is the app's decision (start-up after a day, then every few hours), and the client's own 24-hour cache must not
    stretch it. Only an answer from the network counts. When the registry cannot be reached the client would serve
    its cached copy of any age as `stale-cache`; that is not accepted here, so the Worker is tried next and, failing
    that, the app keeps the table it has (which came from this very registry), instead of stamping old data as new."""
    if not cache_dir:
        raise ValueError('cache_dir is required: the registry client must never fall back to the temp folder')
    started = time.monotonic()
    options: Dict[str, Any] = {}
    if transport is not None:
        options['transport'] = transport
    if timeout is not None:
        options['timeout'] = timeout
    try:
        client = ModelRegistryClient(base_url=base_url, cache_dir=cache_dir, snapshot=None, **options)
        answer = client.catalog(force_refresh=True)
    except Exception as exc:  # noqa: BLE001 - RegistryUnavailableError and anything the client or transport raised
        logger.warning('[Registry] unreachable: %s: %s after %d ms', type(exc).__name__, exc, _elapsed_ms(started))
        return None
    if answer.get('source') != 'network':
        logger.warning('[Registry] not reachable, client offered %s (%s) after %d ms', answer.get('source'),
                       '; '.join(str(w) for w in answer.get('warnings') or []), _elapsed_ms(started))
        return None
    try:
        config = registry_to_config_v2(answer['catalog'], base)
    except Exception as exc:  # noqa: BLE001 - RegistryDataError, or a catalog shaped in a way the adapter did not expect
        logger.warning('[Registry] catalog rejected: %s: %s', type(exc).__name__, exc)
        return None
    logger.info('[Registry] table built: providers=%d chat_models=%d vision_providers=%d not_modified=%s duration_ms=%d',
                len(config['providers_list']) - 1, sum(len(v) for v in config['model_provider_map'].values()),
                len(config['vision_models']), bool(answer.get('not_modified')), _elapsed_ms(started))
    return config


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
