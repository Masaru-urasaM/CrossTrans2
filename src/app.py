"""
Main Application for CrossTrans.
"""
import os
import sys
import time
import ctypes
import queue
import logging
import threading
from typing import Optional

import pyperclip
import tkinter as tk
from tkinter import X, LEFT

try:
    import ttkbootstrap as ttk
    HAS_TTKBOOTSTRAP = True
except ImportError:
    from tkinter import ttk  # noqa: F401 - fallback when ttkbootstrap is missing
    HAS_TTKBOOTSTRAP = False

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False
    DND_FILES = None

# windnd for Windows drag-and-drop (works better with Toplevel)
try:
    import windnd  # noqa: F401 - import probe
    HAS_WINDND = True
except ImportError:
    HAS_WINDND = False

import src.core  # noqa: F401
from config import Config
from src.constants import (VERSION, LANGUAGES, FURIGANA_READING_PANE_DEBOUNCE_MS,
                           FURIGANA_READING_PANE_MAX_ROWS)
from src.core.translation import TranslationService
from src.core.api_manager import AIAPIManager
from src.core.hotkey import HotkeyManager
from src.core.drop_handler import DropHandler
from src.ui.settings import SettingsWindow
from src.ui.dialogs import APIErrorDialog
from src.ui.history_dialog import HistoryDialog
from src.ui.toast import ToastManager
from src.ui.quick_translate import QuickTranslateManager, fill_source_row, is_error_text
from src.core import furigana, lang_id, language_prefs
from src.core.source_marker import ROW_ASK, ROW_CHANGE, language_label, source_row
from src.ui.ruby_text import MAX_ANNOTATE_CHARS, RubyText, insert_output
from src.ui.tray import TrayManager, get_resource_path
from src.ui import components, popup_errors
from src.ui.i18n import tr
from src.ui.theme import engine as theme_engine
from src.ui.theme import fonts as type_ramp
from src.ui.theme.manager import ThemeManager, install_manager
from src.utils.updates import AutoUpdater  # noqa: F401 - kept: tests may patch this module attribute
from src.core.file_processor import FileProcessor
from src.ui.attachments import AttachmentArea
from src.core.multimodal import MultimodalProcessor
from src.core.screenshot import ScreenshotCapture, SHOT_PREFIX, sweep_old_shots
from src.utils.log_events import log_event
from src.ui.expanded_window import ExpandedTranslationWindow
from src.core.update_ui_manager import UpdateUIManager
from src.ui.screenshot_handler import ScreenshotHandler
from src.ui.dictionary_popup import DictionaryPopup
from src.ui.main_linked_reading import MainLinkedReading
from src.ui.translator_window import TranslatorWindow
from src.ui.translation_tasks import TranslationTaskRunner

# ===== Reading pane (furigana under the main-window input box) =====
# The input box itself must stay plain (an embedded ruby frame cannot survive
# edit_undo, and typing between two frames is unpredictable), so the readings
# for what the user typed live in a read-only pane below it.
READING_PANE_DEBOUNCE_MS = FURIGANA_READING_PANE_DEBOUNCE_MS
READING_PANE_MAX_ROWS = FURIGANA_READING_PANE_MAX_ROWS
READING_PANE_PLACEHOLDER = "Readings appear here when the text above contains Japanese."
READING_PANE_PLACEHOLDER_TAG = 'reading_placeholder'
READING_PANE_LABEL = "Reading"

# Shown when a second CrossTrans is launched and this one raises its window
# instead (D4). The window appearing is the real answer; the toast is there so
# nobody wonders whether a second copy started.
SECOND_INSTANCE_TOAST = "CrossTrans is already running"
SECOND_INSTANCE_TOAST_MS = 2000


class TranslatorApp:
    """Main application class."""

    def __init__(self):
        # Initialize configuration
        self.config = Config()
        if self.config.get_debug_logging():
            from src.utils.logging_setup import set_debug_logging
            set_debug_logging(True, persist_flag=True)
        logging.debug("[Boot] Config initialized")

        # Check for version upgrade and clear cache if needed
        if self._check_version_upgrade():
            return  # App will restart after cache clear

        # Fetch remote model/provider config in background
        from src.core.remote_config import get_config
        get_config().fetch_remote_async()

        # Log DnD library availability
        logging.info(f"DnD libraries: tkinterdnd2={HAS_DND}, windnd={HAS_WINDND}")

        # Create root window. The theme manager (System / Light / Dark, Decision 46) is
        # built first: the engine installs ttkbootstrap's Style on the root with our
        # tokens (Decision 45) before any widget exists.
        self.theme_manager = install_manager(ThemeManager(self.config))
        self.root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
        try:
            _tcl_ver = self.root.tk.eval("info patchlevel")
            _tkdnd_ver = getattr(self.root, "TkdndVersion", None)
            logging.info(f"[Boot] Tk root ready: tcl={_tcl_ver}, tkdnd={_tkdnd_ver}")
        except Exception:
            pass
        from src.utils.logging_setup import log_tk_callback_exception
        self.root.report_callback_exception = log_tk_callback_exception
        self.theme_engine = None
        if HAS_TTKBOOTSTRAP:
            self.theme_engine = theme_engine.install(self.root, self.theme_manager)
            self.style = self.theme_engine.style
            self.theme_manager.watch(self.root)
            from src.ui import components
            components.install(self.root, self.theme_manager, self.style)
        # One call gives every window - existing and future - the CrossTrans icon instead of
        # Tk's feather (TODO.md V9; the class icon, so no per-window call is needed)
        from src.ui.theme import windows as theme_windows
        theme_windows.set_default_icon(
            self.root, get_resource_path(os.path.join('src', 'assets', 'CrossTrans.ico')))
        self.root.withdraw()

        # Handle root window close
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        # Initialize services
        self.translation_service = TranslationService(self.config)
        self._hotkey_events = queue.SimpleQueue()
        self._capture_loading_events = queue.SimpleQueue()
        self._source_hwnd = None
        self._last_external_hwnd = None
        self.hotkey_manager = HotkeyManager(self.config, self._on_hotkey_translate)
        self.file_processor = FileProcessor(self.translation_service.api_manager)

        # UI state
        self.popup = None
        self.running = True
        self.selected_language = self.config.get_default_target_language()

        # Current translation data
        self.current_original = ""
        self.current_translated = ""
        self.current_target_lang = ""
        self.settings_window = None

        # Toast notification manager
        self.toast = ToastManager(self.root)

        # Expanded translation window
        self.expanded_window = ExpandedTranslationWindow(self.root, self.toast,
                                                         self.config)

        # Update UI manager (check previous update status)
        self.update_manager = UpdateUIManager(self.root, self.config, self.toast)
        self.update_manager.check_update_status()


        # Screenshot capture for vision/OCR translation
        self.screenshot_capture = ScreenshotCapture(self.root)

        # Quick Translate manager
        self.quick_translate_manager = QuickTranslateManager(self.root, self.config)
        self.quick_translate_manager.configure_callbacks(
            on_copy=self._on_quick_translate_copy,
            on_copy_and_replace=self._on_quick_translate_copy_and_replace,
            on_open_translator=self._on_quick_translate_open_translator,
            on_open_settings=lambda: self._show_settings_tab("API Key"),
            on_open_dictionary=self._on_quick_translate_open_dictionary,
            on_open_settings_hotkeys_tab=self._show_settings_hotkeys_tab,
            on_re_translate=self._on_quick_translate_retranslate,
            on_custom_prompt_send=self._on_quick_translate_custom_prompt_send,
            on_choose_source=self._on_quick_translate_choose_source,
            on_dismiss=self._on_quick_translate_dismissed
        )
        from src.ui.audio_controller import AudioController
        self.audio_controller = AudioController(self)
        self._pump_hotkey_events()

        # Screenshot handler
        self.screenshot_handler = ScreenshotHandler(
            self.root, self.config, self.translation_service,
            self.screenshot_capture, self.toast
        )
        self.screenshot_handler.configure_callbacks(
            on_show_quick_translate=self.show_quick_translate,
            get_quick_translate_manager=lambda: self.quick_translate_manager,
            on_show_settings_tab=self._show_settings_tab,
            get_selected_language=lambda: self.selected_language,
            run_translation=self._start_translation_task
        )
        # Screenshots an earlier run kept (one instance runs at a time, so none of these is in use)
        removed = sweep_old_shots()
        if removed:
            log_event('Screenshot', 'sweep', removed=removed)

        # Dictionary popup manager
        self.dictionary_popup = DictionaryPopup(
            self.root, self.config, self.translation_service,
            self.toast, self.quick_translate_manager
        )
        self.dictionary_popup.configure_callbacks(
            on_show_settings_tab=self._show_settings_tab,
            get_selected_language=lambda: self.selected_language,
            on_source_chosen=self._on_source_chosen
        )

        # Tray manager
        self.tray_manager = TrayManager(self.config)
        self.tray_manager.configure_callbacks(
            on_show_main_window=self.show_main_window,
            on_show_settings=self.show_settings,
            on_quit=self.quit_app,
            on_update=self.update_manager.post_update_request,
            on_open_logs=self._open_logs,
            on_audio_panel=self.show_audio_panel
        )
        self.update_manager.configure_callbacks(
            on_state_changed=self._update_state_changed,
            on_open_update=self._open_available_update,
            is_busy=self._updates_are_busy)

        # Drop handler
        self.drop_handler = DropHandler(self.root)
        self.drop_handler.configure(on_open_settings_tab=self._show_settings_tab)

        # Translation animation state
        self._translate_animation_running = False
        self._translate_animation_step = 0
        self._translate_pulse_direction = 1  # 1 = brightening, -1 = dimming
        self._translate_pulse_level = 0

        # Start the pump for automatic checks and user-initiated update actions.
        self.update_manager.startup_update_check()


        # Show any pending update dialogs (success/failure from previous update)
        self.update_manager.show_pending_dialogs()
        from src.ui.welcome import WelcomeManager
        self.onboarding = WelcomeManager(self.root, self.config, on_try=self._try_welcome_sample,
                                          on_settings=self._show_settings_tab, toast=self.toast,
                                          on_complete=self._setup_complete,
                                          on_api_change=self.translation_service.reconfigure,
                                          on_changed=self._on_settings_changed, update_manager=self.update_manager)

    def _setup_complete(self):
        self.selected_language = self.config.get_default_target_language()
        self.show_main_window()
        self._schedule_hotkey_refresh()
        self._refresh_attachment_area()
        self._refresh_tray_menu()

    def _check_version_upgrade(self) -> bool:
        """Check if app version changed since last run and clear cache if needed.

        Returns:
            True if app will restart (caller should return early)
            False if no restart needed
        """
        from src.constants import VERSION

        last_version = self.config.get_last_run_version()

        if last_version and last_version != VERSION:
            logging.info(f"Version upgrade detected: {last_version} -> {VERSION}")
            if getattr(sys, "frozen", False):
                self.config.set_last_run_version(VERSION)
                return False
            self._clear_caches_and_restart()
            return True

        # Update stored version (first run or same version)
        if last_version != VERSION:
            self.config.set_last_run_version(VERSION)

        return False

    def _clear_caches_and_restart(self):
        """Clear Python caches and restart app after version upgrade."""
        import shutil
        import sys
        import os

        from src.constants import VERSION

        logging.info("Clearing caches after version upgrade...")

        # Get base directory (where src folder is)
        base_dir = os.path.dirname(os.path.abspath(__file__))

        # List of __pycache__ directories to clear
        cache_dirs = [
            os.path.join(base_dir, '__pycache__'),
            os.path.join(base_dir, 'core', '__pycache__'),
            os.path.join(base_dir, 'ui', '__pycache__'),
            os.path.join(base_dir, 'ui', 'settings', '__pycache__'),
            os.path.join(base_dir, 'utils', '__pycache__'),
            os.path.join(base_dir, 'assets', '__pycache__'),
        ]

        cleared_count = 0
        for cache_dir in cache_dirs:
            if os.path.exists(cache_dir):
                try:
                    shutil.rmtree(cache_dir)
                    logging.info(f"Cleared cache: {cache_dir}")
                    cleared_count += 1
                except Exception as e:
                    logging.warning(f"Failed to clear {cache_dir}: {e}")

        logging.info(f"Cleared {cleared_count} cache directories")

        # Update version in config before restart
        self.config.set_last_run_version(VERSION)

        # Restart app
        logging.info("Restarting app after cache clear...")
        python = sys.executable
        os.execl(python, python, *sys.argv)

    def _prepare_external_source_hwnd(self):
        """Record the active external app HWND and restore focus if a CrossTrans popup took it."""
        from src.core.selection_capture import (
            is_own_window, is_valid_external_window, restore_foreground,
        )
        quick = getattr(self, 'quick_translate_manager', None)
        if quick is not None and getattr(quick, 'is_open', False) is True:
            try:
                quick.close()
            except Exception:
                pass
        try:
            fg = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:
            fg = 0
        if fg and not is_own_window(fg):
            self._last_external_hwnd = fg
            self._source_hwnd = fg
            return fg
        previous = getattr(self, '_last_external_hwnd', None)
        if previous and is_valid_external_window(previous):
            restore_foreground(previous)
            self._source_hwnd = previous
            return previous
        self._source_hwnd = fg or None
        return self._source_hwnd

    def _on_hotkey_translate(self, language: str):
        """Handle hotkey translation request.

        Args:
            language: Target language name, or "__screenshot__" for screenshot OCR
        """
        if threading.current_thread() is not threading.main_thread():
            self._hotkey_events.put(language)
            return

        audio = getattr(self, 'audio_controller', None)
        if language == '__audio__':
            if audio:
                self._on_audio_hotkey()
            return
        if audio and (audio.busy or audio.dialog is not None):
            self.toast.show_warning('Finish or cancel audio translation first.')
            return

        # Check for special screenshot hotkey
        if language == "__screenshot__":
            self.root.after(0, self._on_screenshot_hotkey)
            return

        # Capture/restore external source HWND and close any stale popup BEFORE capture.
        # Do NOT open show_loading() until selection capture succeeds, so Tk never steals
        # foreground focus from Excel or other apps before Ctrl+C completes (Task 38).
        self._prepare_external_source_hwnd()
        self.quick_translate_manager.capture_mouse_position()

        loading_queue = getattr(self, '_capture_loading_events', None)
        if loading_queue is None:
            loading_queue = self._capture_loading_events = queue.SimpleQueue()

        def queue_loading(target_label, custom_text=None):
            from src.core.translation_task import current_task
            loading_queue.put((current_task(), target_label, custom_text))

        # Check for special Fix Grammar hotkey (HWND already captured above for Replace)
        if language == "__fix_grammar__":
            if not self.config.get_fix_grammar_hotkey_enabled():
                return
            self._start_translation_task(
                lambda: self.translation_service.do_grammar_fix(
                    on_captured=lambda: queue_loading("Grammar", "Fixing grammar")
                ),
                'Grammar',
            )
            return

        # Normal language hotkey: merged translate-or-fix.
        self._start_translation_task(
            lambda: self.translation_service.do_translate_or_fix(
                language, on_captured=lambda: queue_loading(language, None)
            ),
            language,
        )

    def _translation_tasks(self):
        runner = getattr(self, '_translation_task_runner', None)
        if runner is None:
            runner = self._translation_task_runner = TranslationTaskRunner(self.root)
        return runner

    def _drain_capture_loading_events(self):
        loading_queue = getattr(self, '_capture_loading_events', None)
        if loading_queue is None:
            return
        try:
            while True:
                task, target_label, custom_text = loading_queue.get_nowait()
                if task is not None and not task.is_current():
                    continue
                if custom_text is not None:
                    self.quick_translate_manager.show_loading(target_label, loading_text=custom_text)
                else:
                    self.quick_translate_manager.show_loading(target_label)
        except queue.Empty:
            pass

    def _pump_hotkey_events(self):
        if not self.running:
            return
        try:
            while True:
                self._on_hotkey_translate(self._hotkey_events.get_nowait())
        except queue.Empty:
            pass
        self._drain_capture_loading_events()
        from src.utils.ui_helpers import schedule_window_callback
        schedule_window_callback(self.root, self.root, 20, self._pump_hotkey_events)

    def _reset_translation_controls(self, owner):
        if owner is not getattr(self, 'popup', None):
            return
        self._main_pending_source = None
        self._stop_translate_animation()

    def _start_translation_task(self, operation, target_lang, on_result=None, owner=None):
        def failed(error):
            from src.core.result_status import FailureText
            notice = FailureText(f'Error: {error}')
            if owner is None:
                self.show_quick_translate('', notice, target_lang, model_info='')
            elif owner is self.popup and owner.winfo_exists():
                self._reset_translation_controls(owner)
                self._update_translation(notice)

        return self._translation_tasks().start(
            operation, on_result=on_result, on_error=failed,
            on_cancel=(lambda: self._reset_translation_controls(owner)) if owner else None,
            owner=owner)

    def _cancel_quick_translation(self):
        runner = getattr(self, '_translation_task_runner', None)
        if runner and runner._owner is None:
            runner.cancel()

    def _on_screenshot_hotkey(self):
        """Handle screenshot hotkey press (Win+Alt+S)."""
        self._translation_tasks().cancel()
        self.screenshot_handler.handle_hotkey()

    def _cleanup_pending_screenshot(self):
        """Clean up pending screenshot file if exists."""
        handler = getattr(self, 'screenshot_handler', None)
        if handler is not None:
            handler.cleanup_pending_screenshot()

    def _on_quick_translate_dismissed(self):
        """The user closed the popup: stop its work, and drop the screenshot it was keeping for Open Translator."""
        self._cancel_quick_translation()
        self._cleanup_pending_screenshot()

    def show_quick_translate(self, original: str, translated: str, target_lang: str,
                             trial_info: dict = None, furigana_text: str = None,
                             is_grammar: bool = False, model_info: str = None,
                             source_lang: str = None, audio_result=False, on_audio_play=None):
        """Show compact popup near mouse cursor with translation result.

        Args:
            original: Original text
            translated: Translated text (or grammar-corrected text when is_grammar)
            target_lang: Target language (label "Grammar" for grammar-fix results)
            trial_info: Optional trial mode info dict
            furigana_text: Optional furigana-annotated text with {kanji|reading} notation
            is_grammar: True when showing a Fix Grammar result (hides translation-only buttons)
            model_info: "model (Provider)" credit shown as a note at the top of the popup.
                Defaults to whoever served the result that just arrived, which is what
                every caller wants - the screenshot handler and the translation queue both
                reach this method after the service has recorded it.
            source_lang: The language settled for `original`. Defaults to the verdict
                the service holds for exactly that text (J4) - read here, like the
                credit, rather than carried through the queue.
        """
        audio = getattr(self, 'audio_controller', None)
        if audio and audio.files is not None and not audio.rendering:
            audio.request_close('replace', lambda: self.show_quick_translate(
                original, translated, target_lang, trial_info, furigana_text, is_grammar, model_info, source_lang,
                audio_result=audio_result, on_audio_play=on_audio_play))
            return
        if model_info is None:
            model_info = self.translation_service.last_attribution
        if source_lang is None:
            source_lang = self.translation_service.source_verdict(original)

        self.current_original = original
        self.current_translated = translated
        self.current_target_lang = target_lang


        self.quick_translate_manager.show(translated, target_lang, trial_info, original,
                                          furigana_text=furigana_text, is_grammar=is_grammar,
                                          model_info=model_info, source_lang=source_lang,
                                          **({'audio_result': True, 'on_audio_play': on_audio_play} if audio_result else {}))
        if not is_grammar:
            self._note_first_translation(translated)

    def _note_first_translation(self, translated):
        welcome = getattr(self, 'onboarding', None)
        if welcome is not None:
            welcome.note_success(translated, failed=is_error_text(translated))

    def _sync_main_empty(self):
        view = getattr(self, '_view', None)
        if view is None:
            return
        try:
            view.sync_empty(self.original_text.get('1.0', 'end-1c'), self.trans_text.get_plain())
        except (AttributeError, tk.TclError):
            pass

    def _show_welcome(self):
        return self.onboarding.show()

    def _try_welcome_sample(self, sample=None, target=None):
        from src.core.onboarding import SAMPLE
        sample = sample or SAMPLE
        target = target or self.selected_language

        def use_sample():
            self.selected_language = target
            self.show_popup(sample, '', target, force_new=True)
            self._do_retranslate()

        existing = False
        if getattr(self, 'popup', None) is not None:
            try:
                existing = bool(self.original_text.get('1.0', 'end-1c').strip() or self.trans_text.get_plain().strip())
            except (AttributeError, tk.TclError):
                pass
        if existing:
            from src.ui import components as ui
            question = ui.Dialog(self.popup, 'Try the sample?',
                                  'This replaces the current text in the Translator with the sample.')
            question.add_button('Cancel', default=True)
            question.add_button('Try the sample', use_sample)
            question.show()
            return question
        use_sample()


    def close_quick_translate(self):
        """Close the quick translate popup."""
        audio = getattr(self, 'audio_controller', None)
        if audio and (audio.files is not None or (audio.busy and audio.main_owner is None)):
            audio.request_close()
            return
        self._cancel_quick_translation()
        self.quick_translate_manager.close()

    def _on_quick_translate_copy(self):
        """Handle copy from quick translate popup."""
        pyperclip.copy(self.current_translated)
        self.quick_translate_manager.set_copy_button_text("Copied!")
        self.toast.show_success("Copied to clipboard!")
        # Feedback belongs to the popup that displayed this result.
        from src.utils.ui_helpers import schedule_window_callback
        manager = self.quick_translate_manager
        popup = getattr(manager, 'popup', None)
        if popup is not None:
            schedule_window_callback(self.root, popup, 1000,
                                     lambda: manager.set_copy_button_text("Copy"))

    def _on_quick_translate_retranslate(self):
        """Force a fresh API translation of the current text (bypass cache).

        Triggered by the popup "Re-translate" button. Runs off the UI thread so the
        blocking API call doesn't freeze Tkinter; the result flows back through the
        translation queue and re-renders the popup via _check_queue().
        """
        audio = getattr(self, 'audio_controller', None)
        if audio and audio.files is not None:
            audio.retry()
            return
        text = self.current_original
        target_lang = self.current_target_lang
        if not text:
            self.toast.show_warning("Nothing to re-translate")
            return

        mode = self._quick_result_mode()

        # Show the loading animation in place of the current popup.
        self.quick_translate_manager.show_loading(target_lang)

        def worker():
            self.translation_service.redo_translation(text, target_lang,
                                                       **({'mode': 'merged'} if mode == 'merged' else {}))

        self._start_translation_task(worker, target_lang)

    def _quick_result_mode(self):
        value = getattr(self, 'current_translated', '')
        if not value:
            return 'text'
        data = getattr(value, 'alignment', None)
        if isinstance(data, dict) and data.get('mode') in ('text', 'merged'):
            return data['mode']
        history = getattr(self.translation_service, 'history_manager', None)
        finder = getattr(history, 'find_cached_entry', None)
        entry = finder(self.current_original, self.current_target_lang, source_type='merged') if finder else None
        return 'merged' if entry and entry.get('translated') == value else 'text'

    def _on_quick_translate_choose_source(self, language: str):
        """The popup's Japanese / Chinese row: translate again, with the answer stated.

        Asked (nobody placed the text) or changed (a model placed a term, K4),
        the translation may carry the other language's sense; the answer goes to
        the model and is the text's verdict (J6, Decision 27) - kept before the
        request, so a failed or custom re-translation still keeps it, and on
        History's older entries too, over a model's (Decision 28).
        """
        audio = getattr(self, 'audio_controller', None)
        if audio and audio.files is not None:
            audio.retry(source_language=language)
            return
        text = self.current_original
        target_lang = self.current_target_lang
        if not text:
            return
        mode = self._quick_result_mode()
        logging.info(f"[LangID] user chose source={language} chars={len(text)}")
        self.translation_service.remember_user_source(text, language)
        self.quick_translate_manager.show_loading(target_lang)

        def worker():
            self.translation_service.redo_translation(text, target_lang,
                                                      source_language=language,
                                                      **({'mode': 'merged'} if mode == 'merged' else {}))

        self._start_translation_task(worker, target_lang)

    def _on_quick_translate_custom_prompt_send(self, raw_prompt: str):
        """Send a freeform custom prompt from the popup edit box to the AI.

        The entire box content is sent verbatim (no translate wrapper). Runs off the UI
        thread; the result flows back through the translation queue and replaces the
        popup content via _check_queue(). Freeform asks are not cached.
        """
        audio = getattr(self, 'audio_controller', None)
        if audio and audio.files is not None:
            if raw_prompt and raw_prompt.strip():
                audio.retry(raw_prompt=raw_prompt)
            return
        if not raw_prompt or not raw_prompt.strip():
            self.toast.show_warning("Empty prompt")
            return

        target_lang = self.current_target_lang

        # Show the loading animation in place of the edit popup.
        self.quick_translate_manager.show_loading(target_lang)

        def worker():
            self.translation_service.ask_freeform(raw_prompt, target_lang)

        self._start_translation_task(worker, target_lang)

    def _on_quick_translate_copy_and_replace(self):
        audio = getattr(self, 'audio_controller', None)
        if audio and (audio.files is not None or getattr(self.quick_translate_manager, '_audio_result', False)):
            return
        """Copy translated text and paste it into the source application.

        With WS_EX_NOACTIVATE on popup, source app keeps focus + selection.
        Just copy to clipboard, close popup, and simulate Ctrl+V.
        """
        translated_text = self.current_translated
        if not translated_text:
            self.toast.show_warning("No translation to replace")
            return

        # Preserve trailing newline from original selection
        # When user selects a full line, the selection includes the trailing newline.
        # Without preserving it, the next line would jump up after replace.
        original_text = self.current_original
        if original_text and original_text.endswith('\n') and not translated_text.endswith('\n'):
            if original_text.endswith('\r\n'):
                translated_text += '\r\n'
            else:
                translated_text += '\n'

        # Always copy to clipboard first (fallback if paste fails)
        pyperclip.copy(translated_text)

        # Close popup (source app still has focus due to WS_EX_NOACTIVATE)
        self.quick_translate_manager.close()

        # Paste in background thread (needs small delay for popup to fully close)
        source_hwnd = getattr(self, '_source_hwnd', None)

        def do_paste():
            try:
                from src.core.selection_capture import (
                    VK_V, is_valid_external_window, restore_foreground,
                    send_ctrl_combo, wait_for_modifiers_release,
                )
                time.sleep(0.10)
                if source_hwnd and is_valid_external_window(source_hwnd):
                    restore_foreground(source_hwnd, timeout=0.15)
                wait_for_modifiers_release(timeout=0.25, check=None)
                accepted = send_ctrl_combo(VK_V, release_held_modifiers=True)
                if accepted == 0:
                    raise OSError('SendInput did not deliver Ctrl+V')
                time.sleep(0.1)
                self.root.after(0, lambda: self.toast.show_success("Replaced!"))
            except Exception as e:
                logging.error(f"Replace paste failed: {e}")
                self.root.after(0, lambda: self.toast.show_warning(
                    "Replace failed. Text copied to clipboard."))

        threading.Thread(target=do_paste, daemon=True).start()

    def _on_quick_translate_open_translator(self):
        audio = getattr(self, 'audio_controller', None)
        if audio and audio.files is not None:
            audio.request_close('open', self._on_quick_translate_open_translator)
            return
        """Handle open translator from quick translate popup.

        Also reachable from a *failed* popup, where the button sits next to API
        Settings. In that case there is no translation to carry over - only a
        failure notice - so the output box is left empty and the user can press
        Translate straight away.
        """
        self.close_quick_translate()

        # Check if there's a pending screenshot to load into attachments
        pending_image = self.screenshot_handler.get_pending_screenshot()

        translated = "" if is_error_text(self.current_translated) else self.current_translated

        self.show_popup(
            self.current_original,
            translated,
            self.current_target_lang,
            pending_attachment=pending_image
        )

        # Clear pending screenshot (will be managed by AttachmentArea now)
        self.screenshot_handler.clear_pending_screenshot()

    def _on_quick_translate_open_dictionary(self, text: str, target_lang: str,
                                            anchor: Optional[tk.Misc] = None):
        """The popup's Dictionary button: the main window's flow, beside the popup.

        One Dictionary-mode implementation serves both buttons (DEFERRED D13);
        the popup only says what to analyse, into which language, and where.
        """
        self.dictionary_popup.open_from_text(text, target_language=target_lang, anchor=anchor)

    def on_second_instance(self):
        """Another CrossTrans was launched: raise this one instead of starting it.

        Called from the lock socket's accept thread, so nothing here touches Tk
        directly. `show_main_window` already marshals itself, but the toast does
        not - both are handed over in one `after(0, ...)`, the same way the
        Replace path hands its toast over from a daemon thread.
        """
        try:
            self.root.after(0, self._raise_for_second_instance)
        except tk.TclError:
            pass  # shutting down; the window is going away anyway

    def _raise_for_second_instance(self):
        """The UI half of `on_second_instance`, on the main thread."""
        self.show_main_window()
        self.toast.show_info(SECOND_INSTANCE_TOAST, duration=SECOND_INSTANCE_TOAST_MS)

    def show_main_window(self, icon=None, item=None):
        """Show main translator window from tray."""
        # Ensure runs on main thread
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self.show_main_window(icon, item))
            return

        # If popup already exists, bring it to front instead of creating new one
        if self.popup:
            try:
                if self.popup.winfo_exists():
                    # Restore from minimized/iconified state
                    self.popup.deiconify()
                    # Force window to top using topmost trick
                    self.popup.attributes('-topmost', True)
                    self.popup.update()
                    self.popup.attributes('-topmost', False)
                    # Additional methods to ensure focus
                    self.popup.lift()
                    self.popup.focus_force()
                    return
            except tk.TclError:
                pass  # Window was destroyed, create new one

        # Prevent double-calling by checking if popup is being shown
        if hasattr(self, '_showing_popup') and self._showing_popup:
            return
        self._showing_popup = True
        try:
            self.show_popup("", "", self.selected_language)
        finally:
            self._showing_popup = False

    def show_popup(self, original: str, translated: str, target_lang: str,
                   force_new: bool = False, pending_attachment: str = None):
        """Show the full translator popup window.

        Args:
            original: Original text to display
            translated: Translated text to display
            target_lang: Target language
            force_new: If False and popup exists with content, just bring to front
            pending_attachment: Optional file path to add to attachments (e.g., screenshot)
        """
        # If popup exists with content and we're not forcing new, just bring it to front
        if not force_new and self.popup:
            try:
                if self.popup.winfo_exists():
                    # Check if there's existing content we shouldn't destroy
                    has_content = False
                    if hasattr(self, 'original_text') and self.original_text:
                        try:
                            existing_text = self.original_text.get('1.0', 'end-1c').strip()
                            has_content = bool(existing_text)
                        except:
                            pass
                    if hasattr(self, 'attachment_area') and self.attachment_area:
                        try:
                            has_content = has_content or len(self.attachment_area.get_attachments()) > 0
                        except:
                            pass

                    # If popup has content and we're just opening (empty params), bring to front
                    if has_content and not original and not translated:
                        self.popup.deiconify()
                        self.popup.attributes('-topmost', True)
                        self.popup.update()
                        self.popup.attributes('-topmost', False)
                        self.popup.lift()
                        self.popup.focus_force()
                        return
            except tk.TclError:
                pass

        # Destroy existing popup if present
        if self.popup:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass  # Window already destroyed
            self.popup = None

        view = TranslatorWindow(self.root, self.config, LANGUAGES)
        view.configure_callbacks(
            on_translate=self._do_retranslate, on_fix_grammar=self._do_fix_grammar,
            on_cancel=self._cancel_main_translation, on_copy=self._copy_translation,
            on_expand=self._open_expanded_translation, on_dictionary=self._open_home_dictionary,
            on_history=self._open_history, on_settings=self.show_settings,
            on_swap=self._swap_languages, on_language_change=self._on_language_chosen,
            on_retheme=self._retheme_translator,
            on_resize=self._sync_translator_compact, on_source_change=self._source_selection_changed)
        self._view = view
        self.popup = view.build(original, target_lang, recent=self.config.get_recent_languages,
                                pinned=self._pinned_languages,
                                fix_grammar=self.config.get_fix_grammar_enabled())
        if hasattr(view, 'workspace'):
            view.workspace.configure_home_pages(self._build_home_dictionary, self._build_home_history)
            self.popup.bind('<Control-f>', view.workspace.focus_search, add='+')

        # Delayed callbacks belong to this window, even if the app opens a replacement.
        from src.utils.ui_helpers import schedule_window_callback
        popup = self.popup
        def on_popup_focus_in(e):
            popup.attributes('-topmost', True)
            schedule_window_callback(self.root, popup, 100,
                                     lambda: popup.attributes('-topmost', False))

        popup.bind('<FocusIn>', on_popup_focus_in)

        # Handle close button properly
        def on_popup_close():
            runner = getattr(self, '_translation_task_runner', None)
            if runner:
                runner.cancel(owner=self.popup)
            view.remember_size()
            try:
                if self.popup and self.popup.winfo_exists():
                    self.popup.destroy()
            except tk.TclError:
                pass  # Window already destroyed
            self.popup = None

        self._close_translator = on_popup_close
        self.popup.protocol("WM_DELETE_WINDOW", on_popup_close)
        self.popup.bind('<Escape>', self._on_translator_escape)

        # The widgets the rest of the app (and its tests) have always reached by these names
        self.original_text = view.original_text
        self.translate_btn = view.translate_btn
        self.copy_btn = view.copy_btn
        self.dict_btn = view.dict_btn
        self.expand_btn = view.expand_btn
        self.custom_prompt = view.custom_prompt
        if view.fix_grammar_btn is not None:
            self.fix_grammar_btn = view.fix_grammar_btn
        else:
            self.__dict__.pop('fix_grammar_btn', None)

        # ===== ATTACHMENT AREA =====
        # Show if ANY API has vision or file capabilities (check flags set during API test)
        has_vision = self.config.has_any_vision_capable()
        has_file = self.config.has_any_file_capable()
        audio_checker = getattr(self.config, 'has_usable_audio_keys', None) or getattr(self.config, 'has_any_audio_capable', lambda: False)
        has_audio = (self.config.get('audio_enabled', True) is True) and (audio_checker() is True)

        if has_vision or has_file or has_audio:
            try:
                # Ensure popup window is fully realized before creating DnD widgets
                self.popup.update_idletasks()
                self.attachment_area = AttachmentArea(view.attach_host, self.config, on_change=None,
                                                     on_open_settings_tab=self._show_settings_tab)
                self.attachment_area.pack(fill=X)
                self._attachments_take_drops(view)

                # Load pending attachment (e.g., from screenshot hotkey)
                if pending_attachment and os.path.exists(pending_attachment):
                    try:
                        import shutil
                        import tempfile
                        import time

                        # Create persistent copy in temp directory; the prefix lets the next start remove it
                        temp_dir = tempfile.gettempdir()
                        filename = f"{SHOT_PREFIX}attached-{int(time.time())}.png"
                        persistent_path = os.path.join(temp_dir, filename)
                        shutil.copy2(pending_attachment, persistent_path)

                        # Add to attachments
                        self.attachment_area.add_file(persistent_path, show_warning=False)
                        logging.info("Loaded the screenshot into attachments")

                        # Delete original temp file
                        try:
                            os.unlink(pending_attachment)
                        except Exception:
                            pass
                    except Exception as e:
                        logging.error(f"Failed to load screenshot into attachments: {e}")

            except Exception as e:
                logging.error(f"Error initializing AttachmentArea: {e}")
                self.attachment_area = None
        else:
            self.attachment_area = None

        view.set_attachments_visible(self.attachment_area is not None)

        # The Japanese / Chinese row for kanji-only input (K5), shown at the top of the source
        # card by _refresh_source_row() when there is something to say
        self._source_row = view.source_row
        self._source_row_state = None
        # A translation handed over with the text (Open Translator) is of exactly it
        self._translated_input = (original or '').strip() if translated else None

        # Update Dictionary button state based on NLP availability
        self._update_dict_button_state()

        # The input box and its Reading pane share the source card
        self._create_reading_pane(self.original_text.master)
        self._select_language_in_list(target_lang)

        self._create_translation_box(view.output_host, translated, target_lang)
        self._main_linked = MainLinkedReading(self.root, self.popup, self.original_text,
                                              self.trans_text, self.config, view.linked_bar)
        self._main_linked.set_result(translated, original, target_lang)

        # Enable DnD for popup window - delay to ensure window is fully realized
        schedule_window_callback(self.root, popup, 300, lambda: self._setup_drop_handling(popup))

        # Refresh the product title.
        self._update_popup_title_with_trial()

        view.show()
        self._sync_main_empty()
        self._refresh_source_state()

    # ------------------------------------------------------------------ #
    # Languages (the picker, swap)
    # ------------------------------------------------------------------ #
    def _pinned_languages(self):
        """The picker's Pinned section: the languages that have a hotkey."""
        return language_prefs.pinned_names(self.config.get_all_hotkeys())

    def _on_language_chosen(self, name: str):
        """The user chose a target in the picker."""
        runner = getattr(self, '_translation_task_runner', None)
        if runner:
            runner.cancel(owner=self.popup)
        self.selected_language = name
        self.config.push_recent_language(name)

    def _select_language_in_list(self, lang_name: str):
        """Make `lang_name` the target (History and Open Translator hand one over). A name that
        is not a language leaves the target as it was."""
        if lang_name not in {name for name, _, _ in LANGUAGES}:
            return
        self.selected_language = lang_name
        view = getattr(self, '_view', None)
        if view is not None:
            view.picker.set(lang_name)

    def _swap_languages(self):
        """Exchange the displayed texts and known languages, leaving submission to the user."""
        translated = self.trans_text.get_plain() if getattr(self, 'trans_text', None) else ''
        if not translated.strip() or is_error_text(translated):
            return
        target = self._swap_target_language(detect=True)
        if not target:
            self._view.source_chip.open()
            self.toast.show_info('Choose the original language, then press Swap languages.')
            return
        original = self.original_text.get('1.0', 'end-1c')
        previous_target = self.selected_language
        reading = getattr(self, '_main_linked', None)
        previous_result = getattr(reading, 'result', translated)
        from src.core.translation_alignment import reverse_reading_result
        reversed_result = (reverse_reading_result(previous_result, original, target, previous_target)
                           if previous_result == translated else original)
        runner = getattr(self, '_translation_task_runner', None)
        if runner:
            runner.cancel(owner=self.popup)
        self._select_language_in_list(target)
        picker = getattr(getattr(self, '_view', None), 'source_chip', None)
        if picker is not None:
            picker.set(previous_target)
        self.config.push_recent_language(target)
        self._main_pending_source = None
        self.original_text.edit_separator()
        self.original_text.delete('1.0', tk.END)
        self.original_text.insert('1.0', translated)
        self.original_text.edit_separator()
        self._view.clear_error()
        self.trans_text.clear()
        insert_output(self.trans_text, tk.END, reversed_result,
                      lang_hint=lang_id.output_hint(previous_target, target), enabled=self._ruby_enabled())
        self._translated_input = translated.strip()
        if reading is not None:
            reading.set_result(reversed_result, translated, target)
        self._sync_main_empty()
        self._refresh_source_state()
        self._schedule_reading_refresh(delay_ms=1)

    def _swap_target_language(self, *, detect=False):
        """Use known source evidence first; offline Dictionary detection is click-only."""
        from src.core.source_languages import normalize_source
        def supported(language):
            source = normalize_source(language)
            return next((name for name, _, _ in LANGUAGES if normalize_source(name) == source), None) if source else None

        source = self._input_source()
        target = supported(source)
        if target:
            return target
        picker = getattr(getattr(self, '_view', None), 'source_chip', None)
        if not detect or (picker is not None and isinstance(picker.value, str)
                          and normalize_source(picker.value) is not None):
            return None
        text = self.original_text.get('1.0', 'end-1c')
        if not text.strip():
            return None
        from src.core.nlp_manager import nlp_manager
        from src.ui.dictionary_popup import DETECTION_THRESHOLD
        try:
            language, confidence = nlp_manager.detect_language(text)
            return supported(language) if confidence >= DETECTION_THRESHOLD else None
        except Exception:
            logging.exception('[Swap] offline source-language detection failed')
            return None

    def _clear_translation(self):
        """Empty the output box (the text it described has been replaced)."""
        try:
            self.trans_text.clear()
        except tk.TclError:
            return
        if getattr(self, '_main_linked', None):
            self._main_linked.set_result('', '', self.selected_language)
        self._sync_main_empty()
        self._refresh_source_state()

    def _do_retranslate(self, source_language: Optional[str] = None):
        """Perform translation from popup.

        Args:
            source_language: The Japanese / Chinese answer from the row beside
                "Original:" (K5): stated to the model, and the cache skipped -
                it holds the translation the answer is correcting.
        """
        audio = getattr(self, 'audio_controller', None)
        if audio and audio.busy:
            self.toast.show_warning('Finish or cancel audio translation first.')
            return
        original = self.original_text.get('1.0', tk.END).strip()

        if source_language is None and self._main_translation_pending():
            return      # one at a time: while it runs, Cancel is the way to stop it

        picker = getattr(getattr(self, '_view', None), 'source_chip', None)
        cache_for_auto = bool(
            (source_language == lang_id.JAPANESE or source_language in lang_id.CHINESE)
            and picker is not None and isinstance(picker.value, str) and picker.value == 'Auto')
        if source_language is None:
            if picker is not None and isinstance(picker.value, str) and picker.value != 'Auto':
                from src.core.source_languages import normalize_source
                source_language = normalize_source(picker.value)

        # Check for attachments
        attachments = self.attachment_area.get_attachments() if (hasattr(self, 'attachment_area') and self.attachment_area) else []
        has_attachments = len(attachments) > 0
        media = [item for item in attachments if item['type'] == 'audio']
        if media:
            if len(attachments) != 1:
                self.toast.show_warning('Translate audio or video separately from other attachments.')
                return
            self.audio_controller.translate_file(media[0]['path'], self.selected_language, self.popup,
                                                  self._view.custom_instruction(), source_language)
            return


        if not original and not has_attachments:
            self.toast.show_warning("No content to translate")
            return

        # Get custom prompt
        custom_prompt = self._view.custom_instruction()

        self._translation_tasks().cancel()
        owner = self.popup
        target_lang = self.selected_language
        self._main_pending_source = original
        self._start_translate_animation()

        def translate_thread():
            translated = ""
            extracted_original = ""
            source_text = None
            attachment_errors = []

            if has_attachments:
                # Collect all attachments for single API call
                image_paths = []
                file_contents = {}  # {filename: content}
                image_filenames = []  # Track image filenames for response format

                raw_attachment_errors = []
                for att in attachments:
                    path = att['path']
                    filename = os.path.basename(path)

                    # Skip missing files
                    if not os.path.exists(path):
                        attachment_errors.append(filename + ': ' + tr('File no longer exists.'))
                        raw_attachment_errors.append(f'{filename}: File no longer exists.')
                        continue

                    if att['type'] == 'image':
                        image_paths.append(path)
                        image_filenames.append(filename)
                    elif att['type'] == 'file':
                        try:
                            content = self.file_processor.extract_text(path)
                            file_contents[filename] = content
                        except Exception as e:
                            attachment_errors.append(filename + ': ' + tr(str(e)))
                            raw_attachment_errors.append(f'{filename}: {str(e)}')

                if not image_paths and not file_contents:
                    raise ValueError('\n'.join(raw_attachment_errors) or 'No readable attachments.')

                # Build prompt for multimodal translation
                try:
                    from src.core.attachment_prompt import build_attachment_prompt, split_attachment_answer
                    from src.core.result_status import FailureText, mark_translation
                    prompt = build_attachment_prompt(target_lang, original, image_filenames,
                                                     file_contents, custom_prompt, source_language)

                    # Single API call with all images + file contents
                    result = self.translation_service.api_manager.translate_multimodal(
                        prompt, image_paths, file_contents
                    )

                    extracted_original, translated = split_attachment_answer(result)
                    translated = mark_translation(translated)

                    # Save to history for multimodal translations
                    source_text = original if original else f"[{len(attachments)} attachment(s)]"
                    self.translation_service.history_manager.add_entry(
                        source_text, translated, target_lang, source_type="multimodal",
                        source_lang=source_language or "", source_verdict=source_language or "",
                        source_choice=source_language if source_language and not cache_for_auto else ""
                    )

                except Exception as e:
                    from src.core.result_status import FailureText
                    translated = FailureText(f"Error: {str(e)}")
            else:
                # Standard text translation (no attachments)
                translated = self.translation_service.translate_text(
                    original, target_lang, custom_prompt,
                    skip_cache=bool(source_language), source_language=source_language,
                    **({'cache_for_auto': True} if cache_for_auto else {}),
                    **({'allow_linked': True} if getattr(self.config, 'get_linked_reading_enabled',
                                                        lambda: False)() is True else {}))
                source_text = original

            return translated, extracted_original, source_text, attachment_errors

        def completed(result):
            if owner is self.popup:
                self._main_pending_source = None
                self._update_translation_with_original(*result[:2], source_text=result[2])
                if result[3]:
                    self.toast.show_warning(tr('Some files were left out: {files}', files='\n'.join(result[3])))

        self._start_translation_task(translate_thread, target_lang, completed, owner)

    def _update_translation(self, translated: str):
        """Update translation result in popup."""
        self._update_translation_with_original(translated, "")

    def _do_fix_grammar(self):
        audio = getattr(self, 'audio_controller', None)
        if audio and audio.busy:
            self.toast.show_warning('Finish or cancel audio translation first.')
            return
        """Fix the grammar of the main-window input text (no translation).

        Reads the input box, sends it to TranslationService.fix_grammar() off the UI
        thread, and writes the corrected text (same language) into the output box.
        """
        original = self.original_text.get('1.0', tk.END).strip()
        if not original:
            self.toast.show_warning("No text to fix")
            return

        self._translation_tasks().cancel()
        owner = self.popup
        self._main_pending_source = original
        self._start_translate_animation('Fixing grammar')

        def fix_thread():
            return self.translation_service.fix_grammar(original)

        def completed(corrected):
            if owner is self.popup:
                self._reset_translation_controls(owner)
                self._update_grammar_result(corrected)

        self._start_translation_task(fix_thread, self.selected_language, completed, owner)

    def _show_translation_error(self, text: str):
        """Show `text` as a failure above the translation when it is one (`is_error_text`), else
        take any earlier failure away. What helps first becomes the bar's action: Try again when
        the request can simply be repeated, API Settings when the key is the problem."""
        view = getattr(self, '_view', None)
        if view is None:
            return
        try:
            if not is_error_text(text):
                view.clear_error()
                return
            help_ = popup_errors.explain(text)
            action, on_action = None, None
            if help_.fix == popup_errors.FIX_RETRY:
                action, on_action = 'Try again', self._do_retranslate
            elif help_.fix == popup_errors.FIX_SETTINGS:
                action, on_action = 'API Settings', lambda: self._show_settings_tab("API Key")
            view.show_error(help_.message, help_.kind, action, on_action)
        except tk.TclError:
            pass        # the window closed meanwhile

    def _update_grammar_result(self, corrected: str):
        """Write the grammar-fix result into the output box and reset the button."""
        from src.ui.deferred_resize import defer_update
        if defer_update(getattr(self, 'popup', None), 'main-result', lambda: self._update_grammar_result(corrected)):
            return
        self._show_translation_error(corrected)
        if getattr(self, '_main_linked', None):
            self._main_linked.set_result('', '', self.selected_language)
        try:
            # A grammar fix keeps the source language: Japanese when the input's
            # characters or its verdict say so (J7), else no hint - kanji-only
            # output then stays plain rather than guessed at.
            source = self._input_source()
            self.trans_text.clear()
            insert_output(self.trans_text, tk.END, corrected,
                          lang_hint=lang_id.JAPANESE if source == lang_id.JAPANESE else None,
                          enabled=self._ruby_enabled())
        except tk.TclError:
            return  # Popup closed mid-flight
        self._update_popup_title_with_trial()

    def _update_popup_title_with_trial(self):
        """Refresh the main title without legacy quota decoration."""
        if self.popup and self.popup.winfo_exists():
            self.popup.title("CrossTrans 2")

    def _update_translation_with_original(self, translated: str, extracted_original: str = "",
                                          source_text: Optional[str] = None):
        """Update translation result and optionally the original text in popup.

        Args:
            source_text: The text that was translated, when it was the input box's
                own text - what the Japanese / Chinese row asks about (K5).
        """
        from src.ui.deferred_resize import defer_update
        if defer_update(getattr(self, 'popup', None), 'main-result', lambda: self._update_translation_with_original(
                translated, extracted_original, source_text)):
            return
        # Stop animation
        self._stop_translate_animation()

        if source_text is not None and not is_error_text(translated):
            self._translated_input = source_text

        # Update original text box if extracted_original is provided
        if extracted_original:
            self.original_text.delete('1.0', tk.END)
            self.original_text.insert('1.0', extracted_original)

        # A failure is told in words above the box; the notice stays in it as the details
        self._show_translation_error(translated)

        # Update translation text box (RubyText restores the disabled state)
        self.trans_text.clear()
        insert_output(self.trans_text, tk.END, translated,
                      lang_hint=lang_id.output_hint(self._input_source(),
                                                    self.selected_language),
                      enabled=self._ruby_enabled())

        self._sync_main_empty()
        self._refresh_source_state()
        self._note_first_translation(translated)

        # Refresh the product title after each translation.
        self._update_popup_title_with_trial()

        # The translation may have brought a verdict for the input (J4): the
        # Reading pane can read kanji-only Japanese now, and the language row
        # (K5) can say what the model made of it.
        if self._reading_pane_alive():
            self._schedule_reading_refresh(delay_ms=1)

        if getattr(self, '_main_linked', None):
            self._main_linked.set_result(
                translated, source_text if source_text is not None else
                self.original_text.get('1.0', 'end-1c'), self.selected_language)

    def _start_translate_animation(self, label: str = 'Translating'):
        """Show that a request is running: a status with moving dots and a Cancel beside the
        unchanged Translate button (V25)."""
        self._stop_translate_animation()
        self._translate_animation_running = True
        self._translate_animation_step = 0
        self._translate_status = label
        view = getattr(self, '_view', None)
        if view is not None:
            view.set_busy(True, label)
        self._animate_translate_button()

    def _stop_translate_animation(self):
        """Stop the status animation and give the controls back."""
        self._translate_animation_running = False
        job = getattr(self, '_translate_animation_job', None)
        if job:
            from src.utils.ui_helpers import cancel_window_callback
            cancel_window_callback(self.root, getattr(self, 'popup', None), job)
        self._translate_animation_job = None
        view = getattr(self, '_view', None)
        if view is not None and self.popup:
            try:
                view.set_busy(False)
            except tk.TclError:
                pass        # the window is gone

    def _animate_translate_button(self):
        """One frame of the dots (500 ms apart; a fixed-width label, so nothing shifts)."""
        if not self._translate_animation_running:
            return
        view = getattr(self, '_view', None)
        if view is None:
            return
        if not components.get().manager.animations_enabled:
            view.set_status(self._translate_status, 0)
            return
        try:
            view.set_status(self._translate_status, self._translate_animation_step % 4)
            self._translate_animation_step += 1
            if self.popup and self.popup.winfo_exists():
                from src.utils.ui_helpers import schedule_window_callback
                self._translate_animation_job = schedule_window_callback(
                    self.root, self.popup, 500, self._animate_translate_button)
        except tk.TclError:
            self._translate_animation_running = False       # widget destroyed

    def _main_translation_pending(self) -> bool:
        """True while the Translator's own request is running."""
        audio = getattr(self, 'audio_controller', None)
        if audio and audio.busy and audio.main_owner is self.popup:
            return True
        runner = getattr(self, '_translation_task_runner', None)
        return bool(runner and runner._owner is self.popup
                    and getattr(self, '_main_pending_source', None) is not None)

    def _cancel_main_translation(self):
        """Cancel (the button beside Translate, and Esc while it runs)."""
        audio = getattr(self, 'audio_controller', None)
        if audio and audio.main_owner is self.popup:
            audio.cancel()
        runner = getattr(self, '_translation_task_runner', None)
        if runner:
            runner.cancel(owner=self.popup)

    def _on_translator_escape(self, event=None):
        """Esc stops a running translation first; with nothing running it closes the window."""
        panel = getattr(self, '_home_quick_panel', None)
        if panel is not None and not panel.closed:
            panel.close()
            return 'break'
        workspace = getattr(getattr(self, '_view', None), 'workspace', None)
        if workspace is not None and workspace.primary == 'settings' and self.settings_window:
            return self.settings_window._escape(event)
        if workspace is not None and workspace.home_page == 'history':
            return workspace.controllers['history']._escape(event)
        if workspace is not None and workspace.home_page == 'dictionary':
            if not workspace.controllers['dictionary'].escape():
                workspace.select_home('translate')
            return 'break'
        if self._main_translation_pending():
            self._cancel_main_translation()
        else:
            self._close_translator()
        return 'break'

    def _create_translation_box(self, parent, translated: str, target_lang: str):
        """Build the main window's output box and fill it with the translation.

        A RubyText, so Japanese results show furigana. It stays read-only, which
        satisfies I3 (editable implies plain) by construction; read it back with
        get_plain(), never get(). The mouse wheel, including over ruby frames, is
        bound by the widget itself.

        Args:
            parent: Frame to grid into (its first cell, which fills it)
            translated: Text to display
            target_lang: Target language, the authoritative annotation hint
                (with the input's settled language, `lang_id.output_hint`)
        """
        ctx = components.get()
        p = ctx.palette
        self.trans_text = RubyText(parent, height=1, width=1, wrap=tk.WORD,
                                   bg=ctx.plane_colour('surface'), base_fg=p.text, kanji_fg=p.text,
                                   ruby_fg=p.ruby,
                                   base_font=ctx.font('reading', type_ramp.script_for_language(target_lang)),
                                   relief='flat', spacing1=0, spacing3=0, cursor='xterm',
                                   padx=0, pady=0, selectbackground=p.selection,
                                   selectforeground=p.selection_text, inactiveselectbackground=p.selection,
                                   insertbackground=p.text)
        insert_output(self.trans_text, '1.0', translated,
                      lang_hint=lang_id.output_hint(self._input_source(), target_lang),
                      enabled=self._ruby_enabled())
        self.trans_text.config(state='disabled')  # Make read-only
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self.trans_text.grid(row=0, column=0, sticky='nsew')
        self.trans_bar = components.AutoScrollbar(parent, self.trans_text)
        return self.trans_text

    def _input_source(self) -> Optional[str]:
        """The input box's language as far as it is settled: its characters,
        else the verdict a model (or the user) gave when exactly this text was
        translated - `TranslationService.source_language()`. No request, ever."""
        try:
            text = self.original_text.get('1.0', 'end-1c')
        except tk.TclError:
            return None
        picker = getattr(getattr(self, '_view', None), 'source_chip', None)
        if picker is not None and isinstance(picker.value, str) and picker.value != 'Auto':
            from src.core.source_languages import normalize_source
            return normalize_source(picker.value)
        return self.translation_service.source_language(text)

    def _ruby_enabled(self) -> bool:
        """Whether result text should carry furigana (Settings toggle).

        Annotation happens at render time, so this one setting governs every
        surface rather than only the popup's source block.
        """
        return bool(self.config and self.config.get_furigana_enabled())

    # ------------------------------------------------------------------ #
    # Reading pane (furigana for the text being typed)
    # ------------------------------------------------------------------ #
    def _create_reading_pane(self, parent):
        """Install inline annotations on the existing native editor, without a second text box."""
        from src.ui.inline_reading import InlineReading
        self._reading_pane_job = None
        self._inline_reading = InlineReading(self.original_text)
        self.original_text.bind('<<Modified>>', self._on_input_modified, add='+')
        self._schedule_reading_refresh()

    def _on_input_modified(self, event=None):
        """Handle <<Modified>> on the input box, then re-arm the flag.

        Tk only fires this when the flag flips False -> True, so it has to be
        cleared to hear about the next change. Clearing it fires the event a
        second time; the debounce collapses the pair into one refresh.
        """
        try:
            self.original_text.edit_modified(False)
        except tk.TclError:
            return
        self._sync_main_empty()
        pending = getattr(self, '_main_pending_source', None)
        runner = getattr(self, '_translation_task_runner', None)
        if pending is not None and runner and self.original_text.get('1.0', 'end-1c').strip() != pending:
            runner.cancel(owner=self.popup)
        self._schedule_reading_refresh()

    def _schedule_reading_refresh(self, delay_ms: int = READING_PANE_DEBOUNCE_MS):
        """Debounce a pane refresh: annotation runs on the UI thread."""
        if not self.popup:
            return
        if self._reading_pane_job:
            from src.utils.ui_helpers import cancel_window_callback
            cancel_window_callback(self.root, self.popup, self._reading_pane_job)
        try:
            from src.utils.ui_helpers import schedule_window_callback
            self._reading_pane_job = schedule_window_callback(
                self.root, self.popup, delay_ms, self._refresh_reading_pane)
        except tk.TclError:
            self._reading_pane_job = None

    def _refresh_reading_pane(self):
        self._reading_pane_job = None
        from src.ui.deferred_resize import defer_update
        if defer_update(getattr(self, 'popup', None), 'main-reading', self._refresh_reading_pane):
            return
        if not self._reading_pane_alive():
            return
        self._refresh_source_row()
        self._refresh_source_state()
        text = self.original_text.get('1.0', 'end-1c')
        hint = lang_id.JAPANESE if self._input_source() == lang_id.JAPANESE else None
        self._inline_reading.update(text, hint, self._ruby_enabled() and len(text) <= MAX_ANNOTATE_CHARS)

    def _apply_reading_pane_state(self):
        self._schedule_reading_refresh(delay_ms=1)

    def _reading_is_expanded(self):
        return self._ruby_enabled()

    def _sync_translator_compact(self, mode=None):
        if getattr(self, 'popup', None) is None:
            return
        try:
            ctx = components.get()
            compact = self.popup.winfo_height() / (ctx.scale * ctx.text_scale) < 520
            if compact != getattr(self, '_reading_auto_collapsed', False):
                self._reading_auto_collapsed = compact
                self._reading_compact_override = False
                self._apply_reading_pane_state()
            area = getattr(self, 'attachment_area', None)
            if area is not None:
                area.set_compact(compact)
        except (AttributeError, TypeError, tk.TclError):
            pass

    def _reading_pane_alive(self) -> bool:
        try:
            return bool(getattr(self, '_inline_reading', None) and self.original_text.winfo_exists())
        except tk.TclError:
            return False

    def _source_selection_changed(self, language):
        if self._main_translation_pending():
            self._cancel_main_translation()
        self._main_pending_source = None
        self._schedule_reading_refresh(delay_ms=1)

    def _open_expanded_translation(self):
        """Open translation in expanded fullscreen window."""
        # get_plain(): get() would hand over text with every annotated word
        # deleted, so the expanded window would show a broken sentence.
        translated = self.trans_text.get_plain().strip()
        self.expanded_window.show(translated, self.selected_language,
                                  source_language=self._input_source())

    def _copy_translation(self):
        """Copy translation to clipboard."""
        # get_plain(), never get() - see _open_expanded_translation.
        translated = self.trans_text.get_plain().strip()
        if not translated:
            self.toast.show_warning("No translation to copy")
            return
        pyperclip.copy(translated)
        self.copy_btn.set_icon('check-lg')
        self.copy_btn.configure(text="Copied")
        from src.utils.ui_helpers import schedule_window_callback
        schedule_window_callback(self.root, self.popup, 1000, self._reset_copy_button)

    def _reset_copy_button(self):
        try:
            self.copy_btn.configure(text="Copy")
            self.copy_btn.set_icon('copy')
        except tk.TclError:
            pass        # the window closed meanwhile

    def _update_dict_button_state(self):
        """Update Dictionary button state based on NLP availability."""
        if hasattr(self, 'dict_btn') and self.dict_btn:
            self.dictionary_popup.update_button_state(self.dict_btn)

    def _open_dictionary_popup(self):
        """Open dictionary popup window with word buttons for original text."""
        original = self.original_text.get('1.0', tk.END).strip()
        self.dictionary_popup.open_from_text(original)

    def _refresh_source_row(self):
        """The main window's Japanese / Chinese row, beside "Original:" (K5).

        The popup's rule (`source_marker.source_row`, Decision 29) applied to the
        input box: ask once a translation of exactly this text came back without
        a verdict; offer the other language for a term a model or the user
        placed. Untranslated, unplaced kanji ask nothing - typing is not a
        question. Shown with furigana off too: the answer changes the meaning.
        """
        row = getattr(self, '_source_row', None)
        try:
            if row is None or not row.winfo_exists():
                return
            text = self.original_text.get('1.0', 'end-1c').strip()
        except tk.TclError:
            return
        verdict = self.translation_service.source_verdict(text) if text else None
        picker = getattr(getattr(self, '_view', None), 'source_chip', None)
        mode = None if picker is not None and isinstance(picker.value, str) and picker.value != 'Auto' else source_row(text, verdict)
        if mode == ROW_ASK and text != getattr(self, '_translated_input', None):
            mode = None
        state = (mode, lang_id.normalize_language(verdict) if mode == ROW_CHANGE else None)
        if state == getattr(self, '_source_row_state', None):
            return
        self._source_row_state = state
        for child in row.winfo_children():
            child.destroy()
        if mode:
            fill_source_row(row, mode, verdict, self._choose_input_source, plane='surface')
            row.pack(side=LEFT, pady=(0, 8))
        else:
            row.pack_forget()

    def _refresh_source_state(self):
        """What the source card says about its text: the chip over it (the language, when it is
        settled), whether swap has a language to swap to, how long the text is, and the font it
        is set in. Never a guess - `_input_source()` is evidence or a verdict, nothing else."""
        view = getattr(self, '_view', None)
        if view is None:
            return
        try:
            text = self.original_text.get('1.0', 'end-1c')
            language = self._input_source()
            label = (language_label(language) if language == lang_id.JAPANESE or language in lang_id.CHINESE
                     else language)
            view.set_source_label(label)
            translated = self.trans_text.get_plain() if getattr(self, 'trans_text', None) else ''
            has_result = bool(translated.strip()) and not is_error_text(translated)
            reason = None if has_result else ('Choose a supported source language to swap.' if not self._swap_target_language() else 'Translate text before swapping.')
            view.set_swap_enabled(has_result, reason=reason)
            view.set_count(len(text.strip()), len(text.split()))
            view.set_input_script(type_ramp.script_for_language(lang_id.normalize_language(language)))
        except tk.TclError:
            pass        # the window closed meanwhile

    def _retheme_translator(self, manager):
        """A theme change while the Translator is open: the boxes the app built take the new
        palette (the view recolours its own)."""
        ctx = components.get()
        p = manager.palette
        self._sync_translator_compact()
        try:
            if getattr(self, 'trans_text', None) and self.trans_text.winfo_exists():
                self.trans_text.recolour(bg=ctx.plane_colour('surface'), base_fg=p.text,
                                         kanji_fg=p.text, ruby_fg=p.ruby, selection=p.selection)
            if self._reading_pane_alive():
                self._schedule_reading_refresh(delay_ms=1)
        except tk.TclError:
            return

    def _choose_input_source(self, language: str):
        """An answer from the main window's row (K5): kept as the input's verdict,
        then the input is translated again with it stated - the popup's rule
        (Decision 27). Ignored while a translation is running."""
        text = self.original_text.get('1.0', tk.END).strip()
        if not text:
            return
        try:
            if str(self.translate_btn.cget('state')) == 'disabled':
                return
        except (tk.TclError, AttributeError):
            return
        logging.info(f"[LangID] user chose source={language} chars={len(text)} "
                     f"via=main-window")
        self.translation_service.remember_user_source(text, language)
        self._schedule_reading_refresh(delay_ms=1)
        self._do_retranslate(source_language=language)

    def _on_source_chosen(self, text: str):
        """The user said which language a text's kanji are (the Dictionary's
        dialog, K3): the Reading pane reads the input again if it is that text."""
        if self._reading_pane_alive():
            self._schedule_reading_refresh(delay_ms=1)

    def _show_settings_hotkeys_tab(self):
        """Open Settings at the Replace button's setting.

        Used by popup gear icon to quickly access Replace mode.
        """
        self._show_settings_tab("Translation", highlight="replace_mode")

    def _focus_settings_window(self):
        """Bring Settings window to front and focus it."""
        if self.settings_window and hasattr(self.settings_window, 'window'):
            try:
                win = self.settings_window.window.winfo_toplevel()
                if win and win.winfo_exists():
                    win.deiconify()  # Restore if minimized
                    win.attributes('-topmost', True)
                    win.update()
                    win.attributes('-topmost', False)
                    win.lift()
                    win.focus_force()
            except tk.TclError:
                pass  # Window destroyed

    def _build_home_history(self, container):
        return HistoryDialog(self.popup, self.translation_service.history_manager,
            self._request_open_in_translate, furigana_enabled=self._ruby_enabled(), container=container,
            on_close=lambda: self._view.workspace.select_home('translate'), config=self.config)

    def _build_home_dictionary(self, container):
        from src.ui.home_dictionary import HomeDictionary
        return HomeDictionary(container, self.root, self.config, self.translation_service, self.toast,
            self.quick_translate_manager, self._show_settings_tab, self._request_open_in_translate)

    def _open_home_dictionary(self):
        from src.ui.home_dictionary import HomeDictionary
        panel = self._open_home_quick_panel('dictionary', 'Dictionary', self._view.dict_btn)
        if panel is None:
            return
        page = HomeDictionary(panel.content, self.root, self.config, self.translation_service, self.toast,
            self.quick_translate_manager, self._show_settings_tab, self._request_open_in_translate,
            compact=True, on_close=panel.close)
        panel.controller = page
        try:
            text = self.original_text.get('sel.first', 'sel.last')
        except tk.TclError:
            text = self.original_text.get('1.0', 'end-1c')
        if text.strip():
            page.receive_text(text, source_language=self._input_source(), target_language=self.selected_language)
            page.analyze()
        else:
            page.input.focus_set()
        return page

    def _open_history(self):
        panel = self._open_home_quick_panel('history', 'Choose a translation', self._view.history_btn)
        if panel is None:
            return
        def choose(item):
            panel.close()
            self._request_open_in_translate(item)
        page = HistoryDialog(self.popup, self.translation_service.history_manager, choose,
            furigana_enabled=self._ruby_enabled(), container=panel.content, on_close=panel.close,
            config=self.config, quick_pick=True)
        panel.controller = page
        page.search_entry.focus_set()
        return page

    def _open_home_quick_panel(self, kind, title, anchor):
        from src.ui.home_quick_panel import HomeQuickPanel
        current = getattr(self, '_home_quick_panel', None)
        if current is not None and not current.closed:
            same = current.kind == kind
            current.close()
            if same:
                return None
        self.show_main_window()
        panel = HomeQuickPanel(self._view.workspace.translate_host, title, anchor)
        panel.kind = kind
        self._home_quick_panel = panel
        return panel

    def _request_open_in_translate(self, item):
        from src.ui import components as ui
        incoming = item.get('original', '')
        source_only = bool(item.get('dictionary_source_only'))
        attachments = getattr(self, 'attachment_area', None)
        def snapshot():
            return (self.original_text.get('1.0', 'end-1c'), self.trans_text.get_plain(),
                    tuple(repr(value) for value in attachments.get_attachments()) if attachments else (),
                    self._main_translation_pending())
        before = snapshot()
        current, translated, files, pending = before
        same_source = current == incoming
        if source_only and same_source and translated.strip() and not files and not pending:
            self._view.workspace.select_home('translate')
            return
        def apply():
            if snapshot() != before:
                return self._request_open_in_translate(item)
            if attachments and files:
                attachments.clear()
            self._load_history_item(item)
            self._view.workspace.select_home('translate')
        different_result = bool(translated.strip()) and translated != item.get('translated', '')
        if (current.strip() and not same_source) or different_result or files or pending:
            dialog = ui.Dialog(self.popup, 'Replace the current translation?',
                               'This replaces the current text and clears attached files. Cancel keeps your work.')
            dialog.add_button('Cancel', default=True)
            dialog.add_button('Open in Translate', apply)
            dialog.show()
            return dialog
        apply()

    def _load_history_item(self, item):
        """Load a history item into the translator."""
        if not self.popup:
            return
        runner = getattr(self, '_translation_task_runner', None)
        if runner:
            runner.cancel(owner=self.popup)
            
        self.original_text.delete('1.0', tk.END)
        self.original_text.insert('1.0', item.get('original', ''))
        
        self._select_language_in_list(item.get('target_lang', 'English'))
        picker = getattr(getattr(self, '_view', None), 'source_chip', None)
        if picker is not None:
            picker.set(item.get('source_choice') or 'Auto')
        if getattr(self, '_inline_reading', None) is not None:
            self._schedule_reading_refresh(delay_ms=1)

        value = self.translation_service.history_manager.reading_result(item)
        self._update_translation_with_original(value, source_text=item.get('original', ''))

    def _setup_drop_handling(self, popup_window):
        """Setup drag-and-drop handling for the popup window."""
        # Configure drop handler with current popup and attachment area
        self.drop_handler.set_popup(popup_window)
        self.drop_handler.set_attachment_area(
            self.attachment_area if hasattr(self, 'attachment_area') else None
        )

        # Use tkinterdnd2 if available (built into root window), otherwise use windnd
        # NOTE: Only use ONE drop method to avoid conflicts that can break keyboard input
        if HAS_DND:
            # tkinterdnd2 is already set up on AttachmentArea, just need queue checker
            logging.info("Using tkinterdnd2 for drag-and-drop (via AttachmentArea)")
        elif HAS_WINDND:
            # Use windnd as fallback for Toplevel windows
            self.drop_handler.setup_windnd(popup_window)
            logging.info("Using windnd for drag-and-drop")
        else:
            logging.warning("No drag-and-drop library available")

        # Start queue checker for windnd (processes drops from Windows thread)
        self.drop_handler.start_queue_checker()

    def _on_audio_hotkey(self):
        """The audio shortcut (Decision 88): it opens the audio panel, and while the panel is open it records or
        stops; with Settings' "Hold to record" it records while held, as before."""
        if self.config.get('audio_hotkey_action', 'panel') == 'hold':
            self.audio_controller.start_recording()
            return
        panel = self._audio_panel()
        if panel.is_open():
            panel.hotkey()
        else:
            panel.open()

    def _audio_panel(self):
        panel = getattr(self, 'audio_panel', None)
        if panel is None:
            from src.ui.audio_panel import AudioPanel
            panel = self.audio_panel = AudioPanel(
                self.root, self.audio_controller, self.config,
                on_settings=lambda: self._show_settings_tab('API Key', highlight='audio_mode'),
                on_change=self._audio_panel_changed)
            self.audio_controller.result_anchor = panel.rect
            self.audio_controller.front_window = lambda: panel.window if panel.is_open() else None
        return panel

    def _audio_panel_changed(self, key):
        """The panel stored an option: an open Settings window shows it too."""
        settings = self.settings_window
        try:
            page = settings.pages.get('providers') if settings is not None and settings.window.winfo_exists() else None
        except tk.TclError:
            page = None
        section = getattr(page, 'audio_section', None)
        if section is not None:
            section.refresh()

    def _sync_audio_panel(self):
        panel = getattr(self, 'audio_panel', None)
        if panel is not None and panel.is_open():
            panel.sync_from_config()

    def show_audio_panel(self, icon=None, item=None):
        """Tray > Audio panel: the panel at the cursor, whatever the shortcut does."""
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self.show_audio_panel(icon, item))
            return
        if self.config.get('audio_enabled', True) is not True:
            self.toast.show_warning('Audio translation is disabled. Enable it in Settings > API keys.')
            return
        if getattr(self, 'audio_controller', None) is not None:
            self._audio_panel().open()

    def show_settings(self, icon=None, item=None):
        """Show settings window."""
        # Ensure runs on main thread
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self.show_settings(icon, item))
            return

        self.show_main_window()
        workspace = self._view.workspace
        if self.settings_window and self.settings_window.window.winfo_exists():
            workspace.settings = self.settings_window
            workspace.show_primary('settings')
            return

        def on_api_change():
            """Called when API keys change; refresh configured providers and capabilities."""
            self.translation_service.reconfigure()
            # Refresh attachment area in case API capabilities changed
            self._refresh_attachment_area()
            # Refresh tray menu to show/hide screenshot hotkey based on vision capability
            self._refresh_tray_menu()
            # An audio key decides whether the open audio panel offers One-step
            self._sync_audio_panel()

        self.hotkey_manager.suspended = False
        self.settings_window = SettingsWindow(
            self.root, self.config, self._on_settings_changed, on_api_change,
            update_manager=self.update_manager, is_translation_busy=self._translation_is_busy,
            on_shortcut_recording=self._on_settings_recording,
            shortcut_status=lambda: dict(self.hotkey_manager.registration),
            on_show_welcome=self._show_welcome, container=workspace.settings_host,
            on_close=lambda: workspace.show_primary('home'))
        workspace.settings = self.settings_window
        workspace.show_primary('settings')


    # Settings that change which hotkeys are registered, and the ones the tray menu shows.
    HOTKEY_SETTINGS = frozenset({'shortcuts_enabled', 'language_shortcuts', 'screenshot_shortcut', 'fix_grammar_shortcut', 'audio_shortcut',
                                 'audio_enabled', 'restore_defaults'})
    TRAY_SETTINGS = frozenset({'shortcuts_enabled', 'fix_grammar_button', 'restore_defaults', 'ui_language',
                               'audio_enabled'})
    # What an open audio panel shows: its options, and the audio keys that decide whether One-step is offered.
    AUDIO_PANEL_SETTINGS = frozenset({'audio_mode', 'audio_source', 'audio_language', 'audio_model', 'api_keys',
                                      'restore_defaults'})
    HOTKEY_REFRESH_DELAY_MS = 400           # several shortcuts changed in a row register once

    def _open_logs(self):
        from src.utils.diagnostics import open_logs_folder
        open_logs_folder()

    def _on_settings_changed(self, key):
        """The Settings window stored a change (a `settings_index` key): act on it. The window never
        reaches into the application; this is where the application answers."""
        if key == "debug_logging":
            from src.utils.logging_setup import set_debug_logging
            set_debug_logging(self.config.get_debug_logging())
            return
        if key in self.HOTKEY_SETTINGS:
            self._schedule_hotkey_refresh()
        if key in {'audio_enabled', 'restore_defaults'}:
            self._refresh_attachment_area()
        if key in self.AUDIO_PANEL_SETTINGS:
            self._sync_audio_panel()
        if key == 'restore_defaults':
            self.root.event_generate('<<LinkedReadingChanged>>', when='tail')
        if key in self.TRAY_SETTINGS:
            self._refresh_tray_menu()
        if key in {'furigana', 'restore_defaults'} and self._reading_pane_alive():
            self._schedule_reading_refresh(delay_ms=1)
        view = getattr(self, '_view', None)
        if key in {'fix_grammar_button', 'restore_defaults'} and view is not None:
            view.set_fix_grammar_visible(self.config.get_fix_grammar_enabled())
            if view.fix_grammar_btn is not None:
                self.fix_grammar_btn = view.fix_grammar_btn
            else:
                self.__dict__.pop('fix_grammar_btn', None)

    def _on_settings_recording(self, active):
        """While a shortcut is being recorded the hotkeys stay registered but do nothing: recording the
        one that translates must not translate."""
        self.hotkey_manager.suspended = bool(active)

    def _schedule_hotkey_refresh(self):
        if getattr(self, '_hotkey_refresh_job', None) is not None:
            self.root.after_cancel(self._hotkey_refresh_job)
        self._hotkey_refresh_job = self.root.after(self.HOTKEY_REFRESH_DELAY_MS, self._start_hotkey_refresh)

    def _start_hotkey_refresh(self):
        """Register the hotkeys again. That stops and restarts the manager's thread, which can take two
        seconds, so it runs off the UI thread; the UI thread polls for the end (a worker never calls Tk,
        Decision 43)."""
        self._hotkey_refresh_job = None
        if getattr(self, '_hotkey_refresh_busy', False):
            self._hotkey_refresh_again = True
            return
        self._hotkey_refresh_busy = True
        finished = threading.Event()

        def work():
            try:
                self.hotkey_manager.unregister_all()
            except Exception:
                logging.exception("[Hotkeys] could not register the hotkeys again")
            finally:
                finished.set()
        threading.Thread(target=work, daemon=True, name='HotkeyRefresh').start()
        self._poll_hotkey_refresh(finished)

    def _poll_hotkey_refresh(self, finished):
        if not finished.is_set():
            self.root.after(50, lambda: self._poll_hotkey_refresh(finished))
            return
        self._hotkey_refresh_busy = False
        if getattr(self, '_hotkey_refresh_again', False):
            self._hotkey_refresh_again = False
            self._start_hotkey_refresh()
            return
        self._refresh_tray_menu()
        settings = self.settings_window
        try:
            if settings is not None and settings.window.winfo_exists():
                settings.refresh_shortcut_status()
        except tk.TclError:
            pass

    def _refresh_tray_menu(self):
        """Refresh tray menu to reflect updated hotkeys."""
        self.tray_manager.refresh_menu()

    def _translation_is_busy(self):
        audio = getattr(self, 'audio_controller', None)
        if audio and audio.busy:
            return True
        runner = getattr(self, '_translation_task_runner', None)
        return bool(runner and runner.busy)

    def _updates_are_busy(self):
        settings = self.settings_window
        return (self._translation_is_busy()
                or bool(settings and settings._download_active)
                or self.root.grab_current() is not None)

    def _update_state_changed(self):
        self.tray_manager.set_update_available(self.update_manager.available_version)
        if self.settings_window:
            self.settings_window.refresh_update_state()

    def _open_available_update(self, version, updater):
        self.show_settings()
        self.settings_window.open_page('about')
        self.settings_window.offer_update(version, updater)

    def _attachments_take_drops(self, view):
        """The whole source card is the drop zone, not only the strip that says so."""
        for widget in (view.source_card, view.original_text):
            self.attachment_area.accept_drops_on(widget)

    def _refresh_attachment_area(self):
        """Refresh attachment area based on current API capabilities.

        Called when API keys are saved to immediately show/hide attachments icon
        without requiring user to close and reopen the main window.
        """
        # Only refresh if popup window exists and is visible
        if not hasattr(self, 'popup') or not self.popup or not self.popup.winfo_exists():
            return

        # Check current API capabilities
        has_vision = self.config.has_any_vision_capable()
        has_file = self.config.has_any_file_capable()
        audio_checker = getattr(self.config, 'has_usable_audio_keys', None) or getattr(self.config, 'has_any_audio_capable', lambda: False)
        has_audio = (self.config.get('audio_enabled', True) is True) and (audio_checker() is True)

        if has_vision or has_file or has_audio:
            # Should show attachment area
            if not self.attachment_area:
                try:
                    # Create new AttachmentArea
                    from src.ui.attachments import AttachmentArea
                    self.popup.update_idletasks()
                    self.attachment_area = AttachmentArea(
                        self._view.attach_host, self.config, on_change=None,
                        on_open_settings_tab=self._show_settings_tab
                    )
                    self.attachment_area.pack(fill=X)
                    self._attachments_take_drops(self._view)
                except Exception as e:
                    logging.error(f"Error creating AttachmentArea during refresh: {e}")
                    self.attachment_area = None
        else:
            # Should hide attachment area
            if self.attachment_area:
                try:
                    self.attachment_area.pack_forget()
                    self.attachment_area.destroy()
                except Exception:
                    pass
                self.attachment_area = None

        self._view.set_attachments_visible(self.attachment_area is not None)

    def _create_tray_icon(self):
        """Create system tray icon."""
        self.tray_icon = self.tray_manager.create()
        return self.tray_icon

    def quit_app(self, icon=None, item=None):
        """Quit the application with proper cleanup."""
        audio = getattr(self, 'audio_controller', None)
        if audio and audio.files is not None:
            audio.request_close('quit', self.quit_app)
            return
        logging.info("Quitting application...")
        self.running = False
        if getattr(self, 'audio_controller', None):
            self.audio_controller.shutdown()
        self.update_manager.stop()
        if getattr(self, 'onboarding', None):
            self.onboarding.close()
        try:
            self.theme_manager.stop_watching()
        except Exception as e:
            logging.warning(f"Error stopping the theme watcher: {e}")

        # Cleanup hotkeys
        try:
            self.hotkey_manager.cleanup()
        except Exception as e:
            logging.error(f"Error cleaning up hotkeys: {e}")

        # Stop drop handler
        try:
            self.drop_handler.stop()
        except Exception as e:
            logging.warning(f"Error stopping drop handler: {e}")

        # Close quick translate popup
        self.close_quick_translate()
        self._cleanup_pending_screenshot()

        # Close popup
        if self.popup:
            try:
                if self.popup.winfo_exists():
                    self.popup.destroy()
            except Exception as e:
                logging.warning(f"Error destroying popup: {e}")

        # Stop tray icon
        try:
            self.tray_manager.stop()
        except Exception as e:
            logging.warning(f"Error stopping tray: {e}")

        # Quit root window
        try:
            if self.root.winfo_exists():
                self.root.quit()
        except Exception as e:
            logging.warning(f"Error quitting root: {e}")

        logging.info("Application shutdown complete")
        os._exit(0)

    def _check_queue(self):
        """Check translation queue for results with error handling."""
        if isinstance(getattr(self.translation_service, 'key_usage_events', None), queue.SimpleQueue):
            if self.translation_service.flush_key_usage():
                settings = getattr(self, 'settings_window', None)
                if settings is not None:
                    providers = settings.pages.get('providers')
                    if providers is not None and hasattr(providers, 'refresh_usage'):
                        providers.refresh_usage()
        try:
            while True:
                # Queue item format: (original, translated, target_lang, trial_info, furigana_text)
                result = self.translation_service.translation_queue.get_nowait()
                task = getattr(result, 'task', None)
                if task and (not task.is_current() or task.remaining() <= 0):
                    continue
                furigana_text = None
                is_grammar = False
                if len(result) == 6:
                    # Grammar-fix result: (original, corrected, label, trial_info, furigana, is_grammar)
                    original, translated, target_lang, trial_info, furigana_text, is_grammar = result
                elif len(result) == 5:
                    original, translated, target_lang, trial_info, furigana_text = result
                elif len(result) == 4:
                    original, translated, target_lang, trial_info = result
                else:
                    # Backward compatibility
                    original, translated, target_lang = result
                    trial_info = None
                if self.running:
                    credit = {'model_info': result.attribution or ''} if task else {}
                    self.show_quick_translate(original, translated, target_lang, trial_info,
                                              furigana_text, is_grammar=is_grammar, **credit)
        except queue.Empty:
            pass
        except Exception as e:
            logging.error(f"Error processing queue: {e}")

        # Schedule next check if still running
        if self.running:
            try:
                if self.root.winfo_exists():
                    self.root.after(100, self._check_queue)
            except Exception as e:
                logging.error(f"Error scheduling queue check: {e}")

    def _watchdog_check(self):
        """Lightweight watchdog to monitor app health."""
        if not self.running:
            return

        try:
            # Log heartbeat every 5 minutes
            current_time = time.time()
            if not hasattr(self, '_last_heartbeat'):
                self._last_heartbeat = current_time

            if current_time - self._last_heartbeat >= 300:  # 5 minutes
                logging.info(f"Heartbeat: App running for {int((current_time - self._app_start_time) / 60)} minutes")
                self._last_heartbeat = current_time

            # Schedule next watchdog check (every 60 seconds)
            self.root.after(60000, self._watchdog_check)

            # A running app takes the Worker's newer table too, not only at start-up (Decision 103); after the
            # scheduling above, so that a failure here cannot stop the watchdog
            from src.core.remote_config import get_config
            get_config().refresh_if_stale()
        except Exception as e:
            logging.error(f"Watchdog error: {e}")


    def _show_settings_tab(self, tab_name: str, highlight: Optional[str] = None):
        """Open settings window and navigate to a specific page.

        Args:
            tab_name: Name of the page to open (e.g., "API Key", "Dictionary", "General"; the names the
                tabs had before the redesign still work)
            highlight: a `settings_index` key whose row to scroll to and mark
        """
        self.show_settings()

        def try_open_tab(attempts=3):
            if attempts <= 0:
                return
            if self.settings_window and hasattr(self.settings_window, 'open_tab'):
                self.settings_window.open_tab(tab_name, highlight=highlight)
                self._focus_settings_window()
            else:
                self.root.after(200, lambda: try_open_tab(attempts - 1))

        self.root.after(100, try_open_tab)

    def _show_api_error(self):
        """Guide the user to their own API keys; there is no free-trial path."""
        APIErrorDialog(self.root, on_open_settings_tab=self._show_settings_tab)




    def _startup_api_check(self):
        """Perform a one-time API check on startup and cache results."""
        try:
            has_working_api = False
            has_vision_api = False
            
            api_keys = self.config.get_api_keys()
            manager = AIAPIManager()
            
            for config in api_keys:
                key = config.get('api_key', '').strip()
                model = config.get('model_name', '').strip()
                provider = config.get('provider', 'Auto')
                
                if key:
                    try:
                        manager.test_connection(model, key, provider)
                        self.config.api_status_cache[key] = True
                        has_working_api = True

                        # Check vision capability: use stored flag from Test, heuristic fallback
                        vision_flag = config.get('vision_capable')
                        if vision_flag is True:
                            has_vision_api = True
                        elif vision_flag is None:
                            target_provider = manager._identify_provider(model, key) if provider == 'Auto' else provider.lower()
                            if MultimodalProcessor.is_vision_capable(model, target_provider):
                                has_vision_api = True

                    except Exception as e:
                        logging.debug(f"API check failed for {model}: {e}")
                        self.config.api_status_cache[key] = False
            
            # Update runtime capabilities
            self.config.runtime_capabilities['file'] = has_working_api # Any working API can handle text files
            self.config.runtime_capabilities['vision'] = has_vision_api
            
            logging.info(f"Startup Check Results - Vision Capable: {has_vision_api}, File Capable: {has_working_api}")
            
        except Exception as e:
            logging.error(f"Startup API check failed: {e}")
            # Keep defaults (False) if check fails

    def run(self):
        """Run the application."""
        print("=" * 50)
        logging.info(f"CrossTrans v{VERSION}")
        print(f"CrossTrans v{VERSION}")
        print("=" * 50)
        print()
        print("Hotkeys:")
        for lang, hotkey in self.config.get_hotkeys().items():
            print(f"  {hotkey} → {lang}")
            logging.info(f"Hotkey: {hotkey} -> {lang}")
        print()
        print("Select any text, then press a hotkey to translate!")
        print()
        print("Listening...")
        print("-" * 50)

        # Track app start time for watchdog
        self._app_start_time = time.time()

        # First-run setup explains missing keys before entry into Home.

        # Start hotkey manager thread (registers hotkeys internally)
        self.hotkey_manager.start()
        self.hotkey_manager._ready_event.wait(timeout=2.0)  # Wait for hotkeys to be registered

        # Setup tray icon - use non-blocking approach
        self._create_tray_icon()

        # Run tray icon on separate thread with proper exception handling
        def run_tray_safe():
            try:
                self.tray_icon.run()
            except Exception as e:
                logging.error(f"Tray icon error: {e}")

        tray_thread = threading.Thread(target=run_tray_safe, daemon=True)
        tray_thread.start()

        # Pre-warm in background (non-blocking): the reading engine so the first
        # furigana render does not load dictionaries on the UI thread, and the
        # NLP manager so the Dictionary tab opens instantly.
        def prewarm_background():
            try:
                # Skipped when the feature is off - nothing would ever annotate,
                # so the dictionary read would be pure startup disk I/O.
                if self._ruby_enabled():
                    furigana.prewarm()
                    logging.info("Furigana pre-warming complete")
            except Exception as e:
                logging.debug(f"Furigana prewarm failed (non-critical): {e}")

            try:
                from src.core.nlp_manager import nlp_manager
                languages = nlp_manager.get_installed_languages()
                logging.info(f"NLP pre-warming complete: {len(languages)} languages cached")
            except Exception as e:
                logging.debug(f"NLP prewarm failed (non-critical): {e}")

        prewarm_thread = threading.Thread(target=prewarm_background, daemon=True, name="Prewarm")
        prewarm_thread.start()

        # Note: Update check is already triggered in __init__ via _startup_update_check()

        # Run one-time startup API check (temporarily disabled)
        # threading.Thread(target=self._startup_api_check, daemon=True).start()

        # Start queue checker
        self.root.after(100, self._check_queue)

        # Start watchdog
        self.root.after(60000, self._watchdog_check)

        logging.info(f"[Boot] Hotkeys ready ({len(self.hotkey_manager.registration)}); registration={self.hotkey_manager.registration}; onboarding_pending={self.onboarding.state.should_show}")
        failed_hotkeys = {k: v for k, v in self.hotkey_manager.registration.items() if v != 'ok'}
        if failed_hotkeys:
            logging.warning(f"[Hotkeys] Unregistered or conflicted hotkeys at startup: {failed_hotkeys}")
        self.onboarding.schedule()

        # Run main loop
        try:
            logging.info("Starting main loop")
            self.root.mainloop()
        except KeyboardInterrupt:
            logging.info("Keyboard interrupt received")
        except Exception as e:
            logging.critical(f"Main loop error: {e}", exc_info=True)
        finally:
            self.quit_app()
