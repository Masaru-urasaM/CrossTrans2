"""
AI API Manager for CrossTrans.
Handles communication with various API keys (Google, OpenAI, Anthropic, Groq, etc.)
"""
import json
import time
import threading
import urllib.request
import urllib.error
import logging
import re
from urllib.parse import urlsplit
from typing import Optional, List, Dict, Any, Callable, TYPE_CHECKING


from src.core.remote_config import get_config
from src.core.model_names import takes_temperature, thinking_config
from src.core.multimodal import MultimodalProcessor
from src.core.text_cleanup import strip_thinking_tags
from src.core.ssl_pinning import get_ssl_context_for_url
from src.utils.log_events import log_event
from src.constants import TRANSLATION_TIMEOUT_SECONDS
from src.core.translation_task import (TranslationStopped, active_write, bounded_io,
                                       check_current, current_task, pause, request_budget)
from src.core.generation_result import (IncompleteGenerationError, check_generation_result,
                                        google_text, anthropic_text)

# Default models to try for each provider when model is "Auto"
# Ordered by preference (best models first)
# Keys match PROVIDERS_LIST exactly (Title Case)
# NOTE: These are hardcoded fallbacks. The active list comes from get_config().
DEFAULT_MODELS_BY_PROVIDER = {
    # All three previous entries answer 404 (checked 2026-09-08 with a real call):
    # gemini-2.0-flash, gemini-1.5-flash and gemini-1.5-pro are retired. This list
    # drives auto-pick and _try_model_rotation, so it keeps the old intent - a
    # mid-tier flash model first - with IDs that exist, a faster one to rotate to
    # when the first is busy (2.5-flash really did answer 503 mid-check), and an
    # alias last that will survive the next deprecation.
    # Decision 94 (2026-11-18): Google deprecated gemini-2.5-flash, routes it to
    # gemini-3.5-flash-lite from 2026-12-01 and limits the 2.5 models to existing users,
    # so the replacement it names comes first. It thinks at 'minimal' by default and
    # ignores temperature (no longer sent to it: TODO Task 20). Listed by Google, not yet
    # answered by a real call.
    'Google': ['gemini-3.5-flash-lite', 'gemini-2.5-flash-lite', 'gemini-flash-latest'],
    # D11 (K15, 2026-10-16): the four lists below replace models their own providers
    # document as retired or going (desk check, no key - so no real call: listed by
    # the provider today, not answered). Anthropic's three 3.x pins are retired;
    # xAI lists only grok-4.x; DeepSeek's API docs name deepseek-flash and
    # deepseek-v4-pro; Together removes Llama-3.3-70B-Turbo on 2026-10-22 and names
    # Muse-Glimmer-30B its replacement (the 3.1 entry went in February). Fast,
    # non-reasoning models first: auto-pick wants a translation, and the
    # OpenAI-style path times out at 10 s.
    # OpenAI (2026-11-17, Decision 92): gpt-4o and gpt-4o-mini shut down on
    # 2027-01-14 and OpenAI names gpt-6-luna as the replacement for both
    # (developers.openai.com/api/docs/deprecations). gpt-6.1-terra is the next
    # model up that also takes reasoning_effort 'none' (_openai_generation_settings).
    # Listed by OpenAI, not yet answered: no OpenAI key is at hand (DEFERRED D11).
    'OpenAI': ['gpt-6-luna', 'gpt-6.1-terra'],
    'Anthropic': ['claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-haiku-4-5'],
    'DeepSeek': ['deepseek-flash', 'deepseek-v4-pro'],
    # All three previous entries were decommissioned by Groq (checked 2026-09-07).
    # This list drives auto-pick and _try_model_rotation(), so it holds only models
    # measured to answer a translation directly. Re-measured 2026-09-08 with a real
    # call: gpt-oss-120b/20b answer in 0.6-0.7 s and put their reasoning in a
    # SEPARATE 'reasoning' field, leaving content clean; qwen3.6-27b takes 1.3 s and
    # 509 completion tokens against gpt-oss's 134, with <think> inline in content, so
    # it depends on strip_thinking_tags() and costs 4x the tokens. qwen3.8-27b also
    # answered 429 'Request too large ... on output' on one of two attempts.
    # Groq GPT-OSS now requests its documented output maximum explicitly: an
    # omitted budget returned length at 3072 tokens on the user's long input,
    # with 3070 spent on reasoning (2026-10-20). See _openai_generation_settings.
    # Other existing budgets: Google REST 4096, Anthropic text 1024 (multimodal
    # 4096), trial 4096. A normal stop still cannot prove semantic completeness.
    'Groq': ['openai/gpt-oss-120b', 'openai/gpt-oss-20b'],
    'xAI': ['grok-4.20-0309-non-reasoning', 'grok-4.3'],
    'Mistral': ['mistral-large-latest', 'mistral-small-latest'],
    'Perplexity': ['sonar', 'sonar-pro'],
    # Both previous entries answered 404 "Model does not exist" (2026-09-08, real
    # call). Cerebras only serves three models now. gpt-oss-120b leads because it is
    # the one model here measured directly - on Groq's build it answers in 0.7 s with
    # its reasoning in a separate field, leaving content clean. Cerebras's own build
    # could not be called (402, the account has no credit), so that is an inference
    # from the same base model on another provider, not a measurement of this one.
    'Cerebras': ['gpt-oss-120b', 'qwen-3.8-27b'],
    # Meta-Llama-3.1-70B-Instruct is gone from SambaNova's catalogue (2026-09-08);
    # Meta-Llama-3.3-70B-Instruct survives and stays first. gemma-4-31B-it replaces
    # the dead entry - a plain instruct model, deliberately not one of the MiniMax or
    # DeepSeek reasoning models, since auto-pick wants boring and fast. Unverified by
    # a real call: SambaNova returns 402 before it validates the model.
    'SambaNova': ['Meta-Llama-3.3-70B-Instruct', 'gemma-4-31B-it'],
    'Together': ['meta-models/Muse-Glimmer-30B', 'zai-org/GLM-5.3-Flash'],
    'SiliconFlow': ['deepseek-ai/DeepSeek-V3', 'Qwen/Qwen2.5-72B-Instruct'],
    # Both previous entries are gone: google/gemini-2.0-flash-exp:free and
    # meta-llama/llama-3.3-70b-instruct:free are absent from OpenRouter's live
    # catalogue AND have zero routable endpoints (checked 2026-09-08; the two
    # public signals agreed on all 20 IDs, so either is a sound death test).
    # Free-tier by deliberate choice - an OpenRouter key with no credit still
    # translates. The cost of that choice, accepted knowingly: every free model
    # OpenRouter carries has exactly ONE endpoint, which is how both entries
    # above died. These two at least fail independently, being served by
    # different upstreams (Google AI Studio and Nvidia).
    # UNVERIFIED BY A REAL CALL - no OpenRouter key was available. Both were
    # confirmed routable, which proves they are not dead but not that they
    # translate well. The risk to test is latency, not truncation: this path sends
    # no max_tokens but does use timeout=10 (_call_generic_openai_style), and both
    # of these lean reasoning/agentic. A model that thinks for eleven seconds fails
    # even though its answer was fine.
    'OpenRouter': ['google/gemma-4-31b-it:free', 'nvidia/nemotron-3.5-lightning:free'],
}

if TYPE_CHECKING:
    from src.core.provider_health import ProviderHealthManager


# OpenAI's GPT-6 models that accept reasoning_effort 'none'; GPT-6 Astra and GPT-6.1 Sol do not
# (developers.openai.com/api/docs/guides/latest-model, read 2026-11-17).
OPENAI_UNREASONED_MODELS = ('gpt-6-luna', 'gpt-6.1-terra', 'gpt-6-sol')


def _openai_generation_settings(base_url: str, model_name: str, default_timeout: int):
    """Use the documented Groq GPT-OSS ceiling rather than a small server default, and keep
    OpenAI's GPT-6 defaults from reasoning."""
    if urlsplit(base_url).hostname == 'api.openai.com' and model_name in OPENAI_UNREASONED_MODELS:
        # They reason at 'medium' unless told otherwise, and OpenAI's guide says to remove
        # temperature whenever the effort is not 'none'. 'none' keeps the request this path
        # sends (temperature 0.3) and the time a translation takes. Not yet answered by a real
        # call: no OpenAI key is at hand (Decision 92, DEFERRED D11).
        return {'reasoning_effort': 'none'}, default_timeout
    if (urlsplit(base_url).hostname == 'api.groq.com'
            and model_name in ('openai/gpt-oss-120b', 'openai/gpt-oss-20b')):
        # https://console.groq.com/docs/models (checked 2026-10-20): both models
        # allow 65536 completion tokens, shared by reasoning and final content.
        # This is a ceiling, not a target length or a source-character estimate.
        # The provider still enforces its combined context and account quotas.
        # The user requires the whole operation to finish within 20 seconds.
        return {'max_completion_tokens': 65536, 'reasoning_effort': 'low'}, int(TRANSLATION_TIMEOUT_SECONDS)
    if urlsplit(base_url).hostname == 'api.groq.com' and model_name == 'qwen/qwen3.8-27b':
        return {'max_completion_tokens': 16384, 'reasoning_effort': 'none',
                'reasoning_format': 'hidden'}, int(TRANSLATION_TIMEOUT_SECONDS)
    return {}, default_timeout


# Gemini models that answered 400 to the thinkingConfig `thinking_config()` names, and then answered without it:
# sent none for the rest of the session (Decision 105).
_THINKING_REFUSED: set = set()

AMBIGUOUS_PROVIDER_ERROR = (
    "Cannot auto-detect provider from this API key. "
    "Select the Provider or click Test on the key card in Settings > API keys."
)


def _is_key_auth_failure(error: Exception) -> bool:
    """True when the failure means the API key itself was rejected for this provider."""
    code = getattr(error, 'code', None)
    if code == 401:
        return True
    text = str(error or '').lower()
    if re.search(r'api_key_invalid|api key not valid|invalid api key|incorrect api key|invalid.*key|key.*not valid',
                 text):
        return code in (None, 400, 401, 403)
    return False


def _pick_best_error(errors: List[Exception]) -> Exception:
    """Choose the most informative failure collected across auto-detected model attempts."""
    def rank(err: Exception) -> int:
        if _is_key_auth_failure(err):
            return 0
        code = getattr(err, 'code', None)
        text = str(err or '').lower()
        if code == 402 or 'insufficient_quota' in text or 'billing' in text or 'credit' in text:
            return 1
        if code == 403:
            return 2
        if code == 429 or 'rate limit' in text or 'too many requests' in text:
            return 3
        if code is not None and code >= 500:
            return 4
        if any(tok in text for tok in ('timed out', 'timeout', 'network', 'connection', 'ssl', 'getaddrinfo')):
            return 5
        if code == 400 or code == 422:
            return 6
        if code == 404:
            return 7
        return 8

    return min(errors, key=rank)


class ProviderRequestError(RuntimeError):
    """Keep the HTTP status and a bounded, sanitized provider explanation."""

    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(f'HTTP {code}: {detail}')


def _provider_error(error, headers, data):
    detail = str(getattr(error, 'reason', '') or 'Request rejected')
    try:
        raw = error.read(65536).decode('utf-8', errors='replace')
        try:
            body = json.loads(raw)
        except ValueError:
            body = {'message': re.sub(r'<[^>]+>', '', raw).strip()}
        payload = body.get('error', body)
        if isinstance(payload, dict):
            detail = str(payload.get('message') or payload.get('code') or detail)
        elif isinstance(payload, str):
            detail = payload
    except (ValueError, OSError, AttributeError):
        pass
    for name, value in headers.items():
        if name.lower() in ('authorization', 'x-api-key', 'x-goog-api-key'):
            detail = detail.replace(value, '[credential]')
            detail = detail.replace(value.removeprefix('Bearer '), '[credential]')
    for message in data.get('messages', []):
        content = message.get('content', '')
        parts = [content] if isinstance(content, str) else [part.get('text', '') for part in content]
        for part in parts:
            if part:
                detail = detail.replace(part, '[input]')
    detail = re.sub(r'data:image/[^\s"<>]+', '[image]', detail)
    detail = re.sub(r'\b(?:sk-|gsk_|AIza)[A-Za-z0-9_-]+|\bAQ\.[A-Za-z0-9._-]+', '[credential]', detail)
    return ProviderRequestError(error.code, detail[:1600])


class AIAPIManager:
    """
    Manages AI API with primary/backup key fallback and smart provider selection.
    Uses AI model for fast translations.
    Supports: Google Gemini, OpenAI, Anthropic, Groq, xAI, DeepSeek, Mistral, Perplexity,
              Cerebras, SambaNova, Together, SiliconFlow, OpenRouter.

    Features:
    - Multi-provider support with auto-detection
    - Smart fallback with health tracking
    - Adaptive timeouts based on provider performance
    - Circuit breaker for failing providers
    - Auto-model detection when model is not specified
    """

    MODEL_NAME: str = ''

    # Class-level cache for working models (key: api_key_prefix -> model_name)
    _working_models_cache: Dict[str, str] = {}

    def __init__(self) -> None:
        self.api_configs: List[Dict[str, Any]] = []
        self.notification_callback: Optional[Callable[[str], None]] = None
        self.health_manager: Optional['ProviderHealthManager'] = None
        self.model_rotation_enabled: bool = False
        self._failed_models: Dict[tuple, set] = {}        # (key_prefix, provider) -> {failed_model_names}
        self._rotation_working_cache: Dict[tuple, str] = {} # (key_prefix, provider) -> working_model
        self._rotation_lock = threading.Lock()
        # Which model actually served the last successful call, and on which
        # provider. Not necessarily the configured one: auto-detection and model
        # rotation both substitute a different model silently, which is exactly
        # what the popup's "Translated with" note exists to make visible.
        self.usage_callback = None
        self.last_model: Optional[str] = None
        self.last_provider: Optional[str] = None

    def configure(self, api_configs: List[Dict[str, Any]],
                  notification_callback: Optional[Callable[[str], None]] = None,
                  health_manager: Optional['ProviderHealthManager'] = None,
                  model_rotation_enabled: bool = False) -> None:
        """Configure the API with list of {model_name, api_key, provider}."""
        self.api_configs = api_configs
        self.notification_callback = notification_callback
        self.health_manager = health_manager
        self.model_rotation_enabled = model_rotation_enabled

    @staticmethod
    def _claimant_for_key(claimants: List[str], key: str) -> str:
        """Which of the providers that list the same model a key belongs to (D12).

        The provider whose key prefix the key carries; else the first that has no
        known prefix - one whose prefix the key lacks is not its provider (a key
        that is not csk-... is not a Cerebras key, so gpt-oss-120b goes to
        SambaNova, whose keys are bare UUIDs); else the first, as before.
        """
        prefixes: Dict[str, List[str]] = {}
        for prefix, provider in get_config().api_key_patterns.items():
            prefixes.setdefault(provider, []).append(prefix)
        for provider in claimants:
            if any(key.startswith(prefix) for prefix in prefixes.get(provider, ())):
                return provider
        unexcluded = [provider for provider in claimants if provider not in prefixes]
        return unexcluded[0] if unexcluded else claimants[0]

    def _identify_provider(self, model_name: str, api_key: str = "") -> str:  # noqa: C901
        """
        Identify API provider based on model name and API key format.

        Priority:
        1. Exact match in MODEL_PROVIDER_MAP (case-insensitive); between providers
           that list the same model, the one the key belongs to (D12)
        2. Explicit provider prefix (openrouter/, together/, etc.)
        3. API key pattern (gsk_, sk-ant-, etc.)
        4. Model name pattern matching
        5. Default fallback
        """
        model = model_name.strip()
        model_lower = model.lower()
        key = api_key.strip()

        # 1. HIGHEST PRIORITY: Exact match in model_provider_map. The same open
        #    model is served by several providers (D12): then the key decides.
        claimants = [provider for provider, models in get_config().model_provider_map.items()
                     if any(m.lower() == model_lower for m in models)]
        if claimants:
            if len(claimants) > 1 and key:
                return self._claimant_for_key(claimants, key)
            return claimants[0]

        # 2. Check for explicit provider prefixes
        if model_lower.startswith('openrouter/'): return 'OpenRouter'
        if model_lower.startswith('together/') or model_lower.startswith('meta-llama/'): return 'Together'
        if model_lower.startswith('silicon/') or model_lower.startswith('sf/'): return 'SiliconFlow'

        # 3. Model name contains "/" - likely Together or SiliconFlow format
        if '/' in model:
            # SiliconFlow patterns
            if any(model.startswith(p) for p in ['Qwen/', 'deepseek-ai/', 'THUDM/', '01-ai/', 'internlm/', 'Pro/']):
                return 'SiliconFlow'
            # Together patterns
            if any(model.startswith(p) for p in ['meta-llama/', 'mistralai/', 'google/']):
                return 'Together'

        # 4. API Key Patterns (already returns Title Case)
        for pattern, provider in get_config().api_key_patterns.items():
            if key.startswith(pattern):
                return provider

        # 5. Proprietary Models
        if 'gemini' in model_lower:
            return 'Google'
        if 'claude' in model_lower:
            return 'Anthropic'
        if 'gpt' in model_lower or model_lower.startswith('o1') or model_lower.startswith('o3') or 'dall-e' in model_lower:
            return 'OpenAI'
        if 'grok' in model_lower:
            return 'xAI'

        # 6. Official Provider Models
        if model_lower.startswith('deepseek-'):
            return 'DeepSeek'

        if any(model_lower.startswith(p) for p in ['mistral-', 'codestral-', 'pixtral-', 'ministral-', 'open-mistral', 'open-mixtral']):
            return 'Mistral'

        if 'sonar' in model_lower:
            return 'Perplexity'

        # 7. Groq-specific model signatures
        groq_suffixes = ['-32768', '-8192', '-versatile', '-instant', '-preview', '-specdec']
        if any(model_lower.endswith(s) for s in groq_suffixes):
            return 'Groq'

        if '/' not in model and any(model_lower.startswith(p) for p in ['llama3-', 'llama-3', 'gemma-', 'gemma2-', 'mixtral-', 'whisper-', 'distil-']):
            return 'Groq'

        # 8. SambaNova: Meta-Llama-xxx format
        if model.startswith('Meta-Llama-'):
            return 'SambaNova'

        # 9. Cerebras: llama3.1-xxx format (with dot)
        if model_lower.startswith('llama3.'):
            return 'Cerebras'

        # 10. Generic fallback
        if 'qwen' in model_lower or model_lower.startswith('yi-'):
            return 'SiliconFlow'

        if any(x in model_lower for x in ['llama', 'mixtral', 'gemma', 'whisper']):
            return 'Groq'

        return 'Google'  # Ultimate fallback

    @request_budget
    def _make_request_with_retry(self, url: str, data: dict, headers: dict,
                                  response_parser: Callable[[dict], str],
                                  timeout: int = 10, max_retries: int = 3) -> str:
        """Make HTTP request with retry logic for rate limits and transient errors.

        Args:
            url: API endpoint URL
            data: Request body as dict (will be JSON encoded)
            headers: HTTP headers
            response_parser: Function to extract response text from JSON result
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts

        Returns:
            Extracted response text

        Raises:
            Exception: On authentication failure, rate limit exceeded, or network errors
        """
        ssl_context = get_ssl_context_for_url(url)

        for attempt in range(max_retries):
            check_current()
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers=headers,
                method="POST"
            )

            try:
                socket_timeout = min(timeout, current_task().remaining())

                def read_response():
                    with urllib.request.urlopen(req, timeout=socket_timeout, context=ssl_context) as response:
                        return json.loads(response.read().decode('utf-8'))

                result = bounded_io(read_response)
                check_generation_result(result)
                return response_parser(result)

            except urllib.error.HTTPError as e:
                failure = _provider_error(e, headers, data)
                if e.code == 429:  # Rate limit
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt  # 1s, 2s, 4s
                        logging.warning(f"Rate limit hit, waiting {wait_time}s before retry...")
                        pause(wait_time)
                        continue
                    else:
                        raise failure

                elif e.code in (401, 403):
                    raise failure

                elif e.code >= 500:
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        logging.warning(f"Server error {e.code}, waiting {wait_time}s before retry...")
                        pause(wait_time)
                        continue
                    else:
                        raise failure
                else:
                    raise failure

            except urllib.error.URLError as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logging.warning(f"Network error, waiting {wait_time}s before retry: {e}")
                    pause(wait_time)
                    continue
                else:
                    raise Exception(f"Network error after {max_retries} retries: {e}")

        # Should not reach here, but just in case
        raise Exception("Request failed after all retries")

    def _call_generic_openai_style(self, api_key: str, model_name: str, prompt: str,
                                     base_url: str, image_path: Optional[str] = None) -> str:
        """Helper for OpenAI-compatible APIs with rate limit handling."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        messages = []
        if image_path:
            # Vision request
            b64_data, mime_type = MultimodalProcessor.encode_image_base64(image_path)
            if b64_data:
                content = [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_data}"}}
                ]
                messages.append({"role": "user", "content": content})
            else:
                raise ValueError('The image could not be encoded.')
        else:
            messages.append({"role": "user", "content": prompt})

        data = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.3
        }
        if not takes_temperature(model_name):        # google/gemini-... through OpenRouter
            del data["temperature"]
        generation_options, timeout = _openai_generation_settings(base_url, model_name, 10)
        data.update(generation_options)

        # OpenAI response parser
        def parse_openai_response(result: dict) -> str:
            return result['choices'][0]['message']['content'].strip()

        return self._make_request_with_retry(
            url=base_url,
            data=data,
            headers=headers,
            response_parser=parse_openai_response,
            timeout=timeout
        )

    def _call_anthropic(self, api_key: str, model_name: str, prompt: str,
                         image_path: Optional[str] = None) -> str:
        """Helper for Anthropic Claude API with rate limit handling."""
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        content = []
        if image_path:
            b64_data, mime_type = MultimodalProcessor.encode_image_base64(image_path)
            if b64_data:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": b64_data
                    }
                })

        content.append({"type": "text", "text": prompt})

        data = {
            "model": model_name,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": content}]
        }

        # Anthropic response parser
        def parse_anthropic_response(result: dict) -> str:
            return anthropic_text(result)

        return self._make_request_with_retry(
            url=url,
            data=data,
            headers=headers,
            response_parser=parse_anthropic_response,
            timeout=10
        )

    def _call_google_rest(self, api_key: str, model_name: str, prompt: str,
                          image_path: Optional[str] = None) -> str:
        """Helper for Google Gemini REST API."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"

        # The key in a header, not ?key=: the form Google's key page shows, and the URL stays free of it.
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }

        # Build parts array
        parts = []

        # Add image if provided
        if image_path:
            b64_data, mime_type = MultimodalProcessor.encode_image_base64(image_path)
            if b64_data:
                parts.append({
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": b64_data
                    }
                })

        # Add text prompt
        parts.append({"text": prompt})

        data = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 4096
            }
        }
        if not takes_temperature(model_name):
            del data["generationConfig"]["temperature"]

        # Google response parser
        def parse_google_response(result: dict) -> str:
            if 'candidates' in result and result['candidates']:
                candidate = result['candidates'][0]
                if 'content' in candidate and 'parts' in candidate['content']:
                    return google_text(result)
            raise Exception("Invalid response format from Google API")

        return self._post_gemini(model_name, url, data, headers, parse_google_response, timeout=30)

    def _call_google_rest_multimodal(self, api_key: str, model_name: str, prompt: str,
                                      image_paths: List[str]) -> str:
        """Helper for Google Gemini REST API with multiple images."""
        import os

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"

        # The key in a header, not ?key=: the form Google's key page shows, and the URL stays free of it.
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }

        # Build parts array with all images first, then text
        parts = []

        for img_path in image_paths:
            if os.path.exists(img_path):
                b64_data, mime_type = MultimodalProcessor.encode_image_base64(img_path)
                if b64_data:
                    parts.append({
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_data
                        }
                    })

        # Add text prompt at the end
        parts.append({"text": prompt})

        data = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 4096
            }
        }
        if not takes_temperature(model_name):
            del data["generationConfig"]["temperature"]

        # Google response parser
        def parse_google_response(result: dict) -> str:
            if 'candidates' in result and result['candidates']:
                candidate = result['candidates'][0]
                if 'content' in candidate and 'parts' in candidate['content']:
                    return google_text(result)
            raise Exception("Invalid response format from Google API")

        return self._post_gemini(model_name, url, data, headers, parse_google_response,
                                 timeout=60)  # Longer timeout for multi-image

    @request_budget
    def _post_gemini(self, model_name: str, url: str, data: dict, headers: dict,
                     parser: Callable[[dict], str], timeout: int) -> str:
        """Send a Gemini request with the thinkingConfig `thinking_config()` names (Decision 105).

        If Google answers 400 to it, the request goes once more without it, within the same deadline: from
        2026-12-01 Google routes `gemini-2.5-flash` to `gemini-3.5-flash-lite`, which refuses the budget 2.5 Flash
        is sent, and an alias may come to name a model that refuses `low`. Only a retry that answers marks the
        model, so a 400 the retry gets too (a bad image, a prompt too long) marks nothing."""
        config = thinking_config(model_name)
        if config is None or model_name in _THINKING_REFUSED:
            return self._make_request_with_retry(url=url, data=data, headers=headers, response_parser=parser,
                                                 timeout=timeout)
        data['generationConfig']['thinkingConfig'] = config
        try:
            return self._make_request_with_retry(url=url, data=data, headers=headers, response_parser=parser,
                                                 timeout=timeout)
        except ProviderRequestError as error:
            if error.code != 400:
                raise
        del data['generationConfig']['thinkingConfig']
        result = self._make_request_with_retry(url=url, data=data, headers=headers, response_parser=parser,
                                               timeout=timeout)
        _THINKING_REFUSED.add(model_name)
        log_event('API', 'thinking.refused', level=logging.WARNING, model=model_name, status=400,
                  sent=sorted(config))
        return result

    @property
    def last_attribution(self):
        """"model (Provider)" for the last successful call, or None if there was none.

        `test_connection` and `test_vision_connection` go through the same
        dispatch and therefore also update it - a Settings test overwrites the
        value. Callers that care snapshot it right after their own call rather
        than reading it later (see TranslationService.last_attribution).
        """
        task = current_task()
        if task and task.attribution:
            return task.attribution
        if not self.last_model:
            return None
        if not self.last_provider:
            return self.last_model
        return f"{self.last_model} ({self.last_provider})"

    @active_write
    def _record_attribution(self, model_name: str, provider: str) -> None:
        """Remember the model+provider that just answered successfully."""
        self.last_model = model_name
        self.last_provider = provider
        task = current_task()
        if task:
            task.attribution = f'{model_name} ({provider})'

    def _generate_content(self, provider: str, api_key: str, model_name: str,
                           prompt: str, image_path: Optional[str] = None) -> str:
        """Dispatch one request, clean the answer, record which model served it.

        Every successful text/vision call in this class funnels through here, so
        recording the attribution once covers the configured model, an
        auto-detected one and any rotation substitute without eight separate
        bookkeeping sites. Reasoning tags are stripped for the same reason: the
        image, screenshot and PDF-OCR callers read this result directly, without
        passing through TranslationService.
        """
        result = strip_thinking_tags(
            self._route_generate_content(provider, api_key, model_name,
                                         prompt, image_path))
        self._record_attribution(model_name, provider)
        return result

    def _route_generate_content(self, provider: str, api_key: str, model_name: str,
                                prompt: str, image_path: Optional[str] = None) -> str:
        """Route the request to the correct provider.

        Provider names use Title Case (e.g., 'Google', 'OpenAI', 'Groq').
        """

        # --- Google (REST API) ---
        if provider == 'Google':
            return self._call_google_rest(api_key, model_name, prompt, image_path)

        # --- Anthropic ---
        elif provider == 'Anthropic':
            return self._call_anthropic(api_key, model_name, prompt, image_path)

        # --- OpenAI Compatible APIs ---
        base_urls = get_config().provider_api_urls

        if provider in base_urls:
            return self._call_generic_openai_style(api_key, model_name, prompt, base_urls[provider], image_path)

        raise Exception(f"Unknown provider: {provider}")

    def get_display_name(self, provider_code: str) -> str:
        """Get nice display name for provider.

        Accepts both Title Case (Google) and lowercase (google) provider codes.
        """
        display_map = {
            'Google': 'Google (Gemini)',
            'OpenAI': 'OpenAI',
            'Anthropic': 'Anthropic (Claude)',
            'Groq': 'Groq',
            'xAI': 'xAI (Grok)',
            'DeepSeek': 'DeepSeek',
            'Mistral': 'Mistral AI',
            'Perplexity': 'Perplexity',
            'Cerebras': 'Cerebras',
            'SambaNova': 'SambaNova',
            'Together': 'Together AI',
            'SiliconFlow': 'SiliconFlow',
            'OpenRouter': 'OpenRouter',
            'HuggingFace': 'HuggingFace'
        }
        # Try exact match first, then case-insensitive lookup
        if provider_code in display_map:
            return display_map[provider_code]
        # Fallback: try to find case-insensitive match
        provider_lower = provider_code.lower()
        for key, value in display_map.items():
            if key.lower() == provider_lower:
                return value
        return provider_code.title()

    def test_connection(self, model_name: str, api_key: str, provider: str = 'Auto') -> bool:
        """Test connection with a specific model and key.

        Provider should be Title Case (e.g., 'Google', 'OpenAI', 'Groq') or 'Auto'.
        """
        try:
            target_provider = self._identify_provider(model_name, api_key) if provider == 'Auto' else provider
            self._generate_content(target_provider, api_key, model_name, "Say OK")
            return True
        except Exception as e:
            raise e

    def test_vision_connection(self, model_name: str, api_key: str, provider: str = 'Auto') -> Optional[bool]:
        """Probe actual image transport. None means inconclusive, not a text-only model."""
        import os
        import tempfile

        target_provider = self._identify_provider(model_name, api_key) if provider == 'Auto' else provider

        tmp_path = None
        try:
            from PIL import Image, ImageDraw
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                tmp_path = f.name
            # Patch-based vision encoders can reject a 2x2 image despite supporting vision.
            image = Image.new('RGB', (128, 128), 'white')
            ImageDraw.Draw(image).rectangle((24, 24, 104, 104), fill='red')
            image.save(tmp_path, format='PNG')

            answer = self._generate_content(target_provider, api_key, model_name,
                                            "Describe this image in one word.", tmp_path)
            if not isinstance(answer, str) or not answer.strip():
                return None
            return True
        except Exception as e:
            # Read error response body for debugging
            error_body = ""
            if hasattr(e, 'read'):
                try:
                    error_body = e.read().decode('utf-8', errors='replace')
                except Exception:
                    pass
            code = getattr(e, 'code', None)
            detail = (str(e) + ' ' + error_body).casefold()
            unsupported = any(message in detail for message in (
                'does not support image', 'does not support vision', 'images are not supported',
                'image input is not supported', 'no endpoints found that support image',
                'image_url is not supported', 'image input not supported',
                'content must be a string', 'only supports text input', 'does not support multimodal'))
            logging.debug('[VisionTest] image probe outcome for %s (%s): HTTP %s; unsupported=%s',
                          model_name, target_provider, code, unsupported)
            return False if unsupported and code not in (401, 403, 429) else None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _get_api_key_prefix(self, api_key: str) -> str:
        """Get a prefix of API key for caching (to avoid storing full key)."""
        return api_key[:12] if len(api_key) > 12 else api_key

    def test_audio_connection(self, api_key, provider, model):
        from src.core.audio_service import probe
        return probe(api_key, provider, model)

    def _detect_provider_from_key(self, api_key: str) -> str:
        """Detect provider from API key pattern.

        Returns Title Case provider name (e.g., 'Google', 'Groq'), or '' when the
        key has no distinctive prefix so Auto+Auto never guesses Google silently.
        """
        for pattern, provider in get_config().api_key_patterns.items():
            if api_key.startswith(pattern):
                return provider
        return ''

    # --- Model Rotation helpers ---

    def _record_failed_model(self, api_key: str, provider: str, model_name: str) -> None:
        """Record a model that failed for a given key+provider."""
        key_prefix = self._get_api_key_prefix(api_key)
        cache_key = (key_prefix, provider)
        with self._rotation_lock:
            if cache_key not in self._failed_models:
                self._failed_models[cache_key] = set()
            self._failed_models[cache_key].add(model_name)

    def _get_failed_models(self, api_key: str, provider: str) -> set:
        """Get copy of failed models set for a key+provider."""
        key_prefix = self._get_api_key_prefix(api_key)
        cache_key = (key_prefix, provider)
        with self._rotation_lock:
            return self._failed_models.get(cache_key, set()).copy()

    def _cache_rotation_working_model(self, api_key: str, provider: str, model_name: str) -> None:
        """Cache a working model found via rotation."""
        key_prefix = self._get_api_key_prefix(api_key)
        cache_key = (key_prefix, provider)
        with self._rotation_lock:
            self._rotation_working_cache[cache_key] = model_name

    def _get_rotation_cached_model(self, api_key: str, provider: str) -> Optional[str]:
        """Get cached working model from rotation, if any."""
        key_prefix = self._get_api_key_prefix(api_key)
        cache_key = (key_prefix, provider)
        with self._rotation_lock:
            return self._rotation_working_cache.get(cache_key)

    def _dispatch_call(self, provider: str, api_key: str, model_name: str, prompt: str,
                       image_path: Optional[str] = None,
                       image_paths: Optional[List[str]] = None,
                       file_contents: Optional[Dict[str, str]] = None) -> str:
        """Route to the correct generate method based on arguments."""
        if image_paths is not None or file_contents is not None:
            return self._generate_content_multimodal(
                provider, api_key, model_name, prompt,
                image_paths or [], file_contents or {}
            )
        return self._generate_content(provider, api_key, model_name, prompt, image_path)

    def _try_model_rotation(self, api_key: str, provider: str, failed_model: str,
                            prompt: str, *,
                            image_path: Optional[str] = None,
                            image_paths: Optional[List[str]] = None,
                            file_contents: Optional[Dict[str, str]] = None,
                            require_vision: bool = False) -> Optional[str]:
        """Try other models for the same provider+key before falling back to next key.

        Returns translation result if a working model is found, None otherwise.
        """
        if not self.model_rotation_enabled:
            return None

        self._record_failed_model(api_key, provider, failed_model)
        failed_set = self._get_failed_models(api_key, provider)

        # Try cached working model first (if different from failed)
        cached = self._get_rotation_cached_model(api_key, provider)
        if cached and cached != failed_model and cached not in failed_set:
            logging.info(f"[ModelRotation] Trying cached model: {cached}")
            try:
                result = self._dispatch_call(provider, api_key, cached, prompt,
                                             image_path=image_path,
                                             image_paths=image_paths,
                                             file_contents=file_contents)
                logging.info(f"[ModelRotation] Cached model {cached} succeeded!")
                return result
            except (IncompleteGenerationError, TranslationStopped):
                raise
            except Exception as e:
                logging.warning(f"[ModelRotation] Cached model {cached} failed: {e}")
                self._record_failed_model(api_key, provider, cached)
                failed_set.add(cached)

        # Get candidate models from remote config
        all_models = get_config().default_models_by_provider.get(provider, [])
        if not all_models:
            logging.debug(f"[ModelRotation] No models list for provider {provider}")
            return None

        # Build rotation list: non-failed first, then previously-failed (deprioritized)
        non_failed = [m for m in all_models if m not in failed_set and m != failed_model]
        previously_failed = [m for m in all_models if m in failed_set and m != failed_model]
        rotation_list = non_failed + previously_failed

        # Filter for vision capability if required
        if require_vision:
            rotation_list = [m for m in rotation_list
                             if MultimodalProcessor.is_vision_capable(m, provider)]

        if not rotation_list:
            logging.debug(f"[ModelRotation] No candidate models left for {provider}")
            return None

        logging.info(f"[ModelRotation] Trying {len(rotation_list)} alternative models for {provider}: {rotation_list}")

        for candidate in rotation_list:
            try:
                result = self._dispatch_call(provider, api_key, candidate, prompt,
                                             image_path=image_path,
                                             image_paths=image_paths,
                                             file_contents=file_contents)
                logging.info(f"[ModelRotation] Model {candidate} succeeded!")
                self._cache_rotation_working_model(api_key, provider, candidate)
                return result
            except (IncompleteGenerationError, TranslationStopped):
                raise
            except Exception as e:
                logging.warning(f"[ModelRotation] Model {candidate} failed: {e}")
                self._record_failed_model(api_key, provider, candidate)
                continue

        logging.info(f"[ModelRotation] All models exhausted for {provider}, falling through to next key")
        return None

    def _try_auto_detect_model(self, api_key: str, provider: str, prompt: str) -> Optional[str]:
        """Try to auto-detect a working model for the given provider.

        Tries models from DEFAULT_MODELS_BY_PROVIDER until one works.
        Caches the working model for future use.

        Raises:
            The most informative exception from the tried models when none work
            (stopping immediately on key-level authentication failures).
        """
        key_prefix = self._get_api_key_prefix(api_key)
        attempt_errors: List[Exception] = []
        tried_cached = None

        # Check if we have a cached working model
        cached_model = self._working_models_cache.get(key_prefix)
        if cached_model:
            logging.info(f"[Auto-Model] Trying cached model: {cached_model}")
            tried_cached = cached_model
            try:
                result = self._generate_content(provider, api_key, cached_model, prompt)
                logging.info(f"[Auto-Model] Cached model {cached_model} worked!")
                return result
            except (IncompleteGenerationError, TranslationStopped):
                raise
            except Exception as e:
                logging.warning(f"[Auto-Model] Cached model {cached_model} failed: {e}")
                # Clear cache and try all models
                del self._working_models_cache[key_prefix]
                attempt_errors.append(e)
                if _is_key_auth_failure(e):
                    raise

        # Try models for this provider (from remote config, fallback to hardcoded)
        models_to_try = get_config().default_models_by_provider.get(provider, [])
        if not models_to_try:
            logging.warning(f"[Auto-Model] No default models for provider: {provider}")
            if attempt_errors:
                raise _pick_best_error(attempt_errors)
            return None

        for model in models_to_try:
            if model == tried_cached:
                continue
            logging.info(f"[Auto-Model] Trying model: {model}")
            try:
                result = self._generate_content(provider, api_key, model, prompt)
                # Success! Cache this model
                self._working_models_cache[key_prefix] = model
                logging.info(f"[Auto-Model] Model {model} works! Cached for future use.")
                return result
            except (IncompleteGenerationError, TranslationStopped):
                raise
            except Exception as e:
                logging.warning(f"[Auto-Model] Model {model} failed: {e}")
                attempt_errors.append(e)
                if _is_key_auth_failure(e):
                    raise
                continue

        if attempt_errors:
            raise _pick_best_error(attempt_errors)
        return None

    def _key_slot(self, config):
        """The key's 1-based place in the saved list - what a log line says instead of the key."""
        identity = config.get('id')
        for index, saved in enumerate(self.api_configs, 1):
            if saved is config or (identity and saved.get('id') == identity):
                return index
        return None

    def _log_call(self, kind, item, started, error=None, rotated=False):
        """One line per key tried: provider, model, slot, time, and the HTTP status of a failure."""
        fields = dict(kind=kind, provider=item['provider'], model=item.get('model_name') or 'auto',
                      key_slot=self._key_slot(item['config']), ms=round((time.monotonic() - started) * 1000))
        if error is None:
            log_event('API', 'call.ok', rotated=rotated, credited=self.last_attribution or '', **fields)
        else:
            log_event('API', 'call.fail', level=logging.WARNING, status=getattr(error, 'code', None),
                      error=type(error).__name__, message=str(error), **fields)

    def _emit_usage(self, config, started, error=None):
        """Queue sanitized per-key outcomes; never write configuration or touch Tk here."""
        callback = self.usage_callback
        identity = config.get('id')
        if callback is None or not identity:
            return
        check_current()
        reason = str(error).lower() if error is not None else ''
        kind = 'success' if error is None else (
            'quota' if any(word in reason for word in ('429', 'quota', 'rate limit', 'exhausted')) else 'error')
        event = dict(id=identity, at=time.time(), kind=kind,
                     seconds=max(0.0, time.monotonic() - started))
        try:
            callback(event)
        except Exception:
            logging.warning('[API] Could not queue key usage status')

    @request_budget
    def translate(self, prompt: str) -> str:
        """Translate text using configured keys with smart fallback."""
        if not self.api_configs:
            raise Exception("API not configured. Please set your API key in Settings.")

        errors = []
        has_valid_key = False

        # Resolve metadata without issuing calls, preserving the saved priority.
        configs_with_providers = []
        for i, config in enumerate(self.api_configs):
            api_key = config.get('api_key', '').strip()
            model_name = config.get('model_name', '').strip()
            provider_setting = config.get('provider', 'Auto')

            if not api_key:
                continue

            if provider_setting != 'Auto':
                target_provider = provider_setting
            elif model_name:
                target_provider = self._identify_provider(model_name, api_key)
            else:
                target_provider = self._detect_provider_from_key(api_key)
                if not target_provider:
                    logging.warning(
                        "[API] Key slot %s cannot auto-detect provider (Provider=Auto, Model=Auto)", i + 1
                    )
                    errors.append(f"Auto: {AMBIGUOUS_PROVIDER_ERROR}")
                    continue
            configs_with_providers.append({
                'config': config,
                'provider': target_provider,
                'api_key': api_key,
                'model_name': model_name,
                'index': i
            })

        if not configs_with_providers:
            if errors:
                raise Exception("No valid API configuration.\n" + "\n".join(errors))
            raise Exception("No valid API key configured. Please add an API key in Settings.")

        # Keep the saved key order; health scores must not reorder credentials.

        # Try each config in order
        for item in configs_with_providers:
            has_valid_key = True
            config = item['config']
            target_provider = item['provider']
            api_key = item['api_key']
            model_name = item['model_name']

            start_time = time.time()
            usage_started = time.monotonic()
            try:
                check_current()
                if model_name:
                    result = self._generate_content(target_provider, api_key, model_name, prompt)
                else:
                    result = self._try_auto_detect_model(api_key, target_provider, prompt)
                    if result is None:
                        raise RuntimeError('Auto-detection failed for all models')

                # Record success
                if self.health_manager:
                    response_time_ms = int((time.time() - start_time) * 1000)
                    self.health_manager.record_success(target_provider, response_time_ms)

                self._emit_usage(item['config'], usage_started)
                self._log_call('text', item, usage_started)
                return result

            except (IncompleteGenerationError, TranslationStopped):
                raise
            except Exception as e:
                self._emit_usage(item['config'], usage_started, error=e)
                # Record failure
                if self.health_manager:
                    self.health_manager.record_failure(target_provider)

                self._log_call('text', item, usage_started, error=e)
                label = model_name or f"{target_provider} (Auto)"
                errors.append(f"{label}: {str(e)}")

                # Try model rotation before moving to next key (skip when Auto already tried defaults or key auth failed)
                rotation_result = None
                if model_name and not _is_key_auth_failure(e):
                    rotation_result = self._try_model_rotation(
                        api_key, target_provider, model_name, prompt
                    )
                if rotation_result is not None:
                    if self.health_manager:
                        self.health_manager.record_success(target_provider, 0)
                    self._emit_usage(item['config'], usage_started)
                    self._log_call('text', item, usage_started, rotated=True)
                    return rotation_result

                continue

        if not has_valid_key:
            raise Exception("No valid API key configured. Please add an API key in Settings.")

        raise Exception("All API keys failed.\n" + "\n".join(errors))

    def _configs_with_models(self, config_errors: Optional[List[str]] = None):
        """Expand Auto model choices within each key's saved priority position."""
        defaults = get_config().default_models_by_provider
        for i, config in enumerate(self.api_configs):
            model = config.get('model_name', '').strip()
            if model:
                yield config
                continue
            key = config.get('api_key', '').strip()
            if not key:
                continue
            provider = config.get('provider', 'Auto')
            if provider == 'Auto':
                provider = self._detect_provider_from_key(key)
                if not provider:
                    logging.warning(
                        "[API] Key slot %s cannot auto-detect provider (Provider=Auto, Model=Auto)", i + 1
                    )
                    if config_errors is not None:
                        config_errors.append(f"Auto: {AMBIGUOUS_PROVIDER_ERROR}")
                    continue
            for candidate in defaults.get(provider, DEFAULT_MODELS_BY_PROVIDER.get(provider, [])):
                expanded = dict(config, model_name=candidate)
                yield expanded

    @request_budget
    def translate_image(self, prompt: str, image_path: str) -> str:
        """Translate/Analyze image using configured keys with smart fallback."""
        if not self.api_configs:
            raise Exception("API not configured. Please set your API key in Settings.")

        errors = []
        config_errors: List[str] = []

        # Prepare vision-capable configs
        vision_configs = []
        for i, config in enumerate(self._configs_with_models(config_errors=config_errors)):
            api_key = config.get('api_key', '').strip()
            model_name = config.get('model_name', '').strip()
            provider_setting = config.get('provider', 'Auto')

            if not api_key or not model_name:
                continue

            target_provider = self._identify_provider(model_name, api_key) if provider_setting == 'Auto' else provider_setting

            # Check vision capability: stored flag from Test, heuristic fallback for untested
            if not MultimodalProcessor.config_accepts_images(config, target_provider):
                continue

            vision_configs.append({
                'config': config,
                'provider': target_provider,
                'api_key': api_key,
                'model_name': model_name
            })

        if not vision_configs:
            if config_errors:
                raise Exception("No valid API configuration.\n" + "\n".join(config_errors))
            raise Exception("No configured API supports vision. Please use a vision-capable model (e.g., Gemini 2.0 Flash, GPT-4o).")

        # Keep the saved key order; health scores must not reorder credentials.
        auth_failed_keys = set()

        # Try each config
        for item in vision_configs:
            target_provider = item['provider']
            api_key = item['api_key']
            model_name = item['model_name']
            if (api_key, target_provider) in auth_failed_keys:
                continue

            start_time = time.time()
            usage_started = time.monotonic()
            try:
                result = self._generate_content(target_provider, api_key, model_name, prompt, image_path)

                # Record success
                if self.health_manager:
                    response_time_ms = int((time.time() - start_time) * 1000)
                    self.health_manager.record_success(target_provider, response_time_ms)

                self._emit_usage(item['config'], usage_started)
                self._log_call('image', item, usage_started)
                return result

            except (IncompleteGenerationError, TranslationStopped):
                raise
            except Exception as e:
                self._emit_usage(item['config'], usage_started, error=e)
                # Record failure
                if self.health_manager:
                    self.health_manager.record_failure(target_provider)

                self._log_call('image', item, usage_started, error=e)
                errors.append(f"{model_name}: {str(e)}")

                if _is_key_auth_failure(e):
                    auth_failed_keys.add((api_key, target_provider))
                    continue

                # Try model rotation before moving to next key
                rotation_result = self._try_model_rotation(
                    api_key, target_provider, model_name, prompt,
                    image_path=image_path, require_vision=True
                )
                if rotation_result is not None:
                    if self.health_manager:
                        self.health_manager.record_success(target_provider, 0)
                    self._emit_usage(item['config'], usage_started)
                    self._log_call('image', item, usage_started, rotated=True)
                    return rotation_result

                continue

        raise Exception("All vision APIs failed.\n" + "\n".join(config_errors + errors))

    @request_budget
    def translate_multimodal(self, prompt: str,
                              image_paths: Optional[List[str]] = None,
                              file_contents: Optional[Dict[str, str]] = None) -> str:
        """Translate with multiple images and file contents in ONE request.

        Args:
            prompt: Translation instructions
            image_paths: List of image file paths
            file_contents: Dict of {filename: content} for text files

        Returns:
            Combined translation result
        """
        if not self.api_configs:
            raise Exception("API not configured. Please set your API key in Settings.")

        image_paths = image_paths or []
        file_contents = file_contents or {}
        needs_vision = len(image_paths) > 0

        errors = []
        config_errors: List[str] = []

        # Prepare configs
        multimodal_configs = []
        for i, config in enumerate(self._configs_with_models(config_errors=config_errors)):
            api_key = config.get('api_key', '').strip()
            model_name = config.get('model_name', '').strip()
            provider_setting = config.get('provider', 'Auto')

            if not api_key or not model_name:
                continue

            target_provider = self._identify_provider(model_name, api_key) if provider_setting == 'Auto' else provider_setting

            # If we have images, check vision capability: stored flag from Test, heuristic fallback
            if needs_vision:
                if not MultimodalProcessor.config_accepts_images(config, target_provider):
                    continue

            multimodal_configs.append({
                'config': config,
                'provider': target_provider,
                'api_key': api_key,
                'model_name': model_name
            })

        if not multimodal_configs:
            if config_errors:
                raise Exception("No valid API configuration.\n" + "\n".join(config_errors))
            if needs_vision:
                raise Exception("No configured API supports vision. Please use a vision-capable model (e.g., Gemini 2.0 Flash, GPT-4o).")
            else:
                raise Exception("No valid API key configured. Please add an API key in Settings.")

        # Keep the saved key order; health scores must not reorder credentials.
        auth_failed_keys = set()

        # Try each config
        for item in multimodal_configs:
            target_provider = item['provider']
            api_key = item['api_key']
            model_name = item['model_name']
            if (api_key, target_provider) in auth_failed_keys:
                continue

            start_time = time.time()
            usage_started = time.monotonic()
            try:
                result = self._generate_content_multimodal(
                    target_provider, api_key, model_name, prompt, image_paths, file_contents
                )

                # Record success
                if self.health_manager:
                    response_time_ms = int((time.time() - start_time) * 1000)
                    self.health_manager.record_success(target_provider, response_time_ms)

                self._emit_usage(item['config'], usage_started)
                self._log_call('multimodal', item, usage_started)
                return result

            except (IncompleteGenerationError, TranslationStopped):
                raise
            except Exception as e:
                self._emit_usage(item['config'], usage_started, error=e)
                # Record failure
                if self.health_manager:
                    self.health_manager.record_failure(target_provider)

                self._log_call('multimodal', item, usage_started, error=e)
                errors.append(f"{model_name}: {str(e)}")

                if _is_key_auth_failure(e):
                    auth_failed_keys.add((api_key, target_provider))
                    continue

                # Try model rotation before moving to next key
                rotation_result = self._try_model_rotation(
                    api_key, target_provider, model_name, prompt,
                    image_paths=image_paths, file_contents=file_contents,
                    require_vision=needs_vision
                )
                if rotation_result is not None:
                    if self.health_manager:
                        self.health_manager.record_success(target_provider, 0)
                    self._emit_usage(item['config'], usage_started)
                    self._log_call('multimodal', item, usage_started, rotated=True)
                    return rotation_result

                continue

        raise Exception("All API calls failed.\n" + "\n".join(config_errors + errors))

    def _generate_content_multimodal(self, provider: str, api_key: str, model_name: str,
                                      prompt: str, image_paths: List[str],
                                      file_contents: Dict[str, str]) -> str:
        """Multimodal dispatch; cleans and attributes like _generate_content."""
        result = strip_thinking_tags(
            self._route_generate_multimodal(provider, api_key, model_name,
                                            prompt, image_paths, file_contents))
        self._record_attribution(model_name, provider)
        return result

    def _route_generate_multimodal(self, provider: str, api_key: str, model_name: str,
                                    prompt: str, image_paths: List[str],
                                    file_contents: Dict[str, str]) -> str:
        """Generate content with multiple images and file contents."""

        # Build the full prompt with file contents embedded
        full_prompt = prompt
        if file_contents:
            file_section = "\n\n--- Attached File Contents ---\n"
            for filename, content in file_contents.items():
                file_section += f"\n**{filename}:**\n{content}\n"
            full_prompt = prompt + file_section

        # --- Google (REST API) - supports multiple images ---
        if provider == 'Google':
            return self._call_google_rest_multimodal(api_key, model_name, full_prompt, image_paths)

        # --- Anthropic - supports multiple images ---
        elif provider == 'Anthropic':
            return self._call_anthropic_multimodal(api_key, model_name, full_prompt, image_paths)

        # --- OpenAI Compatible APIs ---
        base_urls = get_config().provider_api_urls

        if provider in base_urls:
            return self._call_openai_style_multimodal(api_key, model_name, full_prompt, base_urls[provider], image_paths)

        raise Exception(f"Unknown provider: {provider}")

    def _call_openai_style_multimodal(self, api_key: str, model_name: str, prompt: str,
                                        base_url: str, image_paths: List[str]) -> str:
        """Helper for OpenAI-compatible APIs with multiple images."""
        import os

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        # Build content array with text + all images
        content = [{"type": "text", "text": prompt}]

        for img_path in image_paths:
            if os.path.exists(img_path):
                b64_data, mime_type = MultimodalProcessor.encode_image_base64(img_path)
                if b64_data:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64_data}"}
                    })

        messages = [{"role": "user", "content": content}]

        data = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.3
        }
        if not takes_temperature(model_name):        # google/gemini-... through OpenRouter
            del data["temperature"]
        generation_options, timeout = _openai_generation_settings(base_url, model_name, 60)
        data.update(generation_options)

        # OpenAI response parser
        def parse_openai_response(result: dict) -> str:
            return result['choices'][0]['message']['content'].strip()

        return self._make_request_with_retry(
            url=base_url,
            data=data,
            headers=headers,
            response_parser=parse_openai_response,
            timeout=timeout
        )

    def _call_anthropic_multimodal(self, api_key: str, model_name: str, prompt: str,
                                     image_paths: List[str]) -> str:
        """Helper for Anthropic Claude API with multiple images."""
        import os

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        content = []

        # Add all images first
        for img_path in image_paths:
            if os.path.exists(img_path):
                b64_data, mime_type = MultimodalProcessor.encode_image_base64(img_path)
                if b64_data:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": b64_data
                        }
                    })

        # Add text prompt
        content.append({"type": "text", "text": prompt})

        data = {
            "model": model_name,
            "max_tokens": 4096,  # Larger for multi-image responses
            "messages": [{"role": "user", "content": content}]
        }

        # Anthropic response parser
        def parse_anthropic_response(result: dict) -> str:
            return anthropic_text(result)

        return self._make_request_with_retry(
            url=url,
            data=data,
            headers=headers,
            response_parser=parse_anthropic_response,
            timeout=60  # Longer timeout for multi-image processing
        )
