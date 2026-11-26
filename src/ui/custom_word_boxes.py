"""
Custom Word Boxes for Dictionary mode in CrossTrans.

Allows users to manually compose lookup phrases by typing or
dragging words from the tokenized word selection area.
Left-drag tags within/between boxes to reorder them.
"""
import math
import tkinter as tk
from tkinter import LEFT, RIGHT, X, END
from typing import Callable, Optional, Sequence

from src.core import furigana
from src.core.furigana import RubySegment
from src.ui import components as ui
from src.ui.components import render
from src.ui.dictionary_pill import WordPill
from src.ui.ruby_text import tk_layout_model
from src.ui.theme.compat import NO_AUTOSTYLE
from src.ui.theme import tokens

MAX_BOXES = 20


class PhraseAction(tk.Canvas):
    """A square, theme-aware phrase action with native focus and keyboard activation."""

    def __init__(self, parent, symbol, label, command):
        self.ctx, self.symbol, self.command = ui.get(), symbol, command
        self.hovered = self.pressed = self.focused = False
        side = self.ctx.px(42)
        super().__init__(parent, width=side, height=side, bd=0, highlightthickness=0,
                         takefocus=True, cursor='hand2', **NO_AUTOSTYLE)
        self._plate = self.create_image(0, 0, anchor='nw')
        self._horizontal = self.create_line(0, 0, 0, 0)
        self._vertical = self.create_line(0, 0, 0, 0) if symbol == '+' else None
        self.tooltip = ui.Tooltip(self, label)
        self.bind('<Enter>', lambda event: self._state('hovered', True))
        self.bind('<Leave>', lambda event: self._state('hovered', False))
        self.bind('<FocusIn>', lambda event: self._state('focused', True))
        self.bind('<FocusOut>', lambda event: self._state('focused', False))
        self.bind('<ButtonPress-1>', self._press)
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<Return>', lambda event: self.invoke() or 'break')
        self.bind('<space>', lambda event: self.invoke() or 'break')
        self._unsubscribe = self.ctx.manager.subscribe(self._paint)
        self.bind('<Destroy>', lambda event: self._unsubscribe() if event.widget is self else None, add='+')
        self._paint()

    def invoke(self):
        self.command()

    def _state(self, name, value):
        setattr(self, name, value)
        self._paint()

    def _press(self, event):
        self.focus_set()
        self._state('pressed', True)

    def _release(self, event):
        pressed = self.pressed
        self._state('pressed', False)
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def _paint(self, manager=None):
        p, px = self.ctx.palette, self.ctx.px
        side, center, reach = px(42), px(21), px(9)
        background = p.surface
        self.configure(bg=background, width=side, height=side)
        fill = p.accent if self.pressed else p.soft
        edge = p.focus if self.focused else p.accent if self.hovered else p.border
        self._photo = render.to_photo(self.ctx.root, render.rounded_rect(
            side, side, px(6), fill, edge, px(2 if self.focused else 1), background))
        self.itemconfigure(self._plate, image=self._photo)
        color = p.on_accent if self.pressed else p.soft_text
        self.coords(self._horizontal, center - reach, center, center + reach, center)
        self.itemconfigure(self._horizontal, fill=color, width=px(2))
        if self._vertical is not None:
            self.coords(self._vertical, center, center - reach, center, center + reach)
            self.itemconfigure(self._vertical, fill=color, width=px(2))


def _ruby_rows() -> int:
    ctx = ui.get()
    model = tk_layout_model(ctx.font('body'), ctx.font('caption', 'japanese'), line_spacing=0)
    return max(1, math.ceil((model.row_ruby + ctx.px(10)) / model.row_plain))


class WordTag:
    """A removable phrase pill; its base text is always the lookup content."""

    def __init__(self, parent_text: tk.Text, word: str, on_remove: Callable,
                 on_drag_start: Optional[Callable] = None,
                 segments: Optional[Sequence[RubySegment]] = None):
        self.word, self.parent_text = word, parent_text
        self._on_remove, self._on_drag_start = on_remove, on_drag_start
        self.segments = tuple(segments or ())
        self.frame = WordPill(parent_text, word, self.segments, selected=True,
                              removable=True, on_remove=self._remove)
        self.row, self.label = self.frame.row, self.frame.label
        self.close_btn, self._parts = self.frame.close_btn, self.frame.parts
        for widget in self._parts:
            if widget is not self.close_btn:
                widget.bind('<Button-1>', self._handle_drag_start)

    @property
    def has_ruby(self) -> bool:
        return bool(self.row and self.row.has_ruby)

    def set_dimmed(self, dimmed: bool):
        self.frame.set_dimmed(dimmed)

    def _handle_drag_start(self, event):
        self.frame.focus_set()
        if self._on_drag_start:
            self._on_drag_start(self, event)

    def _remove(self):
        self._on_remove(self)

    def destroy(self):
        self.frame.destroy()


class CustomWordBox:
    """A single input row with text widget for tags/text and +/- buttons."""

    def __init__(self, parent, index: int, is_first: bool,
                 on_add: Callable, on_remove: Callable,
                 on_tag_drag_start: Optional[Callable] = None,
                 language: Optional[str] = None,
                 furigana_enabled: bool = True):
        self.index = index
        self._tags: list[WordTag] = []
        self._on_tag_drag_start = on_tag_drag_start
        self.language = language
        self.furigana_enabled = furigana_enabled

        self.ctx = ui.get()
        px = self.ctx.px
        self.frame = ui.Frame(parent, on='surface')
        btn_frame = ui.Frame(self.frame)
        btn_frame.pack(side=RIGHT, anchor='n', padx=(px(8), 0))
        self.field = tk.Canvas(self.frame, width=1, height=px(36), bd=0,
                               highlightthickness=0, **NO_AUTOSTYLE)
        self.field.pack(side=LEFT, fill=X, expand=True)
        self.text_widget = tk.Text(
            self.field, height=1, width=1, wrap=tk.WORD, font=self.ctx.font('body'),
            relief='flat', bd=0, highlightthickness=0, padx=0, pady=0, **NO_AUTOSTYLE)
        self._field_plate = self.field.create_image(0, 0, anchor='nw')
        self._field_text = self.field.create_window(px(8), px(8), window=self.text_widget, anchor='nw')
        self.field.bind('<Configure>', self._paint_field)
        self.text_widget.bind('<FocusIn>', self._paint_field)
        self.text_widget.bind('<FocusOut>', self._paint_field)
        self._highlighted = False
        self._retheme()
        self._unsubscribe = self.ctx.manager.subscribe(self._retheme)
        self.frame.bind('<Destroy>', self._on_destroy, add='+')
        self.text_widget.bind('<Return>', self._on_enter)
        self.text_widget.bind('<BackSpace>', self._on_backspace)
        self.text_widget.bind('<Button-1>', self._on_click, add='+')
        self.text_widget.bind('<KeyRelease>', lambda e: self._sync_height())
        self.text_widget.bind('<Configure>', lambda e: self._sync_height())
        self.add_btn = PhraseAction(btn_frame, '+', 'Add a phrase box', lambda: on_add(self.index))
        self.add_btn.pack(side=LEFT)
        self.remove_btn = None
        if not is_first:
            self.remove_btn = PhraseAction(btn_frame, '-', 'Remove this phrase box', lambda: on_remove(self.index))
            self.remove_btn.pack(side=LEFT)

    def _retheme(self, manager=None):
        p = self.ctx.palette
        self.text_widget.configure(bg=p.alt, fg=p.text, insertbackground=p.text,
                                   selectbackground=p.selection, selectforeground=p.selection_text)
        self._paint_field()
        for tag in self._tags:
            tag.frame._paint()

    def _on_destroy(self, event):
        if event.widget is self.frame:
            self._unsubscribe()

    def _paint_field(self, event=None):
        p, px = self.ctx.palette, self.ctx.px
        width = max(px(40), self.field.winfo_width())
        height = max(px(36), self.text_widget.winfo_reqheight() + px(16))
        self.field.configure(bg=p.surface, height=height)
        self.field.itemconfigure(self._field_text, width=max(1, width - px(16)))
        focused = self.text_widget.focus_get() is self.text_widget
        self._field_photo = render.to_photo(self.ctx.root, render.rounded_rect(
            width, height, px(tokens.RADIUS['control']), p.alt,
            p.focus if focused or self._highlighted else p.strong,
            px(2 if focused or self._highlighted else 1), p.surface))
        self.field.itemconfigure(self._field_plate, image=self._field_photo)

    def _on_click(self, event):
        """Ensure text widget gets focus for text selection."""
        self.text_widget.focus_set()

    def _on_enter(self, event):
        """Convert typed text before cursor into a tag."""
        # Remove only typed ranges. Existing embedded tags before the cursor survive.
        pieces = self.text_widget.dump('1.0', 'insert', text=True)
        value = ''.join(item[1] for item in pieces).strip()
        if value:
            for _kind, value_part, index in reversed(pieces):
                self.text_widget.delete(index, f'{index}+{len(value_part)}c')
            self.add_word_tag(value)
        return 'break'

    def _on_backspace(self, event):
        previous = self.text_widget.index('insert-1c')
        windows = self.text_widget.dump(previous, 'insert', window=True)
        if windows:
            name = windows[0][1]
            tag = next((tag for tag in self._tags if str(tag.frame) == name), None)
            if tag:
                self._remove_tag(tag)
                return 'break'

    def _segments_for(self, word: str):
        """Readings for a tag, or None.

        A tag is one lookup phrase, so it is annotated on its own - unlike the
        word chips, which take their readings from the sentence they came from.
        There is no larger context here: the phrase is whatever the user typed
        or dropped.
        """
        if not self.furigana_enabled or not word:
            return None
        try:
            return furigana.annotate(word, self.language)
        except Exception:
            return None

    def add_word_tag(self, word: str):
        """Insert an orange word tag at the current cursor position."""
        tag = WordTag(self.text_widget, word, self._remove_tag,
                      on_drag_start=self._on_tag_drag_start,
                      segments=self._segments_for(word))
        self._tags.append(tag)
        # align='baseline' so typed text keeps the tag's baseline; Tk's default
        # 'center' would lift it against a tag carrying a reading.
        self.text_widget.window_create('insert', window=tag.frame,
                                       align='baseline')
        self._rebuild_tags_order()
        self._sync_height()

    def insert_tag_at_position(self, word: str, tk_index: str):
        """Insert a tag at a specific text widget index."""
        tag = WordTag(self.text_widget, word, self._remove_tag,
                      on_drag_start=self._on_tag_drag_start,
                      segments=self._segments_for(word))
        self._tags.append(tag)
        self.text_widget.window_create(tk_index, window=tag.frame,
                                       align='baseline')
        self._rebuild_tags_order()
        self._sync_height()

    def _sync_height(self):
        """Keep the box tall enough for the tags it holds.

        `height` counts rows of the *base* font, but a tag carrying a reading is
        taller than one such row, so a height of 1 would clip it. The row count
        is derived from the real font metrics rather than guessed.
        """
        lines = self.text_widget.count('1.0', 'end', 'displaylines')
        wrapped = max(1, int(lines[0])) if lines else 1
        tag_rows = _ruby_rows() if any(tag.has_ruby for tag in self._tags) else 2 if self._tags else 1
        rows = min(5, max(tag_rows, wrapped))
        try:
            if int(self.text_widget.cget('height')) != rows:
                self.text_widget.configure(height=rows)
                self._paint_field()
        except (tk.TclError, ValueError):
            pass

    def remove_tag_for_drag(self, tag: WordTag):
        """Remove a tag for drag-move (destroys widget, caller recreates at destination)."""
        if tag in self._tags:
            self._tags.remove(tag)
            try:
                idx = self.text_widget.index(str(tag.frame))
                self.text_widget.delete(idx)
            except tk.TclError:
                pass
            tag.destroy()
            self._sync_height()

    def _rebuild_tags_order(self):
        """Rebuild _tags list to match visual order in the text widget."""
        raw = self.text_widget.dump('1.0', 'end', window=True)
        tag_by_frame = {str(t.frame): t for t in self._tags}
        ordered = []
        for item_type, value, _index in raw:
            if item_type == 'window' and value in tag_by_frame:
                ordered.append(tag_by_frame[value])
        self._tags = ordered

    def get_insert_index_at_coords(self, x_root: int, y_root: int) -> str:
        """Convert screen coordinates to a text widget index for insertion."""
        local_x = x_root - self.text_widget.winfo_rootx()
        local_y = y_root - self.text_widget.winfo_rooty()
        return self.text_widget.index(f"@{local_x},{local_y}")

    def _remove_tag(self, tag: WordTag):
        """Remove a tag from this box."""
        if tag in self._tags:
            self.remove_tag_for_drag(tag)

    @staticmethod
    def _strip_surrogates(text: str) -> str:
        """Remove Unicode surrogate characters (U+D800-U+DFFF).

        Tkinter on Windows may emit surrogates as placeholders for
        embedded windows in Text widgets.
        """
        return ''.join(c for c in text if not ('\ud800' <= c <= '\udfff'))

    @staticmethod
    def _is_cjk(c: str) -> bool:
        """Check if a character is CJK (no word-space needed between them)."""
        cp = ord(c)
        return (0x2E80 <= cp <= 0x9FFF or 0x3040 <= cp <= 0x30FF or
                0xAC00 <= cp <= 0xD7AF or 0xF900 <= cp <= 0xFAFF)

    @classmethod
    def _smart_join(cls, parts: list) -> str:
        """Join parts with spaces only where needed.

        CJK tags join without space: [無礼][講] -> "無礼講"
        Latin tags join with space:  [custom][and] -> "custom and"
        Typed text preserves its own whitespace as-is.
        """
        if not parts:
            return ""
        result = parts[0]
        for i in range(1, len(parts)):
            right = parts[i]
            if not result or not right:
                result += right
                continue
            last_char = result[-1]
            first_char = right[0]
            if last_char.isspace() or first_char.isspace():
                result += right
            elif cls._is_cjk(last_char) and cls._is_cjk(first_char):
                result += right
            else:
                result += ' ' + right
        return result

    def get_content(self) -> str:
        """Get all content (tags + typed text) as a single phrase string.

        Smart joining: no space between CJK tags, space between Latin tags,
        typed text preserves its own whitespace.
        """
        parts = []
        raw_content = self._strip_surrogates(
            self.text_widget.get('1.0', END).strip())
        if not raw_content and not self._tags:
            return ""

        raw_text = self.text_widget.dump('1.0', END, text=True, window=True)
        for item_type, value, _index in raw_text:
            if item_type == 'text':
                text = self._strip_surrogates(value.rstrip('\n'))
                if text:
                    parts.append(text)
            elif item_type == 'window':
                for tag in self._tags:
                    try:
                        if str(tag.frame) == value:
                            parts.append(tag.word)
                            break
                    except tk.TclError:
                        pass

        return self._smart_join(parts).strip()

    def set_highlight(self, active: bool):
        """Highlight border when a drag hovers over this box."""
        self._highlighted = active
        self._retheme()

    def get_drop_screen_coords(self, x_root: int, y_root: int):
        """Get screen coordinates for the insertion point indicator.

        Returns (screen_x, screen_y, height) or None if not available.
        """
        idx = self.get_insert_index_at_coords(x_root, y_root)
        self.text_widget.mark_set('insert', idx)
        bbox = self.text_widget.bbox(idx)
        if bbox:
            x, _y, _w, _h = bbox
            sx = self.text_widget.winfo_rootx() + x
            sy = self.text_widget.winfo_rooty()
            h = self.text_widget.winfo_height()
            return sx, sy, h
        return None

    def hide_add_button(self):
        self.add_btn.pack_forget()

    def show_add_button(self):
        self.add_btn.pack(side=LEFT)

    def pack(self, **kwargs):
        self.frame.pack(**kwargs)

    def pack_forget(self):
        self.frame.pack_forget()

    def destroy(self):
        for tag in self._tags:
            tag.destroy()
        self._tags.clear()
        self.frame.destroy()


class CustomWordBoxesFrame:
    """Container managing multiple CustomWordBox instances.

    Supports left-drag to reorder tags within and between boxes.
    """

    def __init__(self, parent, language: Optional[str] = None,
                 furigana_enabled: bool = True, compact: bool = False, compact_guidance: bool = False):
        self.parent = parent
        self.language = language
        self.furigana_enabled = furigana_enabled
        self._boxes: list[CustomWordBox] = []

        self.ctx = ui.get()
        px = self.ctx.px
        self._padding = 8 if compact else 12
        self.frame = ui.Card(parent, padding=self._padding)
        self.title = title = ui.Label(self.frame, 'Build a phrase', kind='strong')
        title.pack(anchor='w')
        self.hint = ui.Label(self.frame, 'Drag words with the left mouse button. Shift-drag selects a range. Enter makes a tag.',
                             kind='muted', wraplength=px(520), justify='left')
        self._compact_guidance = compact_guidance
        if not compact_guidance:
            self.hint.pack(anchor='w', pady=(px(4), px(8)))
        ui.Tooltip(title, 'Drag words with the left mouse button. Shift-drag selects a range. Enter makes a tag.')
        self.box_scroll = ui.ScrollArea(self.frame, on='surface')
        self.box_scroll.pack(fill=X)
        self._boxes_container = self.box_scroll.content
        self._boxes_container.bind('<Configure>', self._fit_boxes, add='+')
        self.frame.bind('<Configure>', self._fit_hint, add='+')
        self.frame.bind('<Destroy>', self._on_destroy, add='+')
        self._drag_source_tag: Optional[WordTag] = None
        self._drag_source_box: Optional[CustomWordBox] = None
        self._drag_ghost: Optional[tk.Toplevel] = None
        self._drop_line: Optional[tk.Toplevel] = None
        self._drag_moved = False

        self._add_box_at(0, is_first=True)

    def _add_box_at(self, after_index: int, is_first: bool = False):
        """Add a new box after the given index."""
        if len(self._boxes) >= MAX_BOXES:
            return

        new_index = after_index + 1 if not is_first else 0
        box = CustomWordBox(
            self._boxes_container, new_index, is_first,
            on_add=self._on_add_clicked,
            on_remove=self._on_remove_clicked,
            on_tag_drag_start=self._on_tag_drag_start,
            language=self.language,
            furigana_enabled=self.furigana_enabled
        )

        if is_first:
            self._boxes.append(box)
        else:
            self._boxes.insert(new_index, box)

        self._repack_boxes()

    def _on_add_clicked(self, box_index: int):
        """Handle + button click."""
        actual_index = self._find_box_actual_index(box_index)
        if actual_index is not None:
            self._add_box_at(actual_index)

    def _on_remove_clicked(self, box_index: int):
        """Handle - button click."""
        actual_index = self._find_box_actual_index(box_index)
        if actual_index is not None and len(self._boxes) > 1:
            box = self._boxes.pop(actual_index)
            box.destroy()
            self._repack_boxes()

    def _find_box_actual_index(self, original_index: int) -> Optional[int]:
        """Find actual list index from box's stored index."""
        for i, box in enumerate(self._boxes):
            if box.index == original_index:
                return i
        return None

    def _repack_boxes(self):
        """Repack all boxes with updated indices."""
        for box in self._boxes:
            box.pack_forget()

        for i, box in enumerate(self._boxes):
            box.index = i
            box.pack(fill=X, pady=(0, self.ctx.px(8)))
            if len(self._boxes) >= MAX_BOXES:
                box.hide_add_button()
            else:
                box.show_add_button()
        self.box_scroll.adopt()

    # --- Tag drag-reorder (left-drag within/between boxes) ---

    def _dim_tag(self, tag: 'WordTag'):
        """Dim a tag to indicate it is being dragged."""
        tag.set_dimmed(True)

    def _restore_tag(self, tag: 'WordTag'):
        """Restore a tag to normal appearance."""
        tag.set_dimmed(False)

    def _on_tag_drag_start(self, tag: 'WordTag', event):
        """Arm tag movement without changing content on a click."""
        source_box = None
        for box in self._boxes:
            if tag in box._tags:
                source_box = box
                break
        if not source_box:
            return

        self._drag_source_tag = tag
        self._drag_source_box = source_box
        self._drag_moved = False

        self._drag_origin = event.x_root, event.y_root
        top = self.frame.winfo_toplevel()
        self._drag_bindings = [
            ('<B1-Motion>', top.bind('<B1-Motion>', self._on_tag_drag_motion, add='+')),
            ('<ButtonRelease-1>', top.bind('<ButtonRelease-1>', self._on_tag_drag_end, add='+'))]
        self._drag_escape_owner = tag.frame
        self._drag_escape_binding = tag.frame.bind('<Escape>', self._cancel_tag_drag, add='+')

    def _cancel_tag_drag(self, event=None):
        self._drag_moved = False
        self._on_tag_drag_end(event)
        return 'break'

    def _show_drop_line(self, box: 'CustomWordBox', x_root: int, y_root: int):
        """Show a bright vertical line at the insertion point (Toplevel window)."""
        coords = box.get_drop_screen_coords(x_root, y_root)
        if not coords:
            self._hide_drop_line()
            return
        sx, sy, h = coords
        if not self._drop_line:
            self._drop_line = tk.Toplevel(self.frame)
            self._drop_line.overrideredirect(True)
            self._drop_line.attributes('-topmost', True)
            self._drop_line.configure(bg=self.ctx.palette.accent)
        # max(): an unmapped box reports height 1, and "3x-3+..." is a TclError
        # raised inside a live motion handler, which would break the drag.
        self._drop_line.geometry(f"{self.ctx.px(2)}x{max(8, h - 4)}+{sx - 1}+{sy + 2}")
        self._drop_line.deiconify()
        self._drop_line.lift()

    def _hide_drop_line(self):
        """Hide the drop indicator line."""
        if self._drop_line:
            try:
                self._drop_line.withdraw()
            except tk.TclError:
                self._drop_line = None

    def _on_tag_drag_motion(self, event):
        """Move ghost, highlight target box, and show drop line."""
        if not self._drag_moved:
            x, y = self._drag_origin
            if max(abs(event.x_root - x), abs(event.y_root - y)) < self.ctx.px(6):
                return
            tag = self._drag_source_tag
            self._dim_tag(tag)
            self._drag_ghost = tk.Toplevel(self.frame)
            self._drag_ghost.overrideredirect(True)
            self._drag_ghost.attributes('-topmost', True)
            self._drag_ghost.configure(bg=self.ctx.palette.surface)
            WordPill(self._drag_ghost, tag.word, tag.segments, selected=True, takefocus=False).pack()
        self._drag_moved = True
        if self._drag_ghost:
            self._drag_ghost.geometry(f"+{event.x_root + 12}+{event.y_root + 8}")

        self.clear_all_highlights()

        box = self.get_box_at_position(event.x_root, event.y_root)
        if box:
            box.set_highlight(True)
            self._show_drop_line(box, event.x_root, event.y_root)
        else:
            self._hide_drop_line()

    def _on_tag_drag_end(self, event):
        """Complete the drag -- move tag to target position."""
        top = self.frame.winfo_toplevel()
        for sequence, binding in getattr(self, '_drag_bindings', []):
            top.unbind(sequence, binding)
        self._drag_bindings = []
        self._unbind_drag_escape()
        if self._drag_ghost:
            try:
                self._drag_ghost.destroy()
            except tk.TclError:
                pass
            self._drag_ghost = None
        self._hide_drop_line()
        self.clear_all_highlights()

        if not self._drag_moved or not self._drag_source_tag or not self._drag_source_box:
            if self._drag_source_tag:
                self._restore_tag(self._drag_source_tag)
            self._drag_source_tag = None
            self._drag_source_box = None
            return

        target_box = self.get_box_at_position(event.x_root, event.y_root)
        if not target_box:
            if self._drag_source_tag:
                self._restore_tag(self._drag_source_tag)
            self._drag_source_tag = None
            self._drag_source_box = None
            return

        word = self._drag_source_tag.word
        source_box = self._drag_source_box
        source_tag = self._drag_source_tag

        target_index = target_box.get_insert_index_at_coords(event.x_root, event.y_root)

        if target_box is source_box:
            try:
                source_idx = source_box.text_widget.index(str(source_tag.frame))
                if source_box.text_widget.compare(target_index, '>', source_idx):
                    target_index = source_box.text_widget.index(f"{target_index} - 1c")
            except tk.TclError:
                pass

        source_box.remove_tag_for_drag(source_tag)
        target_box.insert_tag_at_position(word, target_index)

        self._drag_source_tag = None
        self._drag_source_box = None

    def _unbind_drag_escape(self):
        owner, binding = getattr(self, '_drag_escape_owner', None), getattr(self, '_drag_escape_binding', None)
        if owner is not None and binding:
            try:
                owner.unbind('<Escape>', binding)
            except tk.TclError:
                pass
        self._drag_escape_owner = self._drag_escape_binding = None

    def set_compact_guidance(self, compact):
        if compact == self._compact_guidance:
            return
        self._compact_guidance = compact
        if compact:
            self.hint.pack_forget()
        else:
            self.hint.pack(anchor='w', before=self.box_scroll, pady=(self.ctx.px(4), self.ctx.px(8)))

    def _fit_hint(self, event=None):
        self.hint.configure(wraplength=max(self.ctx.px(120), self.frame.winfo_width() - self.ctx.px(32)))
        self._fit_boxes()

    def _fit_boxes(self, event=None):
        # Phrase composition can scroll independently, leaving words and actions visible.
        cap = max(self.ctx.px(48), int(self.frame.winfo_toplevel().winfo_height() * 0.38) - self.ctx.px(64))
        selector = getattr(self, 'word_selector', None)
        if selector is not None and selector.frame.winfo_height() > 1:
            pills = max((word.widget.winfo_reqheight() for word in selector.word_labels), default=self.ctx.px(32))
            text = selector.text_widget
            text_padding = sum(int(text.cget(option)) for option in ('spacing1', 'spacing3')) + 2 * int(text.cget('pady'))
            word_floor = pills + text_padding + self.ctx.px(16)
            chrome = self.title.winfo_reqheight() + self.ctx.px(2 * self._padding)
            actions = selector.action_frame.winfo_reqheight() + self.ctx.px(24)
            available = selector.frame.winfo_height() - word_floor - chrome - actions
            hint_height = self.hint.winfo_reqheight() + self.ctx.px(12)
            first_box = self._boxes[0].frame.winfo_reqheight() if self._boxes else self.ctx.px(48)
            show_hint = not self._compact_guidance and available - hint_height >= first_box
            if show_hint:
                if not self.hint.winfo_manager():
                    self.hint.pack(anchor='w', before=self.box_scroll, pady=(self.ctx.px(4), self.ctx.px(8)))
                available -= hint_height
            else:
                self.hint.pack_forget()
            cap = min(cap, max(self.ctx.px(32), available))
        height = min(self._boxes_container.winfo_reqheight(), cap)
        self.box_scroll.canvas.configure(height=max(1, height))

    def _on_destroy(self, event):
        if event.widget is self.frame:
            self._unbind_drag_escape()
            selector = getattr(self, 'word_selector', None)
            binding = getattr(self, '_selector_binding', None)
            if selector is not None and binding:
                try:
                    selector.frame.unbind('<Configure>', binding)
                except tk.TclError:
                    pass
            top = self.frame.winfo_toplevel()
            for sequence, binding in getattr(self, '_drag_bindings', []):
                try:
                    top.unbind(sequence, binding)
                except tk.TclError:
                    pass
            self._drag_bindings = []
            for floating in (self._drag_ghost, self._drop_line):
                if floating:
                    try:
                        floating.destroy()
                    except tk.TclError:
                        pass
            self._drag_ghost = self._drop_line = None

    # --- Public API ---

    def get_all_phrases(self) -> list[str]:
        """Get non-empty phrases from all boxes."""
        phrases = []
        for box in self._boxes:
            content = box.get_content()
            if content:
                phrases.append(content)
        return phrases

    def has_any_content(self) -> bool:
        """Check if any box has content."""
        return any(box.get_content() for box in self._boxes)

    def get_focused_box(self) -> Optional[CustomWordBox]:
        """Get the box that currently has focus, or the last box."""
        try:
            focused = self.frame.focus_get()
            for box in self._boxes:
                if focused == box.text_widget:
                    return box
        except (KeyError, tk.TclError):
            pass
        return self._boxes[-1] if self._boxes else None

    def add_word_to_box(self, word: str, box: Optional[CustomWordBox] = None):
        """Add a word tag to a specific box or the focused/last box."""
        target = box or self.get_focused_box()
        if target:
            target.add_word_tag(word)

    def get_box_at_position(self, x_root: int, y_root: int) -> Optional[CustomWordBox]:
        """Find which box is at the given screen coordinates."""
        for box in self._boxes:
            try:
                widget = box.text_widget
                wx = widget.winfo_rootx()
                wy = widget.winfo_rooty()
                ww = widget.winfo_width()
                wh = widget.winfo_height()
                if wx <= x_root <= wx + ww and wy <= y_root <= wy + wh:
                    return box
            except tk.TclError:
                pass
        return None

    def clear_all_highlights(self):
        """Remove highlight from all boxes."""
        for box in self._boxes:
            box.set_highlight(False)

    def pack(self, **kwargs):
        self.frame.pack(**kwargs)

    def pack_forget(self):
        self.frame.pack_forget()

    def destroy(self):
        if self._drop_line:
            try:
                self._drop_line.destroy()
            except tk.TclError:
                pass
            self._drop_line = None
        for box in self._boxes:
            box.destroy()
        self._boxes.clear()
        self.frame.destroy()
