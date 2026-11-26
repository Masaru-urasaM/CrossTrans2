"""
Quick Translate Manager for CrossTrans.
Handles translation result popups and loading indicators.
"""
import ctypes
from ctypes import wintypes
import logging
import math
import re
import time
import tkinter as tk
from tkinter import BOTH, X, Y, LEFT, RIGHT, TOP, BOTTOM
from tkinter import font
from typing import Tuple, Optional, Callable

try:
    import ttkbootstrap as ttk
    HAS_TTKBOOTSTRAP = True
except ImportError:
    from tkinter import ttk
    HAS_TTKBOOTSTRAP = False

from src.constants import (POPUP_COLLAPSED_ORIGINAL_ROWS, POPUP_FRONT_MS,
                           POPUP_LAYOUT_WATCHDOG_MS,
                           POPUP_MIN_BOX_ROWS, POPUP_REVEAL_DELAY_MS, POPUP_SCREEN_MARGIN,
                           POPUP_USER_MIN_ROWS)
from src.core import lang_id
from src.core.nlp_manager import nlp_manager
from src.core.result_status import is_error_text
from src.core.source_marker import (ROW_CHANGE, language_label, other_language,
                                    source_row)
from src.ui import components as ui
from src.ui.i18n import tr
from src.ui import popup_errors, popup_layout
from src.ui.ruby_text import NO_AUTOSTYLE, RubyText, insert_output
from src.ui.linked_reading import LinkedReadingController
from src.core.translation_alignment import validate_metadata
from src.ui.theme import fonts as type_ramp
from src.ui.theme import tokens, windows  # noqa: F401 - kept: tests may patch this module attribute
from src.ui.native_resize import WindowsSizing
from src.ui.deferred_resize import DeferredContentResize, defer_update
from src.ui.pointer_resize import PointerResize, PointerState
from src.utils.ui_helpers import schedule_window_callback, cancel_window_callback

# The dictionary result window's padding, which calculate_size() still sizes it with.
# The quick-translate popup measures itself instead (Decision 34).
HORIZONTAL_PADDING = 50  # frame(30) + scrollbar(20)

# The quick-translate popup's frame padding, each side, in px at 100 % (`Context.px`
# scales it), and the hairline round the popup - the toplevel's own highlight border,
# which insets everything inside it by exactly its thickness (measured).
POPUP_FRAME_PADDING = 16
POPUP_RING_PX = 1
POPUP_ICON_PX = 16
ARROW = chr(0x2192)           # "Japanese -> English" in the header chip

# The "Translated with ..." credit at the top of the popup: a footnote, so the caption
# style (small, muted), not italic - Japanese has no italic. It shares the header row
# with the language chip and the close button and is cut with an ellipsis rather than
# ever crowding that button out.
HEADER_CREDIT_MIN_PX = 120    # what the credit keeps, at least, before it is cut
HEADER_CREDIT_MAX_PX = 300    # ...and the most the popup grows to show it whole
HEADER_GAP_PX = 8             # between the chip, the credit and the close button

# The action bar's icons are Bootstrap Icons images (`Context.icon`) - no icon font,
# so no fallback to words on a system without one (it used to be Segoe Fluent Icons or
# MDL2, TODO.md Q1). Each names its action in a tooltip.
ACTION_ICONS = {
    'retranslate': 'arrow-repeat',
    'dictionary': 'book',
    'open': 'box-arrow-up-right',
    'prompt': 'pencil',
    'linked': 'link-45deg',
    'settings': 'gear',
    'close': 'x-lg',
}

RESIZE_INTERVAL_MS = 16
RESIZE_SAVE_DELAY_MS = 100

# The popup resizes from every edge and corner, like a window (Decision 35): the outer
# RESIZE_EDGE_PX of the main frame's padding (POPUP_FRAME_PADDING), so no text and no
# button is in the way; along a side, RESIZE_CORNER_PX from a corner is the corner. The
# grip image stays as the visible hint; its double-click resets.
RESIZE_EDGE_PX = 6
RESIZE_CORNER_PX = 14
RESIZE_CURSORS = {'n': 'size_ns', 's': 'size_ns', 'e': 'size_we', 'w': 'size_we',
                  'nw': 'size_nw_se', 'se': 'size_nw_se', 'ne': 'size_ne_sw',
                  'sw': 'size_ne_sw'}

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000


def set_noactivate(window, on: bool = True) -> None:
    """Give `window` WS_EX_NOACTIVATE, or take it away. With it, clicking the window
    never takes the focus from the user's app - which Replace relies on."""
    try:
        hwnd = ctypes.c_void_p(windows.hwnd_of(window))
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        wanted = (style | WS_EX_NOACTIVATE) if on else (style & ~WS_EX_NOACTIVATE)
        if wanted != style:
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(wanted))
    except Exception:
        pass  # Non-critical: the window still works, it may just take the focus


def _pad_pair(widget, value) -> Tuple[int, int]:
    """A pack padding as (before, after): 4 -> (4, 4), (0, 4) -> (0, 4)."""
    if isinstance(value, (tuple, list)):
        parts = [int(float(part)) for part in value]
    elif isinstance(value, (int, float)):
        parts = [int(value)]
    else:
        parts = [int(float(part)) for part in widget.tk.splitlist(value)]
    return (parts[0], parts[-1]) if parts else (0, 0)


def packed_row_width(frame) -> int:
    """The width a row of side-by-side packed widgets asks for, read off the widgets.

    Each child's requested width, its internal padding and its pack padding on both
    sides - the sum the packer makes. Known as soon as the widgets are configured,
    before the popup is shown (Tk lays out only the children of a mapped window, but
    requested sizes are there from the start), so the popup can be made wide enough
    for its rows. A constant did this before - MIN_WIDTH 670 - and drifted as buttons
    were added; the close button, packed last, was the one squeezed out (TODO.md S1).
    """
    total = 0
    for child in frame.pack_slaves():
        info = child.pack_info()
        total += (child.winfo_reqwidth()
                  + sum(_pad_pair(frame, info.get('ipadx', 0)))
                  + sum(_pad_pair(frame, info.get('padx', 0))))
    return total


# The "Kanji-only text - read it as: [Japanese] [Chinese]" row (J6), shown when a
# translation of kanji-only text came back without a source language.


def fill_source_row(row, mode: str, verdict: Optional[str],
                    on_choose: Callable[[str], None], plane: str = 'surface') -> None:
    """The label and buttons of a Japanese / Chinese row - the popup's and the
    main window's (`source_marker.source_row` decides which):

    - ROW_ASK: "Kanji-only text - read it as: [Japanese] [Chinese]" (J6).
    - ROW_CHANGE: "Kanji-only text - read as Chinese. [Read as Japanese]" (K4):
      what a model - or the user, earlier - settled, and the way to the other.

    `on_choose` gets lang_id.JAPANESE or lang_id.CHINESE_SIMPLIFIED. `plane` draws the row
    with the design system's components on that plane ('bg', 'surface' or 'alt') - the
    popup and the Translator both use it.
    """
    if mode == ROW_CHANGE:
        text = f"Kanji-only text - read as {language_label(verdict)}."
        other = other_language(verdict)
        choices = ((other, f"Read as {language_label(other)}"),)
    else:
        text = "Kanji-only text - read it as:"
        choices = ((lang_id.JAPANESE, "Japanese"), (lang_id.CHINESE_SIMPLIFIED, "Chinese"))
    ui.Label(row, text=text, kind='muted', on=plane).pack(side=LEFT)
    if len(choices) == 1:
        language, label = choices[0]
        ui.Button(row, label, command=lambda chosen=language: on_choose(chosen),
                  kind='soft', on=plane).pack(side=LEFT, padx=(HEADER_GAP_PX, 0))
    else:
        ui.SegmentedControl(row, choices, on_change=on_choose,
                            on=plane).pack(side=LEFT, padx=(HEADER_GAP_PX, 0))


def _align_dictionary_text(result: str) -> str:
    """Align dictionary result text so labels and values form clean columns.

    Transforms:
        1. **Translation**: value
        2. **Source Language**: value

    Into tab-aligned format where all values start at the same column:
        1. **Translation**:      value
        2. **Source Language**:   value

    Only aligns numbered field lines (N. **Label**: value).
    Other lines (headers, examples, separators) pass through unchanged.
    """
    lines = result.split('\n')
    output = []

    # Process in chunks per word entry (between ## headers)
    chunk = []  # Lines in current entry

    def _align_chunk(chunk_lines):
        """Align numbered field lines within a single word entry."""
        if not chunk_lines:
            return []

        # Find max label width among numbered lines in this chunk
        max_label_len = 0
        for line in chunk_lines:
            m = re.match(r'^(\d+\.\s+\*\*.+?\*\*:)\s*', line)
            if m:
                max_label_len = max(max_label_len, len(m.group(1)))

        if max_label_len == 0:
            return chunk_lines  # No numbered fields found

        # Pad to nearest multiple of 4 + 2 for clean spacing
        pad_to = max_label_len + 2

        aligned = []
        for line in chunk_lines:
            m = re.match(r'^(\d+\.\s+\*\*.+?\*\*:)\s*(.*)', line)
            if m:
                label_part = m.group(1)
                value_part = m.group(2)
                # Pad label to align values
                aligned.append(label_part.ljust(pad_to) + value_part)
            else:
                aligned.append(line)
        return aligned

    for line in lines:
        if line.strip().startswith('## '):
            # Flush previous chunk
            output.extend(_align_chunk(chunk))
            chunk = [line]
        elif line.strip() == '---':
            # Flush chunk before separator
            output.extend(_align_chunk(chunk))
            chunk = []
            output.append(line)
        else:
            chunk.append(line)

    # Flush last chunk
    output.extend(_align_chunk(chunk))

    return '\n'.join(output)


def get_monitor_work_area(x: int, y: int) -> Tuple[int, int, int, int]:
    """Usable rectangle of the monitor nearest this virtual-screen point, if available."""
    return windows.monitor_work_area(x, y)


class _Look:
    """What one popup is drawn with, read from the design system when it is built. The popup
    lives for seconds, so it does not follow a theme change."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.pad = ctx.px(POPUP_FRAME_PADDING)
        self.ring = ctx.px(POPUP_RING_PX)
        self.edge = self.pad + self.ring         # from the popup's outer edge to its content
        self.gap = ctx.px(HEADER_GAP_PX)

    def px(self, value) -> int:
        return self.ctx.px(value)

    @property
    def p(self):
        return self.ctx.palette

    def font(self, name: str, script: str = 'latin') -> tuple:
        return self.ctx.font(name, script)


class QuickTranslateManager:
    """Manages popup display for translation results."""

    def __init__(self, root: tk.Tk, config=None):
        """Initialize quick translate manager.

        Args:
            root: The root Tk window for screen info and scheduling
            config: Config object for reading settings (e.g., replace mode)
        """
        self.root = root
        self.config = config
        self.popup: Optional[tk.Toplevel] = None
        self._content_resize = None
        self._deferred_user_gesture = False
        self.popup_text: Optional[RubyText] = None  # Read with get_plain(), not get()
        self.popup_furigana: Optional[RubyText] = None  # Read-only reading guide
        self.popup_attribution: Optional[ttk.Label] = None  # "Translated with ..." note
        self.popup_copy_btn: Optional[ttk.Button] = None
        self.popup_dict_btn: Optional[ttk.Button] = None
        self.popup_source_chooser: Optional[ttk.Frame] = None  # Japanese / Chinese row
        self.popup_linked_btn = None
        self._linked_status = None
        self._linked_setting_binding = None
        self._linked_tooltip = None
        self._linked_paused = False
        self._linked_active = True
        self._linked_controller = None

        # Mouse position captured when hotkey was pressed
        self._last_mouse_x = 0
        self._last_mouse_y = 0
        self._anchor_rect = None     # a window the next popups open under (the audio panel), else the cursor

        # Drag state
        self._drag_x = 0
        self._drag_y = 0
        self._move_active = False

        # Grammar-fix result flag (hides translation-only buttons in the popup)
        self._is_grammar = False

        # Dictionary mode state
        self._dict_mode_active = False
        self._dict_frame = None  # WordButtonFrame instance
        self._current_original = ""  # Store original text for dictionary
        self._current_translation = ""
        self._current_target_lang = ""
        self._current_trial_info = None  # Store trial info for title bar
        self._current_furigana = None  # Store furigana for restoring after custom-prompt cancel
        self._current_source_lang = None  # Verdict for the original text (Decision 22)
        self._main_frame = None  # Reference to main frame for dictionary mode
        self._dict_popup_frame = None  # Reference to dict popup's WordButtonFrame for animation

        # Loading animation state
        self._loading_animation_running = False
        self._loading_animation_step = 0
        self._loading_label = None
        self._loading_dots = None
        self._loading_target_lang = ""
        self._loading_base_text = ""  # Base phrase for loading animation (e.g. "Translating to X")
        self._loading_start_time = 0

        # Callbacks
        self._on_copy: Optional[Callable[[], None]] = None
        self._on_copy_and_replace: Optional[Callable[[], None]] = None
        self._on_re_translate: Optional[Callable[[], None]] = None
        self._on_custom_prompt_send: Optional[Callable[[str], None]] = None
        self._on_open_translator: Optional[Callable[[], None]] = None
        self._on_open_settings: Optional[Callable[[], None]] = None
        self._on_open_dictionary: Optional[Callable[[str, str, Optional[tk.Misc]], None]] = None
        self._on_choose_source: Optional[Callable[[str], None]] = None
        self.popup_replace_btn: Optional[tk.Button] = None
        self.popup_retranslate_btn: Optional[tk.Button] = None
        self.popup_custom_prompt_btn: Optional[tk.Button] = None
        self._replace_gear_btn: Optional[tk.Button] = None
        self._btn_frame: Optional[ttk.Frame] = None
        self._on_open_settings_hotkeys_tab: Optional[Callable[[], None]] = None

        # Layout (Decision 34): built at alpha 0, measured, then shown
        self._generation = 0               # bumped per popup; a stale step 2 sees it
        self._layout: Optional[dict] = None
        self.popup_close_btn = None
        self.popup_open_btn = None
        self.popup_original_toggle: Optional[tk.Label] = None
        self.popup_chip = None             # the language pair in the header
        self._error_notice = None          # the info bar of a failure
        self._original_header: Optional[ttk.Frame] = None
        self._original_holder: Optional[tk.Frame] = None
        self._original_scrollbar = None
        self._original_collapsed = True
        self._expanded_restore = None
        self._text_holder: Optional[tk.Frame] = None
        self._text_scrollbar = None
        self._trial_frame: Optional[ttk.Frame] = None
        self._attribution_text: Optional[str] = None
        self._tooltips = []                # kept for the tests; components own their tooltips
        self._ctx = None                   # the design system's context, read once per popup
        self._look: Optional[dict] = None  # this popup's palette, fonts and sizes
        self._popup_plane = 'surface'
        self._error_help: Optional[popup_errors.ErrorHelp] = None
        self._grip: Optional[tk.Label] = None
        self._resize_handles = {}
        self._grip_start: Optional[dict] = None
        self._grip_outline = None
        self._grip_pointer = None
        self._grip_preview_after = None
        self._resize_settle_job = None
        self._resize_save_job = None
        self._resize_pending_save = None
        self._resize_display = {}
        self._resize_final_width = None
        self._resize_gutter = 0
        self._native_sizing = None
        self._installing_native_sizing = False
        self._allow_activation = False
        self._credit_cache = None
        self._user_size: Optional[Tuple[int, int]] = None
        self._user_rect: Optional[Tuple[int, int, int, int]] = None

    def configure_callbacks(self,
                            on_copy: Optional[Callable[[], None]] = None,
                            on_copy_and_replace: Optional[Callable[[], None]] = None,
                            on_open_translator: Optional[Callable[[], None]] = None,
                            on_open_settings: Optional[Callable[[], None]] = None,
                            on_open_dictionary: Optional[Callable[[str, str, Optional[tk.Misc]], None]] = None,
                            on_open_settings_hotkeys_tab: Optional[Callable[[], None]] = None,
                            on_re_translate: Optional[Callable[[], None]] = None,
                            on_custom_prompt_send: Optional[Callable[[str], None]] = None,
                            on_choose_source: Optional[Callable[[str], None]] = None,
                            on_dismiss: Optional[Callable[[], None]] = None):
        """Configure callback functions for quick translate actions.

        Args:
            on_copy: Called when user clicks Copy button
            on_copy_and_replace: Called when user clicks Replace button (copy + paste into source app)
            on_open_translator: Called when user clicks Open Translator
            on_open_settings: Called when user clicks Open Settings (error state)
            on_open_dictionary: Called by the Dictionary button with (text, target_lang,
                popup window) - the shared Dictionary-mode flow
                (DictionaryPopup.open_from_text), which opens beside the popup
            on_open_settings_hotkeys_tab: Called to open Settings directly to Hotkeys tab
            on_re_translate: Called when user clicks Re-translate button (force fresh API call)
            on_custom_prompt_send: Called with the edit-box content when user clicks Send in custom-prompt mode
            on_choose_source: Called with "Japanese" or "Chinese (Simplified)" when the user
                answers the kanji-only chooser - re-translate with that source (J6)
        """
        self._on_copy = on_copy
        self._on_copy_and_replace = on_copy_and_replace
        self._on_open_translator = on_open_translator
        self._on_open_settings = on_open_settings
        self._on_open_dictionary = on_open_dictionary
        self._on_open_settings_hotkeys_tab = on_open_settings_hotkeys_tab
        self._on_re_translate = on_re_translate
        self._on_custom_prompt_send = on_custom_prompt_send
        self._on_choose_source = on_choose_source
        self._on_dismiss = on_dismiss

    def _apply_noactivate(self):
        """Restore the result/edit focus policy after Tk updates its native wrapper."""
        if self._native_sizing is not None:
            self._native_sizing.noactivate = not self._allow_activation
            if self._native_sizing.running:
                # The native style-changing handler preserves this policy. A
                # direct Win32 write here could reenter Tcl outside Tk's boundary.
                return
        if self.popup:
            set_noactivate(self.popup, not self._allow_activation)
            if self._native_sizing is not None and self._native_sizing.hwnd is None:
                self._ensure_native_sizing()

    def capture_mouse_position(self):
        """Capture current mouse position for popup positioning."""
        self._last_mouse_x = self.root.winfo_pointerx()
        self._last_mouse_y = self.root.winfo_pointery()
        self._anchor_rect = None

    def anchor_below(self, rect):
        """Open the next popups directly under the window `rect` (left, top, width, height) - the audio panel's
        result (Decision 88) - until a hotkey captures the cursor again. Its retry and status popups follow it."""
        left, top, width, height = rect
        self._anchor_rect = (int(left), int(top), int(width), int(height))
        self._last_mouse_x, self._last_mouse_y = int(left + width // 2), int(top + height // 2)

    def show_loading(self, target_lang: str, loading_text: str = None):
        """Show loading indicator popup with animation.

        Args:
            target_lang: The target language for translation
            loading_text: Optional base phrase to show instead of "Translating to <lang>"
                (e.g. "Fixing grammar" for the Fix Grammar action)

        A compact card in the theme's primary accent, with the result's hairline
        ring and placement; dots come and go in their own slot, so the line never changes
        width and nothing shifts as it animates.
        """
        if defer_update(self.popup, 'popup-content', lambda: self.show_loading(target_lang, loading_text)):
            return
        self.close()

        self._loading_target_lang = target_lang
        self._loading_base_text = loading_text if loading_text else f"Translating to {target_lang}"

        look = self._look = _Look(ui.get())
        self.popup = self._new_window(look, on='accent')
        self._apply_noactivate()
        self.popup.attributes('-topmost', True)

        frame = ui.Frame(self.popup, on='accent', padding=(look.pad, look.px(12)))
        frame.pack(fill=BOTH, expand=True)

        self._loading_label = ui.Label(frame, text=self._loading_base_text, kind='body',
                                       on='accent')
        self._loading_label.pack(side=LEFT)
        self._loading_dots = ui.Label(frame, text='', kind='body', on='accent', width=3,
                                      anchor='w')
        self._loading_dots.pack(side=LEFT)
        cancel_button = ui.IconButton(frame, ACTION_ICONS['close'], command=self.dismiss,
                                      label='Cancel', size=14, on='accent', tone='on_accent')
        cancel_button.action_name = 'Close'
        cancel_button.pack(side=RIGHT, padx=(look.gap, 0))
        self.popup_close_btn = cancel_button
        self.popup.bind('<Escape>', lambda _event: self.dismiss())
        self.popup.protocol('WM_DELETE_WINDOW', self.dismiss)

        # Placed by the result's rules (Decision 34): beside the cursor and on the
        # screen, so the result then opens at the same corner.
        width = packed_row_width(frame) + 2 * look.edge
        height = (max(self._loading_label.winfo_reqheight(), cancel_button.winfo_reqheight())
                  + 2 * look.px(12) + 2 * look.ring)
        x, y, _ = self._place(width, height, self._work_area(), min_height=height)
        self.popup.geometry(f"+{x}+{y}")

        # Start loading animation
        self._loading_animation_running = True
        self._loading_animation_step = 0
        self._loading_start_time = time.time()
        self._animate_loading()

    def _animate_loading(self):
        """Animate the loading popup: one more dot every 400 ms, then none again."""
        if not self._loading_animation_running:
            return

        if not self.popup or not self._loading_label:
            self._loading_animation_running = False
            return

        # The request runner owns the error message at the shared deadline.
        from src.constants import TRANSLATION_TIMEOUT_SECONDS
        LOADING_TIMEOUT_SECONDS = TRANSLATION_TIMEOUT_SECONDS
        if self._loading_start_time and time.time() - self._loading_start_time > LOADING_TIMEOUT_SECONDS:
            logging.warning(f"Loading animation timed out after {LOADING_TIMEOUT_SECONDS}s, auto-closing")
            self._loading_animation_running = False
            self.close()
            return

        try:
            self._loading_label.configure(
                text=self._loading_base_text or f"Translating to {self._loading_target_lang}")
            if self._loading_dots is not None:
                self._loading_dots.configure(text='.' * (self._loading_animation_step % 4)
                                              if ui.get().manager.animations_enabled else '...')
            self._loading_animation_step += 1

            # Schedule next frame (400ms)
            if self.popup and self.popup.winfo_exists():
                self._loading_job = self.root.after(400, self._animate_loading)

        except tk.TclError:
            # Widget destroyed
            self._loading_animation_running = False

    def calculate_size(self, text: str, base_font: Optional[tuple] = None,
                       vertical_padding: int = 100) -> Tuple[int, int]:
        """Calculate optimal popup dimensions based on text content.

        Uses 20% safety margin on line height for cross-machine font rendering
        compatibility (handles DPI, ClearType, font substitution differences).

        Args:
            text: The text to display
            base_font: The font the text will actually be rendered in. The
                defaults are the popup's own, so existing callers are unchanged;
                a caller that renders in anything else MUST say so, or the row
                height is measured against the wrong font. The dictionary result
                legacy consumers may render shorter rows than the popup
                than Segoe UI 11's - it used to reserve a line's worth of empty
                space per line because of exactly that.
            vertical_padding: Pixels this window spends on everything that is not
                the text box. The default describes the popup (header, furigana
                block, button row); the dictionary window passes its own.

        Returns:
            Tuple of (width, height) in pixels
        """
        MAX_WIDTH = 800
        MIN_WIDTH = 670  # Ensure all action buttons + close fit in one row (incl. Custom Prompt)
        MIN_HEIGHT = 130  # Unified minimum height

        # Get max height from current monitor's work area
        work_area = get_monitor_work_area(self._last_mouse_x, self._last_mouse_y)
        if work_area:
            MAX_HEIGHT = (work_area[3] - work_area[1]) - 80
        else:
            MAX_HEIGHT = self.root.winfo_screenheight() - 80

        # Padding - HORIZONTAL_PADDING lives at module scope (shared with the
        # furigana height estimate)
        VERTICAL_PADDING = vertical_padding   # header + footer + margins

        # Font with 20% safety margin for cross-machine compatibility
        try:
            ui_font = font.Font(root=self.root, font=base_font or ui.get().font('reading'))
        except tk.TclError:
            ui_font = font.Font(root=self.root, font=ui.get().font('reading'))

        base_line_height = ui_font.metrics("linespace")
        LINE_HEIGHT = int(base_line_height)

        # Width calculation
        longest_line = max((ui_font.measure(line) for line in text.split('\n')), default=0)
        ideal_width = longest_line + HORIZONTAL_PADDING
        width = max(MIN_WIDTH, min(ideal_width, MAX_WIDTH))

        # Height with CEILING division (always round UP)
        available_width = width - HORIZONTAL_PADDING

        total_lines = 0
        for para in text.split('\n'):
            if not para:
                total_lines += 1
                continue

            para_width = ui_font.measure(para)
            if para_width <= available_width:
                total_lines += 1
            else:
                # Ceiling division - never underestimate wrap lines
                total_lines += math.ceil(para_width / available_width)

        # Add 1 line buffer for edge cases
        total_lines += 1

        height = (total_lines * LINE_HEIGHT) + VERTICAL_PADDING

        return int(width), int(max(MIN_HEIGHT, min(height, MAX_HEIGHT)))

    def show(self, translated: str, target_lang: str, trial_info: dict = None, original: str = "",
             furigana_text: str = None, is_grammar: bool = False,
             model_info: str = None, source_lang: str = None, audio_result=False, on_audio_play=None):
        """Show popup with translation result.

        Args:
            translated: The translated text
            target_lang: The target language
            trial_info: Optional dict with trial mode info (from TranslationService.get_trial_info())
            original: The original text (for dictionary lookup)
            furigana_text: Optional furigana-annotated text with {kanji|reading} notation
            is_grammar: True when showing a Fix Grammar result. Hides the translation-only
                buttons (Re-translate, Dictionary, Custom Prompt) since they assume a real
                target language; keeps Copy / Replace / Open Translator for fixing in place.
            model_info: "model (Provider)" credit for the result, shown as a small note at
                the very top. None leaves the note off entirely - which is what happens for
                a cache hit whose stored entry predates the credit being recorded, because
                naming the model that merely ran most recently would be a lie.
            source_lang: The language a model (or the user) settled for `original`,
                or None. Decides the readings on kanji-only text (J6), and whether
                the Japanese / Chinese chooser is offered.

        Sized in two steps (Decision 34). Here everything is built at alpha 0 and given
        its width - the rows that must fit whole are measured, the text is wrapped for
        reading. Once Tk has laid the popup out at that width, `_finish_layout()` reads
        what each box really needs, gives it exactly that up to the cap, places the
        popup beside the cursor and makes it visible.
        """
        if defer_update(self.popup, 'popup-content', lambda: self.show(translated, target_lang,
                trial_info, original, furigana_text=furigana_text, is_grammar=is_grammar,
                model_info=model_info, source_lang=source_lang)):
            return
        self.close()

        # Check if this is an error message
        self._audio_result = bool(audio_result)
        self._audio_play_callback = on_audio_play
        self._current_model_info = model_info
        is_error = is_error_text(translated)
        look = self._look = _Look(ui.get())
        p = look.p

        # One hint for the box and its measurement alike
        ruby_hint = self._ruby_hint(target_lang, is_grammar, original, source_lang)
        source_mode = self._source_row(original, source_lang, is_grammar, is_error)

        # Store original and translation for dictionary mode
        self._current_original = original
        self._current_translation = translated
        self._current_target_lang = target_lang
        self._current_trial_info = trial_info  # Store for restoring after custom-prompt cancel
        self._current_furigana = furigana_text  # Store for restoring after custom-prompt cancel
        self._current_source_lang = source_lang
        self._current_alignment = (validate_metadata(getattr(translated, 'alignment', None),
                                   original, translated, target_lang)
                                   if not is_error and not is_grammar else None)
        self._is_grammar = is_grammar  # Grammar-fix result: hide translation-only buttons
        self._replace_preview = False
        self._error_help = popup_errors.explain(translated) if is_error else None
        self._linked_paused = False
        self._original_collapsed = True
        self._generation += 1

        # Create popup window - invisible until it has been measured and placed
        self.popup = self._new_window(look)
        try:
            self.popup.attributes('-alpha', 0.0)
        except tk.TclError:
            pass
        self.popup.protocol("WM_DELETE_WINDOW", self.dismiss)

        # On top while it is measured and for POPUP_FRONT_MS once it appears (`_reveal`),
        # then free to go behind other windows. Counted from here, the 100 ms it used to
        # be could run out before the invisible popup appeared, and the app the text came
        # from rose over it meanwhile (seen 2026-10-19: under a maximized VS Code).
        self.popup.attributes('-topmost', True)
        self._apply_noactivate()

        # Main frame
        main_frame = ui.Frame(self.popup, on='surface', padding=look.pad)
        self._main_frame = main_frame
        self._content_resize = DeferredContentResize(self.popup, background=p.surface,
            outline_colour=p.accent,
            inset=look.ring, host=main_frame, on_start=self._deferred_resize_started,
            on_commit=self._deferred_resize_committed, on_settled=self._deferred_resize_settled,
            editors=lambda: (self.popup_text, self.popup_furigana))
        self._bind_edges(main_frame)
        self._bind_edges(self.popup)

        # The header: the language pair, the credit, and the close button - shown at every width
        self._build_header(main_frame, None if is_error else model_info, is_grammar, is_error,
                           target_lang, original, source_lang, furigana_text)


        # Button frame (Create FIRST to ensure it stays at BOTTOM)
        self._btn_frame = ui.Frame(main_frame, on='surface')
        self._btn_frame.pack(side=BOTTOM, fill=X, pady=(look.px(12), 0))
        self._bind_drag(self._btn_frame)
        if is_error:
            self._build_error_bar()
        else:
            self._build_action_bar(is_grammar)
        if audio_result and on_audio_play:
            self.audio_play_btn = ui.IconButton(self._btn_frame, 'play-fill', command=on_audio_play,
                                                label='Play recording', on='surface')
            self.audio_play_btn.pack(side=LEFT, padx=(self._look.gap, 0))

        # Above the buttons: kanji-only text whose language nobody settled, or
        # a term someone did - and the way to the other language (K4)
        if source_mode:
            self._build_source_chooser(main_frame, source_mode, source_lang)

        # A failure says what happened in plain words; the notice itself is the details
        if is_error:
            self._build_error_notice(main_frame)

        # Source visibility is independent of optional readings and sentence links.
        if (furigana_text or (original and not is_grammar)) and not is_error:
            self._render_furigana(main_frame, furigana_text)
            # Separator between furigana and translation
            ui.Separator(main_frame, on='surface').pack(fill=X, pady=(look.px(8), look.px(8)))

        # Translation text, in a holder whose height step 2 sets in pixels. It is the hero
        # (body size, full-strength colour); the original above it and a failure's details
        # are the muted ones.
        text_fg = p.muted if is_error else p.text
        self._text_holder, self.popup_text, self._text_scrollbar = self._make_box(
            main_frame, expand=True, wrap=tk.WORD, bg=p.surface,
            base_fg=text_fg, kanji_fg=text_fg,
            base_font=look.font('body', type_ramp.script_for_language(target_lang)),
            spacing1=0, spacing3=0, cursor='xterm')
        # Readings on the translation itself, not just the source block. The
        # target language is a reliable hint here, so a kanji-only translation
        # ("東京都") annotates where the source block cannot.
        # Error text is excluded: a diagnostic needs no readings.
        insert_output(self.popup_text, '1.0', translated,
                      lang_hint=ruby_hint,
                      enabled=self._ruby_enabled() and not is_error)
        self.popup_text.config(state='disabled')
        self._sync_linked_reading()
        self._linked_setting_binding = self.root.bind(
            '<<LinkedReadingChanged>>', self._sync_linked_reading, add='+')
        self.popup.bind('<Destroy>', lambda event:
                        self._unbind_linked_setting() if event.widget is self.popup else None, add='+')

        # Mouse wheel scroll is bound by RubyText, including over ruby frames

        self._build_grip()
        self._start_layout()

        # Bindings
        self.popup.bind('<Escape>', self._escape)

    def _new_window(self, look, *, on='surface') -> tk.Toplevel:
        """A borderless window with a hairline ring: the toplevel's own highlight border,
        which insets everything inside it by exactly its thickness (measured). On Windows 11
        DWM rounds its corners once it is mapped; elsewhere it stays square."""
        self._popup_plane = on
        window = tk.Toplevel(self.root, bg=getattr(look.p, on), highlightthickness=look.ring,
                             highlightbackground=look.p.border, highlightcolor=look.p.border, **NO_AUTOSTYLE)
        window.overrideredirect(True)
        self._allow_activation = False
        window.bind('<Map>', lambda event, w=window, p=look.p: (
            windows.style_floating(w, p) if event.widget is w else None), add='+')
        window.bind('<Map>', lambda event, w=window:
                    self._apply_noactivate() if event.widget is w and w is self.popup else None, add='+')
        self._preferences_scale = look.ctx.text_scale
        self._preferences_language = look.ctx.manager.ui_language
        self._preferences_unsubscribe = look.ctx.manager.subscribe(self._preferences_changed)
        window.bind('<Destroy>', lambda e, w=window: self._stop_preferences() if e.widget is w else None,
                    add='+')
        return window

    def _stop_preferences(self):
        unsubscribe = getattr(self, '_preferences_unsubscribe', None)
        if unsubscribe:
            unsubscribe()
            self._preferences_unsubscribe = None

    def _preferences_changed(self, manager):
        if self.popup is None or not self.popup.winfo_exists():
            return
        if defer_update(self.popup, 'popup-theme', lambda: self._preferences_changed(manager)):
            return
        self.popup.configure(bg=getattr(manager.palette, self._popup_plane),
                             highlightbackground=manager.palette.border,
                             highlightcolor=manager.palette.border)
        if self._content_resize is not None:
            self._content_resize.set_background(manager.palette.surface)
            self._content_resize.set_outline_colour(manager.palette.accent)
        grip = getattr(self, '_grip', None)
        if grip is not None and grip.winfo_exists():
            grip.configure(image=self._look.ctx.grip(12, manager.palette.strong),
                           bg=manager.palette.surface)
        for target in self._resize_handles.values():
            target.configure(bg=manager.palette.surface)
        windows.style_floating(self.popup, manager.palette)
        if (self._preferences_scale != self._look.ctx.text_scale
                or self._preferences_language != manager.ui_language):
            self._preferences_scale = self._look.ctx.text_scale
            self._preferences_language = manager.ui_language
            if self.popup_text is not None:
                self.popup.update_idletasks()
                width = min(self._work_area().width, max(self.popup.winfo_width(), self._required_width()))
                self.popup.geometry(f"{width}x{self.popup.winfo_height()}")
                self._fit_header(width)
                self._fit_boxes(reposition=True)

    # ------------------------------------------------------------------ #
    # Building the popup (Decision 34)
    # ------------------------------------------------------------------ #
    def _bind_drag(self, widget) -> None:
        """Drag the popup by `widget`."""
        widget.bind("<Button-1>", self._start_move)
        widget.bind("<B1-Motion>", self._on_drag)
        widget.bind("<ButtonRelease-1>", self._end_move)

    def _bind_edges(self, frame) -> None:
        """Keep uncovered border padding resizable and interior padding draggable.

        Explicit edge targets above the content provide the primary hit surfaces.
        Toplevel bindings must ignore events already owned by child controls.
        """
        for sequence, callback in (('<Motion>', self._edge_hover), ('<Leave>', self._edge_leave),
                ('<Button-1>', self._frame_press), ('<B1-Motion>', self._frame_drag),
                ('<ButtonRelease-1>', self._frame_release)):
            frame.bind(sequence, lambda event, target=frame, action=callback:
                       action(event) if event.widget is target else None)
            # A ttk command can destroy the popup before its toplevel bindtag.
            # Guard in Tcl before invoking a Python command deleted by destroy.
            frame.bind(sequence, 'if {![winfo exists %W]} {break}\n' + frame.bind(sequence))

    @staticmethod
    def _edge_under(event) -> str:
        frame = event.widget
        return popup_layout.edge_at(event.x, event.y, frame.winfo_width(),
                                    frame.winfo_height(), RESIZE_EDGE_PX, RESIZE_CORNER_PX)

    @staticmethod
    def _set_cursor(frame, cursor: str) -> None:
        # Set only when it changes: <Motion> comes many times a second.
        if str(frame.cget('cursor')) != cursor:
            frame.configure(cursor=cursor)

    def _edge_hover(self, event) -> None:
        if self._grip_start is None:
            self._set_cursor(event.widget, RESIZE_CURSORS.get(self._edge_under(event), ''))

    def _edge_leave(self, event) -> None:
        # Children inherit a frame's cursor: left set, it would show over the text.
        if self._grip_start is None:
            self._set_cursor(event.widget, '')

    def _frame_press(self, event):
        edge = self._edge_under(event)
        if edge:
            return self._begin_resize(event, edge)
        self._start_move(event)

    def _frame_drag(self, event):
        if self._grip_start is not None:
            return self._grip_drag(event)
        self._on_drag(event)

    def _frame_release(self, event):
        if self._grip_start is not None:
            self._grip_release(event)
            self._set_cursor(event.widget, '')
            return 'break'
        self._end_move(event)

    def _pair_text(self, is_grammar: bool, target_lang: str, original: str,
                   source_lang: Optional[str], furigana_text: Optional[str]) -> str:
        """The header chip: "Japanese -> English" when the source is settled (by its
        characters, a model or the user), else only where it is going."""
        if is_grammar:
            return "Grammar fix"
        language = (lang_id.resolve(original or '', model=source_lang).language
                    or (lang_id.JAPANESE if furigana_text else None))
        if not language:
            return f"To {target_lang}" if target_lang else ""
        name = (language_label(language)
                if language == lang_id.JAPANESE or language in lang_id.CHINESE else language)
        return f"{name} {ARROW} {target_lang}" if target_lang else name

    def _build_header(self, parent, model_info: Optional[str], is_grammar: bool,
                      is_error: bool = False, target_lang: str = "", original: str = "",
                      source_lang: Optional[str] = None,
                      furigana_text: Optional[str] = None) -> None:
        """One compact row for destination, model credit, expansion and Close."""
        look = self._look
        header = ui.Frame(parent, on='surface')
        self._header_settings = None
        self._header = self._action_header = header
        header.pack(side=TOP, fill=X, pady=(0, look.px(4)))
        self._bind_drag(header)
        close = ui.IconButton(header, ACTION_ICONS['close'], command=self.dismiss, label="Close",
                              size=14, on='surface')
        close.action_name = "Close"
        close.pack(side=RIGHT)
        self.popup_close_btn = close
        self._attribution_text = None
        pair = '' if is_error else self._pair_text(is_grammar, target_lang, original,
                                                   source_lang, furigana_text)
        if pair:
            chip = ui.Chip(header, text=pair, on='surface')
            chip.pack(side=LEFT)
            self._bind_drag(chip)
            self.popup_chip = chip
        if model_info:
            verb = "Fixed" if is_grammar else "Translated"
            self._attribution_text = f"{verb} with {model_info}"
            attribution = ui.Label(header, text=tr(self._attribution_text), kind='caption',
                                   on='surface', localize=False)
            attribution.pack(side=RIGHT, padx=(look.gap if pair else 0, 0))
            attribution.tooltip = ui.Tooltip(attribution, self._attribution_text)
            self._bind_drag(attribution)
            self.popup_attribution = attribution


    def _action_icon(self, parent, key: str, name: str, command) -> 'ui.IconButton':
        """A secondary action: its icon on a quiet square, named by a tooltip. `action_name`
        carries the name for code and tests."""
        if key == 'settings':
            button = ui.Button(parent, 'Settings', command=command, icon='gear', icon_side='left',
                               kind='subtle', on='surface')
            button.tooltip = ui.Tooltip(button, name)
        else:
            button = ui.IconButton(parent, ACTION_ICONS[key], command=command, label=name,
                                   size=POPUP_ICON_PX, on='surface')
        button.action_name = name
        self._tooltips.append(button.tooltip)
        return button

    def _build_action_bar(self, is_grammar: bool) -> None:
        """Copy (the one accent) and Replace as words - the two actions used most - and the
        rest as icons (Decision 34, TODO.md Q1 (c)): the bar no longer holds every popup at
        735 px. A grammar fix keeps Copy, Replace and Open Translator."""
        bar = self._btn_frame
        gap = self._look.px(4)

        # Copy button
        self.popup_copy_btn = ui.Button(bar, "Copy", command=self._handle_copy, kind='accent',
                                        on='surface', icon='copy')
        self.popup_copy_btn.pack(side=LEFT, fill=Y)

        # Replace button (copy translated text + paste into source app)
        self.popup_replace_btn = ui.Button(bar, "Replace", command=self._handle_copy_and_replace,
                                           kind='soft', on='surface', icon='arrow-left-right')
        if not getattr(self, '_audio_result', False):
            self.popup_replace_btn.pack(side=LEFT, fill=Y, padx=(gap, 0))

        # Re-translate + Dictionary are translation-only - skip them for grammar results
        # (Re-translate would "translate to Grammar"; Dictionary expects a target language).
        if not is_grammar:
            # Re-translate - force a fresh API call, bypassing the cache
            self.popup_retranslate_btn = self._action_icon(
                bar, 'retranslate', "Re-translate", self._handle_re_translate)
            self.popup_retranslate_btn.pack(side=LEFT, padx=(gap, 0))

            # Dictionary - opens Dictionary mode for the original text
            self.popup_dict_btn = self._action_icon(
                bar, 'dictionary', "Dictionary", self._open_dictionary_popup)
            self.popup_dict_btn.pack(side=LEFT, padx=(gap, 0))
            self._update_dict_button_state()

        # Open Translator
        self.popup_open_btn = self._action_icon(
            bar, 'open', "Open Translator", self._handle_open_translator)
        self.popup_open_btn.pack(side=RIGHT, padx=(self._look.px(16), 0))

        # Custom Prompt - make the box editable and ask the AI a freeform question
        # (translation-only; skipped for grammar results)
        if not is_grammar:
            self.popup_custom_prompt_btn = self._action_icon(
                bar, 'prompt', "Custom Prompt", self._handle_custom_prompt)
            self.popup_custom_prompt_btn.pack(side=LEFT, padx=(gap, 0))
            self.popup_linked_btn = self._action_icon(
                bar, 'linked', 'Linked reading', self._toggle_linked_reading)
            self.popup_linked_btn.pack(side=LEFT, padx=(gap, 0))
            self._linked_tooltip = self.popup_linked_btn.tooltip

    def _build_error_bar(self) -> None:
        """A failure notice's bar: the first thing that helps takes the accent, the others sit
        beside it, and the way out to the Translator is at the far right - it opens with the
        source text already in its input, ready to translate. What helps first is
        `popup_errors.explain`'s call; "API Settings" is always offered, as it was."""
        bar, help_ = self._btn_frame, self._error_help
        gap = self._look.px(4)
        buttons = []
        if help_.fix == popup_errors.FIX_RETRY and self._current_original:
            buttons.append(("Try again", self._handle_re_translate, 'arrow-repeat'))
        if help_.fix != popup_errors.FIX_NONE:
            buttons.append(("API Settings", self._handle_open_settings, 'gear'))
        for index, (text, command, icon) in enumerate(buttons):
            button = ui.Button(bar, text, command=command, on='surface', icon=icon,
                               kind='accent' if index == 0 else 'soft')
            button.pack(side=LEFT, padx=(gap if index else 0, 0))
        self.popup_open_btn = ui.Button(bar, "Open Translator", command=self._handle_open_translator,
                                        kind='soft' if buttons else 'accent', on='surface',
                                        icon='box-arrow-up-right')
        self.popup_open_btn.pack(side=RIGHT)

    def _build_error_notice(self, parent) -> None:
        """What went wrong, in plain words, in an info bar - and "Details" over the original
        notice, which stays in the box below for whoever wants it (TODO.md V22)."""
        look = self._look
        help_ = self._error_help
        notice = ui.InfoBar(parent, help_.message, kind=help_.kind, on='surface')
        notice.text.configure(wraplength=look.px(280))      # `_start_layout` fits it to the width
        notice.pack(side=TOP, fill=X, pady=(0, look.px(10)))
        self._error_notice = notice
        notice.bind('<Configure>', self._resize_notice_changed, add='+')
        ui.Label(parent, text="Details", kind='muted', on='surface').pack(
            side=TOP, anchor='w', pady=(0, look.px(2)))

    def _make_box(self, parent, expand: bool, **text_options):
        """A text box in a holder of its own. The holder's height is set in pixels
        (step 2), so no two boxes share leftover space - they used to, both packed to
        expand - and the scrollbar is packed beside the text only when the box is
        capped. Returns (holder, box, scrollbar)."""
        p = self._look.p
        text_options.setdefault('bg', p.surface)
        text_options.setdefault('ruby_fg', p.ruby)
        text_options.setdefault('selectbackground', p.selection)
        text_options.setdefault('selectforeground', p.selection_text)
        text_options.setdefault('inactiveselectbackground', p.selection)
        text_options.setdefault('insertbackground', p.text)
        holder = tk.Frame(parent, height=1, bg=text_options['bg'], **NO_AUTOSTYLE)
        holder.pack_propagate(False)
        holder.pack(side=TOP, fill=BOTH if expand else X, expand=expand)
        box = RubyText(holder, **text_options)
        box.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar = ui.ThinScrollbar(holder, orient='vertical', command=box.yview)
        return holder, box, scrollbar

    def _build_grip(self) -> None:
        """A resize grip in the bottom-right corner (Decision 34, TODO.md P4). The size
        the user drags the popup to is remembered and becomes the next popup's reading
        width and height cap; a double-click returns to automatic sizing. All edges
        and corners share outline tracking and apply geometry only on release."""
        look = self._look
        grip = tk.Label(self.popup, image=look.ctx.grip(12, look.p.strong), bg=look.p.surface,
                        cursor='size_nw_se', padx=0, pady=0, bd=0, **NO_AUTOSTYLE)
        grip.place(relx=1.0, rely=1.0, anchor='se', x=-(look.ring + 2), y=-(look.ring + 2))
        grip.bind('<ButtonPress-1>', self._begin_resize)
        grip.bind('<B1-Motion>', self._grip_drag)
        grip.bind('<ButtonRelease-1>', self._grip_release)
        grip.bind('<Double-Button-1>', self._grip_reset)
        self._grip = grip
        self._build_resize_targets()
        grip.lift()
        self.popup.bind('<Destroy>', self._resize_destroyed, add='+')

    def _build_resize_targets(self):
        """Explicit client hit targets share the proven grip entry for every edge."""
        look = self._look
        ring = look.ring
        corner = max(RESIZE_CORNER_PX, look.px(12))
        edge = RESIZE_EDGE_PX
        placements = {
            'nw': dict(x=ring,y=ring,width=corner,height=corner),
            'ne': dict(relx=1,x=-ring,y=ring,anchor='ne',width=corner,height=corner),
            'sw': dict(x=ring,rely=1,y=-ring,anchor='sw',width=corner,height=corner),
            'se': dict(relx=1,rely=1,x=-ring,y=-ring,anchor='se',width=corner,height=corner),
            'n': dict(x=corner+ring,y=ring,relwidth=1,width=-2*(corner+ring),height=edge),
            's': dict(x=corner+ring,rely=1,y=-ring,anchor='sw',relwidth=1,
                      width=-2*(corner+ring),height=edge),
            'w': dict(x=ring,y=corner+ring,width=edge,relheight=1,height=-2*(corner+ring)),
            'e': dict(relx=1,x=-ring,y=corner+ring,anchor='ne',width=edge,
                      relheight=1,height=-2*(corner+ring)),
        }
        self._resize_handles = {}
        for direction, placement in placements.items():
            target = tk.Frame(self.popup,bg=look.p.surface,bd=0,highlightthickness=0,
                              cursor=RESIZE_CURSORS[direction],**NO_AUTOSTYLE)
            target.place(**placement)
            target.bind('<ButtonPress-1>',lambda event,side=direction:self._begin_resize(event,side))
            target.bind('<B1-Motion>',self._grip_drag)
            target.bind('<ButtonRelease-1>',self._grip_release)
            target.bind('<Double-Button-1>',self._grip_reset)
            self._resize_handles[direction] = target

    # ------------------------------------------------------------------ #
    # Measuring and placing (Decision 34)
    # ------------------------------------------------------------------ #
    def _anchor(self) -> Tuple[int, int]:
        """The cursor at hotkey time - what the popup opens beside and never covers."""
        return self._last_mouse_x, self._last_mouse_y

    def _anchor_top(self) -> int:
        """The top of what the popup must not cover: the anchoring window's, else the cursor's."""
        rect = getattr(self, '_anchor_rect', None)
        return rect[1] if rect else self._last_mouse_y

    def _place(self, width, height, work, *, min_height):
        """`popup_layout.place` beside the cursor, or `place_below` the window the popup is anchored to."""
        rect = getattr(self, '_anchor_rect', None)
        if rect:
            return popup_layout.place_below(rect, width, height, work, min_height=min_height)
        return popup_layout.place(self._anchor(), width, height, work, min_height=min_height)

    def _work_area(self) -> popup_layout.WorkArea:
        """The work area of the monitor holding the cursor (the taskbar left out)."""
        area = get_monitor_work_area(self._last_mouse_x, self._last_mouse_y)
        if area:
            return popup_layout.WorkArea(*area)
        return popup_layout.WorkArea(0, 0, self.root.winfo_screenwidth(),
                                     self.root.winfo_screenheight() - 50)

    def _saved_size(self) -> Tuple[Optional[int], Optional[int]]:
        """The size the user last dragged a popup to, or (None, None)."""
        getter = getattr(self.config, 'get_quick_popup_size', None)
        if not callable(getter):
            return None, None
        try:
            width, height = getter()
        except Exception:
            return None, None
        return width, height

    def _remember_size(self, width: Optional[int], height: Optional[int]) -> None:
        setter = getattr(self.config, 'set_quick_popup_size', None)
        if callable(setter):
            try:
                setter(width, height)
            except Exception as e:
                logging.warning(f"[Popup] could not remember the popup size: {e}")

    @staticmethod
    def _count(box, *args) -> int:
        """Text.count() returns a tuple, a bare int or None depending on the Tk build."""
        value = box.count(*args)
        if isinstance(value, tuple):
            return value[0]
        return value or 0

    @staticmethod
    def _insets(box, axis: str) -> int:
        """A Text widget's border, highlight and padding, both sides of one axis."""
        pad = 'pady' if axis == 'y' else 'padx'
        return 2 * sum(int(float(str(box.cget(option))))
                       for option in ('borderwidth', 'highlightthickness', pad))

    def _content_px(self, box) -> int:
        """Pixels `box`'s content needs, laid out at the box's present width."""
        return self._count(box, '1.0', 'end', 'update', 'ypixels')

    def _rows_px(self, box, rows: int) -> int:
        """Pixels of the first `rows` display lines - all of it when there are fewer."""
        # Tk clamps a display-line index past the content to end. Counting all
        # wrapped lines first is quadratic on very long logical paragraphs.
        limit = box.index(f'1.0 + {rows} display lines')
        # Tcl may clamp to the final character rather than the terminal newline.
        # For a document already proven to fit this bounded budget, include its
        # final line's spacing just as the auto-fit content measurement does.
        if box.compare(limit, '>=', 'end-1c'):
            limit = 'end'
        return self._count(box, '1.0', limit, 'update', 'ypixels')

    @staticmethod
    def _visible_rows_px(box, rows: int, *, from_start=False):
        """Read the displayed rows without synchronizing off-screen line metrics.

        A temporarily too-small viewport grows just enough to expose the next
        row; Configure schedules a final exact pass. No document index walk or
        synchronous `count -update` belongs to the resize path.
        """
        line = box.dlineinfo('1.0' if from_start else '@0,0')
        if line is None:
            return None
        top = line[1]
        for number in range(1, rows + 1):
            bottom = line[1] + line[3]
            if number == rows:
                return max(1, bottom - top)
            final = box.dlineinfo('end-1c')
            if final is not None and final[1] == line[1]:
                return max(1, bottom - top)
            following = box.dlineinfo(f'@0,{max(0, bottom)}')
            if following is None or following[1] <= line[1]:
                return max(1, bottom - top + (rows - number) * line[3])
            line = following

    @staticmethod
    def _visible_content_px(box):
        """Exact natural height only when both document ends are in the view."""
        first, final = box.dlineinfo('1.0'), box.dlineinfo('end-1c')
        return max(1, final[1] + final[3] - first[1]) if first and final else None

    def _required_width(self) -> int:
        """The popup width the rows that must fit whole ask for - the action bar, the
        trial line, the chooser row, the original's header, the header (chip, the least of
        the credit, the close button) - plus the edge on both sides. Read off the widgets
        (`packed_row_width`), never a constant (TODO.md S1)."""
        rows = (self._btn_frame, self._trial_frame, self.popup_source_chooser,
                self._original_header)
        widest = max((packed_row_width(row) for row in rows if row is not None), default=0)
        header = self.popup_close_btn.winfo_reqwidth() if self.popup_close_btn else 0
        if self.popup_chip is not None:
            header += self.popup_chip.winfo_reqwidth() + self._look.gap
        metadata = self._look.px(HEADER_CREDIT_MIN_PX) + self._look.gap if self.popup_attribution is not None else 0
        if self.popup_original_toggle is not None and self.popup_original_toggle.winfo_manager():
            header += self.popup_original_toggle.winfo_reqwidth() + self._look.gap
        if self._header_settings is not None:
            metadata += self._header_settings.winfo_reqwidth() + 2 * self._look.gap
        return max(widest, header + metadata) + 2 * self._look.edge

    def _credit_px(self) -> int:
        """The width the header gives the credit: all of it when that is reasonable - a short
        popup grows a little rather than cut "(Google)" - else the most it may take, and
        `_fit_header` cuts the rest with an ellipsis."""
        look = self._look
        measure, insets = self._credit_metrics()
        whole = measure(tr(self._attribution_text or ''))
        return max(look.px(HEADER_CREDIT_MIN_PX), min(whole, look.px(HEADER_CREDIT_MAX_PX))) + insets

    def _credit_metrics(self):
        label = self.popup_attribution
        specification = (label.cget('font') or ui.get().style.lookup(label.cget('style'), 'font')
                         or self._look.font('caption'))
        key = (str(specification), self._look.ctx.scale, self._look.ctx.text_scale,
               self._look.ctx.manager.ui_language)
        cached = self._credit_cache
        if cached is not None and cached[0] == key:
            return cached[1:]
        measure = font.Font(root=self.root, font=specification).measure
        insets = max(0, label.winfo_reqwidth() - measure(str(label.cget('text'))))
        self._credit_cache = key, measure, insets
        return measure, insets

    def _automatic_width(self, saved_width: Optional[int] = None) -> int:
        """The text's natural width, wrapped for reading (70 Latin / 36 CJK characters
        a line) or at the width the user dragged a popup to; never narrower than the
        rows ask for (`popup_layout.popup_width`)."""
        insets = 2 * self._look.edge + self._insets(self.popup_text, 'x')
        base = self.popup_text.base_font
        measure = font.Font(family=base[0], size=base[1]).measure
        natural = popup_layout.natural_width(self._current_translation, measure)
        readable = popup_layout.reading_width(self._current_translation, measure)
        if self.popup_furigana is not None:
            # Both texts wrap at one width: the narrower of their reading widths keeps
            # each within its own (an English line under a Japanese original at 36 CJK
            # characters' width would otherwise run past 70 characters).
            base = self.popup_furigana.base_font
            original = font.Font(family=base[0], size=base[1]).measure
            natural = max(natural, popup_layout.natural_width(self._current_original, original))
            readable_original = popup_layout.reading_width(self._current_original, original)
            if readable_original:
                readable = min(readable, readable_original) if readable else readable_original
        if saved_width:
            readable = max(1, saved_width - insets)
        return popup_layout.popup_width(natural, readable, insets, self._required_width(),
                                        self._work_area().width)

    def _fit_header(self, width: int) -> None:
        """Cut the credit with an ellipsis where it would crowd the chip or the close button."""
        if self.popup_attribution is None or not self._attribution_text:
            return
        look = self._look
        available = width - 2 * look.edge - look.gap
        for control in (self.popup_chip, self.popup_close_btn, self.popup_original_toggle):
            if control is not None and control.winfo_manager():
                available -= control.winfo_reqwidth() + look.gap
        if self._header_settings is not None:
            available -= self._header_settings.winfo_reqwidth() + 2 * look.gap
        measure, insets = self._credit_metrics()
        text = popup_layout.elide(tr(self._attribution_text), measure, available - insets)
        if str(self.popup_attribution.cget('text')) != text:
            self.popup_attribution.configure(text=text)

    def _start_layout(self) -> None:
        """Step 1: the width now, the popup placed at the height cap - still invisible.
        Step 2 (`_finish_layout`) follows when the translation box has been laid out at
        that width (its <Configure>), or after POPUP_LAYOUT_WATCHDOG_MS should that
        never come.

        The cap, not a guess: a guess shorter than the chrome had the packer squeeze the
        translation box out altogether, so it never got its width and step 2 never came
        (measured on the trial line + chooser row case). Never less than the automatic
        cap either: a height the user dragged a popup down to can be shorter than the
        next popup's chrome - the same squeeze. Nobody sees this height."""
        saved_width, saved_height = self._saved_size()
        work = self._work_area()
        width = self._automatic_width(saved_width)
        self._fit_header(width)
        cap = popup_layout.height_cap(work, saved_height)
        # Measuring needs enough temporary room for enlarged chrome as well as text.
        # The final layout still applies the saved/automatic cap and measured row floors.
        provisional = max(cap, work.height - 2 * POPUP_SCREEN_MARGIN)
        x, y, height = self._place(width, provisional, work, min_height=provisional)
        self.popup.geometry(f"{width}x{height}+{x}+{y}")
        if self._content_resize is not None:
            self._content_resize.sync_size(width, height)
        if self._error_notice is not None:
            self._error_notice.fit_to(width - 2 * self._look.edge)
        generation = self._generation
        self._layout = {'generation': generation, 'width': width, 'work': work,
                        'cap': cap, 'done': False}
        self.popup_text.bind('<Configure>',
                             lambda _event, g=generation: self._finish_layout(g), add='+')
        # On the root, not the popup: destroying the popup deletes the commands of its
        # timers but not the timers, which later fire into whatever command reuses the
        # name (seen 2026-10-19: a resize handler called with no event).
        self.root.after(POPUP_LAYOUT_WATCHDOG_MS,
                        lambda g=generation: self._finish_layout(g, forced=True))

    def _finish_layout(self, generation: int, forced: bool = False) -> None:
        """Step 2: measure each box, give it exactly what it needs up to the cap, place
        the popup and make it visible. Once per popup; a popup closed or replaced
        meanwhile is left alone. Whatever goes wrong, the popup is shown."""
        layout = self._layout
        if (layout is None or layout['done'] or generation != self._generation
                or self.popup is None or self.popup_text is None):
            return
        if forced:
            self.popup.update_idletasks()
            if layout['done']:
                self._reveal(generation)
                return
        expected = layout['width'] - 2 * self._look.edge
        laid_out = abs(self.popup_text.winfo_width() - expected) <= 2
        if not laid_out and not forced:
            return                      # an intermediate size; the next <Configure> follows
        try:
            if self.popup_text.winfo_width() > 1:
                layout['done'] = True
                self._fit_boxes(reposition=True)
                # Seen only once Tk has redrawn it at its final size: revealed in the same
                # breath, the first frame still carried the drawing at the provisional
                # size, cut to the new one (caught by the gallery's screenshots).
                if forced:
                    self._reveal(generation)
                else:
                    self.root.after(POPUP_REVEAL_DELAY_MS,
                                    lambda g=generation: self._reveal(g))
                return
            logging.warning("[Popup] layout pending; shown provisionally until text geometry arrives")
        except Exception as e:
            logging.warning(f"[Popup] measuring failed - shown at its estimated size: {e}")
        self._reveal(generation)

    def _reveal(self, generation: Optional[int] = None) -> None:
        """Make the popup visible - its pending redraws flushed first."""
        if self.popup is None or (generation is not None and generation != self._generation):
            return
        try:
            self.popup.update_idletasks()
            self.popup.attributes('-alpha', 1.0)
            self.popup.attributes('-topmost', True)
            self._apply_noactivate()
            self.root.after(POPUP_FRONT_MS, lambda popup=self.popup: self._end_front(popup))
        except tk.TclError:
            pass
        if self._layout is not None:
            self._layout['revealed'] = True

    def _end_front(self, popup) -> None:
        """POPUP_FRONT_MS after it appeared: the popup may go behind other windows."""
        if popup is self.popup and popup is not None:
            try:
                # Tk re-reads the window's place when the topmost bit changes: a geometry() still
                # waiting for its idle update (a resize just released) kept only its size (D31).
                popup.update_idletasks()
                popup.attributes('-topmost', False)
                self._apply_noactivate()
            except tk.TclError:
                pass

    def _fit_boxes(self, reposition: bool = True,
                   size: Optional[Tuple[int, int]] = None,
                   position: Optional[Tuple[int, int]] = None) -> None:
        self._fitting_boxes = True
        try:
            self._fit_boxes_content(reposition, size, position)
        finally:
            self._fitting_boxes = False

    def _fit_boxes_content(self, reposition, size, position):
        """Measure the boxes and give each its height; place the popup or keep it.

        `reposition`: place the popup beside the cursor (a new popup); otherwise its
        corner stays (the replace preview, the original's toggle) unless that would
        run off the screen. `size`: the (width, height) the user dragged the popup to;
        the boxes then share that height, the translation taking the rest, and keep
        POPUP_USER_MIN_ROWS each. `position`: where the user's drag put its corner (a
        drag by the left or top edge moves it); without it the corner stays.
        """
        self._clear_resize_display()
        layout = self._layout
        work = layout['work']
        if size is not None:
            corner = f"+{position[0]}+{position[1]}" if position is not None else ""
            self.popup.geometry(f"{size[0]}x{size[1]}{corner}")
        self.popup.update_idletasks()

        rows = POPUP_MIN_BOX_ROWS if size is None else POPUP_USER_MIN_ROWS
        text_insets = self._insets(self.popup_text, 'y')
        need_t = self._content_px(self.popup_text) + text_insets
        min_t = min(need_t, self._rows_px(self.popup_text, rows) + text_insets)
        need_o = min_o = collapsed_o = 0
        limit_o = None
        original_share = popup_layout.ORIGINAL_SHARE
        if self.popup_furigana is not None:
            original_insets = self._insets(self.popup_furigana, 'y')
            need_o = self._content_px(self.popup_furigana) + original_insets
            collapsed_o = min(need_o, self._rows_px(self.popup_furigana,
                                                    POPUP_COLLAPSED_ORIGINAL_ROWS)
                              + original_insets)
            collapsible = need_o > collapsed_o
            self._show_original_toggle(collapsible)
            if collapsible and self._original_collapsed:
                limit_o = collapsed_o
            # Expanded reading reserves only one source row; translation gets priority.
            if not self._original_collapsed:
                min_o = min(need_o, self._rows_px(self.popup_furigana, POPUP_USER_MIN_ROWS)
                            + original_insets)
            else:
                min_o = collapsed_o if size is None else min(
                    need_o, self._rows_px(self.popup_furigana, rows) + original_insets)
            self.popup.update_idletasks()
        shown_o = need_o if limit_o is None else min(need_o, limit_o)
        chrome = (self._main_frame.winfo_reqheight() - self._text_holder.winfo_reqheight()
                  - (self._original_holder.winfo_reqheight()
                     if self._original_holder is not None else 0)
                  + 2 * self._look.ring)          # the hairline round the popup, both sides

        min_t, min_o = popup_layout.bounded_minimums(
            work.height - 2 * POPUP_SCREEN_MARGIN - chrome, min_t, min_o)
        if size is not None:
            width, height = size
            x, y = position if position is not None else (self.popup.winfo_x(),
                                                          self.popup.winfo_y())
        else:
            width = layout['width']
            desired = min(chrome + need_t + shown_o, layout['cap'])
            if reposition:
                x, y, height = self._place(width, desired, work, min_height=chrome + min_t + min_o)
            else:
                # The edge nearest the cursor stays; the popup grows away from it, so
                # it still never covers the cursor - and stays on the screen.
                x, y = self.popup.winfo_x(), self.popup.winfo_y()
                bottom_edge = y + self.popup.winfo_height()
                if bottom_edge <= self._anchor_top():
                    height = min(desired, bottom_edge - (work.top + POPUP_SCREEN_MARGIN))
                    y = bottom_edge - height
                else:
                    height = min(desired, work.bottom - POPUP_SCREEN_MARGIN - y)
                height = max(height, chrome + min_t + min(min_o, shown_o))

        room = height - chrome
        if not self._original_collapsed:
            heights = popup_layout.allocate_expanded(room, need_t, need_o,
                                                    min_translation=min_t, min_original=min_o)
        else:
            heights = popup_layout.allocate(room, need_t, need_o, min_translation=min_t,
                                            min_original=min_o, original_limit=limit_o,
                                            original_share=original_share)
        self._show_original_toggle(bool(limit_o is not None or need_o > min_o
                                        or heights.translation_capped or not self._original_collapsed))
        if size is not None:
            # The user's size: the translation takes whatever the original leaves.
            translation = max(heights.translation, room - heights.original)
            heights = popup_layout.Heights(translation, heights.original,
                                           need_t > translation, heights.original_capped)
        gutter = self._text_scrollbar.winfo_reqwidth() if heights.any_capped or not self._original_collapsed else 0
        self._fit_box(self._text_holder, self.popup_text, self._text_scrollbar,
                      heights.translation, heights.translation_capped, gutter)
        if self._original_holder is not None:
            self._fit_box(self._original_holder, self.popup_furigana, self._original_scrollbar,
                          heights.original, heights.original_capped, gutter)

        if size is None:
            # The scrollbar sits beside the text, not on it: the text keeps its width
            # and does not re-wrap under the measurement just made.
            width += gutter
            height = chrome + heights.translation + heights.original
            if reposition and gutter:
                x = self._place(width, height, work, min_height=height)[0]
        self.popup.geometry(f"{width}x{height}+{x}+{y}")
        if self._content_resize is not None:
            self._content_resize.sync_size(width, height)
        self._fit_header(width)
        layout.update(chrome=chrome, heights=heights, size=(width, height), position=(x, y))
        # Full content measurement belongs to auto-fit/content changes only. A
        # gesture reuses these bounds and lets Text maintain its own wrap/scroll.
        user_t = min(need_t, self._rows_px(self.popup_text, POPUP_USER_MIN_ROWS) + text_insets)
        user_o = (min(need_o, self._rows_px(self.popup_furigana, POPUP_USER_MIN_ROWS)
                      + original_insets) if self.popup_furigana is not None else 0)
        layout['resize_metrics'] = dict(translation=need_t, original=need_o,
            min_translation=user_t, min_original=user_o, collapsed_original=collapsed_o,
            chrome_without_notice=chrome - (self._error_notice.winfo_reqheight()
                                            if self._error_notice is not None else 0),
            line_floor=min(font.Font(root=self.root, font=box.base_font).metrics('linespace')
                           for box in (self.popup_text, self.popup_furigana) if box is not None))
        self._ensure_native_sizing()

    def _fit_box(self, holder, box, scrollbar, height: int, capped: bool,
                 gutter: int) -> None:
        """Give a box its height, and a scrollbar when it is capped. A box that is not
        capped while the other one is gets a spacer as wide as the scrollbar, so both
        texts keep one width and nothing re-wraps."""
        holder.configure(height=max(1, height))
        spacer = getattr(holder, 'gutter_spacer', None)
        if capped:
            if spacer is not None:
                spacer.pack_forget()
            scrollbar.pack(side=RIGHT, fill=Y, before=box)
            box.configure(yscrollcommand=scrollbar.set)
            if getattr(self, '_linked_controller', None):
                self._linked_controller.watch_scroll(box)
            return
        scrollbar.pack_forget()
        box.configure(yscrollcommand='')
        if getattr(self, '_linked_controller', None):
            self._linked_controller.watch_scroll(box)
        if gutter:
            if spacer is None:
                spacer = tk.Frame(holder, width=gutter, bg=str(holder.cget('bg')), **NO_AUTOSTYLE)
                holder.gutter_spacer = spacer
            spacer.configure(width=gutter)
            spacer.pack(side=RIGHT, fill=Y, before=box)
        elif spacer is not None:
            spacer.pack_forget()

    # ------------------------------------------------------------------ #
    # The grip: a size the user chooses (Decision 34, TODO.md P4)
    # ------------------------------------------------------------------ #
    def _grip_press(self, event, edge: str = 'se') -> str:
        """A drag starts on `edge` (popup_layout.EDGES). The least it may shrink to is
        read now: the rows that must fit whole across, and a row of each box down."""
        self._cancel_resize_settle()
        self._move_active = False
        self._stop_grip_pointer()
        self._grip_start = None
        previous = self._user_rect, self._user_size
        self._hide_grip_outline()
        self._user_rect = None
        self._resize_final_width = None
        if getattr(self, '_linked_controller', None):
            self._linked_controller.clear()
        if (self.popup is not None and self._layout is not None
                and self._layout.get('resize_metrics')):
            metrics = self._layout['resize_metrics']
            # Editing can flatten ruby and change the border. Read the current
            # visible row, without forcing metrics for the entire paragraph.
            for key, box in (('min_translation', self.popup_text), ('min_original', self.popup_furigana)):
                if box is not None:
                    pixels = self._visible_rows_px(box, POPUP_USER_MIN_ROWS)
                    if pixels is not None:
                        metrics[key] = pixels + self._insets(box, 'y')
            least = (self._layout.get('chrome', 0) + metrics['min_translation']
                     + metrics['min_original'])
            self._grip_start = {
                'previous': previous,
                'pointer': (event.x_root, event.y_root), 'edge': edge,
                'rect': (self.popup.winfo_x(), self.popup.winfo_y(),
                         self.popup.winfo_width(), self.popup.winfo_height()),
                'min_width': self._required_width(), 'min_height': least}
            if self._content_resize is not None:
                self._content_resize.begin()
        return 'break'

    def _begin_resize(self, event, edge: str = 'se') -> str:
        """Every client resize target enters the same window-owned pointer tracker."""
        self._grip_press(event, edge)
        self._start_grip_pointer()
        return 'break'

    def _start_grip_pointer(self, reader=None):
        if self.popup is None or self._grip_start is None:
            return
        bridge = self._native_sizing
        if reader is None:
            # Private/scripted gestures must not sample the user's real input.
            if bridge is None or not getattr(bridge,'enabled',False) or bridge.running:
                return
            api = bridge.user
            def reader():
                point = wintypes.POINT()
                if not api.GetCursorPos(ctypes.byref(point)):
                    return None
                return PointerState(point.x, point.y, bool(api.GetAsyncKeyState(1) & 0x8000),
                                    bool(api.GetAsyncKeyState(27) & 0x8000))
        tracker = PointerResize(self.root, self.popup, reader,
            on_move=self._grip_pointer_motion, on_release=self._grip_release,
            on_cancel=self._cancel_user_resize, interval=RESIZE_INTERVAL_MS)
        if tracker.start():
            self._grip_pointer = tracker
        else:
            # The button may already be up when a queued press reaches Tk.
            self._cancel_user_resize()

    def _stop_grip_pointer(self):
        tracker = getattr(self, '_grip_pointer', None)
        self._grip_pointer = None
        if tracker is not None:
            tracker.stop()

    def _grip_pointer_motion(self, x, y):
        if self._update_grip_rectangle(x, y):
            self._cancel_grip_preview()
            self._show_grip_outline(self._user_rect)

    def _ensure_native_sizing(self):
        if (self._installing_native_sizing or self.popup is None or self._layout is None
                or not self._layout.get('resize_metrics')):
            return
        metrics = self._layout['resize_metrics']
        minimum = (self._required_width(), self._layout['chrome']
                   + metrics['min_translation'] + metrics['min_original'])
        bridge = self._native_sizing
        if bridge is not None and bridge.hwnd is not None:
            bridge.minimum = minimum
            bridge.noactivate = not self._allow_activation
            if not bridge.running:
                bridge.update_children()
            if self._content_resize is not None:
                self._content_resize.bind_native(bridge)
            return
        window = self.popup
        if bridge is not None:
            bridge.close(restore=False)
        bridge = WindowsSizing(window, rect=(window.winfo_x(), window.winfo_y(),
            window.winfo_width(), window.winfo_height()), minimum=minimum, bounds=self._screen_at,
            on_rect=self._native_resize_rect, on_start=self._native_resize_started,
            on_end=self._native_resize_finished, on_reset=self._grip_reset,
            edge_px=RESIZE_EDGE_PX, corner_px=RESIZE_CORNER_PX,
            noactivate=not self._allow_activation, outline=True, outline_colour=self._look.p.accent,
            client_edges=True)
        try:
            self._installing_native_sizing = True
            if bridge.attach():
                self._native_sizing = bridge
                if self._content_resize is not None:
                    self._content_resize.bind_native(bridge)
        except (OSError, tk.TclError):
            bridge.close()
            logging.warning('[Popup] native hit testing unavailable; using outline grip sizing', exc_info=True)
        finally:
            self._installing_native_sizing = False

    def _native_resize_started(self, edge, rect, point):
        if self.popup is None:
            return
        from types import SimpleNamespace
        point = point or (self.popup.winfo_pointerx(), self.popup.winfo_pointery())
        self._grip_press(SimpleNamespace(x_root=point[0], y_root=point[1]), edge)
        if self._grip_start is not None:
            self._grip_start['rect'] = rect

    def _native_resize_finished(self):
        if self._native_sizing is not None and getattr(self._native_sizing,'cancelled',False):
            self._cancel_user_resize()
        else:
            self._grip_release()
        self._ensure_native_sizing()

    def _native_resize_rect(self, rect):
        if self.popup is None or self._grip_start is None:
            return
        self._user_rect = tuple(int(value) for value in rect)
        if self._content_resize is not None:
            self._content_resize.observe()

    def _screen_at(self, x: int, y: int) -> popup_layout.WorkArea:
        """The work area of the monitor under (x, y), or the nearest one."""
        area = get_monitor_work_area(x, y)
        return popup_layout.WorkArea(*area) if area else self._layout['work']

    def _grip_drag(self, event) -> str:
        """Keep the latest rectangle; one pending frame consumes it without debounce."""
        if getattr(self._native_sizing, 'running', False):
            return 'break'
        tracker = getattr(self,'_grip_pointer',None)
        if tracker is not None and tracker.active:
            return 'break'
        if not self._update_grip_rectangle(event.x_root, event.y_root):
            return 'break'
        if self._grip_preview_after is None:
            self._grip_preview_after = schedule_window_callback(
                self.root, self.popup, RESIZE_INTERVAL_MS, self._grip_preview)
        return 'break'

    def _update_grip_rectangle(self, x, y):
        if self.popup is None or self._grip_start is None or self._layout is None:
            return False
        start = self._grip_start
        start_x, start_y = start['pointer']
        rect = popup_layout.resize(start['rect'], start['edge'], x - start_x,
                                   y - start_y, min_width=start['min_width'],
                                   min_height=start['min_height'],
                                   bounds=self._screen_at(x, y))
        self._user_rect = tuple(int(value) for value in rect)
        return True

    def _cancel_grip_preview(self) -> None:
        if self._grip_preview_after is not None:
            cancel_window_callback(self.root, self.popup, self._grip_preview_after)
            self._grip_preview_after = None

    def _grip_preview(self) -> None:
        """Move only a hollow marker. Pausing never changes the real window."""
        self._grip_preview_after = None
        if self._grip_start is not None and self._user_rect is not None:
            self._show_grip_outline(self._user_rect)

    def _show_grip_outline(self, rect):
        if self._grip_outline is None:
            from src.ui.resize_outline import ResizeOutline
            self._grip_outline = ResizeOutline(int(self.popup.wm_frame(), 16), self._look.p.accent)
        self._grip_outline.show(rect)

    def _hide_grip_outline(self):
        if self._grip_outline is not None:
            self._grip_outline.close()
            self._grip_outline = None

    def _cancel_user_resize(self):
        self._stop_grip_pointer()
        self._cancel_grip_preview()
        self._hide_grip_outline()
        if self._grip_start is not None:
            self._user_rect, self._user_size = self._grip_start.get('previous', (None, None))
        self._grip_start = None
        self._deferred_user_gesture = False
        if self._content_resize is not None:
            self._content_resize.end()
        return 'break'

    def _escape(self, _event=None):
        if self._grip_start is not None:
            return self._cancel_user_resize()
        self.dismiss()
        return 'break'

    def _apply_user_size(self) -> None:
        """Lay the popup out at the rectangle the user dragged it to - unless it already
        is (a pause laid it out, and the release came at the same place and size)."""
        rect = self._user_rect
        popup, layout = self.popup, self._layout
        if (self.popup is None or self._layout is None or not rect
                or (self._layout.get('position'), self._layout.get('size'))
                == (rect[:2], rect[2:])):
            return
        try:
            if popup is not self.popup or layout is not self._layout:
                return
            popup.geometry(f'{rect[2]}x{rect[3]}+{rect[0]}+{rect[1]}')
        except tk.TclError:
            pass

    def _live_user_layout(self, width, height, *, gutter=None):
        """Allocate actual panes from cached content metrics, with no event pumping."""
        if self._content_resize is not None and self._content_resize.holding:
            return
        popup, layout = self.popup, self._layout
        metrics = layout['resize_metrics']
        self._refresh_user_chrome(width)
        room = max(1, height - self._layout['chrome'])
        min_t, min_o = popup_layout.bounded_minimums(
            room, metrics['min_translation'], metrics['min_original'])
        if self._original_collapsed:
            heights = popup_layout.allocate(room, metrics['translation'], metrics['original'],
                min_translation=min_t, min_original=min_o,
                original_limit=metrics['collapsed_original'])
        else:
            heights = popup_layout.allocate_expanded(room, metrics['translation'], metrics['original'],
                min_translation=min_t, min_original=min_o)
        heights = heights._replace(translation=max(1, room - heights.original))
        gutter = self._text_scrollbar.winfo_reqwidth() if gutter is None else gutter
        self._resize_gutter = gutter
        for holder, box, bar, pixels, capped in (
            (self._text_holder, self.popup_text, self._text_scrollbar,
             heights.translation, heights.translation_capped),
            (self._original_holder, self.popup_furigana, self._original_scrollbar,
             heights.original, heights.original_capped)):
            if holder is not None:
                self._fit_user_box(holder, box, bar, pixels, capped, gutter)
                if popup is not self.popup or layout is not self._layout:
                    return
        self._fit_header(width)
        if popup is self.popup and layout is self._layout:
            layout['heights'] = heights

    def _refresh_user_chrome(self, width):
        if self._error_notice is None:
            return
        self._error_notice.fit_to(width - 2 * self._look.edge)
        metrics = self._layout['resize_metrics']
        self._layout['chrome'] = metrics['chrome_without_notice'] + self._error_notice.winfo_reqheight()
        if self._grip_start is not None:
            self._grip_start['min_height'] = (self._layout['chrome']
                + metrics['min_translation'] + metrics['min_original'])
            if self._native_sizing is not None:
                self._native_sizing.minimum = (self._grip_start['min_width'], self._grip_start['min_height'])

    def _resize_notice_changed(self, event):
        if event.widget is not self._error_notice or not self._resize_display:
            return
        if self._grip_start is None:
            self._schedule_resize_settle()
        elif self._grip_preview_after is None:
            self._grip_preview_after = schedule_window_callback(
                self.root, self.popup, RESIZE_INTERVAL_MS, self._grip_preview)

    def _fit_user_box(self, holder, box, scrollbar, height, capped, gutter):
        state = self._resize_display.get(box)
        if state is None:
            state = self._resize_display[box] = dict(fractions=None, mode=None)
            def scrolled(first, last):
                scrollbar.set(first, last)
                fractions = float(first), float(last)
                if state['fractions'] != fractions:
                    state['fractions'] = fractions
                    if self._grip_start is None:
                        self._schedule_resize_settle()
            state['command'] = box.register(scrolled)
            state['configure_binding'] = box.bind('<Configure>',
                lambda event: self._schedule_resize_settle()
                if event.widget is box and self._grip_start is None else None, add='+')
            box.configure(yscrollcommand=state['command'])
            if self._linked_controller is not None:
                self._linked_controller.watch_scroll(box)
        if int(holder.cget('height')) != max(1, height):
            holder.configure(height=max(1, height))
        mode = bool(capped), bool(gutter)
        if state['mode'] == mode:
            return
        state['mode'] = mode
        spacer = getattr(holder, 'gutter_spacer', None)
        if capped:
            if spacer is not None:
                spacer.pack_forget()
            scrollbar.pack(side=RIGHT, fill=Y, before=box)
        else:
            scrollbar.pack_forget()
            if gutter:
                if spacer is None:
                    spacer = tk.Frame(holder, width=gutter, bg=str(holder.cget('bg')), **NO_AUTOSTYLE)
                    holder.gutter_spacer = spacer
                spacer.configure(width=gutter)
                spacer.pack(side=RIGHT, fill=Y, before=box)
            elif spacer is not None:
                spacer.pack_forget()

    def _schedule_resize_settle(self):
        if (self.popup is not None and self._layout is not None and self._resize_display
                and self._resize_settle_job is None
                and not getattr(self, '_fitting_boxes', False)
                and not (self._content_resize is not None and self._content_resize.holding)):
            self._resize_settle_job = schedule_window_callback(
                self.root, self.popup, RESIZE_INTERVAL_MS, self._settle_user_size)

    def _cancel_resize_settle(self):
        if self._resize_settle_job is not None:
            cancel_window_callback(self.root, self.popup, self._resize_settle_job)
            self._resize_settle_job = None

    def _settle_user_size(self):
        self._resize_settle_job = None
        if self.popup is None or self._layout is None or self._grip_start is not None:
            return
        width, height = self._layout['size']
        self._refresh_user_chrome(width)
        key = (width, self.popup_text.winfo_width(), self.popup_text.winfo_height(),
               self.popup_furigana.winfo_width() if self.popup_furigana is not None else 0,
               self.popup_furigana.winfo_height() if self.popup_furigana is not None else 0,
               self._layout['chrome'])
        if self._resize_final_width != key:
            self._resize_final_width = key
            metrics = self._layout['resize_metrics']
            room = max(1, height - self._layout['chrome'])
            # Visible end points prove a short document's exact height. Asking
            # for even a bounded number of display-line indices can scan a long
            # logical paragraph; leave that work to Text's asynchronous renderer.
            for name, box in (('translation', self.popup_text), ('original', self.popup_furigana)):
                if box is not None:
                    pixels = self._visible_content_px(box)
                    if pixels is not None:
                        metrics[name] = pixels + self._insets(box, 'y')
                    else:
                        metrics[name] = max(metrics[name], room + metrics['line_floor'])
            if self.popup_furigana is not None and self._original_collapsed:
                pixels = self._visible_rows_px(self.popup_furigana,
                    POPUP_COLLAPSED_ORIGINAL_ROWS, from_start=True)
                if pixels is not None:
                    metrics['collapsed_original'] = pixels + self._insets(self.popup_furigana, 'y')
            self._live_user_layout(width, height, gutter=self._resize_gutter)
        caps = []
        for box in (self.popup_text, self.popup_furigana):
            state = self._resize_display.get(box)
            first, last = (state['fractions'] or box.yview()) if state is not None else (0, 1)
            caps.append(first > 1e-6 or last < 1 - 1e-6)
        gutter = self._text_scrollbar.winfo_reqwidth() if any(caps) else 0
        self._resize_gutter = gutter
        heights = self._layout['heights']
        self._fit_user_box(self._text_holder, self.popup_text, self._text_scrollbar,
                           heights.translation, caps[0], gutter)
        if self._original_holder is not None:
            self._fit_user_box(self._original_holder, self.popup_furigana, self._original_scrollbar,
                               heights.original, caps[1], gutter)
        self._layout['heights'] = heights._replace(
            translation_capped=caps[0], original_capped=caps[1])
        self._show_original_toggle(any(caps) or not self._original_collapsed)
        self._fit_header(width)

    def _clear_resize_display(self):
        self._cancel_resize_settle()
        for box, state in self._resize_display.items():
            try:
                box.unbind('<Configure>', state['configure_binding'])
                box.configure(yscrollcommand='')
                if self._linked_controller is not None:
                    self._linked_controller.watch_scroll(box)
                box.deletecommand(state['command'])
            except tk.TclError:
                pass
        self._resize_display.clear()
        self._resize_final_width = None
        self._resize_gutter = 0

    def _flush_resize_save(self):
        if self._resize_save_job is not None:
            cancel_window_callback(self.root, self.popup, self._resize_save_job)
            self._resize_save_job = None
        size, self._resize_pending_save = self._resize_pending_save, None
        if size is not None:
            self._remember_size(*size)

    def _resize_destroyed(self, event):
        if event.widget is self.popup:
            self._move_active = False
            self._stop_grip_pointer()
            self._cancel_grip_preview()
            self._hide_grip_outline()
            self._clear_resize_display()
            self._flush_resize_save()
            self._grip_start = None
            self._resize_handles.clear()
            self._grip = None

    def _grip_release(self, _event=None) -> str:
        if getattr(self._native_sizing, 'running', False):
            return 'break'
        tracker = getattr(self,'_grip_pointer',None)
        if tracker is not None and tracker.active and _event is not None:
            self._update_grip_rectangle(_event.x_root, _event.y_root)
        self._stop_grip_pointer()
        self._cancel_grip_preview()
        self._hide_grip_outline()
        if self._grip_start is not None and self._user_rect:
            self._user_size = self._user_rect[2:]
            self._apply_user_size()
            if self.popup is None or self._layout is None or self._grip_start is None:
                return 'break'
        self._grip_start = None
        if self._content_resize is not None:
            self._content_resize.end()
        else:
            self._schedule_resize_settle()
        return 'break'

    def _deferred_resize_started(self):
        self._cancel_grip_preview()
        self._cancel_resize_settle()
        self._deferred_user_gesture = True
        self._gesture_start_client = self._content_resize._last_client
        self._gesture_saved_size = self._user_size

    def _deferred_resize_committed(self, width, height):
        layout = self._layout
        if (getattr(self, '_fitting_boxes', False) or layout is None
                or not layout.get('revealed') or not layout.get('resize_metrics')
                or (not self._deferred_user_gesture and self._user_size is None)):
            return
        layout.update(size=(width, height), position=(self.popup.winfo_x(), self.popup.winfo_y()))
        self._live_user_layout(width, height)
        if self.popup is None or self._layout is not layout:
            return
        self._apply_noactivate()
        if self._deferred_user_gesture:
            if (width, height) != self._gesture_start_client:
                self._user_size = width, height
                self._resize_pending_save = self._user_size
            else:
                self._user_size = self._gesture_saved_size

    def _deferred_resize_settled(self):
        if (getattr(self, '_fitting_boxes', False) or self.popup is None or self._layout is None
                or not self._layout.get('resize_metrics')
                or (not self._deferred_user_gesture and self._user_size is None)):
            return
        self._settle_user_size()
        if self._deferred_user_gesture:
            self._deferred_user_gesture = False
            if self._resize_pending_save is not None:
                if self._resize_save_job is not None:
                    cancel_window_callback(self.root, self.popup, self._resize_save_job)
                self._resize_save_job = schedule_window_callback(
                    self.root, self.popup, RESIZE_SAVE_DELAY_MS, self._flush_resize_save)

    def _grip_reset(self, _event=None) -> str:
        """Double-click on the grip: forget the dragged size, back to automatic."""
        if self._content_resize is not None and self._content_resize.holding:
            self._content_resize.end()
        self._deferred_user_gesture = False
        self._stop_grip_pointer()
        self._cancel_grip_preview()
        self._hide_grip_outline()
        self._clear_resize_display()
        if self._resize_save_job is not None:
            cancel_window_callback(self.root, self.popup, self._resize_save_job)
            self._resize_save_job = None
        self._resize_pending_save = None
        self._user_size = None
        self._user_rect = None
        self._grip_start = None
        self._remember_size(None, None)
        if self.popup is not None and self._layout is not None:
            width = self._automatic_width(None)
            self._layout['width'] = width
            self._layout['cap'] = popup_layout.height_cap(self._layout['work'])
            self.popup.geometry(f"{width}x{self.popup.winfo_height()}")
            self._fit_boxes(reposition=False)
        return 'break'

    def _ruby_enabled(self) -> bool:
        """Whether popup output should carry readings.

        The Settings toggle now governs every annotated surface, because the
        popup annotates at render time instead of relying on the pipeline
        having shipped a notation string.
        """
        return bool(self.config and self.config.get_furigana_enabled())

    @staticmethod
    def _ruby_hint(target_lang: str, is_grammar: bool = False, original: str = "",
                   source_lang: Optional[str] = None) -> Optional[str]:
        """Language hint for annotating output text, or None when unknown.

        A translation is in its target language - and, by the language-set rule
        (`lang_id.output_hint`), Japanese quoted in, say, English output is
        Japanese when the source was. A grammar fix keeps the source's language:
        Japanese when the original's characters or its verdict say so (J6), else
        no hint - kanji-only output then stays plain rather than guessing.
        """
        source = lang_id.resolve(original, model=source_lang).language if original else None
        if is_grammar:
            return lang_id.JAPANESE if source == lang_id.JAPANESE else None
        return lang_id.output_hint(source, target_lang)

    @staticmethod
    def _source_row(original: str, source_lang: Optional[str], is_grammar: bool,
                    is_error: bool) -> Optional[str]:
        """Which Japanese / Chinese row to offer (`source_marker.source_row`):
        ROW_ASK for kanji that neither the characters nor a model settled
        (Decision 22), ROW_CHANGE for a term a model or the user did (K4) -
        never on a grammar fix or a failure notice."""
        if is_grammar or is_error or not original:
            return None
        return source_row(original, source_lang)

    def _build_source_chooser(self, parent, mode: str, verdict: Optional[str]) -> None:
        """The Japanese / Chinese row, above the buttons (`fill_source_row`).

        A row, not a dialog: the translation is already on screen, and nothing
        is blocked while the question stands. Answering re-translates with the
        chosen source - a translation the model could not place may carry the
        other language's sense (先生 came back "Mr."), so readings alone would sit
        above the wrong meaning.
        """
        row = ui.Frame(parent, on='surface')
        row.pack(side=BOTTOM, fill=X, pady=(self._look.px(8), 0))
        self._bind_drag(row)
        fill_source_row(row, mode, verdict, self._handle_choose_source, plane='surface')
        self.popup_source_chooser = row

    def _handle_choose_source(self, language: str) -> None:
        if self._on_choose_source:
            self._on_choose_source(language)

    def _linked_enabled(self):
        getter = getattr(self.config, 'get_linked_reading_enabled', None)
        return getter is not None and getter() is True

    def _stop_linked_reading(self):
        controller = getattr(self, '_linked_controller', None)
        if controller is not None:
            controller.close()
            self._linked_controller = None
        if self._linked_status is not None:
            try:
                self._linked_status.configure(text='')
            except tk.TclError:
                pass

    def _toggle_linked_reading(self):
        self._linked_active = not self._linked_active
        self._sync_linked_reading()

    def _sync_linked_reading(self, _event=None):
        """Apply the shared preference without regenerating or replacing text."""
        if self.popup is None or not self.popup.winfo_exists() or self._linked_paused:
            return
        visible = self._linked_enabled() and getattr(self.config, 'get_show_linked_reading', lambda: True)() is True
        enabled = visible and self._linked_active
        if self.popup_linked_btn is not None:
            if visible:
                self.popup_linked_btn.pack(side=LEFT, padx=(self._look.px(4), 0), before=self.popup_open_btn)
            else:
                self.popup_linked_btn.pack_forget()
            self.popup_linked_btn.configure(state='normal' if self._current_alignment else 'disabled')
            self.popup_linked_btn.set_on(enabled)
        status = ''
        if enabled:
            status = (('Approximate links' if self._current_alignment.get('method') == 'structure'
                       else 'Hover to link sentences') if self._current_alignment else 'No reliable links')
        if self._linked_status is not None:
            self._linked_status.configure(text=status)
        if self._linked_tooltip is not None:
            self._linked_tooltip.text = (
                ('Linked reading: On. ' + status + '.') if enabled else
                'Linked reading: Off. Click to enable saved or local links.')
        if not enabled:
            self._stop_linked_reading()
        elif (getattr(self, '_linked_controller', None) is None
              and self._current_alignment and self.popup_furigana is not None
              and self.popup_text is not None
              and self.popup_furigana.get_plain() == self._current_original
              and self.popup_text.get_plain() == self._current_translation):
            self._linked_controller = LinkedReadingController(
                self.root, self.popup_furigana, self.popup_text, self._current_alignment,
                suspended=lambda: not self._linked_enabled() or self._grip_start is not None)

    def _unbind_linked_setting(self):
        if self._linked_setting_binding is not None:
            self.root.unbind('<<LinkedReadingChanged>>', self._linked_setting_binding)
            self._linked_setting_binding = None

    def _render_furigana(self, parent, furigana_text: str):
        """The original, with its readings, above the translation - read-only, under a
        small header that names it and, when it runs longer than
        POPUP_COLLAPSED_ORIGINAL_ROWS rows, collapses or opens it (Decision 34, TODO.md
        Q5: collapsed by default - the translation is what was asked for).

        RubyText owns the ruby layout, the plain-text readback and the wheel
        handling; the notation escapes are honored, so source text containing
        a literal "{a|b}" shows as itself instead of a fake reading.

        Args:
            parent: Parent frame to pack into
            furigana_text: Text with {kanji|reading} notation
        """
        look = self._look
        header = ui.Frame(parent, on='surface')
        header.pack(side=TOP, fill=X, pady=(0, look.px(2)))
        self._bind_drag(header)
        known = self._current_source_lang or (lang_id.JAPANESE if furigana_text else None)
        self._linked_status = ui.Label(header, text='', kind='muted', on='surface')
        self._linked_status.pack(side=LEFT, padx=(look.gap + look.px(4), 0))
        # Packed by step 2 when either pane needs expansion.
        toggle = ui.Button(self._header, "Show all", command=self._toggle_original, kind='subtle',
                           on='surface', icon='chevron-down')
        self.popup_original_toggle = toggle
        self._original_header = header

        script = type_ramp.script_for_language(known or (lang_id.JAPANESE if furigana_text else None))
        self._original_holder, self.popup_furigana, self._original_scrollbar = self._make_box(
            parent, expand=False, wrap=tk.WORD, bg=look.p.surface,
            base_fg=look.p.muted, kanji_fg=look.p.muted, base_font=look.font('body', script))
        if furigana_text:
            self.popup_furigana.insert_notation(tk.END, furigana_text)
        else:
            insert_output(self.popup_furigana, tk.END, self._current_original,
                          lang_hint=self._current_source_lang, enabled=self._ruby_enabled())
        self.popup_furigana.config(state='disabled')

    def _show_original_toggle(self, collapsible: bool) -> None:
        """Show the header's expansion control when either pane needs more room."""
        toggle = self.popup_original_toggle
        if toggle is None:
            return
        if collapsible:
            toggle.configure(text="Show all" if self._original_collapsed else "Show less")
            toggle.set_icon('chevron-down' if self._original_collapsed else 'chevron-up')
            toggle.pack(side=RIGHT, after=self.popup_close_btn)
        else:
            toggle.pack_forget()

    def _toggle_original(self, _event=None) -> str:
        """Expand translation first, give source spare room, or restore compact mode."""
        if self.popup is None or self._layout is None or not self._layout['done']:
            return 'break'
        if getattr(self, '_linked_controller', None):
            self._linked_controller.clear()
        if self._original_collapsed:
            self._expand_pair()
        else:
            self._original_collapsed = True
            saved = self._expanded_restore
            self._expanded_restore = None
            if saved:
                width, height, x, y, layout_width, cap = saved
                self._layout.update(width=layout_width, cap=cap)
                self._fit_header(width)
                self._fit_boxes(reposition=False, size=(width, height), position=(x, y))
            else:
                self._fit_boxes(reposition=False, size=self._user_size)
        return 'break'

    def _expand_pair(self):
        self._clear_resize_display()
        current = self.popup.winfo_width()
        self._expanded_restore = (current, self.popup.winfo_height(), self.popup.winfo_x(),
                                  self.popup.winfo_y(), self._layout['width'], self._layout['cap'])
        area = get_monitor_work_area(self.popup.winfo_rootx(), self.popup.winfo_rooty())
        work = popup_layout.WorkArea(*area) if area else self._layout['work']
        self._layout['work'] = work
        cap = max(1, min(round(work.height * 0.9), work.height - 2 * POPUP_SCREEN_MARGIN))
        chrome = self._layout['chrome']
        gutter = self._text_scrollbar.winfo_reqwidth()
        # Reserve one consistent gutter during all probes; scrollbar insertion
        # cannot change the width after the text has been measured.
        for holder, box, scrollbar in ((self._text_holder, self.popup_text, self._text_scrollbar),
                                        (self._original_holder, self.popup_furigana, self._original_scrollbar)):
            if box is not None:
                self._fit_box(holder, box, scrollbar, holder.winfo_height(), False, gutter)
        measurements = []
        for width in popup_layout.expansion_widths(current, self._required_width(), work):
            self.popup.geometry(f'{width}x{cap}')
            self.popup.update_idletasks()
            need_t = self._content_px(self.popup_text) + self._insets(self.popup_text, 'y')
            need_o = (self._content_px(self.popup_furigana) + self._insets(self.popup_furigana, 'y')
                      if self.popup_furigana is not None else 0)
            source_row = (self._rows_px(self.popup_furigana, POPUP_USER_MIN_ROWS)
                          + self._insets(self.popup_furigana, 'y')
                          if self.popup_furigana is not None else 0)
            measurements.append((width, need_t, need_o, source_row))
            if chrome + need_t + min(need_o, source_row) <= cap:
                break
        width, height = popup_layout.choose_expansion(measurements, cap, chrome, round(current * 1.7))
        x = min(max(work.left + POPUP_SCREEN_MARGIN, self._expanded_restore[2]),
                work.right - POPUP_SCREEN_MARGIN - width)
        y = min(max(work.top + POPUP_SCREEN_MARGIN, self._expanded_restore[3]),
                work.bottom - POPUP_SCREEN_MARGIN - height)
        self._original_collapsed = False
        self._layout.update(width=width, cap=cap)
        self._fit_header(width)
        self._fit_boxes(reposition=False, size=(width, height), position=(x, y))

    def _start_move(self, event):
        """Record start position for dragging."""
        if self.popup is None or self._grip_start is not None:
            return
        if getattr(self, '_linked_controller', None):
            self._linked_controller.clear()
        self._move_active = True
        self._drag_x = event.x_root
        self._drag_y = event.y_root

    def _end_move(self, _event=None):
        self._move_active = False

    def _on_drag(self, event):
        """Handle dragging of the popup."""
        if not self.popup or not self._move_active or self._grip_start is not None:
            return

        deltax = event.x_root - self._drag_x
        deltay = event.y_root - self._drag_y

        self._drag_x = event.x_root
        self._drag_y = event.y_root

        x = self.popup.winfo_x() + deltax
        y = self.popup.winfo_y() + deltay
        self.popup.geometry(f"+{x}+{y}")

    def _handle_copy(self):
        """Handle copy button click."""
        if self._on_copy:
            self._on_copy()

    def _handle_copy_and_replace(self):
        """Handle replace button - Quick or Manual mode based on config."""
        if getattr(self, '_audio_result', False):
            return
        is_quick = self.config and self.config.get_quick_replace()

        if is_quick or not self._current_original:
            # Quick Replace mode OR no original text → immediate replace
            if self._on_copy_and_replace:
                self._on_copy_and_replace()
            return

        self._show_replace_preview()

    def _handle_re_translate(self):
        """Handle Re-translate button - force a fresh API call, bypassing the cache."""
        if self._on_re_translate:
            self._on_re_translate()

    def _clear_noactivate(self):
        """Remove WS_EX_NOACTIVATE so the popup can receive keyboard focus (edit mode)."""
        self._allow_activation = True
        if self.popup:
            set_noactivate(self.popup, False)

    def _handle_custom_prompt(self):
        """Handle Custom Prompt button - switch the box to editable freeform-ask mode."""
        self._enter_custom_prompt_mode()

    def _enter_custom_prompt_mode(self):
        """Make the translation box editable and swap the button bar to [Send][Cancel].

        Keeps the current translation as the starting prompt so the user can append or
        edit freely. The entire box content is sent verbatim on Send (no translate
        wrapper). Furigana, if shown, stays as a read-only guide above the box.
        """
        if not self.popup_text or not self._btn_frame:
            return

        self._linked_paused = True
        self._stop_linked_reading()

        # I3 - an editable widget never holds ruby. edit_undo() cannot restore a
        # destroyed embedded window, and typing next to one would leave the
        # readings describing text the user has already changed. The source
        # block above the box keeps its readings.
        if self.popup_text.has_ruby:
            self.popup_text.set_plain(self.popup_text.get_plain())

        # Make the box editable and visually mark edit mode (teal border).
        accent = self._look.p.accent
        self.popup_text.config(state='normal',
                               highlightthickness=1,
                               highlightbackground=accent,
                               highlightcolor=accent)

        # The popup was created WS_EX_NOACTIVATE (so it won't steal focus from the source
        # app). Editing needs keyboard focus, so drop that bit and force focus now.
        self._clear_noactivate()
        try:
            self.popup.attributes('-topmost', True)
            self.popup.lift()
            self.popup.focus_force()
        except Exception:
            pass
        self.popup_text.focus_set()
        self.popup_text.mark_set('insert', 'end')
        self.popup_text.see('end')

        # Swap button bar to [Send] [Cancel] + close.
        for widget in self._btn_frame.winfo_children():
            widget.destroy()

        ui.Button(self._btn_frame, "Send", command=self._handle_custom_prompt_send,
                  kind='accent', on='surface', icon='send').pack(side=LEFT)
        ui.Button(self._btn_frame, "Cancel", command=self._handle_custom_prompt_cancel,
                  kind='soft', on='surface').pack(side=LEFT, padx=(self._look.px(4), 0))

        # The close button is in the header, as in every other state.

    def _handle_custom_prompt_send(self):
        """Read the entire edit-box content and dispatch it as a freeform prompt."""
        if not self.popup_text:
            return
        # get_plain(), never get(): an embedded ruby frame contributes no
        # characters to get(), so the model would receive text with every
        # annotated word deleted. Editing already flattened the box, but this
        # must stay correct regardless of how the content got there.
        content = self.popup_text.get_plain()
        if self._on_custom_prompt_send:
            self._on_custom_prompt_send(content)

    def _handle_custom_prompt_cancel(self):
        """Cancel custom-prompt mode - restore the normal result popup."""
        self.show(self._current_translation, self._current_target_lang,
                  self._current_trial_info, self._current_original,
                  furigana_text=self._current_furigana,
                  source_lang=self._current_source_lang,
                  **({'audio_result': True, 'on_audio_play': self._audio_play_callback,
                      'model_info': self._current_model_info} if getattr(self, '_audio_result', False) else {}))

    def _show_replace_preview(self):
        """Strike the source in its own pane without changing the translated document."""
        self._linked_paused = True
        self._stop_linked_reading()
        original = self._current_original

        if not self.popup_text or not self._btn_frame:
            return

        if getattr(self, '_replace_preview', False):
            return
        self._replace_preview = True
        self._preview_added_source = self.popup_furigana is None
        if self._preview_added_source:
            self._render_furigana(self._main_frame, self._current_furigana)
            self._original_header.pack_configure(before=self._text_holder)
            self._original_holder.pack_configure(before=self._text_holder)
        self._preview_collapsed = self._original_collapsed
        self._original_collapsed = False
        source = self.popup_furigana
        source.tag_configure('replace_preview', font=source.base_font + ('overstrike',),
                             foreground=self._look.p.muted)
        source.set_plain(original, tags='replace_preview')
        source.configure(state='disabled')

        # --- Replace button bar ---
        for widget in self._btn_frame.winfo_children():
            widget.destroy()

        # Agree confirms (the one accent); Cancel leaves the text as it is
        ui.Button(self._btn_frame, "Agree", command=self._handle_replace_agree, kind='accent',
                  on='surface', icon='check-lg').pack(side=LEFT)
        ui.Button(self._btn_frame, "Cancel", command=self._handle_replace_cancel, kind='soft',
                  on='surface').pack(side=LEFT, padx=(self._look.px(4), 0))

        # The close button stays in the header. The bar's icons are gone, and so are
        # their tooltips.
        for tip in self._tooltips:
            tip.hide()
        self._tooltips = []

        # --- Fit the box to the preview: measured, the popup's corner kept ---
        # Before step 2 has run (the popup not yet shown), step 2 measures this content.
        if self.popup is not None and self._layout is not None and self._layout['done']:
            self._fit_boxes(reposition=False, size=self._user_size)

    def _handle_replace_agree(self):
        """User confirmed replace - execute the actual copy+paste."""
        if self._on_copy_and_replace:
            self._on_copy_and_replace()

    def _handle_replace_cancel(self):
        """Return to the same result window and retain the source-application target."""
        if not getattr(self, '_replace_preview', False):
            return
        self._replace_preview = False
        self.popup_furigana.clear()
        if self._preview_added_source:
            for widget in (self._original_header, self._original_holder, self.popup_original_toggle):
                widget.destroy()
            self.popup_furigana = self.popup_original_toggle = self._original_header = None
            self._original_holder = self._original_scrollbar = self._linked_status = None
        else:
            if self._current_furigana:
                self.popup_furigana.insert_notation(tk.END, self._current_furigana)
            else:
                insert_output(self.popup_furigana, tk.END, self._current_original,
                              lang_hint=self._current_source_lang, enabled=self._ruby_enabled())
            self.popup_furigana.configure(state='disabled')
        self._original_collapsed = self._preview_collapsed
        for widget in self._btn_frame.winfo_children():
            widget.destroy()
        self._build_action_bar(self._is_grammar)
        self._linked_paused = False
        self._sync_linked_reading()
        if self._layout is not None and self._layout['done']:
            self._fit_boxes(reposition=False, size=self._user_size)

    def _handle_open_replace_settings(self):
        """Show dropdown menu with Replace settings options."""
        p = self._look.p if self._look else ui.get().palette
        menu = ui.Menu(self.root, tearoff=0, bg=p.surface, fg=p.text,
                       activebackground=p.soft, activeforeground=p.soft_text,
                       font=ui.get().font('body'), relief='flat', bd=1)
        menu.add_command(
            label="Hotkey Settings",
            command=self._goto_hotkey_settings
        )
        # Position menu below the gear button
        if self._replace_gear_btn:
            x = self._replace_gear_btn.winfo_rootx()
            y = self._replace_gear_btn.winfo_rooty() + self._replace_gear_btn.winfo_height()
            menu.post(x, y)

    def _goto_hotkey_settings(self):
        """Navigate to Hotkeys tab in Settings."""
        self.close()
        if self._on_open_settings_hotkeys_tab:
            self._on_open_settings_hotkeys_tab()

    def _handle_open_translator(self):
        """Handle open translator button click.

        The callback closes this popup itself (it has to: the main window is
        positioned over it), so there is nothing to close here.
        """
        if self._on_open_translator:
            self._on_open_translator()

    def _handle_open_settings(self):
        """Handle open settings button click (from error state)."""
        self.close()
        if self._on_open_settings:
            self._on_open_settings()

    def set_copy_button_text(self, text: str):
        """Set copy button text (e.g., for 'Copied!' feedback) - with a tick while it says so."""
        if self.popup_copy_btn:
            try:
                self.popup_copy_btn.configure(text=text)
                self.popup_copy_btn.set_icon('copy' if text == "Copy" else 'check-lg')
            except tk.TclError:
                pass

    def set_replace_button_text(self, text: str):
        """Set replace button text (e.g., for 'Replaced!' feedback)."""
        if self.popup_replace_btn:
            try:
                self.popup_replace_btn.configure(text=text)
            except tk.TclError:
                pass

    def _update_dict_button_state(self):
        """Note whether Dictionary mode has a language pack to work with.

        The button looks the same either way and stays clickable: with no pack the flow
        itself says what to install. Only the cursor tells, as it always did.
        """
        if not self.popup_dict_btn:
            return

        self._dict_btn_enabled = nlp_manager.is_any_installed()

        try:
            self.popup_dict_btn.configure(cursor='hand2' if self._dict_btn_enabled else 'arrow')
        except tk.TclError:
            pass  # Widget destroyed

    def _open_dictionary_popup(self):
        """Open Dictionary mode on the popup's text, beside the popup.

        Opens as ADDITIONAL window - does NOT close the quick translate popup.
        The flow itself is DictionaryPopup's, shared with the main window's
        Dictionary button (DEFERRED D13 - this used to be a second copy of it).
        """
        # Use original text if available, otherwise fall back to translation
        text_to_analyze = self._current_original if self._current_original else self._current_translation
        if not text_to_analyze or not self._on_open_dictionary:
            return
        self._on_open_dictionary(text_to_analyze, self._current_target_lang, self.popup)

    def show_audio_status(self, title, target_lang):
        """Audio lifecycle is independent of the ordinary 20-second translation spinner."""
        self.close()
        look = self._look = _Look(ui.get())
        self.popup = self._new_window(look)
        self._apply_noactivate()
        self.popup.attributes('-topmost', True)
        work = self._work_area()
        width = min(look.px(420), work.width - 2 * POPUP_SCREEN_MARGIN)
        body = ui.Frame(self.popup, on='surface', padding=look.px(16))
        body.pack(fill='both', expand=True)
        close = ui.IconButton(body, 'x-lg', command=self.dismiss, label='Cancel', on='surface')
        close.pack(side='right', anchor='n', padx=(look.px(12), 0))
        labels = ui.Frame(body, on='surface')
        labels.pack(side='left', fill='both', expand=True)
        wrap = max(look.px(120), width - look.px(96))
        self._loading_label = ui.Label(labels, title, kind='body', on='surface', wraplength=wrap, justify='left')
        self._loading_label.pack(anchor='w')
        self._audio_hint = ui.Label(labels, 'Release all shortcut keys to translate', kind='muted',
                                     on='surface', wraplength=wrap, justify='left')
        self._audio_hint.pack(anchor='w', pady=(look.px(6), 0))
        self._audio_level = ui.Progress(labels, mode='determinate', maximum=1, length=wrap)
        self._audio_level.pack(fill='x', pady=(look.px(8), 0))
        self.popup.update_idletasks()
        height = body.winfo_reqheight() + 2 * look.ring
        x, y, height = self._place(width, height, work, min_height=height)
        self.popup.geometry(f'{width}x{height}+{x}+{y}')
        self.popup_close_btn = close
        self._audio_pad = look.px(16)
        self.popup.bind('<Escape>', lambda event: self.dismiss())
        self.popup.protocol('WM_DELETE_WINDOW', self.dismiss)
        self._audio_status = True
        self._audio_result = False

    def update_audio_status(self, title, hint='', level=None):
        if self.popup is None or self._loading_label is None:
            return
        self._loading_label.configure(text=title)
        if getattr(self, '_audio_hint', None) is not None:
            self._audio_hint.configure(text=hint)
        if level is not None and getattr(self, '_audio_level', None) is not None:
            self._audio_level.configure(value=level)
        if getattr(self, '_audio_status', False):
            labels_height = (self._loading_label.winfo_reqheight() + self._audio_hint.winfo_reqheight()
                             + self._audio_level.winfo_reqheight() + self._look.px(14))
            height = max(labels_height, self.popup_close_btn.winfo_reqheight()) + 2 * self._audio_pad + 2 * self._look.ring
            if abs(self.popup.winfo_height() - height) > 1:
                width = self.popup.winfo_width()
                x, y, height = self._place(width, height, self._work_area(), min_height=height)
                self.popup.geometry(f'{width}x{height}+{x}+{y}')

    def set_audio_result(self, active, on_play=None, on_retry=None):
        self._audio_result = active
        self._audio_status = False
        self._on_audio_retry = on_retry
        self._audio_play_callback = on_play
        self._allow_replace = not active
        if active and self.popup_replace_btn is not None:
            self.popup_replace_btn.pack_forget()
        if active and on_play and self._btn_frame is not None:
            self.audio_play_btn = ui.IconButton(self._btn_frame, 'play-fill', command=on_play,
                                                label='Play recording', on='surface')
            self.audio_play_btn.pack(side=LEFT, padx=(self._look.gap, 0))
        if active and self.popup_text is not None:
            self._generation += 1
            self._start_layout()

    def dismiss(self):
        audio_close = getattr(self, 'audio_close_handler', None)
        if audio_close and (getattr(self, '_audio_result', False) or getattr(self, '_audio_status', False)):
            if audio_close():
                return
        callback = getattr(self, '_on_dismiss', None)
        if callback:
            callback()
        self.close()

    def close(self):
        self._audio_status = False
        self._audio_result = False
        self._audio_hint = None
        self._audio_level = None

        self._move_active = False
        self._stop_grip_pointer()
        self._resize_handles.clear()
        self._grip = None
        controller = getattr(self, '_content_resize', None)
        self._content_resize = None
        if controller is not None:
            controller.close()
        self._deferred_user_gesture = False
        self._stop_preferences()
        job = getattr(self, '_loading_job', None)
        if job:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._loading_job = None
        """Close the popup."""
        self._stop_linked_reading()
        self._unbind_linked_setting()
        self.popup_linked_btn = None
        self._linked_status = None
        self._linked_tooltip = None
        # Stop loading animation
        self._loading_animation_running = False
        self._loading_label = None
        self._loading_start_time = 0

        # Clean up dictionary mode first
        if self._dict_frame:
            self._dict_frame.destroy()
            self._dict_frame = None
        self._dict_mode_active = False

        for tip in self._tooltips:
            tip.hide()
        self._tooltips = []
        self._cancel_grip_preview()
        self._hide_grip_outline()
        self._grip_start = None
        self._clear_resize_display()
        self._flush_resize_save()
        if self._native_sizing is not None:
            self._native_sizing.close(restore=False)
            self._native_sizing = None
        self._credit_cache = None

        if self.popup:
            try:
                if self.popup.winfo_exists():
                    self.popup.destroy()
            except tk.TclError:
                pass
            self.popup = None
            self.popup_text = None
            self.popup_furigana = None
            self.popup_attribution = None
            self.popup_copy_btn = None
            self.popup_replace_btn = None
            self._replace_gear_btn = None
            self.popup_dict_btn = None
            self.popup_source_chooser = None
            self._btn_frame = None
            self._main_frame = None
            self.popup_close_btn = None
            self.popup_open_btn = None
            self.popup_chip = None
            self.popup_retranslate_btn = None
            self.popup_custom_prompt_btn = None
            self.popup_original_toggle = None
            self._error_notice = None
            self._loading_dots = None
            self._original_header = None
            self._original_holder = None
            self._original_scrollbar = None
            self._text_holder = None
            self._text_scrollbar = None
            self._trial_frame = None
            self._grip = None
        self._layout = None
        self._expanded_restore = None
        self._grip_start = None
        self._user_size = None
        self._user_rect = None

    @property
    def is_open(self) -> bool:
        """Check if popup is currently open."""
        return self.popup is not None

    def stop_dictionary_animation(self):
        """Stop the dictionary lookup animation if running."""
        if self._dict_popup_frame:
            try:
                self._dict_popup_frame.stop_lookup_animation()
            except Exception:
                pass  # Frame might be destroyed

    def show_dictionary_result(self, result: str, target_lang: str, trial_info: dict = None,
                               looked_up_words: list = None, source_lang: str = None,
                               animation_frame=None):
        """Open an independent, themed card view of the same Dictionary fields."""
        if defer_update(self.popup, 'dictionary-result', lambda: self.show_dictionary_result(
                result, target_lang, trial_info, looked_up_words, source_lang, animation_frame)):
            return
        from src.ui.dictionary_result import DictionaryResultWindow
        if animation_frame is None:
            self.stop_dictionary_animation()
        else:
            animation_frame.stop_lookup_animation()
        area = get_monitor_work_area(self._last_mouse_x, self._last_mouse_y)
        if self.popup and self.popup.winfo_exists():
            position = self.popup.winfo_x() + 50, self.popup.winfo_y() + 50
        else:
            position = self._last_mouse_x + 30, self._last_mouse_y + 50
        view = DictionaryResultWindow(
            self.root, result, target_lang, source_lang=source_lang, trial_info=trial_info,
            words=looked_up_words or (), annotate=self._ruby_enabled(), area=area, position=position)
        return view.window
