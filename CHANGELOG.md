# Changelog

All notable user-facing changes to **CrossTrans 2** are documented in this file.

## [Unreleased]

### Phase 62 - Model registry as the primary source of the model table (2026-11-27)

CrossTrans2's side of the model registry's legacy migration (P5, Decision 110). The shared registry at
`https://models.masaru.uk` (schema v1) is now asked first for the providers, their model ids, key prefixes, vision
models and chat URLs; the Worker's `/v1/config`, the cache file and the hardcoded tables follow, unchanged. Nothing
downstream was touched: the registry's catalog is rebuilt as the same v2 payload and goes through the existing
validation and cache file. A registry that answers means the Worker is not contacted at all.

**Added**
- `src/core/model_registry_client.py`: the registry's Python client (stdlib only), vendored unmodified except for a three-line
  header comment. It sets its own `User-Agent`: Cloudflare answers urllib's default `Python-urllib/x` with HTTP 403, error 1010.
- `src/core/registry_source.py`: `registry_to_config_v2(catalog, base)` (the adapter), `build_key_patterns()`,
  `build_api_urls()`, `fetch_registry_config(cache_dir, base_url, base)` (client wrapper, never raises) and
  `RegistryDataError`. Mapping per the registry's INTEGRATION.md section 5: display-name table, chat ids of `active` and
  `preview` models, vision ids, `(openai_compatible_base_url or base_url) + /chat/completions` for the 12 providers the
  Worker lists. `default_models_by_provider` and `trial_daily_limit` have no registry equivalent and come from the table
  the app holds (hardcoded defaults when none; `trial_daily_limit` is left out rather than invented).
- Guard against bad data: fewer than 5 providers with chat models, fewer than 5 usable key prefixes, any of the 12 chat
  URLs missing or not `https://`, no vision model, a wrong or missing `schema_version` all raise `RegistryDataError`, and the
  app falls through to the Worker as it did before.
- `src/constants.py`: `MODEL_REGISTRY_URL`, `MODEL_REGISTRY_ENABLED` (on; `CROSSTRANS_MODEL_REGISTRY=0|false|no|off` turns it off
  and only the Worker is asked) and `_env_switch()`.
- `src/core/remote_config.py`: `_fetch_table()` (registry, else Worker), `_fetch_registry()`, `_registry_base()`,
  `REGISTRY_CACHE_DIRNAME`, `_save_cache(data, source='remote')` and `config_source == 'registry'`; `clear_cache()` also
  removes the registry client's copy. The client's cache is `%APPDATA%\CrossTrans2\model_registry\catalog-v1.json`, never the temp
  folder, and no snapshot is bundled (the hardcoded tier stays the offline floor).
- `CrossTrans.spec` (`hiddenimports`) and `tools/verify_ui_bundle.py` (`ESSENTIAL_MODULES`): both new modules. `remote_config`
  imports them inside a function and swallows an ImportError, so a bundle without them would have fallen back to the Worker
  without anyone noticing. No data files changed.
- `tests/test_registry_source.py` (+152), `tests/test_remote_config_registry.py` (+44), `tests/registry_support.py` (a loopback
  server standing in for the registry and the Worker), `tests/fixtures/registry_catalog_trimmed.json` (the real catalog cut to
  89 models, at most 8 per provider) and `tests/fixtures/legacy_config_v2.json` (the Worker's real payload of 2026-11-27).

**Changed**
- Key prefixes: the registry lists the ambiguous bare `sk-` for OpenAI, DeepSeek and SiliconFlow. A prefix is dropped when
  several providers list it or when it begins another provider's prefix, and the rest is ordered longest first. With today's
  catalog the result is exactly the app's own 11 prefixes, so Decision 106 (an unplaceable key is refused, never sent to
  Google) and `_claimant_for_key()` behave as before.
- Vision capability includes deprecated chat models that read images: the `gemini-*` pattern is gone, and a key saved with
  `gemini-2.5-flash` must stay vision-capable. Measured on the 832 ids of the Worker's lists: 119 stop answering vision (OpenAI 65,
  Anthropic 36, xAI 5, Mistral 8, DeepSeek 4, SiliconFlow 1), every one an id the registry does not list at all (retired, dated or
  `:batch` variants); none that the registry knows. 338 start answering it, 316 of them OpenRouter models (its `*` was skipped by the
  app). A saved card's own `vision_capable` flag still overrides the table.
- A provider for which the registry lists no `active` or `preview` chat model keeps the list of the held table. Today that
  is Perplexity: the registry knows its four sonar models only as `deprecated`, while the app's own defaults name
  `sonar` and `sonar-pro`.
- Every fetch asks the registry (`force_refresh`, a conditional request): when to fetch stays the app's decision (start-up
  after a day, then every 6 hours, Decision 103). Only an answer from the network counts; the client's stale-cache answer is
  refused, so the Worker is tried next instead of old data being stamped as new.
- The model table now differs in content (counts of model ids, legacy -> registry): Google 22 -> 27, OpenAI 120 -> 24,
  Anthropic 50 -> 13 (+1 deprecated), DeepSeek 24 -> 2, Groq 4 -> 4, xAI 18 -> 7, Mistral 39 -> 17, Perplexity 8 -> 8 (kept),
  Cerebras 7 -> 2, SambaNova 12 -> 4, Together 17 -> 24, SiliconFlow 17 -> 43, OpenRouter 477 -> 441, HuggingFace 17 -> 105.
  Ids that two providers list went from 20 to 78 (HuggingFace and OpenRouter share most); the key tie-break of Decision 12
  decides them. Providers, key prefixes, chat URLs and default models are identical to the Worker's.

**Fixed during implementation**
- A provider's `key_prefixes` given as a string instead of a list was read character by character (`'xai-'` became four
  one-letter prefixes). Found by a test of junk input; only lists and tuples count now.

**Tests**: 6,107 -> 6,303 collected (+196: 152 in `tests/test_registry_source.py`, 44 in `tests/test_remote_config_registry.py`; all
pass). 21 mutations of the new code (ambiguous prefixes kept, stale cache accepted, Worker always asked, switch ignored,
snapshot passed to the client, ...) were each caught by a test. Full suite on a copy of the working tree on this machine:
4,165 passed, 577 failed, 1,434 errors, 127 skipped. The machine's ttkbootstrap lacks its themes (`TclError: 'bootstrap-light' is not a
valid theme`, `bootstrap.ttf` missing), and a clean export of HEAD gives 3,967 passed, 577 failed, 1,434 errors, 129 skipped: the same
failing tests, none new. The 2 extra passes are tests that skip in the export for lack of `.git` and the local `cloudflare_worker.js`.
`check_test_counts.py --min-total 6107 --max-skipped 28` therefore fails here for that reason alone and has to be run on CI.
`ruff check src tools tests`: 0 errors.

**Commit**: `<uncommitted>`

## [2.0.0] - 2026-11-27

Initial public release of **CrossTrans 2** for Windows 10/11.

### Highlights
- **Universal Quick Translate (`Win + Alt + V/E/J/C`)**
  - Instant popup near your cursor with one-click **Copy**, **Replace** (with diff preview or immediate paste), **Dictionary**, custom re-translation instruction, **Linked Reading**, and **Open Translator**.
  - Resilient 4-step text capture pipeline designed for browsers, PDFs, Office apps (including Microsoft Excel cells and Formula Bar), and desktop editors, plus `Ctrl + C` fallback support.
  - Supports 104 languages and up to 4 custom additional language shortcuts.
- **Screenshot OCR & Translation (`Win + Alt + S`)**
  - Multi-monitor region capture overlay that extracts and translates on-screen text via vision-capable AI models.
- **Audio & Video Translation (`Win + Alt + A`)**
  - Live recording of **PC playback audio** (WASAPI loopback), **Microphone**, or **both** (up to 3 minutes), with optional hold-to-record mode and microphone noise reduction.
  - Upload audio or video files up to 30 minutes directly from the Audio panel or Translator window.
  - Supports **Two-step** (verbatim transcription + translation) and **One-step** direct translation modes, with configurable recording save policy.
- **Full Translator Workspace, Documents & Subtitles**
  - Side-by-side or stacked responsive layout with tone chips (*Formal*, *Casual*, *Concise*, *Natural*) and custom instructions.
  - Drag-and-drop translation for `.txt`, `.md`, `.docx`, `.pdf` (including scanned PDFs), `.srt` subtitles (timings preserved), and images.
  - Local searchable translation history (up to 200 entries) with language-pair filters and an option to pause saving.
- **Japanese & Language-Learning Tools**
  - Built-in **Furigana** ruby readings above Japanese kanji across the Translator, Quick Translate popup, History, and Dictionary.
  - **Interactive Dictionary** with clickable word chips, range selection, phrase builder, and structured entries (meaning, word type, pronunciation, synonyms, antonyms, examples).
  - On-demand **Language Packs** in **Settings → Language packs** for accurate word segmentation in Japanese, Chinese, Korean, Thai, and 20+ languages.
  - **Linked Reading** sentence-level hover highlighting between source text and translation.
- **14 AI Providers, Automatic Model Routing & Failover**
  - Supports Google Gemini, OpenAI, Anthropic Claude, DeepSeek, Groq, xAI, Mistral, Perplexity, Cerebras, SambaNova, Together, SiliconFlow, OpenRouter, and HuggingFace.
  - Drag-to-reorder primary & backup keys, live key testing, capability badges (*Reads images*, *Reads files*, *Reads audio*), and optional key/model rotation.
- **Modern UI, Themes, Localization & Security**
  - **CrossTrans II Visual Identity:** Royal Sapphire Crest branding with crisp multi-DPI icons (16–256 px) across the application executable, installer, shortcuts, window title bars, and Windows system tray.
  - Light, Dark, Pastel, and *Follow Windows* themes, adjustable text scaling, high-DPI support, and **English / Japanese (日本語)** interface languages.
  - Per-user Windows DPAPI encryption for API keys and optional GitHub update tokens, with Windows Hello verification before displaying saved API keys.
  - Public GitHub Releases auto-update with SHA-256 verification and privacy-safe Diagnostics / bug report export.
