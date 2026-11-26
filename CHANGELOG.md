# Changelog

All notable user-facing changes to **CrossTrans 2** are documented in this file.

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
  - Light, Dark, Pastel, and *Follow Windows* themes, adjustable text scaling, high-DPI support, and **English / Japanese (日本語)** interface languages.
  - Per-user Windows DPAPI encryption for API keys and optional GitHub update tokens, with Windows Hello verification before displaying saved API keys.
  - Public GitHub Releases auto-update with SHA-256 verification and privacy-safe Diagnostics / bug report export.
