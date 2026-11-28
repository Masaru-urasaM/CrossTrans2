import os

from src.ui.theme.tokens import DARK
from src.product import APP_ID, REPOSITORY, INSTANCE_PORT
"""
Constants and configuration values for CrossTrans.
"""

# ============== VERSION ==============
VERSION = "2.0.1"
APP_NAME = APP_ID
GITHUB_REPO = REPOSITORY
FEEDBACK_URL = f"https://github.com/{GITHUB_REPO}/issues/new"

# ============== NETWORK ==============
LOCK_PORT = INSTANCE_PORT  # Port for single instance lock

# ============== REMOTE CONFIG ==============
REMOTE_CONFIG_URL = "https://crosstrans.trial-api.workers.dev/v1/config"
REMOTE_CONFIG_CACHE_TTL = 86400  # 24 hours in seconds
# A running app fetches again once its table is this old (the Worker rebuilds it every 12 hours), and waits
# this long after any attempt, failed or not, before the next (Decision 103).
REMOTE_CONFIG_REFRESH_SECONDS = 6 * 3600
REMOTE_CONFIG_RETRY_SECONDS = 3600


def _env_switch(name, default=True):
    """A feature switch that an environment variable can turn off ('0', 'false', 'no', 'off') or on."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in ('0', 'false', 'no', 'off')


# The model registry is the first source of the model/provider table; the Worker above, the cache file and the
# hardcoded tables follow it (Decision 110). CROSSTRANS_MODEL_REGISTRY=0 turns it off: only the Worker is asked then.
MODEL_REGISTRY_URL = "https://models.masaru.uk"
MODEL_REGISTRY_ENABLED = _env_switch('CROSSTRANS_MODEL_REGISTRY', True)

# ============== TIMING ==============
COOLDOWN = 2.0  # Translation cooldown in seconds
TRANSLATION_TIMEOUT_SECONDS = 20.0  # Whole request, including retries and fallback

# ============== FURIGANA ==============
# Every tuning knob for the Japanese reading guides, in one place. The engine
# (src/core/furigana.py), the renderer (src/ui/ruby_text.py) and the main
# window's Reading pane (src/app.py) alias these to their own short names, so a
# value is changed here and nowhere else.

# Cost guards. Both bound work that happens on the UI thread.
# Measured on Tk 8.6: 200 ruby pairs ~124 ms to build and lay out, 500 ~400 ms,
# 1000 ~626 ms. Above the pair cap the engine returns plain text; above the
# character cap the renderer skips annotation entirely (that one also bounds the
# tokenizer, which the pair cap cannot, since counting pairs means running it).
FURIGANA_MAX_RUBY_PAIRS = 400
FURIGANA_MAX_ANNOTATE_CHARS = 3000

# Text annotated by prewarm() to build the reading engine off the UI thread.
# It MUST contain kanji AND kana or should_annotate() rejects it and the prewarm
# silently does nothing; the all-kanji compound is what pulls in the pykakasi
# dictionary that _refine_compounds needs on every annotation.
FURIGANA_PREWARM_SAMPLE = "日本語を読む"

# Appearance. Yu Gothic renders kana at 7pt legibly; a Latin UI font does not.
FURIGANA_FONT_FAMILY = 'Yu Gothic'
FURIGANA_BASE_FONT_SIZE = 11
FURIGANA_RUBY_FONT_SIZE = 7

FURIGANA_DEFAULT_BG = DARK.surface      # popup background
FURIGANA_BASE_FG = DARK.text         # plain (unannotated) runs
FURIGANA_KANJI_FG = DARK.text        # base characters under a reading
FURIGANA_RUBY_FG = DARK.ruby         # the reading itself
FURIGANA_RUBY_BG = DARK.surface         # opt-in plate; RubyText matches its own bg instead

FURIGANA_LINE_SPACING = 4            # spacing1 == spacing3 on the Text widget
FURIGANA_RUBY_PAD_X = 0              # per side, on both ruby labels (0 = Word-like, no gap)
FURIGANA_RUBY_PAD_Y = 1              # padding above the reading and below the base. The
                                     # bottom half is charged back by the baseline lift, so
                                     # changing it keeps the two in step - see ruby_text.
FURIGANA_WHEEL_UNITS = 3             # lines scrolled per wheel notch
FURIGANA_DEFAULT_MAX_ROWS = 12       # rows a RubyText grows to before scrolling

# Reading pane under the main window's input box.
FURIGANA_READING_PANE_DEBOUNCE_MS = 350  # keystroke burst absorbed before annotating
FURIGANA_READING_PANE_MAX_ROWS = 4       # taller than this and the pane scrolls

# ============== QUICK-TRANSLATE POPUP ==============
# How the quick-translate popup sizes itself (Decision 34, src/ui/popup_layout.py).
# Policy only: how tall or wide a widget is gets measured on the widget, never
# written down here - constants for that drifted once (the hidden close button).
POPUP_READING_CHARS_LATIN = 70      # characters per line, Latin text (WCAG 1.4.8: at most 80)
POPUP_READING_CHARS_CJK = 36        # characters per line, CJK text (WCAG 1.4.8: at most 40)
POPUP_HEIGHT_CAP_SHARE = 0.60       # of the monitor's work area; beyond it a box scrolls
POPUP_MAX_WIDTH_SHARE = 0.90        # of the monitor's work area
POPUP_ORIGINAL_SHARE = 0.40         # of the text room the original may keep when both overflow
POPUP_MIN_BOX_ROWS = 3              # a box squeezed by the cap still shows this many rows
POPUP_USER_MIN_ROWS = 1             # ...and by the user's own drag, this many
POPUP_COLLAPSED_ORIGINAL_ROWS = 3   # the original block (with readings) when collapsed
POPUP_ANCHOR_OFFSET = (15, 20)      # from the cursor to the popup's nearest corner
POPUP_SCREEN_MARGIN = 10            # kept clear along the work area's edges
POPUP_LAYOUT_WATCHDOG_MS = 300      # the popup is shown by then even if measuring never ran
POPUP_REVEAL_DELAY_MS = 30          # after the final size, for Tk to redraw before it is seen
POPUP_FRONT_MS = 500                # on top this long after it appears, then free to go behind

# The Translator window (TODO.md U7). Sizes are at 100 %; `translator_layout` scales them.
TRANSLATOR_DEFAULT_SIZE = (1200, 720)   # before the work area limits it; was 1400 x 850 (V11)
TRANSLATOR_MIN_SIZE = (640, 560)        # stacked panes with nothing clipped
TRANSLATOR_WORK_SHARE = 0.9             # never larger than this share of the work area
TRANSLATOR_SIDE_BY_SIDE_MIN_WIDTH = 900  # two panes from this width, stacked below it
TRANSLATOR_RECENT_LANGUAGES = 5         # the picker's Recent section

# The Settings window (TODO.md U8). Sizes are at 100 %; `translator_layout.window_size` scales them.
SETTINGS_DEFAULT_SIZE = (960, 640)      # before the work area limits it; was 1400 x 650
SETTINGS_MIN_SIZE = (680, 480)          # the navigation and a page at one reading width
SETTINGS_NAV_WIDTH = 232                # the left-hand navigation and its search field
SETTINGS_SAVED_MS = 1600                # how long the quiet "Saved" stays after a change
SETTINGS_RESULTS_MAX = 8                # search results listed before "keep typing"
SETTINGS_PAGE_MAX_WIDTH = 720           # a page's reading width: wider rows only spread the words
SETTINGS_MAX_ADDED_LANGUAGES = 4        # languages added to the four that ship, each with its own shortcut
SETTINGS_HIGHLIGHT_MS = 2500            # how long a search result's row keeps its accent bar
SETTINGS_KEY_SAVE_MS = 800              # a key typed or pasted is saved once typing pauses this long

LINKED_READING_DELAY_MS = 200
LINKED_READING_FADE_MS = 96
LINKED_READING_FADE_STEPS = 6

# ============== AVAILABLE LANGUAGES ==============
# Format: (English name, ISO code, Native name)
LANGUAGES = [
    ("Vietnamese", "vi", "Tiếng Việt"),
    ("English", "en", "English"),
    ("Japanese", "ja", "日本語"),
    ("Chinese Simplified", "zh-CN", "中文简体"),
    ("Chinese Traditional", "zh-TW", "中文繁體"),
    ("Korean", "ko", "한국어"),
    ("French", "fr", "Français"),
    ("German", "de", "Deutsch"),
    ("Spanish", "es", "Español"),
    ("Italian", "it", "Italiano"),
    ("Portuguese", "pt", "Português"),
    ("Russian", "ru", "Русский"),
    ("Thai", "th", "ไทย"),
    ("Indonesian", "id", "Bahasa Indonesia"),
    ("Malay", "ms", "Bahasa Melayu"),
    ("Hindi", "hi", "हिन्दी"),
    ("Arabic", "ar", "العربية"),
    ("Dutch", "nl", "Nederlands"),
    ("Polish", "pl", "Polski"),
    ("Turkish", "tr", "Türkçe"),
    ("Swedish", "sv", "Svenska"),
    ("Danish", "da", "Dansk"),
    ("Norwegian", "no", "Norsk"),
    ("Finnish", "fi", "Suomi"),
    ("Greek", "el", "Ελληνικά"),
    ("Czech", "cs", "Čeština"),
    ("Romanian", "ro", "Română"),
    ("Hungarian", "hu", "Magyar"),
    ("Ukrainian", "uk", "Українська"),
    ("Afrikaans", "af", "Afrikaans"),
    ("Albanian", "sq", "Shqip"),
    ("Amharic", "am", "አማርኛ"),
    ("Armenian", "hy", "Հայերեն"),
    ("Azerbaijani", "az", "Azərbaycan"),
    ("Basque", "eu", "Euskara"),
    ("Belarusian", "be", "Беларуская"),
    ("Bengali", "bn", "বাংলা"),
    ("Bosnian", "bs", "Bosanski"),
    ("Bulgarian", "bg", "Български"),
    ("Catalan", "ca", "Català"),
    ("Cebuano", "ceb", "Cebuano"),
    ("Chichewa", "ny", "Chichewa"),
    ("Corsican", "co", "Corsu"),
    ("Croatian", "hr", "Hrvatski"),
    ("Esperanto", "eo", "Esperanto"),
    ("Estonian", "et", "Eesti"),
    ("Filipino", "tl", "Filipino"),
    ("Frisian", "fy", "Frysk"),
    ("Galician", "gl", "Galego"),
    ("Georgian", "ka", "ქართული"),
    ("Gujarati", "gu", "ગુજરાતી"),
    ("Haitian Creole", "ht", "Kreyòl Ayisyen"),
    ("Hausa", "ha", "Hausa"),
    ("Hawaiian", "haw", "Ōlelo Hawaiʻi"),
    ("Hebrew", "iw", "עברית"),
    ("Hmong", "hmn", "Hmoob"),
    ("Icelandic", "is", "Íslenska"),
    ("Igbo", "ig", "Igbo"),
    ("Irish", "ga", "Gaeilge"),
    ("Javanese", "jw", "Jawa"),
    ("Kannada", "kn", "ಕನ್ನಡ"),
    ("Kazakh", "kk", "Қазақ"),
    ("Khmer", "km", "ខ្មែរ"),
    ("Kurdish (Kurmanji)", "ku", "Kurdî"),
    ("Kyrgyz", "ky", "Кыргызча"),
    ("Lao", "lo", "ລາວ"),
    ("Latin", "la", "Latina"),
    ("Latvian", "lv", "Latviešu"),
    ("Lithuanian", "lt", "Lietuvių"),
    ("Luxembourgish", "lb", "Lëtzebuergesch"),
    ("Macedonian", "mk", "Македонски"),
    ("Malagasy", "mg", "Malagasy"),
    ("Malayalam", "ml", "മലയാളം"),
    ("Maltese", "mt", "Malti"),
    ("Maori", "mi", "Māori"),
    ("Marathi", "mr", "मराठी"),
    ("Mongolian", "mn", "Монгол"),
    ("Myanmar (Burmese)", "my", "မြန်မာ"),
    ("Nepali", "ne", "नेपाली"),
    ("Pashto", "ps", "پښتو"),
    ("Persian", "fa", "فارسی"),
    ("Punjabi", "pa", "ਪੰਜਾਬੀ"),
    ("Samoan", "sm", "Gagana faʻa Sāmoa"),
    ("Scots Gaelic", "gd", "Gàidhlig"),
    ("Serbian", "sr", "Српски"),
    ("Sesotho", "st", "Sesotho"),
    ("Shona", "sn", "chiShona"),
    ("Sindhi", "sd", "سنڌي"),
    ("Sinhala", "si", "සිංහල"),
    ("Slovak", "sk", "Slovenčina"),
    ("Slovenian", "sl", "Slovenščina"),
    ("Somali", "so", "Soomaali"),
    ("Sundanese", "su", "Basa Sunda"),
    ("Swahili", "sw", "Kiswahili"),
    ("Tajik", "tg", "Тоҷикӣ"),
    ("Tamil", "ta", "தமிழ்"),
    ("Telugu", "te", "తెలుగు"),
    ("Urdu", "ur", "اردو"),
    ("Uzbek", "uz", "Oʻzbek"),
    ("Welsh", "cy", "Cymraeg"),
    ("Xhosa", "xh", "isiXhosa"),
    ("Yiddish", "yi", "ייִדיש"),
    ("Yoruba", "yo", "Yorùbá"),
    ("Zulu", "zu", "isiZulu"),
]

# ============== AI PROVIDERS ==============
PROVIDERS_LIST = [
    "Auto", "Google", "OpenAI", "Anthropic", "DeepSeek",
    "Groq", "xAI", "Mistral", "Perplexity", "Cerebras",
    "SambaNova", "Together", "SiliconFlow", "OpenRouter", "HuggingFace"
]

# Maps specific model patterns to their native providers.
# Keys match PROVIDERS_LIST exactly (Title Case).
# Offline tier-3 floor snapshot synced against https://models.masaru.uk on 2026-11-28.
# At runtime, remote_config replaces this table from the live registry (tier 1) or cache (tier 2).
# Keep these lists compact and curated: every non-Perplexity ID here is live-verified on 2026-11-28.
MODEL_PROVIDER_MAP = {
    # === GOOGLE (Gemini & Gemma) ===
    # Chat models only: omits robotics, computer-use, deep-research and antigravity preview entries.
    'Google': [
        # Gemini 4
        'gemini-4-argon', 'gemini-4-carbon', 'gemini-4-hydrogen', 'gemini-4-neon',
        # Gemini 3.x
        'gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-3.5-flash-lite',
        'gemini-3.1-pro-preview', 'gemini-3.1-flash-lite-preview', 'gemini-3-flash-preview',
        # Gemini 2.5
        'gemini-2.5-flash-lite',
        # Aliases that track the current release
        'gemini-flash-latest', 'gemini-flash-lite-latest', 'gemini-pro-latest',
        # Gemma 4
        'gemma-4-31b-it', 'gemma-4-26b-a4b-it',
    ],
    # === OPENAI ===
    'OpenAI': [
        # GPT-6 Series - defaults since 2026-11-17 (Decision 92); all live models accept image input
        'gpt-6-luna', 'gpt-6.1-terra', 'gpt-6-astra', 'gpt-6-sol',
        'gpt-6.1-sol', 'gpt-6.2-astra', 'gpt-6.2-sol',
        # GPT-5 Series
        'gpt-5.6', 'gpt-5.6-luna', 'gpt-5.6-terra', 'gpt-5.6-sol',
        'gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.2',
    ],
    # === ANTHROPIC (Claude) ===
    'Anthropic': [
        # Claude 5.5 / 5 & Fable 5.x
        'claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5',
        'claude-fable-5-2', 'claude-fable-5-1', 'claude-fable-5',
        'claude-sonnet-5', 'claude-opus-5',
        # Claude 4.x
        'claude-opus-4-8', 'claude-opus-4-7', 'claude-opus-4-6',
        'claude-sonnet-4-6', 'claude-haiku-4-5-20251001',
    ],
    # === DEEPSEEK ===
    'DeepSeek': [
        'deepseek-flash', 'deepseek-v4-pro',
    ],
    # === GROQ ===
    # Deliberately omits allam-2-7b (Arabic specialist, 4k ctx) from the offline floor.
    'Groq': [
        'openai/gpt-oss-120b', 'openai/gpt-oss-20b',
        'qwen/qwen3.8-27b',
    ],
    # === XAI (Grok) ===
    'xAI': [
        'grok-4.20-0309-non-reasoning', 'grok-4.3', 'grok-4.7', 'grok-4.6', 'grok-4.5',
        'grok-4.20-0309-reasoning', 'grok-4.20-multi-agent-0309',
    ],
    # === MISTRAL AI ===
    'Mistral': [
        'mistral-large-latest', 'mistral-large-4',
        'mistral-small-latest', 'mistral-small-2603',
        'ministral-14b-latest', 'ministral-8b-latest', 'ministral-3b-latest',
        'codestral-latest', 'codestral-2508',
    ],
    # === PERPLEXITY ===
    # Registry marks sonar* deprecated; registry_source intentionally preserves this list so
    # Perplexity keys keep a non-empty model picker.
    'Perplexity': [
        'sonar', 'sonar-pro', 'sonar-reasoning', 'sonar-reasoning-pro',
        'sonar-deep-research',
        'llama-3.1-sonar-small-128k-online', 'llama-3.1-sonar-large-128k-online',
        'llama-3.1-sonar-huge-128k-online',
    ],
    # === CEREBRAS ===
    'Cerebras': [
        'gpt-oss-120b', 'qwen-3.8-27b',
    ],
    # === SAMBANOVA ===
    'SambaNova': [
        'gemma-4-31B-it', 'gpt-oss-120b', 'MiniMax-M3.1-Flash', 'GLM-5.3',
    ],
    # === TOGETHER ===
    'Together': [
        # Defaults first: no prefix rule knows meta-models/ or zai-org/, so exact-match keeps them here.
        'meta-models/Muse-Glimmer-30B', 'zai-org/GLM-5.3-Flash', 'zai-org/GLM-5.3', 'zai-org/GLM-5.2',
        'Qwen/Qwen4-Max', 'Qwen/Qwen3.8-Flash', 'Qwen/Qwen3.7-Max',
        'Qwen/Qwen3.7-Plus', 'Qwen/Qwen3.6-Plus', 'Qwen/Qwen3.5-9B',
        'deepseek-ai/DeepSeek-V4.1-Flash',
        'moonshotai/Kimi-K3.1-Pro', 'moonshotai/Kimi-K3',
        'MiniMaxAI/MiniMax-M3.1-Flash',
        'nvidia/NVIDIA-Nemotron-3.5-Super-VL',
    ],
    # === SILICONFLOW ===
    'SiliconFlow': [
        'deepseek-ai/DeepSeek-V3', 'deepseek-ai/DeepSeek-V3.2',
        'deepseek-ai/DeepSeek-V4-Flash', 'deepseek-ai/DeepSeek-V4-Pro',
        'deepseek-ai/DeepSeek-V4.1-Flash', 'deepseek-ai/DeepSeek-R1',
        'Qwen/Qwen2.5-72B-Instruct', 'Qwen/Qwen2.5-7B-Instruct',
        'Qwen/Qwen3.8-27B', 'Qwen/Qwen3.6-27B', 'Qwen/Qwen3.5-27B', 'Qwen/Qwen3-32B',
        'Qwen/Qwen3-VL-32B-Instruct', 'Qwen/Qwen3-VL-8B-Instruct',
        'zai-org/GLM-5.3', 'zai-org/GLM-5.3-Flash', 'zai-org/GLM-4.5V',
        'Pro/deepseek-ai/DeepSeek-V3', 'Pro/deepseek-ai/DeepSeek-R1',
    ],
    # === OPENROUTER (aggregator - curated live subset; runtime registry holds ~440 IDs) ===
    # No ':batch' variants: those are async batch endpoints and cannot serve interactive translation.
    'OpenRouter': [
        # OpenAI
        'openai/gpt-6-luna', 'openai/gpt-6.1-terra', 'openai/gpt-4o', 'openai/gpt-4o-mini',
        # Anthropic
        'anthropic/claude-sonnet-5.5', 'anthropic/claude-haiku-5.5', 'anthropic/claude-opus-5.5',
        'anthropic/claude-sonnet-5', 'anthropic/claude-opus-5',
        # Google
        'google/gemini-3.8-flash', 'google/gemini-3.5-flash-lite', 'google/gemini-3-flash-preview',
        # Meta
        'meta-llama/llama-3.3-70b-instruct', 'meta-llama/llama-3.1-70b-instruct',
        'meta-llama/llama-4-maverick',
        # Mistral
        'mistralai/mistral-large', 'mistralai/mistral-large-4-0', 'mistralai/mistral-small-2603',
        # DeepSeek
        'deepseek/deepseek-chat', 'deepseek/deepseek-v4-flash', 'deepseek/deepseek-v4-pro',
        # Qwen
        'qwen/qwen-2.5-72b-instruct', 'qwen/qwen3.8-flash',
        # Free tier - also the OpenRouter entries in DEFAULT_MODELS_BY_PROVIDER, and MUST stay listed
        # here so _identify_provider() matches OpenRouter before the 'google/' -> Together prefix rule.
        'google/gemma-4-31b-it:free', 'nvidia/nemotron-3.5-lightning:free',
    ],
    # === HUGGINGFACE (curated live subset; runtime registry holds ~100 IDs) ===
    'HuggingFace': [
        'Qwen/Qwen2.5-72B-Instruct', 'Qwen/Qwen2.5-Coder-32B-Instruct', 'Qwen/Qwen2.5-VL-72B-Instruct',
        'Qwen/Qwen3-32B', 'Qwen/Qwen3-VL-30B-A3B-Instruct', 'Qwen/Qwen3.8-27B',
        'meta-llama/Llama-3.3-70B-Instruct', 'meta-llama/Llama-3.2-3B-Instruct',
        'meta-llama/Llama-3.1-8B-Instruct', 'meta-llama/Llama-3.1-70B-Instruct',
        'meta-llama/Llama-4-Maverick-17B-128E-Instruct',
        'mistralai/Mistral-Large-4-1T-A52B',
        'google/gemma-2-27b-it', 'google/gemma-3-27b-it', 'google/gemma-4-31B-it',
        'deepseek-ai/DeepSeek-V3', 'deepseek-ai/DeepSeek-V4-Flash', 'deepseek-ai/DeepSeek-R1-0528',
        'zai-org/GLM-5.3', 'zai-org/GLM-5.3-Flash',
    ],
}

# API Key Patterns for provider detection
# Values match PROVIDERS_LIST exactly (Title Case)
API_KEY_PATTERNS = {
    'gsk_': 'Groq',           # Groq keys start with gsk_
    'sk-ant-': 'Anthropic',   # Anthropic keys start with sk-ant-
    'xai-': 'xAI',            # xAI keys start with xai-
    'sk-or-v1-': 'OpenRouter', # OpenRouter keys
    'pplx-': 'Perplexity',    # Perplexity keys
    'hf_': 'HuggingFace',     # HuggingFace keys start with hf_
    'csk-': 'Cerebras',       # Cerebras keys start with csk-
    # Google: standard keys start AIza; every key AI Studio has created since 2026-05-28 is an
    # auth key, AQ.Ab... (TODO Task 21). Both, so that neither kind stops counting as Google's.
    'AIza': 'Google',
    'AQ.': 'Google',
    # OpenAI project and service-account keys. Legacy sk-... user keys stay ambiguous.
    'sk-proj-': 'OpenAI',
    'sk-svcacct-': 'OpenAI',
    # Note: bare sk- is used by OpenAI (legacy), DeepSeek, Together, SiliconFlow - need model name
    # SambaNova cannot be added: its keys are bare UUIDs with no prefix to match.
    # A provider missing from this table falls through _detect_provider_from_key()
    # to its 'default to Google' branch, so a Cerebras user with Provider=Auto and
    # a blank model field was being handed gemini-2.5-flash and sending it to
    # api.cerebras.ai (measured 2026-09-08).
}

# ============== VISION MODELS ==============
# Offline tier-3 floor snapshot synced against https://models.masaru.uk (input=image) on 2026-11-28.
# Supports wildcards (*). Naming heuristics ('vision', 'pixtral') also apply in multimodal.py.
VISION_MODELS = {
    'google': ['gemini-*', 'gemma-4-*'],
    'openai': ['gpt-6*', 'gpt-5*', 'gpt-4.1*', 'gpt-4o', 'gpt-4o-*', 'gpt-4-vision-*'],
    'anthropic': ['claude-3-*', 'claude-3.5-*', 'claude-3-5-*', 'claude-*-4-*',
                  'claude-*-5', 'claude-*-5-*', 'claude-fable-*'],
    'deepseek': ['deepseek-flash', 'deepseek-v4-flash*'],
    # Groq: qwen3 accepts images; gpt-oss-* rejects them ('content must be a string').
    'groq': ['qwen/qwen3*'],
    'xai': ['grok-4*', 'grok-*-vision*', 'grok-vision-*'],
    'mistral': ['mistral-large-*', 'mistral-medium-*', 'mistral-small-*',
                'ministral-*', 'magistral-*', 'pixtral-*'],
    'perplexity': ['sonar-pro', 'sonar-reasoning-pro'],
    'cerebras': ['qwen-3.8-27b'],
    'sambanova': ['gemma-4-31B-it', 'MiniMax-M3.1-Flash'],
    'together': ['meta-models/Muse-*', 'zai-org/GLM-5.3-Flash', 'Qwen/Qwen4-*', 'Qwen/Qwen3.8-*',
                 'Qwen/Qwen3.5-*', 'deepseek-ai/DeepSeek-V4.1-Flash*', 'moonshotai/Kimi-K3*',
                 'MiniMaxAI/MiniMax-M3.1-Flash', '*-VL*', '*-Vision-*', '*-vision-*'],
    'siliconflow': ['Qwen/Qwen3-VL-*', 'Qwen/Qwen3.5-*', 'Qwen/Qwen3.6-*', 'Qwen/Qwen3.8-*',
                    'Qwen/Qwen2-VL-*', 'deepseek-ai/DeepSeek-V4.1-Flash', 'zai-org/GLM-4.5V', '*-VL-*'],
    'openrouter': ['*'],  # Runtime registry supplies explicit IDs; offline relies on heuristics
    'huggingface': ['Qwen/Qwen*-VL-*', 'Qwen/Qwen3.5-*', 'Qwen/Qwen3.6-*', 'Qwen/Qwen3.8-*',
                    'google/gemma-3-*', 'google/gemma-4-*', 'MiniMaxAI/MiniMax-M3*', '*-VL-*'],
}
