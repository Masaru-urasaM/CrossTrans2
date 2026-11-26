"""
What the Settings window offers, as data (TODO.md U8).

Pure logic, no Tk: the pages, every setting on them with the words the user reads, and a search over
them. The window builds its rows from these entries, so a row and its search result can never say
different things, and the strings sit in one place for the Japanese interface (U12).

The Settings used to be five tabs organised by how the code was written - "Hotkeys" also held Replace
mode, Furigana, Linked reading and Fix Grammar. The pages are organised by what a person wants to do.
"""
from typing import Dict, List, NamedTuple, Optional, Tuple

from src.core.guide_content import TOPICS
from src.ui import i18n


class Page(NamedTuple):
    key: str
    title: str
    icon: str                 # a Bootstrap Icons name


class Entry(NamedTuple):
    key: str
    page: str
    title: str
    description: str
    keywords: Tuple[str, ...] = ()


PAGES: Tuple[Page, ...] = (
    Page('general', 'General', 'sliders'),
    Page('appearance', 'Appearance', 'palette'),
    Page('translation', 'Translation', 'translate'),
    Page('shortcuts', 'Shortcuts', 'keyboard'),
    Page('providers', 'API keys', 'key'),
    Page('dictionary', 'Language packs', 'book'),
    Page('help', 'Help', 'question-circle'),
    Page('about', 'About', 'info-circle'),
)

ENTRIES: Tuple[Entry, ...] = (
    # --- General ---
    Entry('autostart', 'general', 'Start with Windows',
          'Open CrossTrans in the tray when you sign in.', ('startup', 'boot', 'login', 'autostart')),
    Entry('auto_update', 'general', 'Check for updates automatically',
          'Looks when CrossTrans starts and once a day. You choose whether to install.',
          ('update', 'version', 'release', 'upgrade')),
    Entry('history_saving', 'general', 'Save translation history',
          'Keep translations on this device. Turning this off keeps existing history.',
          ('history', 'privacy', 'save', 'record')),
    Entry('restore_defaults', 'general', 'Restore defaults',
          'Reset shortcuts and preferences. Your API keys, history and history-saving choice stay.',
          ('reset', 'default', 'factory')),
    # --- Appearance ---
    Entry('theme', 'appearance', 'Theme', 'Follow Windows, or always use light or dark.',
          ('dark', 'light', 'mode', 'colour', 'color', 'night')),
    Entry('text_size', 'appearance', 'Text size', 'Make text larger throughout CrossTrans.',
          ('font', 'accessibility', 'zoom', 'size', 'read')),
    Entry('ui_language', 'appearance', 'Interface language',
          'Choose the language used for buttons, settings and help.',
          ('locale', 'japanese', 'english', 'language')),
    # --- Translation ---
    Entry('default_language', 'translation', 'Translate into',
          'The language the Translator starts with.', ('target', 'default', 'language')),
    Entry('furigana', 'translation', 'Show Furigana', 'Show the reading above kanji.',
          ('japanese', 'reading', 'ruby', 'hiragana', 'kana')),
    Entry('linked_reading', 'translation', 'Show Linked reading',
          'Generate sentence links while translating. Uses more tokens and may affect translation quality.',
          ('highlight', 'align', 'sentence', 'match')),
    Entry('show_linked_reading', 'translation', 'Show Linked reading button',
          'Show the Linked reading button in Home and quick translation when the feature is enabled.',
          ('link', 'button', 'visibility', 'popup')),
    Entry('replace_mode', 'translation', 'Replace button',
          'What happens when you press Replace in the popup.', ('paste', 'preview', 'quick', 'replace')),
    Entry('fix_grammar_button', 'translation', 'Show Fix grammar button',
          'Show it in the Translator.', ('grammar', 'correct', 'spelling', 'proofread')),
    Entry('screenshot_language', 'translation', 'Screenshot translation',
          "The language a screenshot is translated into. Auto uses the Translator's.",
          ('ocr', 'capture', 'target', 'image', 'screen')),
    Entry('audio_enabled', 'providers', 'Audio translation',
          'Win + Alt + A opens the audio panel. Record PC audio, your microphone or both, then read the '
          'translation.',
          ('audio', 'record', 'speech', 'enable', 'disable', 'hotkey')),
    Entry('audio_mode', 'providers', 'Audio processing mode',
          'Two-step verbatim transcription or one-step direct translation.',
          ('audio', 'mode', 'two-step', 'one-step', 'direct')),
    Entry('audio_language', 'providers', 'Translate audio into',
          "The language recorded audio is translated into. Auto uses the Translator's.",
          ('audio', 'speech', 'recording', 'target', 'microphone')),
    Entry('audio_source', 'providers', 'Recording source',
          'Record PC playback, your microphone, or both.', ('audio', 'microphone', 'loopback')),
    Entry('audio_model', 'providers', 'Speech recognition model',
          'Uses an Audio key. Speech recognition and text translation have separate quotas.',
          ('audio', 'whisper', 'groq', 'speech', 'model')),
    Entry('audio_denoise', 'providers', 'Reduce microphone noise',
          'Apply light noise reduction before speech recognition.', ('audio', 'noise', 'filter')),
    Entry('audio_save_policy', 'providers', 'Save recordings',
          'Ask when closing, discard, or offer a save location for each recording.',
          ('audio', 'privacy', 'save', 'recording', 'ask')),
    Entry('audio_devices', 'providers', 'Audio devices',
          'Choose the microphone and PC playback device. Uploaded files can be up to 30 minutes.',
          ('audio', 'device', 'video', 'upload', 'microphone')),
    # --- Shortcuts ---
    Entry('shortcuts_enabled', 'shortcuts', 'Enable shortcuts',
          'Enable global translation, screenshot and grammar shortcuts.',
          ('hotkey', 'keyboard', 'disable', 'enable')),
    Entry('language_shortcuts', 'shortcuts', 'Translate shortcuts',
          'One per language: select text, press it, read the translation.',
          ('hotkey', 'keyboard', 'win', 'alt', 'key', 'custom')),
    Entry('screenshot_shortcut', 'shortcuts', 'Screenshot shortcut',
          'Drag over the screen to translate what is in it.', ('hotkey', 'ocr', 'capture', 'keyboard')),
    Entry('fix_grammar_shortcut', 'shortcuts', 'Fix grammar shortcut',
          'Off until you turn it on: Win + Alt + G is also the Xbox Game Bar.',
          ('hotkey', 'grammar', 'xbox', 'game bar', 'keyboard')),
    Entry('audio_shortcut', 'shortcuts', 'Record and translate shortcut',
          'Opens the audio panel; pressed while it is open, it records or stops. Recordings: up to 3 minutes.',
          ('audio', 'record', 'speech', 'hold', 'keyboard', 'microphone')),
    # On the API keys page beside the audio shortcut, but listed after the Shortcuts page's entries: a search for
    # "shortcut" ties on score and should open that page first.
    Entry('audio_hotkey_action', 'providers', 'Audio shortcut',
          'Open the audio panel, or record while the shortcut is held.',
          ('audio', 'panel', 'hold', 'shortcut', 'record', 'hotkey', 'keyboard')),
    # --- API keys ---
    Entry('api_keys', 'providers', 'API keys',
          'Add your own key for the best speed and quota. Backups are tried in order.',
          ('key', 'token', 'openai', 'google', 'gemini', 'anthropic', 'groq', 'model', 'provider')),
    Entry('failover', 'providers', 'Rotate keys',
          'Uses the same key with another model before moving to the next key.',
          ('rotation', 'fallback', 'failover', 'backup')),
    Entry('capabilities', 'providers', 'Image and file support',
          '', ('image', 'vision', 'file', 'pdf', 'docx', 'audio', 'speech', 'whisper')),
    # --- Dictionary ---
    Entry('language_packs', 'dictionary', 'Language packs',
          'Install a pack so the Dictionary can split a language into words.',
          ('nlp', 'tokenizer', 'install', 'download', 'japanese', 'chinese', 'korean', 'thai')),
    # --- About & help ---
    Entry('version', 'about', 'Version and updates',
          'Which version you have, and whether a newer one is out.', ('update', 'check', 'release')),
    Entry('github_update_token', 'about', 'GitHub update token (optional)',
          'Public updates work without a token. Add one only for higher rate limits or private forks.',
          ('github', 'token', 'optional', 'private', 'rate limit', 'update', 'access', 'authentication')),
    Entry('guide', 'about', 'How to use CrossTrans', 'Step-by-step help for every feature.',
          ('help', 'guide', 'manual', 'tutorial', 'how')),
    Entry('keyboard_help', 'about', 'Keyboard shortcuts',
          'See every translation, screenshot and grammar shortcut.', ('keyboard', 'keys', 'help')),
    Entry('welcome', 'about', 'Welcome', 'See the introduction and try a first translation.',
          ('onboarding', 'tutorial', 'start', 'sample')),
    Entry('feedback', 'about', 'Send feedback', 'Report a bug or suggest something.',
          ('bug', 'issue', 'report', 'contact')),
    Entry('diagnostics', 'about', 'Diagnostics and bug report',
          'Inspect runtime status, copy a bug report, or export logs.',
          ('debug', 'bug', 'report', 'log', 'crash', 'support', 'export')),
    Entry('debug_logging', 'about', 'Verbose debug logging',
          'Record detailed step-by-step logs for troubleshooting.',
          ('debug', 'verbose', 'log', 'trace', 'troubleshoot')),
    Entry('source', 'about', 'CrossTrans on GitHub', 'The source code and every release.',
          ('github', 'source', 'code', 'licence', 'license')),
) + tuple(Entry(topic.key, 'help', topic.title, topic.summary, topic.keywords) for topic in TOPICS)

_PAGE_BY_KEY: Dict[str, Page] = {page.key: page for page in PAGES}
_ENTRY_BY_KEY: Dict[str, Entry] = {entry.key: entry for entry in ENTRIES}

# What the application called a page before the redesign (`_show_settings_tab("API Key")`).
_OLD_NAMES: Dict[str, str] = {
    'ai providers': 'providers',
    'general': 'general', 'hotkeys': 'shortcuts', 'hotkey': 'shortcuts', 'api key': 'providers',
    'api keys': 'providers', 'api': 'providers', 'dictionary': 'dictionary', 'guide': 'help',
    'about & help': 'about',
}


def page(key: str) -> Page:
    return _PAGE_BY_KEY[key]


def entry(key: str) -> Entry:
    return _ENTRY_BY_KEY[key]


def entries_of(page_key: str) -> Tuple[Entry, ...]:
    return tuple(item for item in ENTRIES if item.page == page_key)


def page_for(name: Optional[str]) -> Optional[str]:
    """The page a name asks for - a page key, its title, an old tab name, or the start of one
    ("API" for "API keys") - or None."""
    wanted = ' '.join(str(name or '').lower().split())
    if not wanted:
        return None
    if wanted in _PAGE_BY_KEY:
        return wanted
    for item in PAGES:
        if item.title.lower() == wanted:
            return item.key
    if wanted in _OLD_NAMES:
        return _OLD_NAMES[wanted]
    for old, key in _OLD_NAMES.items():
        if old.startswith(wanted) or wanted.startswith(old):
            return key
    for item in PAGES:
        if item.title.lower().startswith(wanted):
            return item.key
    return None


def _terms(query: str) -> List[str]:
    return [term for term in query.lower().split() if term]


def score(item: Entry, terms: List[str]) -> int:
    """How well `item` answers `terms` (all of them must match somewhere): the sum, per term, of
    3 for the start of the title, 2 for the start of a title word or a keyword, 1 for anywhere in the
    title, description, keywords or its page's name. 0 when a term matches nowhere."""
    title = item.title.lower()
    title_words = title.split()
    keywords = [word for keyword in item.keywords for word in keyword.lower().split()] + \
        [keyword.lower() for keyword in item.keywords]
    everything = ' '.join([title, item.description.lower(), ' '.join(item.keywords).lower(),
                           _PAGE_BY_KEY[item.page].title.lower()])
    japanese = i18n.catalog()
    everything += ' ' + ' '.join(japanese.get(text, {}).get('ja', '') for text in
                                  (item.title, item.description, _PAGE_BY_KEY[item.page].title))
    total = 0
    for term in terms:
        if title.startswith(term):
            total += 3
        elif any(word.startswith(term) for word in title_words) or any(word.startswith(term) for word in keywords):
            total += 2
        elif term in everything:
            total += 1
        else:
            return 0
    return total


def search(query: str, entries: Tuple[Entry, ...] = ENTRIES) -> List[Entry]:
    """The entries that answer `query`, best first and the list's own order within a score. A blank
    query finds nothing (the window shows the page, not a result list)."""
    terms = _terms(query)
    if not terms:
        return []
    graded = [(score(item, terms), index, item) for index, item in enumerate(entries)]
    return [item for points, _, item in sorted((g for g in graded if g[0] > 0), key=lambda g: (-g[0], g[1]))]


def pages_with_results(found: List[Entry]) -> List[str]:
    """The page keys of `found`, in the order of their best result, once each."""
    seen: List[str] = []
    for item in found:
        if item.page not in seen:
            seen.append(item.page)
    return seen
