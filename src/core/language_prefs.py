"""
Which languages the Translator's picker offers first (TODO.md U7, UQ8).

Pure logic, no Tk and no config: the picker used to be a search field above a two-row list, with
2 of 104 languages visible and no memory of what the user chose last. Now it opens on **Pinned**
(the languages that have a hotkey - the ones the user has already said they use) and **Recent**
(the last few targets chosen), with every language below; typing searches all of them.
"""
from typing import Iterable, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from src.constants import TRANSLATOR_RECENT_LANGUAGES as RECENT_MAX
from src.core import lang_id

Language = Tuple[str, str, str]           # (English name, ISO code, native name) - constants.LANGUAGES

PINNED = 'Pinned'
RECENT = 'Recent'
ALL = 'All languages'
RESULTS = 'Results'


class Section(NamedTuple):
    title: str
    languages: Tuple[Language, ...]


def push_recent(recent: Iterable[str], language: str, limit: int = RECENT_MAX) -> List[str]:
    """`recent` with `language` first, once, cut to `limit`. A blank name changes nothing."""
    kept = [name for name in recent if isinstance(name, str) and name]
    if not language:
        return kept[:limit]
    return ([language] + [name for name in kept if name != language])[:limit]


def pinned_names(hotkeys: Iterable[str]) -> List[str]:
    """The hotkey languages in the order given, once each, blanks dropped."""
    if isinstance(hotkeys, Mapping):
        hotkeys = (name for name, binding in hotkeys.items() if binding)
    seen, names = set(), []
    for name in hotkeys:
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def target_name(language: Optional[str]) -> Optional[str]:
    """The picker's name for a language `lang_id` has settled - Japanese, or either Chinese - and
    None for anything else (nothing is guessed: this is what the swap button puts in the picker)."""
    canonical = lang_id.normalize_language(language) if language else None
    return {lang_id.JAPANESE: 'Japanese', lang_id.CHINESE_SIMPLIFIED: 'Chinese Simplified',
            lang_id.CHINESE_TRADITIONAL: 'Chinese Traditional'}.get(canonical)


def matches(language: Language, query: str) -> int:
    """How well `language` answers `query`: 0 no match, 1 somewhere in its names or code,
    2 a word or the code starts with it, 3 its English name starts with it. Case-insensitive,
    and a query of only spaces matches everything weakly."""
    needle = query.strip().lower()
    if not needle:
        return 1
    name, code, native = language
    name_l, code_l, native_l = name.lower(), code.lower(), native.lower()
    if name_l.startswith(needle):
        return 3
    words = name_l.split() + native_l.split() + [code_l]
    if any(word.startswith(needle) for word in words):
        return 2
    return 1 if needle in f"{name_l} {code_l} {native_l}" else 0


def search(languages: Sequence[Language], query: str) -> List[Language]:
    """The languages matching `query`, best match first and the list's own order within a grade."""
    graded = [(matches(language, query), index, language) for index, language in enumerate(languages)]
    return [language for grade, _, language in sorted(
        (g for g in graded if g[0] > 0), key=lambda g: (-g[0], g[1]))]


def sections(languages: Sequence[Language], query: str, recent: Iterable[str],
             pinned: Iterable[str]) -> List[Section]:
    """What the picker lists. With no query: Recent, Pinned, then every language (a language may
    appear in more than one section - that is how the user finds it where they look). With a
    query: one Results section over every language. Empty sections are left out, and a name that
    is not in `languages` (a stale setting) is ignored."""
    by_name = {language[0]: language for language in languages}
    if query.strip():
        found = search(languages, query)
        return [Section(RESULTS, tuple(found))] if found else []
    out = []
    for title, names in ((RECENT, recent), (PINNED, pinned)):
        chosen = tuple(by_name[name] for name in names if name in by_name)
        if chosen:
            out.append(Section(title, chosen))
    out.append(Section(ALL, tuple(languages)))
    return out


def flatten(shown: Sequence[Section]) -> List[Language]:
    """Every row of `shown` in order - what the arrow keys walk."""
    return [language for section in shown for language in section.languages]
