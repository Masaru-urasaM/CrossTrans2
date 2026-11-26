"""
The in-app help, as data (TODO.md U8, step 6).

The guide used to be 430 lines of widget code: every line a hard-coded label, the page names written into the
sentences ("Settings -> Hotkeys", a tab that no longer exists), screenshots of an interface that has since
been redrawn. Here it is a list of topics - a title, one sentence, numbered steps, bullet points and, where
it helps, a link to the page it talks about. The Help page draws them and the Settings search finds them (their
title, sentence and keywords become `settings_index` entries), so a person who types "hotkey" gets the
Shortcuts page and the help on shortcuts.

A sentence may name a number that changes - the languages, providers and free translations - as `{languages}`,
`{providers}` or `{trial_limit}`; `fill()` puts the current value in. Pure logic, no Tk, ASCII only (the
Japanese interface, U12, translates this file).
"""
from typing import Dict, NamedTuple, Optional, Tuple


class Topic(NamedTuple):
    key: str
    title: str
    summary: str                                   # one sentence ending in a full stop
    steps: Tuple[str, ...] = ()                    # numbered, in order
    notes: Tuple[str, ...] = ()                    # bullet points
    keywords: Tuple[str, ...] = ()                 # what the Settings search matches besides the words
    link: Optional[Tuple[str, str]] = None         # (page key, button text): where to do what the topic says


TOPICS: Tuple[Topic, ...] = (
    Topic('help_start', 'Getting started',
          'Select text, press a shortcut, read the translation.',
          steps=('Select text in any app: a browser, Word, a PDF.',
                 'Press a translate shortcut, for example Win + Alt + V for Vietnamese.',
                 'The translation appears in a popup beside your cursor.',
                 "Use the popup's buttons: Copy, Replace, Dictionary or Open Translator."),
          notes=('{languages} languages are supported.',
                 'Add your own API key in API keys before translating.',
                 'Use Home to switch between Translate, Dictionary and History.'),
          keywords=('start', 'begin', 'first', 'how', 'basics'),
          link=('providers', 'Open API keys')),
    Topic('help_shortcuts', 'Using shortcuts',
          'Every translate, screenshot and grammar shortcut can be changed.',
          steps=('Open Shortcuts and press Change next to the shortcut.',
                 'Press the keys you want. CrossTrans tells you if another shortcut or app already has them.'),
          notes=('Win + Alt + V, E, J and C translate into Vietnamese, English, Japanese and Chinese.',
                 'Win + Alt + S captures part of the screen and translates it.',
                 'Fix grammar has a shortcut too. It is off until you turn it on: Win + Alt + G is also the '
                 "Xbox Game Bar's.",
                 'Add up to four more languages, each with its own shortcut.'),
          keywords=('hotkey', 'keyboard', 'keys', 'xbox', 'game bar', 'change'),
          link=('shortcuts', 'Open Shortcuts')),
    Topic('help_popup', 'The translation popup',
          'What the buttons under a translation do.',
          notes=('Copy puts the translation on the clipboard.',
                 'Replace pastes it over the text you selected in the other app.',
                 'The icons beside them: the Replace settings, translate again, Dictionary, your own '
                 'instruction, linked reading, and Open Translator at the far right.',
                 'Press Esc to close the popup.'),
          keywords=('copy', 'replace', 'popup', 'buttons', 'icons', 'escape')),
    Topic('help_replace', 'Replacing text',
          'Put the translation back into the app you were writing in.',
          steps=('Press Replace in the popup.',
                 'Look at the preview: your text crossed out, then the translation.',
                 'Press Agree to replace it, or Cancel to keep your text.'),
          notes=('To paste at once without the preview, choose Replace immediately in the Translation settings.',
                 'CrossTrans copies the translation and presses Ctrl + V for you. If the app refuses the paste, '
                 'the translation is still on the clipboard.'),
          keywords=('paste', 'preview', 'agree', 'immediately', 'quick'),
          link=('translation', 'Open Translation settings')),
    Topic('help_screenshot', 'Translate a screenshot',
          'Drag over any part of the screen and read it in your language.',
          steps=('Press Win + Alt + S.',
                 'The screen dims. Drag over the part you want.',
                 'Let go: the text is read and translated.'),
          notes=('It works across several monitors.',
                 'It needs a model that can read images. Test your key in API keys to find out.',
                 'Open Translator keeps the picture as an attachment.'),
          keywords=('ocr', 'capture', 'image', 'picture', 'screen', 'vision'),
          link=('providers', 'Open API keys')),
    Topic('help_audio', 'Record and translate audio',
          'Press a shortcut to open the audio panel, then record PC audio, microphone speech, or both.',
          steps=('Add a supported API key in API keys; Audio uses your main keys automatically in Auto mode, or set a custom key under Audio translation.',
                 'Choose the audio language and recording source in API keys.',
                 'Press Win + Alt + A: the audio panel opens at the mouse pointer. Press Record (or Space), '
                 'then Stop to translate.',
                 'Read the quick result, play the recording, then choose whether to save when closing.'),
          notes=('Quick recordings stop at three minutes. You can change the shortcut in Shortcuts.',
                 'To record while holding the shortcut instead, choose Hold to record in API keys. Tray > Audio '
                 'panel opens the panel too.',
                 'Attach one audio or video file in Translate for recordings up to thirty minutes.',
                 'Uploaded originals stay unchanged. Only the first audio track is translated.',
                 'Speech recognition and text translation can share your main API keys or use a dedicated audio key.',
                 'Use headphones when recording both sources to reduce audio leaking into the microphone.',
                 'History stores text only. Change the recording save question in API keys.'),
          keywords=('audio', 'speech', 'microphone', 'record', 'video', 'whisper', 'groq', 'auto'),
          link=('providers', 'Open API keys')),
    Topic('help_dictionary', 'Looking up words',
          'Click words in a translation to see what they mean.',
          steps=('Translate something, then press the Dictionary button.',
                 'Words appear as buttons. Click the ones you want.',
                 'Press Dictionary Lookup.'),
          notes=('Drag across words to select a range, or type your own phrase in the boxes below.',
                 'An answer has the translation, definition, word type, pronunciation, synonyms, antonyms '
                 'and examples.',
                 'A language pack splits Japanese, Chinese, Korean and many other languages into words '
                 'much better. Install one in Language packs.'),
          keywords=('word', 'definition', 'meaning', 'pack', 'language pack', 'nlp'),
          link=('dictionary', 'Open Language packs')),
    Topic('help_files', 'Files and images',
          'Translate a whole document or a picture from the Translator.',
          steps=('Open the Translator from the tray icon.',
                 'Drop files on the box, or use Attach.',
                 'Choose the language and press Translate.'),
          notes=('Text files, Word documents, subtitle files (the timings are kept) and PDFs, '
                 'scanned ones too.',
                 'Pictures are read with the same model that reads screenshots.',
                 'Documents and images need a key that can read them. API keys shows which of yours can.'),
          keywords=('file', 'document', 'docx', 'pdf', 'srt', 'txt', 'attach', 'drag', 'drop'),
          link=('providers', 'Open API keys')),
    Topic('help_keys', 'Keys and providers',
          'Your own key gives the best speed and quota.',
          steps=('Sign up with a provider and create an API key. Many have a free tier.',
                 'Open API keys and paste the key.',
                 'Press Test to check it.'),
          notes=('{providers} providers are supported, with more than 180 models.',
                 'Leave Provider and Model on Auto: CrossTrans finds one that works and remembers it.',
                 'Add backup keys. They are tried in order when one fails.',
                 'Keys are stored encrypted by Windows. Showing a saved key asks Windows to confirm it is you.'),
          keywords=('api', 'key', 'provider', 'model', 'gemini', 'openai', 'groq', 'test', 'backup', 'free'),
          link=('providers', 'Open API keys')),
    Topic('help_translator', 'The Translator window',
          'More room, more options, and every past translation.',
          notes=('Open it from the popup or the tray icon.',
                 'The language you translate into is a list with your recent and shortcut languages first.',
                 'More translate options adds an instruction: formal, casual, concise, natural, or your own words.',
                 'Furigana puts the reading above Japanese kanji. Turn it off in Translation settings.',
                 'History keeps your last {history_limit} translations and can be searched.'),
          keywords=('window', 'history', 'furigana', 'tone', 'options', 'tray', 'instruction', 'prompt'),
          link=('translation', 'Open Translation settings')),
    Topic('help_trouble', 'If something does not work',
          'The usual causes, and what to try.',
          notes=('A shortcut does nothing: another app may hold it. Shortcuts says so under the row; pick '
                 'another combination.',
                 'No translation appears: select the text first, or copy it with Ctrl + C and try again. '
                 'A few apps block clipboard access.',
                 'Errors or no connection: check the key and your internet connection, then press Test in '
                 'API keys.',
                 'Replace does not paste: keep the text selected in the other app. Some apps block a pasted '
                 'Ctrl + V; the translation is on the clipboard.',
                 'CrossTrans will not start: only one copy can run. Look in the tray, or end it in Task Manager.'),
          keywords=('problem', 'error', 'fix', 'broken', 'not working', 'troubleshooting', 'help', 'crash'),
          link=('shortcuts', 'Open Shortcuts')),
)

_BY_KEY: Dict[str, Topic] = {topic.key: topic for topic in TOPICS}


def topic(key: str) -> Topic:
    return _BY_KEY[key]


def fill(text: str, languages: int, providers: int, trial_limit: int = 0) -> str:
    """`text` with the numbers that change put in."""
    from src.core.history import HistoryManager
    return text.format(languages=languages, providers=providers, trial_limit=trial_limit,
                       history_limit=HistoryManager.MAX_HISTORY)
