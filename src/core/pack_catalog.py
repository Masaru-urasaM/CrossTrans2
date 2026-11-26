"""
What the Dictionary page says and does about language packs, as pure logic (TODO.md U8, step 5).

The old tab decided all of this inside widget code: which packs show, how big they are, what a failed
install means, the order of a bulk install, the two places where installing or removing the Japanese pack
must change the furigana reader (D19). Here the decisions are functions of plain values and the page only
draws them; the work itself (`install_pack`, `remove_pack`, `run_batch`) takes any object with
`NLPManager`'s `install` / `uninstall`, so a test runs it with a fake and never pip-installs anything.

Nothing here touches Tk. The page runs the work on a worker thread (`BackgroundRunner`, Decision 43).
"""
import logging
import re
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

BASIC_MODE = 'BASIC_MODE'            # `NLPManager.install()`'s "it works, but splits on whitespace only"
INSTALL, REMOVE = 'install', 'remove'
INFO, SUCCESS, WARNING, DANGER = 'info', 'success', 'warning', 'danger'      # an info bar's kinds


def furigana_reader_note(accurate: bool, japanese_installed: bool) -> str:
    """The Dictionary page's line on which reader furigana uses (D19)."""
    if accurate:
        return "Furigana: accurate readings (UniDic, from the Japanese pack)."
    if japanese_installed:
        return ("Furigana: basic readings - the Japanese pack is installed, but its reader "
                "could not be loaded here (see the log).")
    return ("Furigana: basic readings. Install the Japanese pack below (~250 MB) for "
            "accurate ones - they apply at once, no restart.")


def apply_to_furigana(language: str, success: bool) -> None:
    """Installing or removing the Japanese pack changes the reader furigana uses (D19): look again now, not
    at the next start. Never raises: it runs on the worker thread just before the result is handed back,
    which must still happen, or the page would stay "working" for good."""
    if success and language == "Japanese":
        try:
            from src.core import furigana
            furigana.refresh_providers()
        except Exception as error:
            logging.warning(f"[Dictionary] furigana not refreshed after the {language} pack: {error}")


# --- the work ------------------------------------------------------------------------------------------
class PackOutcome(NamedTuple):
    language: str
    ok: bool
    basic: bool = False              # installed, but only whitespace word splitting could be set up
    error: str = ''


def _outcome(manager, language: str, ok: bool, error: str, install: bool) -> PackOutcome:
    apply_to_furigana(language, ok)
    if ok:
        cache = getattr(manager, '_installed_cache', None)
        if cache is not None:
            cache.clear()                       # a shared package may have changed another language's state
    return PackOutcome(language, bool(ok), bool(ok) and install and error == BASIC_MODE,
                       '' if ok else (error or ''))


def install_pack(manager, language: str, progress: Optional[Callable[[str, int], None]] = None) -> PackOutcome:
    ok, error = manager.install(language, progress)
    return _outcome(manager, language, ok, error, install=True)


def remove_pack(manager, language: str, progress: Optional[Callable[[str, int], None]] = None) -> PackOutcome:
    ok, error = manager.uninstall(language, progress)
    return _outcome(manager, language, ok, error, install=False)


def run_batch(manager, action: str, languages: Sequence[str],
              progress: Optional[Callable[[int, int, str, str, int], None]] = None,
              cancelled: Optional[Callable[[], bool]] = None) -> List[PackOutcome]:
    """Install or remove each of `languages` in turn; one that fails does not stop the rest. `progress`
    gets (index, total, language, message, percent): once as each pack starts (message '') and again for
    every step the manager reports. Returns an outcome for each pack that was tried."""
    work = install_pack if action == INSTALL else remove_pack
    total = len(languages)
    outcomes: List[PackOutcome] = []
    for index, language in enumerate(languages, 1):
        if cancelled is not None and cancelled():
            break
        if progress is not None:
            progress(index, total, language, '', 0)
            step = lambda message, percent, i=index, name=language: progress(i, total, name, message, percent)
        else:
            step = None
        try:
            outcomes.append(work(manager, language, step))
        except Exception as error:                      # one pack's crash is that pack's failure
            logging.exception(f"[Dictionary] {action} of {language} raised")
            outcomes.append(PackOutcome(language, False, False, str(error)))
    return outcomes


# --- what the page shows -----------------------------------------------------------------------------------
def describe(pack) -> str:
    """The sentence under a pack's name: "Asian, about 120 MB"."""
    size = f"about {pack.size_mb} MB" if pack.size_mb else ''
    return f"{pack.category}, {size}" if size else pack.category


def total_size(packs: Dict[str, object], names: Iterable[str]) -> int:
    return sum(packs[name].size_mb for name in names if name in packs)


def summary(installed: Sequence[str], packs: Dict[str, object]) -> str:
    if not installed:
        return "No language packs installed yet."
    return f"{len(installed)} installed, about {total_size(packs, installed)} MB."


def split(packs: Dict[str, object], installed: Iterable[str], query: str = '') -> Tuple[List[str], List[str]]:
    """The packs that answer `query` (a language's name, its category or its code; blank = every pack), as
    (installed, available), each A to Z without regard to case."""
    needle = (query or '').strip().lower()
    have = set(installed)

    def answers(name: str) -> bool:
        pack = packs[name]
        return (not needle or needle in name.lower() or needle in pack.category.lower()
                or needle == getattr(pack, 'code', '').lower())

    chosen = [name for name in sorted(packs, key=lambda n: (n.lower(), n)) if answers(name)]
    return [n for n in chosen if n in have], [n for n in chosen if n not in have]


def progress_title(action: str, index: int, total: int, language: str) -> str:
    verb = 'Installing' if action == INSTALL else 'Removing'
    where = f" ({index} of {total})" if total > 1 else ''
    return f"{verb} {language}{where}..."


def confirm_text(action: str, languages: Sequence[str], packs: Dict[str, object]) -> Tuple[str, str]:
    """(title, message) of the question asked before installing or removing `languages`."""
    count = len(languages)
    if action == INSTALL:
        return (f"Install {count} language packs?",
                f"They are about {total_size(packs, languages)} MB in all and download from PyPI, so an "
                "internet connection is needed. This can take several minutes.")
    title = f"Remove the {languages[0]} pack?" if count == 1 else f"Remove all {count} language packs?"
    message = ("Its packages are uninstalled from this computer. You can install it again later."
               if count == 1 else
               "Their packages are uninstalled from this computer. You can install them again later.")
    if 'Japanese' in languages:
        message += " Furigana goes back to basic readings."
    return title, message


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def explain_failure(error: str) -> str:
    """What a failed install or removal most likely means, in a sentence. An error that is not recognised
    is shown as its first line (the whole of it is in the log)."""
    text = (error or '').lower()
    if _has(text, r'python not found', r'no python'):
        return "Python was not found. Install Python and add it to PATH, then try again."
    if _has(text, r'permission', r'access is denied', r'winerror 5', r'used by another process'):
        return ("Windows would not let CrossTrans change the files. Close any program that may be "
                "using them, then try again.")
    if _has(text, r'no space left', r'not enough space', r'disk full'):
        return "There is not enough free disk space."
    if _has(text, r'connection', r'network', r'timed? ?out', r'getaddrinfo', r'name resolution', r'\bssl\b',
            r'proxy', r'could not find a version', r'no matching distribution'):
        return "CrossTrans could not download the packages. Check your internet connection, then try again."
    first = next((line.strip() for line in (error or '').splitlines() if line.strip()), '')
    if not first:
        return "Try again, or look in the log for details."
    return first if len(first) <= 160 else first[:157] + '...'


def result_notice(action: str, outcomes: Sequence[PackOutcome], requested: int = 0) -> Tuple[str, str]:
    """(info bar kind, sentence) for what a run did. `requested` is how many packs it was asked for
    (default: the outcomes it has)."""
    total = requested or len(outcomes)
    done = [o for o in outcomes if o.ok]
    failed = [o for o in outcomes if not o.ok]
    verb, past = ('install', 'Installed') if action == INSTALL else ('remove', 'Removed')
    if total == 1 and len(outcomes) == 1:
        outcome = outcomes[0]
        if outcome.ok:
            if outcome.basic:
                return INFO, f"{outcome.language} is installed, with basic word splitting."
            return SUCCESS, (f"{outcome.language} is installed." if action == INSTALL
                             else f"{outcome.language} was removed.")
        return DANGER, f"Could not {verb} {outcome.language}. {explain_failure(outcome.error)}"
    if not outcomes:
        return INFO, "Nothing was done."
    if not failed and len(done) == total:
        return SUCCESS, f"{past} {total} language packs."
    if not done:
        return DANGER, (f"Could not {verb} any of the {total} language packs. "
                        f"{explain_failure(failed[0].error)}")
    if not failed:                                          # it stopped before the end, with nothing failed
        return INFO, f"{past} {len(done)} of {total}."
    names = ', '.join(o.language for o in failed)
    return WARNING, f"{past} {len(done)} of {total}. Could not {verb}: {names}."
