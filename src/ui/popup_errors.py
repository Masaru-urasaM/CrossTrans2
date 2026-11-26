"""
A failed translation in plain words (TODO.md V22, section 6.5).

The popup used to print the raw failure ("Error: API key invalid or quota exceeded (401)") in red
over two coloured buttons. What a person needs is what happened and what to do next, so the popup
shows `explain()`'s sentence in an info bar and keeps the original text beneath it as "Details".

Pure logic, no Tk: the text goes in, a decision comes out. The wording is a draft for the string
pass (TODO.md U10, user review); the classes of failure are what matters here.
"""
import re
from dataclasses import dataclass

INFO = 'info'
WARNING = 'warning'
DANGER = 'danger'

# What helps first. The popup's bar always offers API Settings and Open Translator; this decides
# which of them (or "Try again") is the one the accent colour points at.
FIX_SETTINGS = 'settings'
FIX_RETRY = 'retry'
FIX_NONE = ''

_MULTI_KEY_HEADER = re.compile(
    r'^(?:error:\s*)?all\s+(?:api keys|vision apis|api calls)\s+failed\.?\s*$',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ErrorHelp:
    message: str      # what happened and what to do, in a sentence or two
    fix: str          # FIX_SETTINGS, FIX_RETRY or FIX_NONE
    kind: str         # the info bar's kind: INFO, WARNING or DANGER


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _explain_single(text: str) -> ErrorHelp:
    """Classify a single normalized (lowercased) failure notice."""
    if _has(text, r'please wait a moment'):
        return ErrorHelp("Please wait a moment before starting another quick translation.",
                         FIX_NONE, INFO)

    if _has(text, r'no prompt provided'):
        return ErrorHelp("Enter a prompt before sending.", FIX_NONE, INFO)

    if text.startswith('no text'):
        if 're-translate' in text:
            return ErrorHelp("There is no text to translate again.", FIX_NONE, INFO)
        return ErrorHelp("CrossTrans could not copy any text from the active app. Select text, keep that app focused, "
                         "and try the shortcut again. If text is already selected, the app may block copying; "
                         "copy it manually and paste it into Translate.", FIX_NONE, WARNING)

    if _has(text, r'recording was too short'):
        return ErrorHelp("The recording was too short to recognize speech. Hold the shortcut or record a little "
                         "longer, then try again.", FIX_RETRY, WARNING)

    if _has(text, r'recording device', r'recording source', r'microphone', r'audio capture',
            r'playback.*device', r'no active audio',
            r'choose a pc playback', r'playback loopback', r'recording stopped before'):
        return ErrorHelp("CrossTrans could not find or open an active audio device. Connect speakers, "
                         "headphones, or a microphone, check Windows microphone permissions, or check Audio "
                         "devices in Settings > API keys.", FIX_SETTINGS, WARNING)

    if _has(text, r'audio support is not installed'):
        return ErrorHelp("Audio components are missing from this installation. Reinstall CrossTrans "
                         "or repair its audio dependencies.", FIX_NONE, DANGER)

    if _has(text, r'could not be decoded', r'no audio track', r'must be 30 minutes'):
        return ErrorHelp("CrossTrans could not read speech from that media file. Check that it "
                         "contains a supported audio track under 30 minutes.", FIX_NONE, WARNING)

    if _has(text, r'no speech.*detected', r'no speech was detected'):
        return ErrorHelp("No speech was detected in the audio. Speak clearly or check your recording source in Settings.",
                         FIX_RETRY, WARNING)

    if _has(text, r'manual audio key', r'audio key in settings', r'add a working api key'):
        return ErrorHelp("Audio recognition has no working API key configured. Update the manual audio key "
                         "or switch Audio to Auto in Settings > API keys.", FIX_SETTINGS, WARNING)

    if _has(text, r'cannot auto-detect provider', r'choose a speech provider'):
        return ErrorHelp("CrossTrans could not detect which provider this API key belongs to. "
                         "Open Settings > API keys, choose the Provider, or click Test on the key card.",
                         FIX_SETTINGS, WARNING)

    if _has(text, r'no api key', r'not configured', r'missing api key'):
        return ErrorHelp("CrossTrans has no API key to use yet. Add your own key in Settings.",
                         FIX_SETTINGS, WARNING)

    if _has(text, r'\b401\b', r'unauthori[sz]ed', r'invalid.*key', r'key.*invalid', r'api_key_invalid',
            r'api key not valid', r'key.*not valid', r'incorrect.*key', r'did not accept.*api key'):
        return ErrorHelp("The provider rejected the API key (authentication failed). Check that the entire key "
                         "is saved under the correct provider in Settings > API keys, then test it.", FIX_SETTINGS, DANGER)

    if _has(text, r'\b403\b', r'forbidden', r'model.*permission', r'account.*permission', r'access denied',
            r'does not have permission'):
        return ErrorHelp("The provider denied access to this model. The key may work for other models, "
                         "but this request needs permission. Check the model and account access in Settings > API keys.",
                         FIX_SETTINGS, DANGER)

    # Explicit billing / credit exhaustion beats HTTP 429 (e.g. OpenAI 429 insufficient_quota).
    if _has(text, r'\b402\b', r'insufficient_quota', r'exceeded your current quota', r'credit',
            r'billing', r'payment required', r'trial quota'):
        return ErrorHelp("The provider reports that the account has no available quota or credit. "
                         "Check your usage and billing with the provider, or select another saved key.", FIX_SETTINGS, DANGER)

    # HTTP 429 / temporary rate limits (including Google Gemini 429 "Resource has been exhausted (e.g. check quota)").
    if _has(text, r'\b429\b', r'rate.?limit', r'too many requests'):
        return ErrorHelp("The provider is limiting requests or tokens for this key (HTTP 429). "
                         "Wait for its limit to reset, shorten the text, or use another saved key.", FIX_RETRY, WARNING)

    if _has(text, r'\bquota\b', r'exhausted'):
        return ErrorHelp("The provider reports that the account has no available quota or credit. "
                         "Check your usage and billing with the provider, or select another saved key.", FIX_SETTINGS, DANGER)

    if _has(text, r'no .*supports vision', r'does not support.*image', r'does not support.*vision',
            r'image.*not supported', r'only supports text', r'content must be a string'):
        return ErrorHelp("This provider/model configuration is not accepting image input. "
                         "Select an image-capable model and test it in Settings > API keys. A working text key alone "
                         "does not verify image access.", FIX_SETTINGS, WARNING)

    if _has(text, r'\b404\b', r'unknown model', r'model.*not found', r'model.*was not found',
            r'model.*does not exist', r'decommission'):
        return ErrorHelp("The provider could not find the selected model. Check its exact model ID in "
                         "Settings > API keys, or choose a model currently offered by that provider.", FIX_SETTINGS, DANGER)

    if _has(text, r'context.length', r'context window', r'too many tokens', r'\b413\b', r'too large', r'image.*limit'):
        return ErrorHelp("The request exceeds the model's text or image limit. Shorten the text, "
                         "reduce the number or size of attachments, then try again.", FIX_NONE, WARNING)

    if _has(text, r'content.filter', r'safety', r'blocked.*content',
            r'prohibited_content', r'blocklist', r'recitation'):
        return ErrorHelp("The provider blocked this content under its content policy. "
                         "Review the provider's reason in Details and adjust the input if appropriate.", FIX_NONE, WARNING)

    if _has(text, r'tool_calls', r'function_call'):
        return ErrorHelp("The model returned a tool call instead of a translation. "
                         "Try again or select another model in Settings > API keys.", FIX_RETRY, WARNING)

    if _has(text, r'no readable attachments', r'file no longer exists', r'no extractable text',
            r'contains no readable text', r'failed to extract text from pdf'):
        return ErrorHelp("CrossTrans could not read text from the attached file. Check that the file exists "
                         "and contains readable text, or try a different format.", FIX_NONE, WARNING)

    if _has(text, r'\b400\b', r'\b422\b', r'invalid.request', r'invalid.*image', r'unsupported.*format'):
        return ErrorHelp("The provider rejected the request format or an attachment. Details below contain its "
                         "specific reason. Check the model and attachment format before trying again.", FIX_NONE, DANGER)

    if _has(text, r'timed? ?out', r'took too long', r'deadline'):
        return ErrorHelp("The AI service took too long to answer. Try again in a moment.",
                         FIX_RETRY, WARNING)

    if _has(text, r'connection', r'network', r'unreachable', r'getaddrinfo', r'resolve',
            r'\bssl\b', r'certificate', r'offline', r'could not reach'):
        return ErrorHelp("CrossTrans could not reach the AI service. "
                         "Check your internet connection, then try again.", FIX_RETRY, WARNING)

    if _has(text, r'\b5\d\d\b', r'overload', r'unavailable', r'internal server', r'bad gateway'):
        return ErrorHelp("The AI service is having a problem right now. Try again in a moment, "
                         "or open the Translator to pick another model.", FIX_RETRY, WARNING)

    if _has(text, r'incomplete', r'truncat', r'finish.reason.*length', r'empty.*response', r'no.*content',
            r'speech provider returned an invalid response', r'did not return any response',
            r'did not return any answer'):
        return ErrorHelp("The model returned an empty or incomplete answer. No complete translation was received. "
                         "Try again with less text or select another model.", FIX_RETRY, WARNING)

    return ErrorHelp("Translation failed. The exact error received is shown in Details below; "
                     "its cause could not be identified automatically. Use that message to check the provider "
                     "or open Translate to try again.", FIX_RETRY, DANGER)


def explain(raw: str) -> ErrorHelp:
    """The failure notice `raw` ("Error: ..." or "No text ...") as an ErrorHelp.

    Matching is on the words the services and our own code use; the first class that fits wins,
    most specific first. Anything unrecognised gets a calm general sentence and a retry.
    When `raw` is a `FailureText`, its canonical `raw_reason` is classified so localized UI
    languages (e.g. Japanese) never lose error classification.
    """
    canonical = getattr(raw, 'raw_reason', None) or raw
    text = (canonical or '').strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    if len(lines) >= 3 and _MULTI_KEY_HEADER.match(lines[0]):
        per_key = [_explain_single(line.lower()) for line in lines[1:]]
        unique = list(dict.fromkeys(per_key))
        if len(unique) == 1:
            return unique[0]
        has_settings = any(item.fix == FIX_SETTINGS for item in per_key)
        has_retry = any(item.fix == FIX_RETRY for item in per_key)
        if has_settings and has_retry:
            severity = DANGER if any(item.kind == DANGER for item in per_key) else WARNING
            return ErrorHelp(
                "All saved API keys failed for different reasons (a key or model setting error on one key, "
                "and a temporary service or rate-limit error on another). Check Details below, try again, "
                "or review Settings > API keys.",
                FIX_SETTINGS,
                severity,
            )
        # Multiple keys failed in different ways within the same action class: keep highest priority.
        return _explain_single(text.lower())

    return _explain_single(text.lower())
