"""
Dictionary mode UI for word-by-word lookup in CrossTrans.

Provides interactive word selection with click, drag, and shift+drag support.
Words flow like a paragraph with automatic line wrapping.
Uses NLP tokenization for smart compound word recognition when available.
"""
import re
import tkinter as tk
from tkinter import LEFT, RIGHT, BOTH, X, TOP, BOTTOM
from typing import Callable, Optional, Sequence

from src.core import furigana
from src.core.furigana import RubySegment
from src.ui import components as ui
from src.ui.dictionary_pill import WordPill
from src.ui.ruby_text import NO_AUTOSTYLE, WHEEL_UNITS



class WordLabel:
    """Individual clickable word label that flows in text.

    A word with no reading stays a single `tk.Label`, exactly as before, so
    non-Japanese dictionary mode is untouched. When the word has furigana the
    chip becomes a two-row grid instead - readings on top, bases below, one
    column per segment - which keeps the bases on one line however many
    readings the word carries (取り消し needs two).
    """

    def __init__(self, parent_text: tk.Text, word: str, index: int,
                 on_click: Callable, on_drag_enter: Callable,
                 on_right_click: Optional[Callable] = None,
                 segments: Optional[Sequence[RubySegment]] = None,
                 on_wheel: Optional[Callable] = None):
        """Initialize word label.

        Args:
            parent_text: Parent Text widget to embed in
            word: The word text to display
            index: Index of this word in the sentence
            on_click: Callback when clicked (index, event)
            on_drag_enter: Callback when mouse drags over (index)
            on_right_click: Callback when right-clicked for drag-to-box (word, event)
            segments: Annotated segments for this word; falsy or reading-free
                segments produce the plain single-label chip.
            on_wheel: Wheel handler; an embedded widget swallows <MouseWheel>,
                which would leave a scroll dead zone over every word.
        """
        self.word = word
        self.index = index
        self.selected = False
        self.on_click = on_click
        self.on_drag_enter = on_drag_enter
        self.on_right_click = on_right_click
        self.parent_text = parent_text

        self.segments = tuple(segments or ())
        self.widget = WordPill(parent_text, word, self.segments)
        self.row = self.widget.row
        self._bases = self.row.bases if self.row else [self.widget.label]
        self._parts = self.widget.parts
        self.label = self._bases[0]
        for widget in self._parts:
            widget.bind('<Button-1>', self._handle_click)
            widget.bind('<Enter>', self._handle_enter, add='+')
            if on_wheel:
                widget.bind('<MouseWheel>', on_wheel)
        self.widget.bind('<space>', self._key_click)
        self.widget.bind('<Return>', self._key_click)

    @property
    def has_ruby(self) -> bool:
        """True when this chip displays at least one reading."""
        return bool(self.row and self.row.has_ruby)

    def _key_click(self, event):
        self._handle_click(event)
        return 'break'

    def _handle_click(self, event):
        self.widget.focus_set()
        self.on_click(self.index, event)

    def _handle_right_click(self, event):
        if self.on_right_click:
            self.on_right_click(self.word, event)

    def _handle_enter(self, event):
        if event.state & 0x100:
            self.on_drag_enter(self.index)

    def _handle_leave(self, event):
        self.widget._hover(False)

    def set_selected(self, selected: bool):
        self.selected = selected
        self.widget.set_selected(selected)

    def destroy(self):
        """Destroy the chip widget (its children go with it)."""
        self.widget.destroy()


class WordButtonFrame:
    """Container displaying text as clickable words with selection support.

    Words flow like a paragraph with automatic line wrapping.
    Uses NLP tokenization for smart compound word recognition when available.
    """

    def __init__(self, parent, text: str, on_selection_change: Callable[[str], None],
                 on_lookup: Optional[Callable[[str], None]] = None,
                 on_expand: Optional[Callable[[], None]] = None,
                 on_no_selection: Optional[Callable[[], None]] = None,
                 language: Optional[str] = None,
                 furigana_enabled: bool = True):
        """Initialize word button frame.

        Args:
            parent: Parent widget
            text: Text to split into word buttons
            on_selection_change: Callback when selection changes (selected_text)
            on_lookup: Callback when lookup is triggered (selected_text)
            on_expand: Callback when expand button is clicked
            on_no_selection: Callback when lookup clicked but no words selected
            language: Language for NLP tokenization (e.g., "Vietnamese", "Japanese")
            furigana_enabled: Show readings above kanji (Settings toggle)
        """
        self.parent = parent
        self.on_selection_change = on_selection_change
        self.on_lookup = on_lookup
        self.on_expand = on_expand
        self.on_no_selection = on_no_selection
        self.language = language
        self.furigana_enabled = furigana_enabled
        self.word_labels: list[WordLabel] = []
        self.selected_indices: set[int] = set()
        self.anchor_index: Optional[int] = None
        self.drag_start_index: Optional[int] = None

        # Drag-to-box state
        self._drop_target = None
        self._drag_ghost = None
        self._dragging_word = None
        self._drag_moved = False
        self._drag_selection_before = None
        self._drag_escape_owner = self._drag_escape_binding = None

        self.ctx = ui.get()
        px = self.ctx.px
        self.frame = ui.Frame(parent)
        self.action_frame = ui.Frame(self.frame)
        self.action_frame.pack(side=BOTTOM, fill=X, pady=(px(12), 0))
        self.word_card = ui.Card(self.frame, padding=8)
        self.word_card.pack(side=TOP, fill=BOTH, expand=True)
        self.text_widget = tk.Text(
            self.word_card, wrap=tk.WORD, height=3, width=1,
            font=self.ctx.font('body'), relief='flat', padx=px(4), pady=px(4),
            cursor='arrow', borderwidth=0, highlightthickness=0,
            spacing1=px(4), spacing3=px(4), **NO_AUTOSTYLE)
        self.text_widget.pack(fill=BOTH, expand=True)
        self.text_widget.bind('<MouseWheel>', self._on_wheel)
        self._retheme()
        self._create_word_labels(text)
        self.lookup_btn = ui.Button(self.action_frame, 'Look up', kind='accent', icon='search',
                                    command=self._trigger_lookup)
        self.lookup_btn.pack(side=LEFT)
        self.clear_btn = ui.Button(self.action_frame, 'Clear', kind='subtle',
                                   command=self.clear_selection)
        self.clear_btn.pack(side=LEFT, padx=px(8))
        self.exit_btn = ui.Button(self.action_frame, 'Done', command=self._trigger_exit)
        self.exit_btn.pack(side=RIGHT)
        self.expand_btn = ui.IconButton(self.action_frame, 'arrows-fullscreen',
                                        label='Expand or restore Dictionary', command=self._trigger_expand)
        self.expand_btn.pack(side=RIGHT, padx=px(8))
        self.selection_label = ui.Label(self.action_frame, '0 selected', kind='muted')
        self.selection_label.pack(side=LEFT)
        self._unsubscribe = self.ctx.manager.subscribe(self._retheme)
        self.frame.bind('<Destroy>', self._on_destroy, add='+')
        self._release_binding = self.frame.winfo_toplevel().bind(
            '<ButtonRelease-1>', self._release_selection, add='+')

        # Callbacks (set by parent)
        self._on_exit: Optional[Callable] = None

        self._lookup_animation_running = False

    def _retheme(self, manager=None):
        p = self.ctx.palette
        self.text_widget.configure(bg=p.surface, fg=p.text,
                                   selectbackground=p.selection, selectforeground=p.selection_text)
        for label in self.word_labels:
            label.widget._paint()

    def _on_destroy(self, event):
        if event.widget is self.frame:
            self._unsubscribe()
            self._cancel_drag()
            self.frame.winfo_toplevel().unbind('<ButtonRelease-1>', self._release_binding)

    def _create_word_labels(self, text: str):
        """Parse text into words and create embedded labels.

        Uses NLP tokenization if available for the language, otherwise
        falls back to simple whitespace-based tokenization.
        """
        # Enable text widget temporarily for editing
        self.text_widget.configure(state='normal')
        self.text_widget.delete('1.0', tk.END)

        # Split text by newlines first to preserve line structure
        lines = text.split('\n')

        for line_idx, line in enumerate(lines):
            # Insert newline between lines (not before first line)
            if line_idx > 0:
                self.text_widget.insert(tk.END, '\n')

            # Skip empty lines (but the \n above still creates the blank line)
            if not line.strip():
                continue

            # Tokenize each line separately using NLP
            words = self._tokenize_text(line)

            # Readings come from the whole line, not from each token: this
            # tokenizer splits 日本語 into 日本 + 語, and 日本 alone reads
            # にっぽん where the compound is にほん. annotate_tokens() leaves a
            # token blank rather than give it a reading cut from a compound.
            readings = self._token_readings(words, line)

            # Create a label for each word and embed in text widget
            # Punctuation/symbol-only tokens are shown as plain text (non-clickable)
            for i, word in enumerate(words):
                if not word:
                    continue

                # Check if this token is a real word (has at least one Unicode letter)
                is_word = bool(re.search(r'[^\W\d_]', word, re.UNICODE))

                if is_word:
                    label = WordLabel(
                        self.text_widget, word, len(self.word_labels),
                        on_click=self._on_word_click,
                        on_drag_enter=self._on_word_drag_enter,
                        on_right_click=self._start_drag_to_box,
                        segments=readings[i] if readings else None,
                        on_wheel=self._on_wheel
                    )
                    self.word_labels.append(label)
                    # align='baseline', not Tk's default 'center': a chip with a
                    # reading is taller, and centring it lifts the plain chips
                    # 7px off its baseline (measured).
                    self.text_widget.window_create(tk.END, window=label.widget,
                                                   align='baseline')
                else:
                    # Punctuation/symbol: insert as plain text (visible but not clickable)
                    self.text_widget.insert(tk.END, word)

                # Add space after each token (except last in line)
                if i < len(words) - 1:
                    self.text_widget.insert(tk.END, " ")

        # Disable text widget again
        self.text_widget.configure(state='disabled')

    def _token_readings(self, words: list, line: str):
        """Per-token readings for one line, or None when furigana is off.

        Returns a list parallel to `words`, so the caller can index it directly.
        """
        if not self.furigana_enabled or not words:
            return None
        try:
            return list(furigana.annotate_tokens(words, line, self.language))
        except Exception as e:      # never let a reading break the word area
            import logging
            logging.error(f"[DICT_FURIGANA] annotate_tokens failed: {e}")
            return None

    def _on_wheel(self, event):
        """Scroll the word area, including when the pointer is over a chip.

        Every chip is an embedded widget, and an embedded widget swallows
        <MouseWheel> - without this the wheel is dead over the words themselves,
        which is most of the area.
        """
        try:
            self.text_widget.yview_scroll(
                int(-WHEEL_UNITS * (event.delta / 120)), 'units')
        except tk.TclError:
            pass
        return 'break'

    def _tokenize_text(self, text: str) -> list:
        """Use the same word-selection contract as the alternative renderer."""
        from src.core.dictionary_tokens import tokenize
        return tokenize(text, self.language)

    def _tokenize_simple(self, text: str) -> list:
        """Simple tokenizer that keeps hyphenated words together."""
        # Split by whitespace, preserving hyphenated compounds
        return re.findall(r'\S+', text)

    def _merge_hyphenated_tokens(self, tokens: list) -> list:
        """Merge tokens that were split by hyphens.

        UDPipe often splits "auto-update" into ["auto", "-", "update"].
        This merges them back into ["auto-update"].

        Also handles chained hyphens like "state-of-the-art" ->
        ["state", "-", "of", "-", "the", "-", "art"] -> "state-of-the-art"

        Args:
            tokens: List of tokens from NLP tokenizer

        Returns:
            List with hyphenated words merged
        """
        if len(tokens) < 3:
            return tokens

        result = []
        i = 0

        while i < len(tokens):
            # Check for pattern: word + hyphen + word (+ hyphen + word)*
            if (i + 2 < len(tokens) and
                tokens[i + 1] == '-' and
                tokens[i].isalnum() and
                tokens[i + 2].isalnum()):
                # Start building merged token
                merged = tokens[i]
                i += 1

                # Keep merging while we see hyphen + word pattern
                while (i + 1 < len(tokens) and
                       tokens[i] == '-' and
                       tokens[i + 1].isalnum()):
                    merged += '-' + tokens[i + 1]
                    i += 2

                result.append(merged)
            else:
                result.append(tokens[i])
                i += 1

        return result

    def _on_word_click(self, index: int, event):
        """Handle word label click."""
        before = set(self.selected_indices), self.anchor_index
        # Check for Shift key (range selection)
        if event.state & 0x1:  # Shift pressed
            if self.anchor_index is not None:
                self._select_range(self.anchor_index, index)
            else:
                self._toggle_word(index)
                self.anchor_index = index
        else:
            # Normal click - toggle selection
            self._toggle_word(index)
            self.anchor_index = index

        # Start potential drag
        self.drag_start_index = index
        self._notify_selection_change()
        if not event.state & 0x1 and getattr(event, 'num', None) == 1:
            self._start_drag_to_box(self.word_labels[index].word, event)
            if self._dragging_word is not None:
                self._drag_selection_before = before

    def _on_word_drag_enter(self, index: int):
        """Handle mouse drag entering a word."""
        if self.drag_start_index is not None and self._dragging_word is None:
            self._select_range(self.drag_start_index, index)
            self._notify_selection_change()

    def _release_selection(self, event=None):
        self.drag_start_index = None

    def _toggle_word(self, index: int):
        """Toggle selection state of a single word."""
        if index in self.selected_indices:
            self.selected_indices.remove(index)
        else:
            self.selected_indices.add(index)
        self.word_labels[index].set_selected(index in self.selected_indices)

    def _select_range(self, start: int, end: int):
        """Select all words in a range (inclusive)."""
        # Clear existing selection
        for idx in list(self.selected_indices):
            self.word_labels[idx].set_selected(False)
        self.selected_indices.clear()

        # Select range
        min_idx, max_idx = min(start, end), max(start, end)
        for i in range(min_idx, max_idx + 1):
            self.selected_indices.add(i)
            self.word_labels[i].set_selected(True)

    def clear_selection(self):
        """Clear all selected words."""
        for idx in list(self.selected_indices):
            self.word_labels[idx].set_selected(False)
        self.selected_indices.clear()
        self.anchor_index = None
        self.drag_start_index = None
        self._notify_selection_change()

    def get_selected_text(self) -> str:
        """Get selected words as concatenated text."""
        if not self.selected_indices:
            return ""
        sorted_indices = sorted(self.selected_indices)
        return " ".join(self.word_labels[i].word for i in sorted_indices)

    def get_selected_words(self) -> list[str]:
        """Get selected words as a list of individual words."""
        if not self.selected_indices:
            return []
        sorted_indices = sorted(self.selected_indices)
        return [self.word_labels[i].word for i in sorted_indices]

    def _notify_selection_change(self):
        """Notify parent of selection change."""
        self.selection_label.configure(text=f'{len(self.selected_indices)} selected')
        if self.on_selection_change:
            self.on_selection_change(self.get_selected_text())

    def _trigger_lookup(self):
        """Trigger dictionary lookup for selected words with animation.

        Each selected word is looked up separately (not as a combined phrase).
        If no words selected, calls on_no_selection callback.
        """
        if self._lookup_animation_running:
            return
        words = self.get_selected_words()
        if words and self.on_lookup:
            self.start_lookup_animation()
            # Pass list of words for individual lookup
            self.on_lookup(words)
        elif not words and self.on_no_selection:
            # No words selected - notify parent to show warning
            self.on_no_selection()

    def start_lookup_animation(self):
        self._lookup_animation_running = True
        self.lookup_btn.configure(text='Looking up...', state='disabled')

    def stop_lookup_animation(self):
        self._lookup_animation_running = False
        try:
            self.lookup_btn.configure(text='Look up', state='normal')
        except tk.TclError:
            pass

    def _trigger_expand(self):
        """Trigger expand callback."""
        if self.on_expand:
            self.on_expand()

    def _trigger_exit(self):
        """Trigger exit callback."""
        if self._on_exit:
            self._on_exit()

    def set_lookup_callback(self, callback: Callable[[str], None]):
        """Set the lookup button callback."""
        self.on_lookup = callback

    def set_expand_callback(self, callback: Callable):
        """Set the expand button callback."""
        self.on_expand = callback

    def set_exit_callback(self, callback: Callable):
        """Set the exit button callback."""
        self._on_exit = callback

    def set_drop_target(self, custom_boxes_frame):
        """Set the phrase composer as the left-drag destination."""
        self._drop_target = custom_boxes_frame
        custom_boxes_frame.word_selector = self
        custom_boxes_frame._selector_binding = self.frame.bind('<Configure>', custom_boxes_frame._fit_boxes, add='+')

    def insert_custom_widget(self, widget):
        """Insert a widget between text area and action buttons.

        Repacks text_widget so the custom widget sits below it but above action_frame.
        """
        self.word_card.pack_forget()
        widget.pack(in_=self.frame, side=BOTTOM, fill=X, pady=(self.ctx.px(12), 0))
        self.word_card.pack(side=TOP, fill=BOTH, expand=True)

    def _start_drag_to_box(self, word: str, event):
        """Arm a left drag; a click remains an ordinary selection toggle."""
        if not self._drop_target:
            return
        self._cancel_drag()
        self._dragging_word = word
        self._drag_moved = False
        self._drag_selection_before = None
        self._drag_origin = event.x_root, event.y_root
        # Scoped to this word window: another window's drag bindings are untouched.
        top = self.frame.winfo_toplevel()
        self._drag_bindings = [
            ('<B1-Motion>', top.bind('<B1-Motion>', self._drag_motion, add='+')),
            ('<ButtonRelease-1>', top.bind('<ButtonRelease-1>', self._end_drag, add='+'))]
        owner = getattr(event, 'widget', None)
        while owner is not None and not isinstance(owner, WordPill):
            owner = getattr(owner, 'master', None)
        self._drag_escape_owner = owner or next((label.widget for label in self.word_labels if label.word == word), self.frame)
        self._drag_escape_binding = self._drag_escape_owner.bind('<Escape>', self._escape_drag, add='+')

    def _restore_drag_selection(self):
        if self._drag_selection_before is None:
            return
        indices, anchor = self._drag_selection_before
        self.selected_indices, self.anchor_index = indices, anchor
        for index, label in enumerate(self.word_labels):
            label.set_selected(index in indices)
        self._drag_selection_before = None
        self._notify_selection_change()

    def _escape_drag(self, event=None):
        self._restore_drag_selection()
        self._cancel_drag()
        self._dragging_word = None
        self.drag_start_index = None
        self._drop_target.clear_all_highlights()
        self._drop_target._hide_drop_line()
        return 'break'

    def _segments_of(self, word: str):
        """The annotated segments the chip for `word` is showing, if any."""
        for label in self.word_labels:
            if label.word == word and label.has_ruby:
                return label.segments
        return None


    def _drag_motion(self, event):
        """Move ghost label, highlight target box, and show drop line."""
        if not self._drag_moved:
            x, y = self._drag_origin
            if max(abs(event.x_root - x), abs(event.y_root - y)) < self.ctx.px(6):
                return
            self._restore_drag_selection()
            self._drag_ghost = tk.Toplevel(self.frame)
            self._drag_ghost.overrideredirect(True)
            self._drag_ghost.attributes('-topmost', True)
            self._drag_ghost.configure(bg=self.ctx.palette.surface)
            WordPill(self._drag_ghost, self._dragging_word, self._segments_of(self._dragging_word) or (),
                     selected=True, takefocus=False).pack()
        self._drag_moved = True
        if self._drag_ghost:
            self._drag_ghost.geometry(f"+{event.x_root + 12}+{event.y_root + 8}")

        if self._drop_target:
            self._drop_target.clear_all_highlights()
            box = self._drop_target.get_box_at_position(event.x_root, event.y_root)
            if box:
                box.set_highlight(True)
                self._drop_target._show_drop_line(box, event.x_root, event.y_root)
            else:
                self._drop_target._hide_drop_line()

    def _end_drag(self, event):
        """End drag - drop word into target box at cursor position or cancel."""
        self._cancel_drag()

        if self._drop_target and self._dragging_word and self._drag_moved:
            self._drop_target.clear_all_highlights()
            self._drop_target._hide_drop_line()
            box = self._drop_target.get_box_at_position(event.x_root, event.y_root)
            if box:
                target_index = box.get_insert_index_at_coords(event.x_root, event.y_root)
                box.insert_tag_at_position(self._dragging_word, target_index)
        elif self._drop_target:
            self._drop_target.clear_all_highlights()
            self._drop_target._hide_drop_line()

        self._dragging_word = None
        self._drag_moved = False
        self._drag_selection_before = None

    def _cancel_drag(self):
        top = self.frame.winfo_toplevel()
        for sequence, binding in getattr(self, '_drag_bindings', []):
            try:
                top.unbind(sequence, binding)
            except tk.TclError:
                pass
        self._drag_bindings = []
        if self._drag_escape_owner is not None and self._drag_escape_binding:
            try:
                self._drag_escape_owner.unbind('<Escape>', self._drag_escape_binding)
            except tk.TclError:
                pass
        self._drag_escape_owner = self._drag_escape_binding = None
        if self._drag_ghost:
            try:
                self._drag_ghost.destroy()
            except tk.TclError:
                pass
            self._drag_ghost = None
        if self._drop_target:
            try:
                self._drop_target.clear_all_highlights()
                self._drop_target._hide_drop_line()
            except tk.TclError:
                pass

    def pack(self, **kwargs):
        """Pack the main frame."""
        self.frame.pack(**kwargs)

    def pack_forget(self):
        """Remove the main frame from layout."""
        self.frame.pack_forget()

    def destroy(self):
        """Destroy all widgets."""
        for label in self.word_labels:
            label.destroy()
        self.word_labels.clear()
        self.frame.destroy()
