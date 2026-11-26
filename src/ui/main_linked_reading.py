"""Linked reading for an editable plain input and a read-only translation."""
import tkinter as tk
from copy import deepcopy

from src.core.translation_alignment import AlignedText, attach_alignment, make_metadata, validate_metadata
from src.ui import components as ui
from src.ui.linked_reading import LinkedReadingController
from src.ui.ruby_text import RubyText


class PlainTextLinkAdapter:
    """Share canonical mapping/painting without changing the editable Tk widget."""
    prepare_plain_map = RubyText.prepare_plain_map
    plain_offset_at = RubyText.plain_offset_at
    highlight_plain_range = RubyText.highlight_plain_range
    see_plain_range = RubyText.see_plain_range
    _cancel_link_animation = RubyText._cancel_link_animation
    _fade_link_background = RubyText._fade_link_background

    def __init__(self, widget):
        self.widget = widget
        self._plain_map = None
        self._window_base = {}
        self._linked_frames = set()
        self._linked_bg = None
        self._linked_animation = None

    def __getattr__(self, name):
        return getattr(self.widget, name)

    def get_plain(self):
        return self.widget.get('1.0', 'end-1c')

    def linked_event_parts(self):
        return [self.widget]

    def _paint_link_frames(self, _keys):
        pass


class MainLinkedReading:
    def __init__(self, root, window, source, translated, config, toolbar):
        self.root, self.window, self.config = root, window, config
        self.source = PlainTextLinkAdapter(source)
        self.translated = translated
        self.controller = None
        self.result = ''
        self.source_snapshot = ''
        self.closed = False
        self.reading_active = True
        # An icon that is a switch (lit while Linked reading is on) with what it found beside it;
        # both follow the theme by themselves
        self.button = ui.IconButton(toolbar, 'link-45deg', command=self.toggle, label='Linked reading')
        self.button.pack(side=tk.LEFT)
        self.status = ui.Label(toolbar, '', kind='muted')
        self.status.pack(side=tk.LEFT, padx=(ui.get().px(8), 0))
        toolbar.bind('<Configure>', lambda event: self._fit_status(toolbar), add='+')
        self.setting_binding = root.bind('<<LinkedReadingChanged>>', self.sync, add='+')
        self.modified_binding = source.bind('<<Modified>>', self.modified, add='+')
        window.bind('<Destroy>', self.destroyed, add='+')
        self.sync()

    def _fit_status(self, toolbar):
        room = max(ui.get().px(120), min(ui.get().px(240), toolbar.master.winfo_width() // 3))
        if int(float(str(self.status.cget('wraplength')) or 0)) != room:
            self.status.configure(wraplength=room, justify='right')

    def enabled(self):
        getter = getattr(self.config, 'get_linked_reading_enabled', None)
        return getter is not None and getter() is True

    def toggle(self):
        self.reading_active = not self.reading_active
        self.sync()

    def stop(self):
        if self.controller is not None:
            self.controller.close()
            self.controller = None

    def set_result(self, result, source, target):
        self.stop()
        self.source_snapshot = source
        self.result = attach_alignment(result, getattr(result, 'alignment', None), source, target)
        live = self.source.get_plain()
        data = getattr(self.result, 'alignment', None)
        if data and live != source and live.strip() == source.strip():
            # Translation requests trim outside whitespace; keep the editable
            # input untouched and rebase only the displayed source coordinates.
            shift = (len(live) - len(live.lstrip())) - (len(source) - len(source.lstrip()))
            groups = deepcopy(data['groups'])
            for group in groups:
                group['source'] = [offset + shift for offset in group['source']]
            groups[0]['source'][0], groups[-1]['source'][1] = 0, len(live)
            rebased = make_metadata(live, self.result, target, data['mode'], groups)
            if data.get('method'):
                rebased['method'] = data['method']
                rebased['structure_version'] = data.get('structure_version')
            if validate_metadata(rebased, live, self.result, target):
                self.source_snapshot, self.result = live, AlignedText(self.result, rebased)
        self.source._plain_map = None
        self.sync()

    def modified(self, _event=None):
        self.source.edit_modified(False)
        self.source._plain_map = None
        self.sync()

    def sync(self, _event=None):
        if self.closed:
            return
        visible = self.enabled() and getattr(self.config, 'get_show_linked_reading', lambda: True)() is True
        enabled = visible and self.reading_active
        if visible:
            self.button.pack(side=tk.LEFT, fill='y', before=self.status)
        else:
            self.button.pack_forget()
        self.button.set_on(enabled)
        self.button.tooltip.text = ('Linked reading: On. Point at a sentence to see its counterpart.'
                                    if enabled else 'Linked reading: Off. Click to turn it on.')
        data = getattr(self.result, 'alignment', None)
        exact_pair = (self.source.get_plain() == self.source_snapshot
                      and self.translated.get_plain() == self.result)
        self.button.configure(state='normal' if data and exact_pair else 'disabled')
        self.status.configure(text=(('Approximate links' if data.get('method') == 'structure'
                                     else 'Sentence links') if data else 'No reliable links')
                              if enabled and exact_pair and self.result else '')
        if not enabled or not exact_pair or not data:
            self.stop()
        elif self.controller is None:
            self.controller = LinkedReadingController(
                self.root, self.source, self.translated, data,
                suspended=lambda: bool(self.source.edit_modified()))

    def destroyed(self, event):
        if event.widget is self.window:
            self.close()

    def close(self):
        if self.closed:
            return
        self.stop()
        self.closed = True
        self.root.unbind('<<LinkedReadingChanged>>', self.setting_binding)
        try:
            self.source.unbind('<<Modified>>', self.modified_binding)
        except tk.TclError:
            pass


class ReadonlyLinkedReading:
    """Saved or conservative local links for a History detail, owned by its frame."""

    def __init__(self, root, window, source, translated, config, toolbar, metadata):
        self.root, self.window, self.config = root, window, config
        self.source, self.translated, self.metadata = source, translated, metadata
        self.controller = None
        self.closed, self.active = False, True
        self.button = ui.IconButton(toolbar, 'link-45deg', command=self.toggle, label='Linked reading')
        self.status = ui.Label(toolbar, '', kind='caption', wraplength=ui.get().px(180))
        self.setting_binding = root.bind('<<LinkedReadingChanged>>', self.sync, add='+')
        window.bind('<Destroy>', self.destroyed, add='+')
        self.sync()

    def toggle(self):
        self.active = not self.active
        self.sync()

    def sync(self, event=None):
        if self.closed:
            return
        enabled = self.config.get_linked_reading_enabled() is True
        visible = enabled and self.config.get_show_linked_reading() is True
        if visible:
            self.button.pack(side='right')
        else:
            self.button.pack_forget()
        self.button.configure(state='normal' if self.metadata else 'disabled')
        self.button.set_on(enabled and self.active and bool(self.metadata))
        message = ('Approximate links' if self.metadata and self.metadata.get('method') == 'structure'
                   else 'Sentence links' if self.metadata else 'No reliable links')
        self.status.configure(text=message)
        if enabled and self.active:
            self.status.pack(side='right', padx=ui.get().px(8))
        else:
            self.status.pack_forget()
        if enabled and self.active and self.metadata:
            if self.controller is None:
                self.controller = LinkedReadingController(self.root, self.source, self.translated, self.metadata)
        elif self.controller is not None:
            self.controller.close()
            self.controller = None

    def destroyed(self, event):
        if event.widget is self.window:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.controller is not None:
            self.controller.close()
            self.controller = None
        self.root.unbind('<<LinkedReadingChanged>>', self.setting_binding)
