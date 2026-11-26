"""
Dictionary mode for CrossTrans - the one implementation of it.

Both Dictionary buttons open it: the main window's (on the input box text) and the
quick-translate popup's (on the popup's original text, beside the popup). It owns
the whole flow - NLP availability, language detection, the "Select Source
Language" dialog, the word window (WordButtonFrame + CustomWordBoxesFrame) and the
lookup. The result window is QuickTranslateManager.show_dictionary_result().

Until 2026-10-15 the popup carried a second copy of this flow (DEFERRED D13). The
copies had drifted: the popup's dialogs used the safe topmost pattern while these
used grab_set() (D15), and a failed lookup raised NameError in both instead of
showing its message (D14).
"""
import tkinter as tk
from tkinter import BOTH, X
import logging
from typing import Optional, Callable, Tuple

from src.core import lang_id
from src.core.dictionary_prompt import dictionary_source_language
from src.core.nlp_manager import AMBIGUOUS_CJK_CONFIDENCE, nlp_manager
from src.utils.ui_helpers import filter_dictionary_words
from src.ui import components as ui
from src.ui.settings.background import BackgroundRunner
from src.ui.theme import engine
from src.ui.translator_layout import window_size

# (x, y, width, height) of a window, or (left, top, right, bottom) of a work area.
Rect = Tuple[int, int, int, int]

# Detection must be at least this sure for the word window to open without asking.
DETECTION_THRESHOLD = 0.7
# Below this the detected language is not even offered as the dialog's default.
SUGGESTION_FLOOR = 0.3

# The word window: normal and expanded size, the distance it keeps from the edges
# of the work area, and its gap from the quick-translate popup it opens beside.
WORD_WINDOW_SIZE = (760, 560)
WORD_WINDOW_EXPANDED_SIZE = (1000, 700)
WORD_WINDOW_MARGIN = 10
WORD_WINDOW_ANCHOR_GAP = 10

# Taskbar allowance when the monitor's work area cannot be read.
TASKBAR_FALLBACK_PX = 50


def word_window_geometry(size: Tuple[int, int], work_area: Rect,
                         anchor: Optional[Rect] = None) -> Rect:
    """Where the word window goes, as (width, height, x, y).

    Beside the quick-translate popup when there is one (`anchor` = its x, y,
    width and height): below it, above it when there is no room below, and
    pinned to the bottom of the work area when neither fits. Without an anchor,
    centred in the work area. Always shrunk to fit inside the work area.
    """
    left, top, right, bottom = work_area
    margin = WORD_WINDOW_MARGIN
    width = min(size[0], right - left - 2 * margin)
    height = min(size[1], bottom - top - 2 * margin)

    if anchor:
        anchor_x, anchor_y, _, anchor_h = anchor
        x = anchor_x
        y = anchor_y + anchor_h + WORD_WINDOW_ANCHOR_GAP
        if y + height > bottom - margin:
            y = anchor_y - height - WORD_WINDOW_ANCHOR_GAP
            if y < top + margin:
                y = bottom - margin - height
    else:
        x = left + (right - left - width) // 2
        y = top + (bottom - top - height) // 2

    x = max(left + margin, min(x, right - margin - width))
    y = max(top + margin, min(y, bottom - margin - height))
    return width, height, x, y


def dialog_geometry(size: Tuple[int, int], pointer: Tuple[int, int],
                    work_area: Rect) -> Rect:
    """A dialog centred on the mouse pointer and kept inside the work area:
    (width, height, x, y)."""
    width, height = size
    left, top, right, bottom = work_area
    x = max(left, min(pointer[0] - width // 2, right - width))
    y = max(top, min(pointer[1] - height // 2, bottom - height))
    return width, height, x, y


class DictionaryPopup:
    """Dictionary mode: pick words from a text, look them up, show the entries.

    Manages the complete flow for both Dictionary buttons:
    - NLP availability checking
    - Language detection and selection
    - Word button window for selection
    - Dictionary lookup execution
    """

    def __init__(self, root, config, translation_service, toast_manager, quick_translate_manager):
        """Initialize the dictionary popup manager.

        Args:
            root: Root Tk window
            config: Config object
            translation_service: TranslationService for lookups
            toast_manager: ToastManager for notifications
            quick_translate_manager: QuickTranslateManager for showing results
        """
        self.root = root
        self.config = config
        self.translation_service = translation_service
        self.toast = toast_manager
        self.quick_translate_manager = quick_translate_manager

        # State
        self._dict_btn_enabled = False

        # Callbacks
        self._on_show_settings_tab: Optional[Callable] = None
        self._get_selected_language: Optional[Callable] = None
        self._on_source_chosen: Optional[Callable[[str], None]] = None

    def _furigana_enabled(self) -> bool:
        """Whether word chips should carry readings (Settings toggle)."""
        return bool(self.config and self.config.get_furigana_enabled())

    def configure_callbacks(self,
                           on_show_settings_tab: Optional[Callable] = None,
                           get_selected_language: Optional[Callable] = None,
                           on_source_chosen: Optional[Callable[[str], None]] = None,
                           on_selection=None, on_result=None):
        """Configure callback functions.

        Args:
            on_show_settings_tab: Callback to open settings and navigate to a tab
            get_selected_language: Callback to get the main window's target
                language - the target when the caller names none
            on_source_chosen: Called with the text once the user has said which
                language its kanji are, so the main window can read it (K3)
        """
        self._on_show_settings_tab = on_show_settings_tab
        self._get_selected_language = get_selected_language
        self._on_source_chosen = on_source_chosen
        self._embedded_selection = on_selection
        self._embedded_result = on_result

    def check_nlp_availability(self) -> bool:
        """Check if NLP is available for dictionary mode.

        Returns:
            True if at least one NLP language pack is installed
        """
        return nlp_manager.is_any_installed()

    def update_button_state(self, button: tk.Button) -> None:
        """Update dictionary button state based on NLP availability.

        Button keeps same visual appearance (reddish-brown color) whether
        enabled or disabled. Only interaction changes.

        Args:
            button: The dictionary button widget
        """
        if not button:
            return

        self._dict_btn_enabled = nlp_manager.is_any_installed()

        try:
            if self._dict_btn_enabled:
                button.configure(cursor='hand2')
            else:
                button.configure(cursor='arrow')
            # Unbind any previous hover handlers
            button.unbind('<Enter>')
            button.unbind('<Leave>')
        except tk.TclError:
            pass  # Widget destroyed

    def is_enabled(self) -> bool:
        """Check if dictionary button is enabled."""
        return self._dict_btn_enabled

    def open_from_text(self, original_text: str, *, target_language: Optional[str] = None,
                       anchor: Optional[tk.Misc] = None) -> None:
        """Open Dictionary mode on a text.

        Args:
            original_text: Text to analyze and tokenize
            target_language: Language to look the words up into; None = the main
                window's selected language
            anchor: The window to open beside (the quick-translate popup), or
                None to centre the word window under the mouse
        """
        # Checked live: a pack installed since the button was drawn counts.
        if not nlp_manager.is_any_installed():
            self._show_nlp_not_installed_dialog()
            return

        if not original_text:
            self.toast.show_warning("No text to analyze")
            return

        target_language = target_language or self._selected_language()

        # Detect language
        detected_lang, confidence = nlp_manager.detect_language(original_text)

        # Kanji that prove neither Japanese nor Chinese: the model's verdict for
        # this text, else the user (Decision 22).
        if detected_lang == "Unknown" and confidence >= AMBIGUOUS_CJK_CONFIDENCE:
            self._open_ambiguous_kanji(original_text, target_language=target_language,
                                       anchor=anchor)
            return

        # Check if detection is confident and NLP is installed for that language
        if confidence >= DETECTION_THRESHOLD and nlp_manager.is_installed(detected_lang):
            # Auto-proceed with detected language
            self._open_with_language(original_text, detected_lang,
                                     target_language=target_language, anchor=anchor)
        else:
            # Determine if language was detected but not installed
            detected_but_not_installed = (
                confidence >= DETECTION_THRESHOLD and
                detected_lang and
                not nlp_manager.is_installed(detected_lang)
            )
            # Show language selection dialog with context
            self._show_language_selection_dialog(
                original_text,
                detected_lang if confidence > SUGGESTION_FLOOR else None,
                detected_but_not_installed=detected_but_not_installed,
                target_language=target_language,
                anchor=anchor
            )

    def _open_ambiguous_kanji(self, text: str, *, target_language: str,
                              anchor: Optional[tk.Misc]) -> None:
        """Kanji-only text: open in the language a model reported for it, or ask.

        The verdict comes free when the text was translated - the popup's text
        always was, the main window's input when the user pressed Translate (J4).
        Untranslated main-window input goes straight to the user: one click,
        where an identification call would cost a request and, measured on a
        word alone, comes back confidently wrong 2 times in 10 and unsure 3-4
        (Decision 26).
        """
        verdict = self._model_verdict(text)
        if verdict:
            pack = self._installed_pack(verdict)
            if pack:
                logging.info(f"[Dictionary] open language={pack} via=model-verdict")
                self._open_with_language(text, pack, trusted=True,
                                         target_language=target_language, anchor=anchor)
                return
            # The model named a language whose pack is missing: offer to install it.
            self._show_language_selection_dialog(text, verdict, detected_but_not_installed=True,
                                                 target_language=target_language, anchor=anchor)
            return
        logging.info("[Dictionary] open language=ask via=kanji-only")
        self._show_language_selection_dialog(text, None, kanji_only=True,
                                             target_language=target_language, anchor=anchor)

    def _remember_choice(self, text: str, language: str) -> None:
        """The answer to "Kanji-only text: Japanese or Chinese?" is the text's
        verdict from now on (K3): the next Dictionary opens without asking, and
        the main window's Reading pane can read it. Another pack picked from
        the list says nothing about the kanji and is not kept."""
        remember = getattr(self.translation_service, 'remember_user_source', None)
        if remember and remember(text, language) and self._on_source_chosen:
            self._on_source_chosen(text)

    def _model_verdict(self, text: str) -> Optional[str]:
        """The Japanese / Chinese verdict a model reported - or the user gave, in
        this dialog or a row (K3) - for exactly this text."""
        finder = getattr(self.translation_service, 'source_verdict', None)
        verdict = lang_id.normalize_language(finder(text)) if finder else None
        return verdict if verdict == lang_id.JAPANESE or verdict in lang_id.CHINESE else None

    @staticmethod
    def _installed_pack(language: str) -> Optional[str]:
        """The NLP pack to tokenize `language` with: its own, or for Chinese the
        other variant's (both segment hanzi the same way), else None."""
        if nlp_manager.is_installed(language):
            return language
        if language in lang_id.CHINESE:
            other = next(c for c in lang_id.CHINESE if c != language)
            if nlp_manager.is_installed(other):
                return other
        return None

    def _selected_language(self) -> str:
        """The main window's target language."""
        return self._get_selected_language() if self._get_selected_language else "Vietnamese"

    def _pointer(self) -> Tuple[int, int]:
        return self.root.winfo_pointerx(), self.root.winfo_pointery()

    def _work_area(self, x: int, y: int) -> Rect:
        """Work area (left, top, right, bottom) of the monitor holding (x, y)."""
        from src.ui.quick_translate import get_monitor_work_area
        work_area = get_monitor_work_area(x, y)
        if work_area:
            return work_area
        return (0, 0, self.root.winfo_screenwidth(),
                self.root.winfo_screenheight() - TASKBAR_FALLBACK_PX)

    @staticmethod
    def _window_rect(window: Optional[tk.Misc]) -> Optional[Rect]:
        """(x, y, width, height) of a live window, or None."""
        if window is None:
            return None
        try:
            if not window.winfo_exists():
                return None
            return (window.winfo_x(), window.winfo_y(),
                    window.winfo_width(), window.winfo_height())
        except tk.TclError:
            return None

    def _show_nlp_not_installed_dialog(self):
        dialog = ui.Dialog(
            self.root, 'No Language Pack Installed',
            'Add a language pack to pick words and build phrases in Dictionary.', icon='book')
        dialog.add_button('Open Dictionary Settings', command=self._open_pack_settings, default=True)
        dialog.add_button('Cancel')
        self.language_dialog = dialog
        return dialog.show()

    def _open_pack_settings(self):
        if self._on_show_settings_tab:
            self._on_show_settings_tab('Dictionary')

    def _show_language_selection_dialog(self, original_text: str, suggested_lang: str = None,
                                        detected_but_not_installed: bool = False, *,
                                        kanji_only: bool = False,
                                        target_language: Optional[str] = None,
                                        anchor: Optional[tk.Misc] = None):
        installed = nlp_manager.get_installed_languages()
        if not installed:
            return self._show_nlp_not_installed_dialog()
        if kanji_only:
            first = [lang for lang in (lang_id.JAPANESE,) + lang_id.CHINESE if lang in installed]
            installed = first + [lang for lang in installed if lang not in first]
        if detected_but_not_installed and suggested_lang:
            message = f'The {suggested_lang} language pack is missing. Add it in Settings, or choose an installed language below.'
        elif kanji_only:
            message = 'Kanji-only text: Japanese or Chinese? The meaning can differ.'
        else:
            message = 'Cannot detect language. Choose the source language for these words.'
        dialog = ui.Dialog(self.root, 'Select Source Language', message, icon='translate')
        px = ui.get().px
        ui.Label(dialog.body, 'Source language', kind='strong').pack(anchor='w', pady=(px(16), px(8)))
        value = suggested_lang if suggested_lang in installed else installed[0]
        dialog.language_picker = ui.Dropdown(
            dialog.body, [(lang, lang) for lang in installed], value,
            label='Source language for Dictionary')
        dialog.language_picker.pack(fill=X)
        ui.Label(dialog.body, 'Only installed language packs are shown.', kind='muted').pack(
            anchor='w', pady=(px(8), 0))

        def settings():
            dialog.close()
            self._open_pack_settings()

        install_text = f'Install {suggested_lang} pack' if detected_but_not_installed else 'Add languages'
        ui.Button(dialog.body, install_text, command=settings, kind='subtle', icon='gear', icon_side='left').pack(
            anchor='w', pady=(px(8), 0))

        def confirm():
            selected = dialog.language_picker.value
            dialog.close()
            if kanji_only:
                self._remember_choice(original_text, selected)
            self._open_with_language(original_text, selected, trusted=True,
                                     target_language=target_language, anchor=anchor)

        dialog.add_button('Continue', command=confirm, default=True, close=False)
        dialog.add_button('Cancel')
        self.language_dialog = dialog
        return dialog.show()

    def _window_title(self, language: str, target_language: str) -> str:
        """The word-selection language pair."""
        return f"Dictionary ({language} → {target_language})"

    def _open_with_language(self, original: str, language: str,
                            trusted: bool = False, *,
                            target_language: Optional[str] = None,
                            anchor: Optional[tk.Misc] = None) -> None:
        """Open the word window with specified language for NLP tokenization.

        Args:
            original: Text to tokenize and display
            language: Source language for NLP processing
            trusted: True when the user picked `language` in the selection
                dialog or a model's verdict chose it. Then - or when detection
                proved Japanese or Chinese - it is stated to the lookup prompt
                as fact (dictionary_source_language).
            target_language: Language to look the words up into; None = the
                main window's selected language
            anchor: The window to open beside (the quick-translate popup), or
                None to centre the word window under the mouse
        """
        from src.ui.dictionary_mode import WordButtonFrame
        from src.ui.custom_word_boxes import CustomWordBoxesFrame

        target_language = target_language or self._selected_language()
        if getattr(self, '_embedded_selection', None) is not None:
            return self._embedded_selection(original, language, trusted, target_language)

        # Create popup window (ADDITIONAL - never replaces the quick translate popup)
        dict_popup = tk.Toplevel(self.root)
        dict_popup.title(self._window_title(language, target_language))
        self.ctx = ui.get()
        px = self.ctx.px
        dict_popup.withdraw()
        engine.attach(dict_popup)

        # Beside the popup when there is one, else centred under the mouse,
        # inside the work area of that monitor (excludes the taskbar).
        anchor_rect = self._window_rect(anchor)
        if anchor_rect:
            work_area = self._work_area(anchor_rect[0] + anchor_rect[2] // 2,
                                        anchor_rect[1] + anchor_rect[3] // 2)
        else:
            work_area = self._work_area(*self._pointer())
        size = window_size(work_area, scale=self.ctx.scale, default=WORD_WINDOW_SIZE, minimum=(480, 480))
        width, height, x, y = word_window_geometry((size.width, size.height), work_area, anchor_rect)
        dict_popup.sizes_from_work_area = True
        normal_geometry = f"{width}x{height}+{x}+{y}"
        dict_popup.geometry(normal_geometry)

        dict_popup.minsize(min(px(480), width), min(px(480), height))
        compact = height < px(480)
        compact_guidance = height / (self.ctx.scale * self.ctx.text_scale) < 280
        main_frame = ui.Frame(dict_popup, padding=px(8 if compact else 16))
        main_frame.pack(fill=BOTH, expand=True)
        header = ui.Frame(main_frame)
        header.pack(fill=X, pady=(0, px(4 if compact else 12)))
        language_pair = f'{language} → {target_language}'
        pair_label = ui.Label(header, language_pair, kind='caption') if compact else ui.Chip(header, language_pair)
        pair_label.pack(anchor='w', pady=(0, 0 if compact else px(8)))
        title = ui.Label(header, 'Pick words to look up', kind='strong' if compact else 'subtitle')
        title.pack(anchor='w')
        guidance = 'Click words to select them. Shift-click selects a range.'
        ui.Tooltip(title, language_pair + '\n' + guidance)
        guidance_label = ui.Label(header, guidance, kind='caption' if compact else 'muted')
        guidance_label.pack(anchor='w', pady=(px(4), 0))
        # Track expanded state for toggle
        expanded_state = [False]

        # Expand/Collapse function
        def expand_dictionary():
            if expanded_state[0]:
                # Collapse: restore original size
                dict_popup.geometry(normal_geometry)
                dict_frame.expand_btn.set_on(False)
            else:
                # Expand: larger, centred on the same work area
                size = window_size(work_area, scale=self.ctx.scale, default=WORD_WINDOW_EXPANDED_SIZE,
                                   minimum=(480, 480))
                w, h, cx, cy = word_window_geometry((size.width, size.height), work_area)
                dict_popup.geometry(f"{w}x{h}+{cx}+{cy}")
                dict_frame.expand_btn.set_on(True)
            expanded_state[0] = not expanded_state[0]

        # What the lookup prompt is told the words are, or None to let the model decide.
        lookup_source = dictionary_source_language(language, trusted)

        # Word button frame with language for NLP tokenization
        def on_lookup(selected_words):
            all_words = list(selected_words)
            all_words.extend(custom_boxes.get_all_phrases())
            if all_words:
                self._do_lookup(all_words, target_language,
                                source_language=lookup_source, context_text=original, origin=dict_frame)

        def on_no_selection():
            box_phrases = custom_boxes.get_all_phrases()
            if box_phrases:
                dict_frame.start_lookup_animation()
                self._do_lookup(box_phrases, target_language,
                                source_language=lookup_source, context_text=original, origin=dict_frame)
            else:
                self.toast.show_warning_with_shake("Please select a word or enter a custom phrase")

        dict_frame = WordButtonFrame(
            main_frame,
            original,
            on_selection_change=lambda t: None,
            on_lookup=on_lookup,
            on_expand=expand_dictionary,
            on_no_selection=on_no_selection,
            language=language,  # Pass language for NLP tokenization
            furigana_enabled=self._furigana_enabled()
        )
        dict_frame.set_exit_callback(dict_popup.destroy)
        dict_frame.pack(fill=BOTH, expand=True)

        # Custom word boxes (between text area and action buttons)
        custom_boxes = CustomWordBoxesFrame(
            dict_frame.frame, language=language,
            furigana_enabled=self._furigana_enabled(), compact=compact, compact_guidance=compact_guidance)
        dict_frame.insert_custom_widget(custom_boxes.frame)
        dict_frame.set_drop_target(custom_boxes)
        density = [None]

        def fit_guidance(event=None):
            if event is not None and event.widget not in (dict_popup, header):
                return
            available = max(px(120), header.winfo_width() - px(4))
            if str(guidance_label.cget('wraplength')) != str(available):
                guidance_label.configure(wraplength=available)
            short = dict_popup.winfo_height() / (self.ctx.scale * self.ctx.text_scale) < 280
            if short == density[0]:
                return
            density[0] = short
            custom_boxes.set_compact_guidance(short)
            if short:
                pair_label.pack_forget()
                guidance_label.pack_forget()
            else:
                pair_label.pack(anchor='w', before=title, pady=(0, 0 if compact else px(8)))
                guidance_label.pack(anchor='w', pady=(px(4), 0))

        dict_popup.bind('<Configure>', fit_guidance, add='+')
        unsubscribe_density = self.ctx.manager.subscribe(lambda manager: fit_guidance())

        # Store reference for animation control (so stop_dictionary_animation() works)
        self.quick_translate_manager._dict_popup_frame = dict_frame

        def closed(event):
            if event.widget is dict_popup:
                unsubscribe_density()
                if self.quick_translate_manager._dict_popup_frame is dict_frame:
                    self.quick_translate_manager._dict_popup_frame = None

        dict_popup.bind('<Destroy>', closed, add='+')
        dict_popup.bind('<Escape>', lambda e: dict_popup.destroy())
        dict_popup.bind('<Control-Return>', lambda e: dict_frame._trigger_lookup() or 'break')
        dict_popup.deiconify()
        dict_popup.lift()
        dict_popup.focus_force()
        self.word_window, self.word_frame, self.custom_boxes = dict_popup, dict_frame, custom_boxes
        return dict_popup

    def _do_lookup(self, words: list, target_lang: str,
                   source_language: Optional[str] = None,
                   context_text: Optional[str] = None, origin=None) -> None:
        """Perform dictionary lookup for selected words.

        Uses batch lookup (single API call) for efficiency.

        Args:
            words: List of words to look up
            target_lang: Target language for translation
            source_language: Language to state in the prompt, or None to let the
                model decide
            context_text: The text the words were selected from
        """
        if not words:
            return

        # Filter out punctuation, symbols, and special characters
        words = filter_dictionary_words(words)
        if not words:
            self._stop_lookup(origin)
            self.toast.show_info("No valid words to look up (only punctuation/symbols)")
            return

        # Show loading toast
        display_text = ", ".join(words[:3]) + ("..." if len(words) > 3 else "")
        self.toast.show_info(f"Looking up {len(words)} word(s): {display_text}")

        # The worker only returns data. BackgroundRunner delivers it on Tk's thread.
        if not hasattr(self, '_runner'):
            owner = getattr(origin, 'frame', None)
            self._runner = BackgroundRunner(self.root if owner is None else owner)
            self._lookup_serial = 0
        self._lookup_serial += 1

        def work(progress, cancelled):
            result = self.translation_service.dictionary_lookup(
                words, target_lang, source_language=source_language, context=context_text)
            return result, self.translation_service.get_trial_info()

        def done(value, error):
            if error:
                self._lookup_failed(f'Lookup failed: {error}', origin)
            else:
                result, trial_info = value
                if getattr(self, '_embedded_result', None) is not None:
                    self._stop_lookup(origin)
                    self._embedded_result(result, target_lang, words, source_language, context_text)
                else:
                    from src.core.result_status import is_error_text
                    if is_error_text(result):
                        from src.ui import popup_errors
                        self._stop_lookup(origin)
                        help_ = popup_errors.explain(result)
                        if (help_.fix == popup_errors.FIX_SETTINGS
                                and self._on_show_settings_tab
                                and hasattr(self.toast, 'show_warning_with_action')):
                            self.toast.show_warning_with_action(
                                help_.message, "Open API keys",
                                lambda: self._on_show_settings_tab("API Key"))
                        else:
                            self.toast.show_error(help_.message)
                    else:
                        self._show_result(result, target_lang, trial_info, words, source_language, origin)

        self._runner.start(f'dictionary-{self._lookup_serial}', work, on_done=done)

    def _stop_lookup(self, origin=None):
        try:
            if origin is None:
                self.quick_translate_manager.stop_dictionary_animation()
            else:
                origin.stop_lookup_animation()
        except tk.TclError:
            pass

    def _lookup_failed(self, message: str, origin=None) -> None:
        """Stop the Lookup button's animation and say what went wrong."""
        self._stop_lookup(origin)
        self.toast.show_error(message)

    def _show_result(self, result: str, target_lang: str, trial_info: dict = None,
                     looked_up_words: list = None, source_language: Optional[str] = None,
                     origin=None) -> None:
        """Show dictionary lookup result in SEPARATE window.

        Args:
            result: Dictionary lookup result text
            target_lang: Target language
            trial_info: Trial mode info
            looked_up_words: Words that were looked up
            source_language: The language the prompt was told the words are in,
                or None - the result window then trusts each entry's field 2
        """
        # Use quick translate manager to show result in SEPARATE dictionary window
        self.quick_translate_manager.capture_mouse_position()
        kwargs = {'source_lang': source_language}
        if origin is not None:
            kwargs['animation_frame'] = origin
        self.quick_translate_manager.show_dictionary_result(result, target_lang, trial_info,
                                                            looked_up_words, **kwargs)
