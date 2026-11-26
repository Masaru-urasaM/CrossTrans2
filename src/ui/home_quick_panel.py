"""Dismissible quick tools drawn inside the Translate workspace."""
import tkinter as tk

from src.ui import components as ui
from src.utils.ui_helpers import schedule_window_callback, cancel_window_callback


class HomeQuickPanel:
    def __init__(self, parent, title, anchor, on_close=None):
        self.parent, self.anchor, self.on_close = parent, anchor, on_close
        self.root, self.top = parent._root(), parent.winfo_toplevel()
        self.closed, self._job = False, None
        self.frame = ui.Card(parent, padding=12)
        self.frame.occludes_underlay = True
        header = ui.Frame(self.frame)
        header.pack(fill='x', pady=(0, ui.get().px(8)))
        ui.IconButton(header, 'x-lg', label='Close', command=self.close).pack(side='right')
        ui.Label(header, title, kind='subtitle').pack(side='left')
        self.content = ui.Frame(self.frame)
        self.content.pack(fill='both', expand=True)
        self._resize = parent.bind('<Configure>', self._schedule_fit, add='+')
        self._unmap = parent.bind('<Unmap>', self._hidden, add='+')
        self._click = self.top.bind('<ButtonPress-1>', self._outside, add='+')
        self.frame.bind('<Destroy>', self._destroyed, add='+')
        self._unsubscribe = ui.get().manager.subscribe(self._schedule_fit)
        self.fit()

    def _hidden(self, event):
        if event.widget is self.parent:
            self.close(restore_focus=False)

    def _schedule_fit(self, event=None):
        if not self.closed and self._job is None:
            self._job = schedule_window_callback(self.root, self.frame, 'idle', self.fit)

    def fit(self):
        self._job = None
        if self.closed:
            return
        px = ui.get().px
        room_w, room_h = self.parent.winfo_width(), self.parent.winfo_height()
        width = max(1, min(px(740), room_w - px(24)))
        height = max(1, min(px(570), room_h - px(24)))
        self.frame.place(x=max(px(12), room_w - width - px(12)), y=px(12), width=width, height=height)
        self.frame.lift()

    def _outside(self, event):
        if self.closed:
            return
        widget = event.widget
        while widget is not None:
            if widget is self.frame or widget is self.anchor:
                return
            widget = getattr(widget, 'master', None)
        self.close(restore_focus=False)

    def _destroyed(self, event):
        if event.widget is self.frame:
            self._cleanup()

    def _cleanup(self):
        if self.closed:
            return
        self.closed = True
        if self._job is not None:
            cancel_window_callback(self.root, self.frame, self._job)
            self._job = None
        self.parent.unbind('<Configure>', self._resize)
        self.parent.unbind('<Unmap>', self._unmap)
        self.top.unbind('<ButtonPress-1>', self._click)
        self._unsubscribe()
        if self.on_close:
            self.on_close()

    def close(self, restore_focus=True):
        if self.closed:
            return
        self._cleanup()
        self.frame.destroy()
        if restore_focus:
            try:
                self.anchor.focus_set()
            except tk.TclError:
                pass
