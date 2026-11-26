"""Bound Windows Text font measurement runs without changing the document.

Tk's Windows font backend measures the remaining font run before clipping it to
one display line. A long CJK paragraph can therefore repeatedly measure thousands
of off-screen characters. Alternating the original font with one identical named
font bounds those runs. Use CJK boundaries or whole-word boundaries in entirely
ASCII paragraphs: arbitrary cuts can change kerning or split a Unicode cluster.
"""
import tkinter as tk
import unicodedata


RUN_CHARS = 32
MIN_PARAGRAPH_CHARS = 1024


def _cjk_base(character):
    point = ord(character)
    return (0x3000 <= point <= 0x30ff or 0x3400 <= point <= 0x9fff
            or 0x20000 <= point <= 0x323af) and not unicodedata.category(character).startswith('M')


def font_run_ranges(text):
    """Return alternate runs as Python offsets, never bisecting a text cluster."""
    ranges, start = [], 0
    for paragraph in text.split('\n'):
        if len(paragraph) >= MIN_PARAGRAPH_CHARS:
            edges = [0]
            ascii_paragraph = paragraph.isascii()
            for offset in range(RUN_CHARS, len(paragraph)):
                before, after = paragraph[offset - 1], paragraph[offset]
                boundary = ((before == ' ' and after.isalpha()) if ascii_paragraph
                            else _cjk_base(before) and _cjk_base(after))
                if offset - edges[-1] >= RUN_CHARS and boundary:
                    edges.append(offset)
            if len(edges) > 1:
                edges.append(len(paragraph))
                ranges.extend((start + edges[index], start + edges[index + 1])
                              for index in range(1, len(edges) - 1, 2))
        start += len(paragraph) + 1
    return ranges


class TextFontRuns:
    """One optional font/tag per Text, maintained on revisions, never on resize."""

    TAG = 'ct2_font_runs'

    def __init__(self, widget, *, base_tag=None, watch_edits=False):
        self.widget, self.base_tag = widget, base_tag
        widget._ct_font_runs = self
        self.enabled = widget.tk.call('tk', 'windowingsystem') == 'win32'
        self.font_name = None
        self._font_options = None
        self._snapshot = None
        self._active = False
        self.closed = False
        widget.bind('<Destroy>', self._destroyed, add='+')
        if watch_edits and self.enabled:
            widget.bind('<<Modified>>', self._modified, add='+')

    def _options(self):
        widget = self.widget
        spec = widget.tag_cget(self.base_tag, 'font') if self.base_tag else widget.cget('font')
        try:
            # Named font configuration preserves a negative pixel size exactly.
            return tuple(widget.tk.splitlist(widget.tk.call('font', 'configure', spec)))
        except tk.TclError:
            options = list(widget.tk.splitlist(widget.tk.call('font', 'actual', spec)))
            parts = widget.tk.splitlist(spec)
            if len(parts) > 1:
                try:
                    size = int(parts[1])
                except (ValueError, TypeError):
                    pass
                else:
                    # `font actual` rounds pixel sizes to points. Restore the
                    # requested size so large DPI does not change glyph widths.
                    options[options.index('-size') + 1] = size
            return tuple(options)

    def refresh_font(self):
        if self.closed or self.font_name is None:
            return
        options = self._options()
        if options != self._font_options:
            self.widget.tk.call('font', 'configure', self.font_name, *options)
            self._font_options = options

    def _ensure_font(self):
        if self.font_name is None:
            # Tcl's automatic fontN allocator is independent of Tkinter's
            # Font.counter. A shared fontN name can break a later Font().
            self.font_name = f'CrossTransTextRun_{id(self)}'
            self.widget.tk.call('font', 'create', self.font_name)
            self.widget.tag_configure(self.TAG, font=self.font_name)
            self.widget.tag_lower(self.TAG)
            if self.base_tag:
                self.widget.tag_raise(self.TAG, self.base_tag)
        self.refresh_font()

    def _plain_runs(self):
        """Merge adjacent text across tags, but never across embedded ruby windows."""
        pieces, start = [], None
        for kind, value, index in self.widget.dump('1.0', 'end-1c', text=True, window=True, image=True):
            if kind == 'text':
                if start is None:
                    start = index
                pieces.append(value)
            elif start is not None:
                yield start, ''.join(pieces)
                pieces, start = [], None
        if start is not None:
            yield start, ''.join(pieces)

    def refresh(self, text=None):
        if self.closed or not self.enabled:
            return
        if text is not None and text == self._snapshot:
            self.refresh_font()
            return
        self.clear()
        if text is None:
            count = self.widget.count('1.0', 'end-1c', 'chars')
            count = count[0] if isinstance(count, tuple) else count or 0
            if count < MIN_PARAGRAPH_CHARS:
                return
        runs = (('1.0', text),) if text is not None else self._plain_runs()
        indices = []
        for index, value in runs:
            previous, units = 0, 0
            for first, last in font_run_ranges(value):
                units += int(self.widget.tk.call('string', 'length', value[previous:first]))
                indices.append(f'{index}+{units} chars')
                units += int(self.widget.tk.call('string', 'length', value[first:last]))
                indices.append(f'{index}+{units} chars')
                previous = last
        if indices:
            self._ensure_font()
            self.widget.tk.call(self.widget._w, 'tag', 'add', self.TAG, *indices)
            self._active = True
        self._snapshot = text

    def clear(self):
        if self._active:
            self.widget.tag_remove(self.TAG, '1.0', 'end')
        self._active = False
        self._snapshot = None

    def _modified(self, event):
        if not self.closed and event.widget is self.widget and self.widget.edit_modified():
            # Observe the flag; its owner still re-arms it. Tags create no undo
            # record, replace no text and do not alter the modified state.
            self.refresh(self.widget.get('1.0', 'end-1c'))

    def _destroyed(self, event):
        if event.widget is not self.widget or self.closed:
            return
        self.closed = True
        if getattr(self.widget, '_ct_font_runs', None) is self:
            del self.widget._ct_font_runs
        self._snapshot = None
        if self.font_name is not None:
            try:
                self.widget.tk.call('font', 'delete', self.font_name)
            except tk.TclError:
                pass
            self.font_name = None
