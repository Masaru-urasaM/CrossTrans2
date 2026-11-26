"""
Model names for the Settings model picker (TODO.md U8, step 4), as pure logic.

The picker lists the models a provider offers - up to a few hundred for "Auto" - and lets the user
search them or type one the list does not have. `models_for()` is the list (what the old
`widgets.get_all_models_list()` built for the combobox) and `rank()` the search over it.
"""
import re
from typing import Dict, List, Optional, Sequence

AUTO = 'Auto'
_WORD = re.compile(r'[^a-z0-9]+')
_GEMINI_VERSION = re.compile(r'gemini-(\d+)(?:\.(\d+))?(?=[-:]|$)')


def models_for(provider: str, model_map: Dict[str, Sequence[str]]) -> List[str]:
    """"Auto" first, then the models of `provider` (of every provider for "Auto") once each, A to Z
    without regard to case. A provider the map does not know offers only "Auto"."""
    if not provider or provider == AUTO:
        names = [name for models in model_map.values() for name in models]
    else:
        names = list(model_map.get(provider) or [])
    seen, unique = set(), []
    for name in sorted((n for n in names if n and n != AUTO), key=lambda n: (n.lower(), n)):
        if name.lower() not in seen:
            seen.add(name.lower())
            unique.append(name)
    return [AUTO] + unique


def takes_temperature(model: str) -> bool:
    """Whether a request for `model` should carry `temperature` (TODO Task 20).

    Google: for "Gemini 3.6 Flash and Gemini 3.5 Flash-Lite", and "all future Gemini model releases",
    `temperature`, `top_p` and `top_k` "are deprecated and ignored", and "In future model generations,
    supplying these parameters returns an HTTP 400 error" (aistudio.google.com/docs/whats-new-gemini-3.6,
    read 2026-11-19). Those models are not sent it, nor is an alias without a version such as
    `gemini-flash-latest`, which names the newest generation; dropping a parameter they ignore changes
    nothing they return. Older Gemini models, whose handling the page does not describe, and every other
    model keep it. Takes a native ID or an OpenRouter slug (`google/gemini-...`).
    """
    name = (model or '').strip().lower().rsplit('/', 1)[-1]
    if not name.startswith('gemini-'):
        return True
    match = _GEMINI_VERSION.match(name)
    if not match:
        return False
    version = (int(match.group(1)), int(match.group(2) or 0))
    return version < (3, 5) or (version == (3, 5) and 'flash-lite' not in name)


def _words(name: str) -> List[str]:
    return [word for word in _WORD.split(name.lower()) if word]


def score(name: str, query: str) -> int:
    """How well `name` answers `query`: 3 when it starts with it, 2 when a word of it does, 1 when it is
    anywhere inside, 0 when it is not."""
    needle = query.strip().lower()
    if not needle:
        return 1
    lowered = name.lower()
    if lowered.startswith(needle):
        return 3
    if any(word.startswith(needle) for word in _words(name)):
        return 2
    return 1 if needle in lowered else 0


def rank(models: Sequence[str], query: str) -> List[str]:
    """The models that answer `query`, best first and in the list's own order within a score. A blank
    query keeps the whole list as it is (`score()` gives every name 1 then)."""
    graded = [(score(name, query), index, name) for index, name in enumerate(models)]
    return [name for points, _, name in sorted((g for g in graded if g[0] > 0), key=lambda g: (-g[0], g[1]))]


def offers_custom(models: Sequence[str], query: str) -> bool:
    """Whether to offer 'use what I typed': the query is not blank and is not already a model."""
    typed = query.strip()
    return bool(typed) and typed.lower() not in {name.lower() for name in models}


def thinking_config(model: str) -> Optional[Dict]:
    """The `thinkingConfig` a Gemini `generateContent` request for `model` carries to keep its thinking short, or
    None for none (Decision 105). For the Google REST text and image calls only, not speech or OpenRouter.

    Measured 2026-11-20 with the developer's keys, a 290-character paragraph into Japanese: `gemini-2.5-flash`
    thought 2,669-3,699 tokens and took 13.3-17.4 s, and with `thinkingBudget: 0` it took 1.2 s with none;
    it answers 400 to `thinkingLevel`. `gemini-3.5-flash-lite` answers 400 to `thinkingBudget: 0` and accepts
    `low` and `minimal`; `gemini-flash-latest` refuses `minimal`. Google's thinking page lists `low` for every
    Gemini 3 and 4 model, with defaults from `minimal` (the Flash-Lite models) to `high`.
    - a `-lite` model: none - 2.5 Flash-Lite does not think by default and 3.x Flash-Lite thinks at `minimal`,
      so `low` would raise it;
    - Gemini 2.5 Flash: `thinkingBudget: 0`;
    - Gemini 3 or later, or a name without a version such as `gemini-flash-latest`: `thinkingLevel: low`;
    - anything else (2.5 Pro cannot turn thinking off, older Gemini models do not think, other models): none.
    A model that refuses what this names is sent none after one retry (`AIAPIManager._post_gemini`).
    """
    name = (model or '').strip().lower().rsplit('/', 1)[-1]
    if not name.startswith('gemini-') or 'lite' in name:
        return None
    match = _GEMINI_VERSION.match(name)
    if not match:
        return {'thinkingLevel': 'low'}
    version = (int(match.group(1)), int(match.group(2) or 0))
    if version >= (3, 0):
        return {'thinkingLevel': 'low'}
    if version == (2, 5) and 'flash' in name:
        return {'thinkingBudget': 0}
    return None
