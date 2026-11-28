"""A provider's default models must route back to that provider.

`DEFAULT_MODELS_BY_PROVIDER` is read whenever the model field is blank. When the
provider is set to 'Auto', the chosen model is then handed straight back to
`_identify_provider()` to decide which API to call - so a default that does not
identify as its own provider sends the user's key to somebody else's endpoint.

These tests run against **tier 3**: `src/constants.py` alone, no remote config
and no cache. That is a fresh install with no network, and it is the one tier
where a wrong entry cannot be corrected from Cloudflare.

Measured 2026-09-08: `google/gemma-4-31b-it:free`, chosen as an OpenRouter
default, identified as **Together**. `_identify_provider()` tries an exact match
in `model_provider_map` first, then a set of prefix rules in which `google/`
means Together - and the free models were not in the map, so the prefix rule
won. Both of the OpenRouter defaults it replaced did the same thing, on top of
being dead.
"""

import pytest

import src.core.api_manager as api_manager_module
from src.core.api_manager import AIAPIManager, DEFAULT_MODELS_BY_PROVIDER
from src.constants import API_KEY_PATTERNS, MODEL_PROVIDER_MAP, VISION_MODELS
from src.core import multimodal as multimodal_module
from src.core.multimodal import MultimodalProcessor


class _HardcodedConfig:
    """The tier-3 view: what constants.py provides with no network."""

    model_provider_map = MODEL_PROVIDER_MAP
    api_key_patterns = API_KEY_PATTERNS


@pytest.fixture
def offline_manager(monkeypatch):
    """An AIAPIManager whose config is the hardcoded fallback only."""
    monkeypatch.setattr(api_manager_module, 'get_config', _HardcodedConfig)
    return AIAPIManager()


def _key_for(provider):
    """A plausible API key for `provider`, or a neutral one if it has no pattern."""
    for prefix, name in API_KEY_PATTERNS.items():
        if name == provider:
            return prefix + 'x' * 8
    return 'x' * 8


@pytest.mark.parametrize(
    'provider,model',
    [(p, m) for p, models in DEFAULT_MODELS_BY_PROVIDER.items() for m in models],
)
def test_a_default_model_identifies_as_its_own_provider(offline_manager, provider, model):
    """Every entry, for every provider, offline. The whole point of the table."""
    identified = offline_manager._identify_provider(model, _key_for(provider))
    assert identified == provider, (
        f"{provider}'s default model {model!r} identifies as {identified!r}. "
        f"Under provider='Auto' that routes the request to {identified}'s API "
        f"with a {provider} key. Add it to MODEL_PROVIDER_MAP[{provider!r}] so "
        f"the exact-match step of _identify_provider() wins."
    )


def test_openrouter_defaults_are_listed_in_the_model_map(offline_manager):
    """The specific trap: a slash-prefixed default that a prefix rule can steal.

    Kept separate from the parametrized sweep because it names the mechanism.
    `_identify_provider()` reads `model_provider_map` before its prefix rules,
    so listing the model there is what makes the exact match win. Drop these two
    from the map and `google/gemma-4-31b-it:free` answers Together again.
    """
    listed = MODEL_PROVIDER_MAP['OpenRouter']
    for model in DEFAULT_MODELS_BY_PROVIDER['OpenRouter']:
        assert model in listed, (
            f"{model!r} is an OpenRouter default but is missing from "
            f"MODEL_PROVIDER_MAP['OpenRouter']"
        )


def test_the_google_prefix_rule_really_does_claim_together(offline_manager):
    """Pins the mechanism itself, so the test above cannot quietly stop meaning anything.

    If this ever fails, the prefix rule changed and the guard above is no longer
    protecting against anything - which is worth knowing.
    """
    stolen = offline_manager._identify_provider('google/not-a-real-model', 'sk-or-v1-xxxxxxxx')
    assert stolen == 'Together'


def test_no_default_model_is_a_batch_variant():
    """':batch' endpoints are asynchronous and cannot answer an interactive translation."""
    for provider, models in DEFAULT_MODELS_BY_PROVIDER.items():
        for model in models:
            assert ':batch' not in model, f'{provider} default {model!r} is a batch endpoint'


def test_a_cerebras_key_is_not_mistaken_for_google(offline_manager):
    """`_detect_provider_from_key()` defaults to Google, so a missing pattern is expensive.

    Measured 2026-09-08 with a real key: without the `csk-` entry a Cerebras user
    who leaves Provider on 'Auto' and the model field blank was handed Google's
    default (`gemini-2.5-flash`) and sent it to `api.cerebras.ai`. The failure is
    silent and looks like a dead model, which is what made it worth a test rather
    than just a table entry.
    """
    assert offline_manager._detect_provider_from_key('csk-' + 'x' * 20) == 'Cerebras'


def test_an_unknown_model_on_a_known_key_still_finds_its_provider(offline_manager):
    """The key pattern is the backstop when a model is too new to be in the map."""
    for prefix, provider in API_KEY_PATTERNS.items():
        identified = offline_manager._identify_provider('some-unreleased-model', prefix + 'x' * 20)
        assert identified == provider, (
            f'a {provider} key ({prefix}...) with an unlisted model identified as {identified}'
        )


# Retired or going, by their own providers' documentation (desk check 2026-10-16,
# DEFERRED D11, K15). No key was at hand, so the replacements are listed by each
# provider today rather than answered - but these are known dead, and a default
# must never be one of them again.
RETIRED = {
    'Google': {'gemini-1.5-flash', 'gemini-1.5-pro', 'gemini-2.0-flash',  # answered 404 on 2026-09-08
               'gemini-2.5-flash'},                                 # deprecated, routed away 2026-12-01 (Decision 94)
    'OpenAI': {'gpt-3.5-turbo',                                     # shut down 2026-10-23
               'gpt-4o', 'gpt-4o-mini'},                            # shut down 2027-01-14 (Decision 92)
    'Anthropic': {'claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022',
                  'claude-3-haiku-20240307',                        # retired 2025-10 .. 2026-04
                  'claude-haiku-4-5'},                              # replaced by claude-haiku-4-5-20251001
    'SambaNova': {'Meta-Llama-3.3-70B-Instruct'},                   # removed from SambaNova catalogue
    'DeepSeek': {'deepseek-chat', 'deepseek-coder'},                # gone from the API docs
    'xAI': {'grok-2', 'grok-beta'},                                 # not on xAI's model list
    'Together': {'meta-llama/Llama-3.3-70B-Instruct-Turbo',         # removed 2026-10-22
                 'meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo'},   # removed 2026-02-25
}


@pytest.mark.parametrize('provider', sorted(RETIRED))
def test_no_default_is_a_model_its_provider_has_retired(provider):
    stale = set(DEFAULT_MODELS_BY_PROVIDER[provider]) & RETIRED[provider]
    assert not stale, (f"{provider}'s defaults {sorted(stale)} are retired by {provider} "
                       f"itself - every auto-pick would fail (DEFERRED D11)")


def test_google_leads_with_the_replacement_it_names_for_gemini_2_5_flash():
    """Google routes gemini-2.5-flash to gemini-3.5-flash-lite from 2026-12-01 (Decision 94). The
    not-deprecated gemini-2.5-flash-lite stays the fast model to rotate to, the alias the last resort."""
    assert DEFAULT_MODELS_BY_PROVIDER['Google'] == ['gemini-3.5-flash-lite', 'gemini-2.5-flash-lite',
                                                   'gemini-flash-latest']
    assert 'gemini-3.5-flash-lite' in MODEL_PROVIDER_MAP['Google']


def test_every_provider_has_a_second_default_to_rotate_to():
    """_try_model_rotation() moves to the next default when the first fails; a
    provider left with one has nowhere to go (D11 removed gpt-3.5-turbo)."""
    for provider, models in DEFAULT_MODELS_BY_PROVIDER.items():
        assert len(models) >= 2, f"{provider} has {models}"


def test_every_provider_model_list_has_unique_non_empty_non_batch_entries():
    for provider, models in MODEL_PROVIDER_MAP.items():
        assert models, f'{provider} list is empty'
        seen = set()
        for model in models:
            assert isinstance(model, str) and model.strip() == model and model
            assert ':batch' not in model, f'{provider} lists batch endpoint {model!r}'
            key = model.lower()
            assert key not in seen, f'{provider} lists duplicate model {model!r}'
            seen.add(key)


def test_every_default_is_listed_in_its_providers_model_map():
    for provider, defaults in DEFAULT_MODELS_BY_PROVIDER.items():
        listed = MODEL_PROVIDER_MAP[provider]
        for model in defaults:
            assert model in listed, f'{provider} default {model!r} is missing from MODEL_PROVIDER_MAP'


@pytest.mark.parametrize('model,expected', [
    ('gpt-4o', 'OpenAI'),
    ('gpt-4o-mini', 'OpenAI'),
    ('gpt-4.1', 'OpenAI'),
    ('gpt-3.5-turbo', 'OpenAI'),
    ('o1-preview', 'OpenAI'),
    ('o3-mini', 'OpenAI'),
    ('claude-3-5-sonnet-20241022', 'Anthropic'),
    ('claude-3-opus-20240229', 'Anthropic'),
    ('claude-haiku-4-5', 'Anthropic'),
    ('deepseek-chat', 'DeepSeek'),
    ('deepseek-reasoner', 'DeepSeek'),
    ('deepseek-r1', 'DeepSeek'),
    ('grok-3', 'xAI'),
    ('grok-2', 'xAI'),
    ('gemini-2.5-flash', 'Google'),
    ('gemini-2.0-flash', 'Google'),
    ('mistral-large-2411', 'Mistral'),
    ('pixtral-large-latest', 'Mistral'),
    ('Meta-Llama-3.3-70B-Instruct', 'SambaNova'),
])
def test_manually_typed_legacy_ids_still_route_to_their_provider(offline_manager, model, expected):
    assert offline_manager._identify_provider(model, '') == expected


@pytest.mark.parametrize('provider,model,capable', [
    ('anthropic', 'claude-haiku-5-5', True),
    ('anthropic', 'claude-sonnet-5-5', True),
    ('anthropic', 'claude-opus-5-5', True),
    ('anthropic', 'claude-fable-5-1', True),
    ('anthropic', 'claude-haiku-4-5-20251001', True),
    ('anthropic', 'claude-3-5-sonnet-20241022', True),
    ('openai', 'gpt-6-luna', True),
    ('openai', 'gpt-6.1-terra', True),
    ('openai', 'gpt-5.6', True),
    ('openai', 'gpt-4o', True),
    ('openai', 'o3-mini', False),
    ('google', 'gemini-3.5-flash-lite', True),
    ('google', 'gemini-4-argon', True),
    ('google', 'gemma-4-31b-it', True),
    ('deepseek', 'deepseek-flash', True),
    ('deepseek', 'deepseek-v4-pro', False),
    ('groq', 'qwen/qwen3.8-27b', True),
    ('groq', 'openai/gpt-oss-120b', False),
    ('xai', 'grok-4.20-0309-non-reasoning', True),
    ('mistral', 'mistral-large-latest', True),
    ('mistral', 'ministral-8b-latest', True),
    ('mistral', 'codestral-latest', False),
    ('perplexity', 'sonar-pro', True),
    ('perplexity', 'sonar', False),
    ('cerebras', 'qwen-3.8-27b', True),
    ('cerebras', 'gpt-oss-120b', False),
    ('sambanova', 'gemma-4-31B-it', True),
    ('sambanova', 'gpt-oss-120b', False),
])
def test_offline_vision_patterns_match_live_capabilities(monkeypatch, provider, model, capable):
    monkeypatch.setattr(multimodal_module, 'get_config',
                        lambda: type('Cfg', (), {'vision_models': VISION_MODELS})())
    assert MultimodalProcessor.is_vision_capable(model, provider) is capable
