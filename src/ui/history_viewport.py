"""A bounded, reusable card viewport for the saved History list."""
from src.ui import components as ui
from src.utils.ui_helpers import schedule_window_callback, cancel_window_callback

PAGE_SIZE = 12
MAX_CARDS = 36


class HistoryViewport(ui.ScrollArea):
    def __init__(self, parent, create_row, bind_row):
        self.items = []
        self.pool = []
        self.bound = {}
        self.prepared = 0
        self._buffer_start = self._buffer_count = 0
        self._render_job = self._prime_job = self._measure_job = None
        self._closed = False
        self._rendering = False
        self._create_row, self._bind_row = create_row, bind_row
        super().__init__(parent)
        self.pitch = ui.get().px(156)
        self.bind('<Map>', self._mapped, add='+')
        self.bind('<Unmap>', self._unmapped, add='+')

    @property
    def rows(self):
        return [self.bound[index] for index in sorted(self.bound)]

    @property
    def pending(self):
        return self._render_job or self._prime_job or self._measure_job

    def set_items(self, items):
        self.cancel_pending()
        self.items = list(items)
        self.prepared = min(PAGE_SIZE, len(self.items))
        self._buffer_start, self._buffer_count = 0, self.prepared
        self.bound.clear()
        for row in self.pool:
            row.place_forget()
            row.history_index = None
            row.entry = None
        if not self.items:
            self.canvas.coords(self._window, 0, 0)
            self.content.pack_propagate(True)
            self.content.configure(height=1)
            self.canvas.yview_moveto(0)
            return
        self.content.pack_propagate(False)
        self._extent()
        self.canvas.yview_moveto(0)
        self._render(immediate=True)
        self._prime_job = schedule_window_callback(self._timer_root, self, 30, self._prime)

    def _extent(self):
        height = max(1, self._buffer_count * self.pitch)
        if int(self.content.cget('height')) != height:
            self.content.configure(height=height)
        position = [0.0, float(self._buffer_start * self.pitch)]
        if self.canvas.coords(self._window) != position:
            self.canvas.coords(self._window, *position)
        bounds = (0, 0, max(1, self.canvas.winfo_width()), max(1, len(self.items) * self.pitch))
        current = tuple(map(float, self.canvas.tk.splitlist(self.canvas.cget('scrollregion'))))
        if current != bounds:
            self.canvas.configure(scrollregion=bounds)

    def _sync_layout(self):
        self._layout_job = None
        if not self.canvas.winfo_exists() or not self.content.winfo_exists():
            return
        width = max(1, self.canvas.winfo_width())
        if width != self._layout_width:
            self._layout_width = width
            self.canvas.itemconfigure(self._window, width=width)
        if self.items:
            self._extent()
        else:
            bounds = self.canvas.bbox('all')
            if bounds:
                self.canvas.configure(scrollregion=bounds)

    def _prime(self):
        self._prime_job = None
        self.prepared = min(len(self.items), max(self.prepared, PAGE_SIZE * 2))
        self.request_render()

    def _on_scroll(self, first, last):
        super()._on_scroll(first, last)
        if hasattr(self, 'canvas'):
            self.request_render()

    def request_render(self):
        if (not self._closed and self.items and self._render_job is None
                and not self._rendering and self.winfo_ismapped()):
            self._render_job = schedule_window_callback(self._timer_root, self, 16, self._render)

    def _mapped(self, event):
        if event.widget is self:
            if self.items and self.prepared <= PAGE_SIZE and self._prime_job is None:
                self._prime_job = schedule_window_callback(self._timer_root, self, 30, self._prime)
            self.request_render()

    def _unmapped(self, event):
        if event.widget is self:
            self.cancel_pending()

    def _render(self, immediate=False):
        self._render_job = None
        if self._closed or not self.items or (not immediate and not self.winfo_ismapped()):
            return
        self.pool = [row for row in self.pool if row.winfo_exists()]
        self.bound = {index: row for index, row in self.bound.items() if row.winfo_exists()}
        first = max(0, min(len(self.items) - 1, int(self.canvas.canvasy(0) // self.pitch)))
        if first >= PAGE_SIZE:
            self.prepared = min(len(self.items), max(self.prepared, (first // PAGE_SIZE + 2) * PAGE_SIZE))
        visible = max(1, self.canvas.winfo_height() // self.pitch + 2)
        above = min(PAGE_SIZE, max(0, (MAX_CARDS - visible) // 2))
        start = max(0, min(first - above, self.prepared - MAX_CARDS))
        stop = min(len(self.items), self.prepared, start + MAX_CARDS)
        self._buffer_start, self._buffer_count = start, stop - start
        self._extent()
        wanted = set(range(start, stop))
        focused = self.focus_get()
        for index in set(self.bound) - wanted:
            row = self.bound.pop(index)
            if focused is row or (focused and str(focused).startswith(str(row) + '.')):
                self.canvas.focus_set()
            row.place_forget()
            row.history_index = None
        free = [row for row in self.pool if row.history_index is None]
        made = 0
        self._rendering = True
        try:
            order = [*range(max(start, first), stop), *range(min(first, stop) - 1, start - 1, -1)]
            for index in order:
                if index in self.bound:
                    continue
                if not immediate and made >= 4:
                    break
                if free:
                    row = free.pop(0)
                    self._bind_row(row, self.items[index])
                elif len(self.pool) < MAX_CARDS:
                    row = self._create_row(self.items[index])
                    row.pack_forget()
                    self.pool.append(row)
                else:
                    break
                row.history_index = index
                self.bound[index] = row
                made += 1
            for index, row in self.bound.items():
                row.place(x=0, y=(index - start) * self.pitch, relwidth=1,
                          height=max(1, self.pitch - ui.get().px(8)))
            self.measure_later()
        finally:
            self._rendering = False
        if wanted - set(self.bound):
            self.request_render()

    def measure_later(self):
        if not self._closed and self.items and self._measure_job is None:
            self._measure_job = schedule_window_callback(self._timer_root, self, 'idle', self._measure)

    def _measure(self):
        self._measure_job = None
        rows = [row for row in self.rows if row.winfo_exists()]
        if not rows:
            return
        pitch = max(row.winfo_reqheight() for row in rows) + ui.get().px(8)
        if pitch <= ui.get().px(40) or pitch == self.pitch:
            return
        anchor = max(0, self.canvas.canvasy(0)) / self.pitch
        self.pitch = pitch
        self._extent()
        self.canvas.yview_moveto(anchor / len(self.items))
        self.request_render()

    def show_index(self, index):
        if not self.items:
            return None
        index = max(0, min(index, len(self.items) - 1))
        self.prepared = min(len(self.items), max(self.prepared, (index // PAGE_SIZE + 2) * PAGE_SIZE))
        top = self.canvas.canvasy(0)
        if index * self.pitch < top or (index + 1) * self.pitch > top + self.canvas.winfo_height():
            self.canvas.yview_moveto(index / len(self.items))
        if self._render_job is not None:
            cancel_window_callback(self._timer_root, self, self._render_job)
            self._render_job = None
        self._render(immediate=True)
        return self.bound.get(index)

    def reveal(self, widget, margin=0):
        self.update_idletasks()
        total = len(self.items) * self.pitch
        if not total:
            return
        top = widget.winfo_rooty() - self.content.winfo_rooty() + self._buffer_start * self.pitch
        bottom = top + widget.winfo_height()
        first = self.canvas.canvasy(0)
        if top - margin >= first and bottom + margin <= first + self.canvas.winfo_height():
            return
        self.canvas.yview_moveto(max(0.0, min(1.0, (top - margin) / total)))

    def cancel_pending(self):
        for name in ('_render_job', '_prime_job', '_measure_job'):
            job = getattr(self, name, None)
            if job is not None:
                cancel_window_callback(self._timer_root, self, job)
                setattr(self, name, None)

    def _on_destroy(self, event=None):
        if event is None or event.widget is self:
            self._closed = True
            self.cancel_pending()
            self.pool.clear()
            self.bound.clear()
        super()._on_destroy(event)
