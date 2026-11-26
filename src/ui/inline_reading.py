"""Ruby overlays on the native editable Text; the document and IME stay untouched."""
import tkinter as tk
from tkinter import font as tkfont
from bisect import bisect_right
from src.core import furigana
from src.ui import components as ui
from src.ui.text_font_runs import TextFontRuns
from src.ui.theme.compat import NO_AUTOSTYLE
from src.utils.ui_helpers import schedule_window_callback, cancel_window_callback


class InlineReading:
    def __init__(self, widget):
        self.widget = widget
        self.root = widget._root()
        self._font_runs = TextFontRuns(widget, watch_edits=True)
        self._font_runs.refresh(widget.get('1.0', 'end-1c'))
        self.labels = []
        self.segments = ()
        self.snapshot = ''
        self.closed = False
        self._job = None
        self._render_state = None
        self._units = [0]
        self._units_complete = False
        self._runs = []
        self._run_starts = []
        self._run_ends = []
        self._ruby_widths = {}
        self._spacing = {key: widget.cget(key) for key in ('spacing1', 'spacing2', 'pady')}
        self._old_yscroll = widget.cget('yscrollcommand')
        widget.configure(yscrollcommand=self._scrolled)
        widget.bind('<Configure>', lambda event: self.schedule(), add='+')
        widget.bind('<Map>', lambda event: self.schedule(), add='+')
        widget.bind('<Destroy>', self._destroyed, add='+')
        self._unsubscribe = ui.get().manager.subscribe(lambda manager: self.schedule())

    def update(self, text, language=None, enabled=True):
        from src.ui.deferred_resize import defer_update
        if self.closed or defer_update(self.widget, ('reading-update', str(self.widget)),
                lambda: self.update(text, language, enabled)):
            return
        self._font_runs.refresh(text)
        ctx = ui.get()
        state = (text, language, bool(enabled), str(self.widget.cget('font')),
                 ctx.scale, ctx.manager.text_scale)
        if state == self._render_state:
            self.schedule()
            return
        self._render_state = state
        self.snapshot = text
        self.segments = furigana.annotate(text, language) if enabled else ()
        self._units = [0]
        self._runs = []
        self._ruby_widths = {}
        has_ruby = any(segment.ruby for segment in self.segments)
        self._units_complete = has_ruby
        if has_ruby:
            # Tcl 8 uses UTF-16 units; Tcl 9 counts supplementary characters once.
            # Cache conversions only for an annotated document, once per revision.
            supplementary = int(self.widget.tk.call('string', 'length', '\U0001f600'))
            for character in text:
                self._units.append(self._units[-1] + (supplementary if ord(character) > 0xffff else 1))
            base = tkfont.Font(root=self.widget, font=self.widget.cget('font'))
            self.ruby_font = tkfont.Font(root=self.widget, family=base.actual('family'),
                                        size=-max(round(ui.get().px(12) * ui.get().manager.text_scale),
                                                  round(base.metrics('linespace') * .55)))
            self.ruby_height = self.ruby_font.metrics('linespace')
            self.reserved_lanes = 1
            pad = self.ruby_height + ui.get().px(2)
            self.widget.configure(spacing1=pad, spacing2=pad,
                                  pady=max(int(self.widget.winfo_pixels(self._spacing['pady'])), ui.get().px(2)))
            offset = 0
            for segment in self.segments:
                start, offset = offset, offset + len(segment.base)
                if segment.ruby:
                    self._runs.append((segment, self._index(start), self._index(offset - 1),
                                       self._units[start], self._units[offset]))
                    if segment.ruby not in self._ruby_widths:
                        self._ruby_widths[segment.ruby] = self.ruby_font.measure(segment.ruby)
        else:
            self.widget.configure(**self._spacing)
        self._run_starts = [run[3] for run in self._runs]
        self._run_ends = [run[4] for run in self._runs]
        self.schedule()

    def schedule(self):
        if not self.closed and self._job is None:
            self._job = schedule_window_callback(self.root, self.widget, 'idle', self.draw)

    def _scrolled(self, first, last):
        if self._old_yscroll:
            try:
                self.widget.tk.call(*self.widget.tk.splitlist(self._old_yscroll), first, last)
            except tk.TclError:
                pass
        self.schedule()

    def _index(self, offset):
        units = (self._units[max(0, min(offset, len(self._units) - 1))] if self._units_complete else
                 int(self.widget.tk.call('string', 'length', self.snapshot[:offset])))
        return self.widget.index(f'1.0 + {units} chars')

    def _visible_runs(self):
        width, height = self.widget.winfo_width(), self.widget.winfo_height()
        def units(index):
            value = self.widget.count('1.0', index, 'chars')
            return int(value[0] if isinstance(value, tuple) else value or 0)
        top = units('@0,0')
        bottom = units(self.widget.index(f'@{width},{height} + 1 display lines'))
        first = bisect_right(self._run_ends, top)
        last = bisect_right(self._run_starts, bottom)
        return self._runs[first:last]

    def draw(self):
        self._job = None
        if self.closed:
            return
        from src.ui.deferred_resize import defer_update
        if defer_update(self.widget, ('reading-draw', str(self.widget)), self.draw):
            return
        if not self._runs:
            for label in self.labels:
                label.destroy()
            self.labels.clear()
            return
        if (not self.widget.winfo_ismapped()
                or self.widget.get('1.0', 'end-1c') != self.snapshot):
            for label in self.labels:
                label.place_forget()
            return
        p, plans, occupied = ui.get().palette, [], {}
        bounds = self.widget.winfo_width()
        needed = 1
        gap = ui.get().px(2)
        for segment, start, end, _start_units, _end_units in self._visible_runs():
            first = self.widget.bbox(start)
            final = self.widget.bbox(end)
            if not first:
                continue
            width = min(max(1, bounds - 4), self._ruby_widths[segment.ruby])
            base_width = final[0] + final[2] - first[0] if final and final[1] == first[1] else first[2]
            x = max(2, min(bounds - width - 2, first[0] + (base_width - width) // 2))
            lanes = occupied.setdefault(first[1], [])
            lane = next((index for index, right in enumerate(lanes) if right + gap <= x), len(lanes))
            if lane == len(lanes):
                lanes.append(x + width)
            else:
                lanes[lane] = x + width
            needed = max(needed, lane + 1)
            plans.append((segment, start, first, x, width, lane))
        if plans and needed > self.reserved_lanes:
            self.reserved_lanes = needed
            pad = needed * (self.ruby_height + gap)
            self.widget.configure(spacing1=pad, spacing2=pad)
            # Text geometry must settle before using its character bounds again.
            self._job = schedule_window_callback(self.root, self.widget, 16, self.draw)
            return
        used = 0
        for segment, start, first, x, width, lane in plans:
            y = first[1] - (lane + 1) * (self.ruby_height + gap) + gap
            if y < 0:
                continue
            if used == len(self.labels):
                label = tk.Label(self.widget, bd=0, padx=0, pady=0, takefocus=False, **NO_AUTOSTYLE)
                label.bind('<Button-1>', self._click)
                self.labels.append(label)
            label = self.labels[used]
            label.source_index = start
            appearance = (segment.ruby, str(self.ruby_font), p.ruby,
                          str(self.widget.cget('background')))
            if getattr(label, '_reading_appearance', None) != appearance:
                label.configure(text=segment.ruby, font=self.ruby_font,
                                foreground=p.ruby, background=appearance[-1])
                label._reading_appearance = appearance
            placement = (x, y, width, self.ruby_height)
            if getattr(label, '_reading_placement', None) != placement or not label.winfo_manager():
                label.place(x=x, y=y, width=width, height=self.ruby_height)
                label._reading_placement = placement
            used += 1
        for label in self.labels[used:]:
            label.place_forget()
        # Keep a small reuse reserve, bounded by the viewport rather than document size.
        keep = used + 8 if self._runs else 0
        for label in self.labels[keep:]:
            label.destroy()
        del self.labels[keep:]

    def _click(self, event):
        self.widget.mark_set('insert', event.widget.source_index)
        self.widget.focus_set()
        return 'break'

    @property
    def has_ruby(self):
        return any(segment.ruby for segment in self.segments)

    def _destroyed(self, event):
        if event.widget is not self.widget:
            return
        self.closed = True
        if self._job is not None:
            cancel_window_callback(self.root, self.widget, self._job)
            self._job = None
        self._unsubscribe()
