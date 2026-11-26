"""
History Dialog for CrossTrans.
"""
import tkinter as tk
from tkinter import BOTH, X, LEFT, RIGHT, W
from tkinter import font as tkfont
from datetime import datetime
from typing import Optional

from src.core import furigana, lang_id
from src.core.furigana import RubySegment
from src.ui import components as ui
from src.ui.components.dialog import work_area
from src.ui.ruby_text import RubyLine, fit_segments
from src.ui.theme import engine, fonts
from src.ui.theme.compat import NO_AUTOSTYLE
from src.ui.translator_layout import window_size, window_position
from src.utils.ui_helpers import schedule_window_callback, cancel_window_callback

# A line is cut to the row's width in pixels, "..." included (K7). By character
# count (60, cut to 57) a Japanese line ran 685 px into a 560 px row, clipped,
# and its "..." never showed, while a Latin line was cut with room to spare.
LIST_WIDTH = 580            # the list's width before the window is first laid out
ROW_PADDING = 12            # each row frame's padding, on either side
LABEL_PADDING_PX = 4        # a ttk.Label's own, around its text (measured)
# No row shows more than this many characters of a line; only these are measured.
LINE_SCAN_CHARS = 200
# Readings are generated for this much of a line - more than is shown, so a word
# that straddles the cut is read in its context - but never for a whole document.
LINE_ANNOTATE_CHARS = 120
# The rows' text, as before J9.
ROW_FONT = fonts.font_tuple('body', ())
# Show one page immediately, prepare one more, and recycle three pages on scroll.
FIRST_ROWS = 12
ROW_BATCH = 12
MAX_LIVE_ROWS = 36


def entry_pair(item):
    """Filter by stored labels, never use them as evidence for furigana."""
    return item.get('source_lang') or 'Unknown', item.get('target_lang') or 'Unknown'


def pair_label(pair):
    source, target = pair
    return f'{source} {chr(0x2192)} {target}' if source != 'Unknown' else f'To {target}'


def filter_entries(entries, query='', pair=None):
    query = query.casefold().strip()
    return [item for item in entries
            if (pair is None or entry_pair(item) == pair)
            and (not query or any(query in (item.get(key) or '').casefold()
                                  for key in ('original', 'translated', 'target_lang', 'source_lang')))]


class _HistoryAction(ui.IconButton):
    """Keep keyboard targets laid out while their icons wait for hover or focus."""

    def __init__(self, *args, **kwargs):
        self.hidden = True
        super().__init__(*args, **kwargs)
        self.hide()

    def show(self):
        self.hidden = False
        self.set_on(False)

    def hide(self):
        self.hidden = True
        self.configure(image='')

    def _retheme(self, manager):
        super()._retheme(manager)
        if self.hidden:
            self.hide()


class _HistoryArea(ui.ScrollArea):
    """Keep logical rows in the scroll range without allocating their widgets."""

    def __init__(self, parent):
        self._row_windows = {}
        self._empty_window = self._empty_widget = None
        self._extent_height = 0
        super().__init__(parent)
        self.canvas.delete(self._window)
        self.content.destroy()
        self.content = self.canvas
        self.on_reveal = None

    def set_extent(self, height):
        if height == self._extent_height:
            return False
        self._extent_height = height
        self.canvas.configure(scrollregion=(0, 0, max(1, self.canvas.winfo_width()), height))
        self._schedule_layout()
        return True

    def place_row(self, row, index, pitch, gap):
        item = self._row_windows.get(row)
        if item is None:
            item = self.canvas.create_window(0, index * pitch, anchor='nw', window=row)
            self._row_windows[row] = item
        self.canvas.coords(item, 0, index * pitch)
        self.canvas.itemconfigure(item, width=max(1, self.canvas.winfo_width()), height=pitch - gap, state='normal')
        row._history_active = True

    def hide_row(self, row):
        item = self._row_windows.get(row)
        if item is not None:
            self.canvas.itemconfigure(item, state='hidden')
        row._history_active = False

    def prune_rows(self):
        for row, item in list(self._row_windows.items()):
            if not row.winfo_exists():
                self.canvas.delete(item)
                del self._row_windows[row]

    def clear_empty(self):
        if self._empty_window is not None:
            self.canvas.delete(self._empty_window)
            self._empty_window = self._empty_widget = None

    def show_empty(self, widget):
        self._empty_widget = widget
        self._empty_window = self.canvas.create_window(0, ui.get().px(24), anchor='nw', window=widget)
        widget.bind('<Configure>', lambda event: self._schedule_layout(), add='+')
        self._schedule_layout()

    def _sync_layout(self):
        self._layout_job = None
        width = max(1, self.canvas.winfo_width())
        for item in self._row_windows.values():
            self.canvas.itemconfigure(item, width=width)
        height = self._extent_height
        if self._empty_window is not None:
            self.canvas.itemconfigure(self._empty_window, width=width)
            height = self._empty_widget.winfo_reqheight() + ui.get().px(48)
        region = (0, 0, width, height)
        current = tuple(map(float, self.canvas.tk.splitlist(self.canvas.cget('scrollregion'))))
        if current != region:
            self.canvas.configure(scrollregion=region)

    def request_render(self):
        self._schedule_layout()

    def reveal(self, widget, margin=0):
        if self.on_reveal is not None:
            self.on_reveal(widget)

    def _on_destroy(self, event=None):
        if event is None or event.widget is self:
            self._row_windows.clear()
            self._empty_window = self._empty_widget = self.on_reveal = None
        super()._on_destroy(event)


class HistoryDialog:
    """Dialog to view and manage translation history."""

    def __init__(self, parent, history_manager, on_load_callback, furigana_enabled=False, *, container=None, on_close=None, config=None, quick_pick=False):
        self.parent, self.ctx = parent, ui.get()
        self.container, self.on_close = container, on_close
        self.detail_frame = None
        self.config, self.quick_pick = config, quick_pick
        self.detail_links = None
        self.history_manager, self.on_load_callback = history_manager, on_load_callback
        self.furigana_enabled = furigana_enabled
        self.row_font = self.ctx.font('body')
        self.ruby_font = self.ctx.font('caption', 'japanese')
        self.all_history, self.visible_entries, self.pairs = [], [], []
        self.root = parent._root()
        self._row_pool, self._row_map = [], {}
        self._loaded_count = 0
        self._row_job = self._viewport_job = self._measure_job = None
        self._focused_id = None
        self._line_cache = {}
        self._closed = False
        self._row_pitch = self.ctx.px(80) + 3 * tkfont.Font(root=self.root, font=self.row_font).metrics('linespace')
        self.selected_pair = None
        self._last_query = ''
        self._focused_id = None
        self._preview_cache = {}
        self._resize_job, self._last_width = None, 0
        self.menu, self.confirmation, self.empty_state = None, None, None
        if container is not None:
            self.window = ui.Frame(container, on='bg')
            self.window.pack(fill='both', expand=True)
        else:
            self.window = tk.Toplevel(parent)
            self.window.withdraw()
            self.window.title('Translation history')
            self.window.sizes_from_work_area = True
            engine.attach(self.window)
            area = work_area()
            size = window_size(area, scale=self.ctx.scale, default=(720, 680), minimum=(480, 360))
            x, y = window_position(size, area)
            self.window.geometry(f'{size.width}x{size.height}{x:+d}{y:+d}')
            self.window.minsize(size.min_width, size.min_height)
        self._create_widgets()
        self._unsubscribe = self.ctx.manager.subscribe(self._retheme)
        self.window.bind('<Destroy>', self._on_destroy, add='+')
        self.window.bind('<Escape>', self._escape)
        self.window.bind('<Control-f>', lambda e: self.search_entry.focus_set() or 'break')
        self._refresh_list()
        if self.container is None:
            self.window.deiconify()
            self.window.lift()

    def _on_destroy(self, event):
        if event.widget is self.window:
            self._closed = True
            self._close_detail_links()
            self._cancel_rows()
            for name in ('_resize_job', '_viewport_job', '_measure_job'):
                job = getattr(self, name)
                if job:
                    cancel_window_callback(self.root, self.window, job)
                    setattr(self, name, None)
            self._row_map.clear()
            self._row_pool.clear()
            self._line_cache.clear()
            self.search_var.trace_remove('write', self._search_trace)
            self._unsubscribe()

    def _create_widgets(self):
        px = self.ctx.px
        self.body = body = ui.Frame(self.window, padding=px(8 if self.quick_pick else 20))
        body.pack(fill=BOTH, expand=True)
        header = ui.Frame(body)
        header.pack(fill=X, pady=(0, px(16)))
        self.more_button = ui.Button(header, 'Clear all history', command=self._clear_all, kind='subtle', icon='trash')
        self.more_button.pack(side=RIGHT)
        ui.Label(header, 'Translation history', kind='subtitle').pack(side=LEFT)
        if self.quick_pick:
            header.pack_forget()
        search = ui.Frame(body)
        search.pack(fill=X, pady=(0, px(12)))
        ui.Label(search, image=self.ctx.icon('search')).pack(side=LEFT, padx=(0, px(8)))
        self.search_var = tk.StringVar(master=self.window)
        self.search_entry = ui.TextField(search, 'Search history...', textvariable=self.search_var)
        self.search_entry.pack(side=LEFT, fill=X, expand=True)
        self.search_entry.bind('<Down>', lambda event: self._quick_move(1))
        if self.quick_pick:
            self.search_entry.bind('<Return>', self._quick_choose)
        self.clear_search_btn = ui.IconButton(search, 'x-lg', label='Clear search', command=self._clear_search)
        self._search_trace = self.search_var.trace_add('write', self._on_search_change)
        self.filter_bar = ui.Frame(body)
        self.filter_bar.pack(fill=X, pady=(0, px(12)))
        self.filter_bar.bind('<Configure>', self._arrange_filters)
        self.pair_variable = tk.IntVar(master=self.window, value=-1)
        self.area = _HistoryArea(body)
        self.area.pack(fill=BOTH, expand=True)
        self.canvas, self.scrollable_frame = self.area.canvas, self.area.content
        self.area.on_reveal = self._reveal_row
        self.canvas.configure(yscrollcommand=self._scrolled)
        self.canvas.bind('<Configure>', self._resized, add='+')
        self._bind_navigation(self.canvas)

    def _escape(self, event=None):
        if self.detail_frame is not None and self.detail_frame.winfo_ismapped():
            self.show_list()
        elif self.search_var.get():
            self._clear_search()
        elif self.selected_pair is not None:
            self.set_pair(None)
        elif self.container is not None:
            if self.on_close:
                self.on_close()
        else:
            self.window.destroy()
        return 'break'

    def _refresh_pairs(self):
        self.pairs = list(dict.fromkeys(entry_pair(item) for item in self.all_history))
        if self.selected_pair not in self.pairs:
            self.selected_pair = None
        for widget in self.filter_bar.winfo_children():
            widget.destroy()
        self._filters = []
        visible = self.pairs[:3]
        if self.selected_pair is not None and self.selected_pair not in visible:
            visible = visible[:2] + [self.selected_pair]
        for pair, label in [(None, 'All')] + [(p, pair_label(p)) for p in visible]:
            index = self.pairs.index(pair) if pair is not None else -1
            self._filters.append(ui.Choice(self.filter_bar, label, self.pair_variable, index,
                                           command=lambda p=pair: self.set_pair(p)))
        if len(self.pairs) > 3:
            self.more_pairs_button = ui.Button(self.filter_bar, 'More languages', kind='subtle',
                                               command=self.show_pairs)
            self._filters.append(self.more_pairs_button)
        self.pair_variable.set(self.pairs.index(self.selected_pair) if self.selected_pair else -1)
        self._arrange_filters()

    def _arrange_filters(self, event=None):
        width = self.filter_bar.winfo_width()
        width = width if width > 1 else self.ctx.px(LIST_WIDTH)
        x, y, row_height = 0, 0, 0
        gap = self.ctx.px(6)
        for button in getattr(self, '_filters', []):
            needed = button.winfo_reqwidth()
            if x and x + needed > width:
                x, y, row_height = 0, y + row_height + gap, 0
            button.place(x=x, y=y)
            x += needed + gap
            row_height = max(row_height, button.winfo_reqheight())
        requested_height = y + row_height
        if int(self.filter_bar.cget('height')) != requested_height:
            self.filter_bar.configure(height=requested_height)

    def set_pair(self, pair):
        self.selected_pair = pair
        self._refresh_pairs()
        self._filter_and_display()

    def _new_menu(self):
        if self.menu is not None:
            self.menu.destroy()
        p = self.ctx.palette
        self.menu = ui.Menu(self.window, tearoff=False, bg=p.surface, fg=p.text,
                            activebackground=p.soft, activeforeground=p.soft_text,
                            disabledforeground=p.muted, font=self.ctx.font('body'), **NO_AUTOSTYLE)
        return self.menu

    def show_pairs(self):
        menu = self._new_menu()
        for pair in self.pairs:
            menu.add_command(label=pair_label(pair), command=lambda p=pair: self.set_pair(p))
        menu.post(self.more_pairs_button.winfo_rootx(),
                  self.more_pairs_button.winfo_rooty() + self.more_pairs_button.winfo_height())

    def _resized(self, event=None):
        if self._resize_job:
            cancel_window_callback(self.root, self.window, self._resize_job)
        self._resize_job = schedule_window_callback(self.root, self.window, 100, self._refit)
        self._schedule_viewport()

    def _refit(self):
        if self._resize_job:
            cancel_window_callback(self.root, self.window, self._resize_job)
        self._resize_job = None
        width = self._line_px()
        if width == self._last_width:
            return
        self._last_width = width
        for row in self._row_map.values():
            for widget, segments, more in getattr(row, 'lines', []):
                shown, _ = fit_segments(widget, segments, width - self.ctx.px(LABEL_PADDING_PX),
                                         base_font=self.row_font, ruby_font=self.ruby_font, more=more)
                if isinstance(widget, RubyLine):
                    widget.set_segments(shown)
                else:
                    widget.configure(text=''.join(segment.base for segment in shown))
        self._schedule_measure()

    def _retheme(self, manager):
        self.row_font = self.ctx.font('body')
        self.ruby_font = self.ctx.font('caption', 'japanese')
        p = self.ctx.palette
        for row in self._row_pool:
            for index, (widget, _, _) in enumerate(getattr(row, 'lines', [])):
                fg = p.muted if index == 0 else p.text
                if isinstance(widget, RubyLine):
                    widget.base_font, widget.ruby_font = self.row_font, self.ruby_font
                    widget.recolour(bg=p.surface, base_fg=fg, ruby_fg=p.ruby)
                    widget.set_segments(widget.segments)
                else:
                    widget.configure(foreground=fg, font=self.row_font)
        for box in getattr(self, 'detail_boxes', []):
            if box.winfo_exists():
                box.recolour(bg=p.surface, base_fg=p.text, kanji_fg=p.text, ruby_fg=p.ruby, selection=p.selection)
        self._last_width = 0
        self._refit()

    def _bind_wheel(self, widget):
        self.area.adopt(widget)
    def _on_search_change(self, *args):
        if self.search_var.get() == self._last_query:
            return
        if self.search_var.get():
            self.clear_search_btn.pack(side=LEFT, padx=(self.ctx.px(8), 0))
        else:
            self.clear_search_btn.pack_forget()
        self._filter_and_display()

    def _clear_search(self):
        self.search_entry.clear()

    def _refresh_list(self):
        from src.ui.deferred_resize import defer_update
        if defer_update(self.more_button, ('history-refresh', str(self.more_button)), self._refresh_list):
            return
        self._preview_cache.clear()
        self.all_history = list(self.history_manager.get_history())
        self._refresh_pairs()
        self._filter_and_display()

    def _filter_and_display(self):
        self.more_button.configure(state='normal' if self.all_history else 'disabled')
        self._last_query = self.search_var.get()
        self._cancel_rows()
        if self.empty_state is not None:
            self.area.clear_empty()
            self.empty_state.destroy()
        self.visible_entries = filter_entries(self.all_history, self.search_var.get(), self.selected_pair)
        self._row_map.clear()
        self._focused_id = None
        self._loaded_count = min(FIRST_ROWS, len(self.visible_entries))
        self.empty_state = None
        if not self.visible_entries:
            for row in self._row_pool:
                row.entry = None
                item = self.area._row_windows.pop(row, None)
                if item is not None:
                    self.area.canvas.delete(item)
                row._history_active = False
            self._row_pool.clear()
            self.area.set_extent(0)
            filtered = bool(self.search_var.get().strip() or self.selected_pair)
            self.empty_state = ui.EmptyState(
                self.scrollable_frame, 'No results' if filtered else 'No history yet',
                'Try another search or language pair.' if filtered else 'Your translations will appear here.',
                image=ui.mascot('sleepy', 72), action='Clear filters' if filtered else 'Open the Translator',
                on_action=self._clear_filters if filtered else self._open_translator)
            self.area.show_empty(self.empty_state)
            self.area.adopt()
            return
        self._render_window(0)
        self.area.scroll_to_top()
        if self._loaded_count < len(self.visible_entries):
            self._queue_page(30)

    def _quick_choose(self, event=None):
        if self.visible_entries:
            self._load_item(self.visible_entries[0])
        return 'break'

    def _quick_move(self, step):
        if not self.visible_entries:
            return 'break'
        focused = self.window.focus_get()
        row = self._row_for_widget(focused)
        index = row.entry_index if row is not None else next(
            (i for i, item in enumerate(self.visible_entries) if self._entry_id(item) == self._focused_id), -1)
        self._focus_index(max(0, min(len(self.visible_entries) - 1, index + step)))
        return 'break'

    @staticmethod
    def _entry_id(item):
        return item.get('id') or id(item)

    def _row_for_widget(self, widget):
        while widget is not None and widget is not self.scrollable_frame:
            if hasattr(widget, 'entry_index'):
                return widget
            widget = getattr(widget, 'master', None)
        return None

    def _focus_index(self, index, action=None):
        if not self.visible_entries:
            return 'break'
        index = max(0, min(index, len(self.visible_entries) - 1))
        if index >= self._loaded_count:
            self._cancel_rows()
            self._loaded_count = min(len(self.visible_entries), (index // ROW_BATCH + 2) * ROW_BATCH)
        self._render_window(index)
        self.canvas.yview_moveto(index * self._row_pitch / max(1, self._loaded_count * self._row_pitch))
        row = self._row_map[index]
        self._focused_id = self._entry_id(row.entry)
        target = row if self.quick_pick else row.actions[action or 0]
        target.focus_set()
        return 'break'

    def _bind_navigation(self, widget):
        widget.bind('<Down>', lambda event: self._quick_move(1))
        widget.bind('<Up>', lambda event: self._quick_move(-1))
        widget.bind('<Home>', lambda event: self._focus_index(0))
        widget.bind('<End>', lambda event: self._focus_index(len(self.visible_entries) - 1))
        widget.bind('<Next>', lambda event: self._quick_move(ROW_BATCH))
        widget.bind('<Prior>', lambda event: self._quick_move(-ROW_BATCH))

    def _tab_row(self, row, backwards=False):
        index = row.entry_index + (-1 if backwards else 1)
        if 0 <= index < len(self.visible_entries):
            return self._focus_index(index, 2 if backwards else 0)

    def _reveal_row(self, widget):
        row = self._row_for_widget(widget)
        if row is None:
            return
        top = row.entry_index * self._row_pitch
        first = self.canvas.canvasy(0)
        if top < first or top + self._row_pitch - self.ctx.px(8) > first + self.canvas.winfo_height():
            self.canvas.yview_moveto(top / max(1, self._loaded_count * self._row_pitch))

    def _scrolled(self, first, last):
        self.area._on_scroll(first, last)
        self._schedule_viewport()

    def _schedule_viewport(self):
        if not self._closed and self._viewport_job is None:
            self._viewport_job = schedule_window_callback(self.root, self.window, 16, self._sync_viewport)

    def _sync_viewport(self):
        self._viewport_job = None
        if self._closed or not self.visible_entries or not self.canvas.winfo_ismapped():
            return
        first = max(0, int(self.canvas.canvasy(0) // self._row_pitch))
        self._render_window(first)
        if first >= self._loaded_count - ROW_BATCH and self._loaded_count < len(self.visible_entries):
            self._queue_page(16)

    def _queue_page(self, delay):
        if self._row_job is None:
            self._pending_rows = self.visible_entries[self._loaded_count:self._loaded_count + ROW_BATCH]
            if self._pending_rows:
                self._row_job = schedule_window_callback(self.root, self.window, delay, self._prepare_page)

    def _prepare_page(self):
        self._row_job = None
        if self._closed:
            return
        self._loaded_count += len(self._pending_rows)
        self._pending_rows = []
        first = max(0, int(self.canvas.canvasy(0) // self._row_pitch))
        self._render_window(first)

    def _render_window(self, first):
        if not self._loaded_count:
            return
        first = max(0, min(first, self._loaded_count - 1))
        start = max(0, min((first // ROW_BATCH - 1) * ROW_BATCH,
                           self._loaded_count - MAX_LIVE_ROWS))
        indices = range(start, min(self._loaded_count, start + MAX_LIVE_ROWS))
        self._row_pool[:] = [row for row in self._row_pool if row.winfo_exists()]
        self.area.prune_rows()
        kept = {i: row for i, row in self._row_map.items() if i in indices and row.winfo_exists()}
        spare = [row for row in self._row_pool if row not in kept.values()]
        focused = self._row_for_widget(self.window.focus_get())
        for index in indices:
            if index in kept:
                continue
            if spare:
                row = spare.pop(0)
                if row is focused:
                    self._focused_id = self._entry_id(row.entry)
                    self.canvas.focus_set()
                self._update_history_item(row, self.visible_entries[index])
            else:
                row = self._create_history_item(self.visible_entries[index])
                self._row_pool.append(row)
            row.entry_index = index
            kept[index] = row
        for row in spare:
            self.area.hide_row(row)
            row._position_key = None
        self._row_map = kept
        self._place_rows()
        self._schedule_measure()

    def _place_rows(self):
        anchor = max(0, self.canvas.canvasy(0))
        gap = self.ctx.px(8)
        for index, row in self._row_map.items():
            position = (index, self._row_pitch)
            if getattr(row, '_position_key', None) != position:
                self.area.place_row(row, index, self._row_pitch, gap)
                row._position_key = position
        if self.area.set_extent(self._loaded_count * self._row_pitch):
            self.canvas.yview_moveto(anchor / max(1, self._loaded_count * self._row_pitch))

    def _schedule_measure(self):
        if not self._closed and self._measure_job is None:
            self._measure_job = schedule_window_callback(self.root, self.window, 'idle', self._measure_rows)

    def _measure_rows(self):
        self._measure_job = None
        if self._closed or not self._row_map:
            return
        pitch = max(row.winfo_reqheight() for row in self._row_map.values()) + self.ctx.px(8)
        if pitch != self._row_pitch:
            anchor = max(0, self.canvas.canvasy(0)) / self._row_pitch
            self._row_pitch = pitch
            self._place_rows()
            self.canvas.yview_moveto(anchor / max(1, self._loaded_count))

    def _clear_filters(self):
        self.selected_pair = None
        self._refresh_pairs()
        if self.search_var.get():
            self._clear_search()
        else:
            self._filter_and_display()

    def _open_translator(self):
        if self.container is not None:
            if self.on_close:
                self.on_close()
            return
        self.window.destroy()
        self.parent.deiconify()
        self.parent.lift()
        self.parent.focus_set()

    def _cancel_rows(self):
        """Cancel only this window's pending page and viewport work."""
        self._pending_rows = []
        for name in ('_row_job', '_viewport_job', '_measure_job'):
            job = getattr(self, name, None)
            if job:
                cancel_window_callback(self.root, self.window, job)
                setattr(self, name, None)

    def _line_px(self):
        width = self.canvas.winfo_width()
        return (width if width > 1 else self.ctx.px(LIST_WIDTH)) - 2 * self.ctx.px(ROW_PADDING)

    def _create_history_item(self, item):
        px = self.ctx.px
        frame = ui.Card(self.scrollable_frame, padding=ROW_PADDING)
        frame.entry = item
        frame.lines = []
        top = ui.Frame(frame)
        frame.header = top
        top.pack(fill=X, pady=(0, px(8)))
        top.columnconfigure(0, weight=1)
        pair = ui.Chip(top, pair_label(entry_pair(item)))
        pair.grid(row=0, column=0, sticky='w')
        date = ui.Label(top, '', kind='caption', localize=False)
        date.grid(row=0, column=1, sticky='e', padx=(px(8), 0))
        actions = ui.Frame(top)
        actions.grid(row=0, column=2, sticky='ne', padx=(px(8), 0))
        frame.pair_label, frame.date_label = pair, date
        frame.actions = []
        for column, (icon, label, command) in enumerate([
            ('copy', 'Copy translation', lambda: self._copy_item(frame.entry)),
            ('box-arrow-up-right', 'Open in Translator', lambda: self._load_item(frame.entry)),
            ('trash', 'Delete translation', lambda: self._delete_item(frame.entry)),
        ]):
            button = _HistoryAction(actions, icon, label=label, command=command)
            button.grid(row=0, column=column, sticky='nsew')
            actions.columnconfigure(column, minsize=px(32))
            frame.actions.append(button)
            self._bind_navigation(button)
        frame.actions[-1].bind('<Tab>', lambda event: self._tab_row(frame))
        frame.actions[0].bind('<Shift-Tab>', lambda event: self._tab_row(frame, True))
        frame.actions[0].bind('<ISO_Left_Tab>', lambda event: self._tab_row(frame, True))
        if self.quick_pick:
            for button in frame.actions:
                button.grid_remove()
            frame.configure(takefocus=True)
            frame.bind('<Return>', lambda event: self._load_item(frame.entry) or 'break')
            frame.bind('<space>', lambda event: self._load_item(frame.entry) or 'break')
            self._bind_navigation(frame)
            frame.bind('<Tab>', lambda event: self._tab_row(frame))
            frame.bind('<Shift-Tab>', lambda event: self._tab_row(frame, True))
            frame.bind('<ISO_Left_Tab>', lambda event: self._tab_row(frame, True))
            frame.bind('<FocusIn>', lambda event: pair.configure(image=self.ctx.icon('arrow-right', 16), compound='left'), add='+')
            frame.bind('<FocusOut>', lambda event: pair.configure(image=''), add='+')
        def fit_header(event=None):
            room = top.winfo_width()
            if room <= 1:
                return
            narrow = room < date.winfo_reqwidth() + actions.winfo_reqwidth() + px(160)
            date.grid_configure(row=1 if narrow else 0, column=0 if narrow else 1,
                                sticky='w' if narrow else 'e', padx=(0 if narrow else px(8), 0))
            actions.grid_configure(row=1 if narrow else 0, column=1 if narrow else 2)
            pair.configure(wraplength=max(px(60), room - (px(24) if narrow else
                date.winfo_reqwidth() + actions.winfo_reqwidth() + px(36))))
            self._schedule_measure()
        top.bind('<Configure>', fit_header, add='+')
        for widget in (frame, top, pair, date):
            widget.bind('<Button-1>', lambda event: self._activate_row(frame))
        self._update_history_item(frame, item)
        self._wire_row(frame, frame)
        return frame

    def _activate_row(self, frame):
        if self.container is not None and not self.quick_pick:
            self.select_item(frame.entry)
        else:
            self._load_item(frame.entry)

    def _update_history_item(self, frame, item):
        frame.entry = item
        frame.pair_label.configure(text=pair_label(entry_pair(item)), image='')
        frame.date_label.configure(text=datetime.fromtimestamp(item.get('timestamp', 0)).strftime('%Y-%m-%d %H:%M'))
        for action in frame.actions:
            action.hide()
        original = item.get('original', '')
        source = lang_id.resolve(original, model=item.get('source_verdict')).language
        self._content_line(frame, original,
                                        lang_id.JAPANESE if source == lang_id.JAPANESE else None,
                                        fg=self.ctx.palette.muted, slot=0)
        self._content_line(frame, item.get('translated', ''),
                                        lang_id.output_hint(source, item.get('target_lang')),
                                        prefix=chr(0x2192) + ' ', fg=self.ctx.palette.text,
                                        pady=(self.ctx.px(4), 0), slot=1)

    def _wire_row(self, frame, widget):
        if not getattr(widget, '_history_wired', False):
            widget._history_wired = True
            widget.bind('<Enter>', lambda e: self._show_actions(frame), add='+')
            def focused(event):
                self._focused_id = self._entry_id(frame.entry)
                self._show_actions(frame)
            widget.bind('<FocusIn>', focused, add='+')
            widget.bind('<Leave>', lambda e: self._hide_actions(frame), add='+')
            widget.bind('<FocusOut>', lambda e: self._hide_actions(frame), add='+')
            self.area.bind_wheel(widget)
        for child in widget.winfo_children():
            self._wire_row(frame, child)

    def _show_actions(self, frame):
        if self._closed or not frame.winfo_exists():
            return
        for button in frame.actions:
            if button.winfo_exists():
                button.show()

    def _hide_actions(self, frame):
        if self._closed or not frame.winfo_exists():
            return
        if self.window.focus_get() in frame.actions:
            return
        x, y = frame.winfo_pointerxy()
        if (frame.winfo_rootx() <= x < frame.winfo_rootx() + frame.winfo_width()
                and frame.winfo_rooty() <= y < frame.winfo_rooty() + frame.winfo_height()):
            return
        for button in frame.actions:
            if button.winfo_exists():
                button.hide()

    def _copy_item(self, item):
        if item is None:
            return
        self.window.clipboard_clear()
        self.window.clipboard_append(item.get('translated', ''))

    def _content_line(self, parent, text: str, hint: Optional[str], prefix: str = "",
                      fg: Optional[str] = None, pady=(0, 0), slot=0) -> list:
        """One line of a row - a RubyLine when it carries a reading, else the plain
        label it always was. Returns the widgets a click must reach.

        Cut to the row's width in pixels with "..." (`fit_segments`, K7), never
        through a ruby pair.
        """
        line = text[:LINE_SCAN_CHARS].replace('\n', ' ')
        key = (line[:LINE_SCAN_CHARS], hint, self.furigana_enabled, prefix,
               len(text) > LINE_ANNOTATE_CHARS, len(text) > LINE_SCAN_CHARS)
        cached = self._line_cache.get(key)
        if cached is None:
            segments = ()
            if self.furigana_enabled and furigana.should_annotate(line[:LINE_ANNOTATE_CHARS], hint):
                segments = furigana.annotate(line[:LINE_ANNOTATE_CHARS], hint)
            ruby = any(segment.ruby for segment in segments)
            full = (((RubySegment(prefix, None),) if prefix else ()) + tuple(segments)
                    if ruby else (RubySegment(prefix + line, None),))
            cached = full, ruby, len(text) > (LINE_ANNOTATE_CHARS if ruby else LINE_SCAN_CHARS)
            if len(self._line_cache) >= 512:
                self._line_cache.pop(next(iter(self._line_cache)))
            self._line_cache[key] = cached
        full, ruby, more = cached
        shown, _ = fit_segments(parent, full,
                               self._line_px() - (0 if ruby else self.ctx.px(LABEL_PADDING_PX)),
                               base_font=self.row_font, ruby_font=self.ruby_font, more=more)
        widget = parent.lines[slot][0] if slot < len(parent.lines) else None
        reused = widget is not None and isinstance(widget, RubyLine) == ruby
        if not reused:
            if widget is not None:
                widget.destroy()
            widget = RubyLine(parent, shown, base_font=self.row_font, ruby_font=self.ruby_font,
                              bg=self._row_bg(), base_fg=fg or self.ctx.palette.text,
                              ruby_fg=self.ctx.palette.ruby) if ruby else ui.Label(
                                  parent, '', localize=False, font=self.row_font)
        if ruby:
            widget.base_font, widget.ruby_font = self.row_font, self.ruby_font
            widget.recolour(bg=self._row_bg(), base_fg=fg or self.ctx.palette.text, ruby_fg=self.ctx.palette.ruby)
            widget.set_segments(shown)
        else:
            widget.configure(text=''.join(segment.base for segment in shown),
                             foreground=fg or self.ctx.palette.text, font=self.row_font)
        if not reused:
            before = parent.lines[slot + 1][0] if slot + 1 < len(parent.lines) else None
            widget.pack(anchor=W, pady=pady, **({'before': before} if before is not None else {}))
            widget.bind('<Button-1>', lambda event: self._activate_row(parent))
            self._wire_row(parent, widget)
        if slot < len(parent.lines):
            parent.lines[slot] = widget, full, more
        else:
            parent.lines.append((widget, full, more))
        return [widget]

    def _row_bg(self):
        return self.ctx.palette.surface

    def _load_item(self, item):
        """Load item into main translator."""
        if item is None:
            return
        self.on_load_callback(item)
        if self.container is None:
            self.window.destroy()

    def activate(self):
        self._refresh_list()

    def show_list(self):
        self._close_detail_links()
        if self.detail_frame is not None:
            self.detail_frame.pack_forget()
        self.body.pack(fill='both', expand=True)
        if hasattr(self, 'viewport') and hasattr(self.viewport, 'request_render'):
            self.viewport.request_render()
        elif hasattr(self.area, 'request_render'):
            self.area.request_render()

    def select_item(self, item):
        from src.ui.ruby_text import RubyText, insert_output
        self._close_detail_links()
        if self.detail_frame is not None:
            self.detail_frame.destroy()
        self.body.pack_forget()
        self.detail_frame = frame = ui.Frame(self.window, padding=self.ctx.px(16))
        frame.pack(fill='both', expand=True)
        header = ui.Frame(frame)
        header.pack(fill='x', pady=(0, self.ctx.px(12)))
        ui.Button(header, 'Back to history', self.show_list, kind='subtle', icon='arrow-left').pack(side='left')
        actions = ui.Frame(frame)
        actions.pack(side='bottom', fill='x', pady=(self.ctx.px(12), 0))
        ui.Button(actions, 'Copy', lambda: self._copy_item(item), icon='copy').pack(side='left')
        ui.Button(actions, 'Open in Translate', lambda: self._load_item(item), kind='accent').pack(side='right')
        original = item.get('original', '')
        source = lang_id.resolve(original, model=item.get('source_verdict')).language
        self.detail_area = ui.ScrollArea(frame, stretch=True)
        self.detail_area.pack(fill='both', expand=True)
        content = ui.Frame(self.detail_area.content)
        content.grid(row=0, column=0, sticky='nsew')
        self.detail_area.bind_wheel(content)
        content.columnconfigure(0, weight=1)
        for index in range(2):
            content.rowconfigure(index, weight=1, uniform='history-detail')
        self.detail_boxes = []
        for index, (title, value, hint) in enumerate((
                ('Original', original, source),
                ('Translation', item.get('translated', ''), lang_id.output_hint(source, item.get('target_lang'))))):
            card = ui.Card(content, padding=12)
            card.grid(row=index, column=0, sticky='nsew', pady=(0, self.ctx.px(8)))
            heading = ui.Label(card, title, kind='strong')
            heading.pack(anchor='w')
            self.detail_area.bind_wheel(card)
            self.detail_area.bind_wheel(heading)
            holder = ui.Frame(card)
            holder.pack(fill='both', expand=True)
            box = RubyText(holder, width=1, height=2, wrap='word', base_font=self.ctx.font('body'))
            p = self.ctx.palette
            box.recolour(bg=p.surface, base_fg=p.text, kanji_fg=p.text, ruby_fg=p.ruby, selection=p.selection)
            scrollbar = ui.ThinScrollbar(holder, command=box.yview)
            scrollbar.pack(side='right', fill='y')
            box.configure(yscrollcommand=scrollbar.set)
            box.pack(side='left', fill='both', expand=True)
            insert_output(box, '1.0', value, lang_hint=hint, enabled=self.furigana_enabled)
            box.configure(state='disabled')
            box.bind('<FocusIn>', lambda event, current=box: self.detail_area.reveal(current), add='+')
            self.detail_boxes.append(box)
        if self.config is not None:
            from src.ui.main_linked_reading import ReadonlyLinkedReading
            from src.core.translation_alignment import structural_alignment
            result = self.history_manager.reading_result(item)
            metadata = getattr(result, 'alignment', None)
            if metadata is None and not item.get('alignment') and item.get('source_type', 'text') in ('text', 'merged'):
                metadata = structural_alignment(original, str(result), item.get('target_lang', ''),
                                                item.get('source_type', 'text'))
            self.detail_links = ReadonlyLinkedReading(
                self.parent._root(), frame, *self.detail_boxes, self.config, header, metadata)

    def _close_detail_links(self):
        if self.detail_links is not None:
            self.detail_links.close()
            self.detail_links = None

    def _delete_item(self, item):
        """Delete item."""
        if item is None:
            return
        self._preview_cache.clear()
        self.history_manager.delete_entry(item.get('id'))
        self.all_history = [h for h in self.all_history if h.get('id') != item.get('id')]
        self._refresh_pairs()
        self._filter_and_display()

    def _clear_all(self):
        if self.confirmation is not None and self.confirmation.exists:
            self.confirmation.window.lift()
            return self.confirmation
        question = ui.Dialog(self.window, 'Clear all history?',
                             'This removes every saved translation. This cannot be undone.', icon='trash')
        question.add_button('Clear all', kind='danger', command=self._confirm_clear)
        question.add_button('Cancel', default=True)
        self.confirmation = question.show()
        return question

    def _confirm_clear(self):
        self._preview_cache.clear()
        self.history_manager.clear_history()
        self.all_history = []
        self._clear_filters()
