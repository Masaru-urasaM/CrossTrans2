"""Window-owned fixed content geometry during interactive Windows resizing."""
from collections import OrderedDict
import logging
import tkinter as tk
import weakref
from types import SimpleNamespace

from src.ui.theme.compat import NO_AUTOSTYLE
from src.utils.ui_helpers import schedule_window_callback, cancel_window_callback


_owners = weakref.WeakSet()


def controller_for(widget):
    try:
        owner = getattr(widget.winfo_toplevel(), '_ct_deferred_resize', None) if widget is not None else None
        return owner if isinstance(owner, DeferredContentResize) else None
    except (AttributeError, tk.TclError):
        return None


def defer_update(widget, key, callback):
    """Keep the latest view update for one semantic key, without blocking its worker."""
    owner = controller_for(widget)
    return bool(owner and owner.defer(key, callback))


def defer_global_update(key, callback):
    """Global font/theme changes wait for the current gesture, including its close path."""
    for owner in tuple(_owners):
        if owner.holding:
            return owner.defer(key, callback, survive_close=True)
    return False


class DeferredContentResize:
    """Own the shell/content boundary; native procedures only expose cached state."""
    def __init__(self, window, *, background, size=(1, 1), inset=0,
                 on_start=None, on_commit=None, on_settled=None, editors=None, host=None,
                 outline_colour=None):
        self.window = window
        self.root = window._root()
        self.inset = max(0, int(inset))
        self.on_start, self.on_commit, self.on_settled = on_start, on_commit, on_settled
        self.editors = editors or (lambda: ())
        self.state = 'IDLE'
        self.native = None
        self.outline_colour = outline_colour
        self.owns_native = False
        self.generation = self.commit_count = 0
        self.pending = OrderedDict()
        self.bindings = []
        self._commit_job = self._watch_job = self._restore_job = None
        self._scroll = []
        self._last_client = tuple(max(1, int(value)) for value in size)
        self.host = host if host is not None else tk.Frame(
            window, bg=background, bd=0, highlightthickness=0, **NO_AUTOSTYLE)
        self._place(self._last_client)
        window._ct_deferred_resize = self
        _owners.add(self)
        self.bindings.append(('<Configure>', window.bind('<Configure>', self._configured, add='+')))
        self.bindings.append(('<Destroy>', window.bind('<Destroy>', self._destroyed, add='+')))

    @property
    def holding(self):
        return self.state == 'RESIZING' or bool(self.state != 'CLOSED' and self.native is not None
            and self.native.running and getattr(self.native, 'resizing', False))

    def _place(self, size):
        width, height = size
        self.host.place(x=self.inset, y=self.inset,
            width=max(1, width-2*self.inset), height=max(1, height-2*self.inset),
            relwidth=0, relheight=0, bordermode='ignore')

    def set_background(self, colour):
        if self.state != 'CLOSED':
            self.window.configure(background=colour)
            if 'background' in self.host.keys():
                self.host.configure(background=colour)

    def bind_native(self, bridge, *, owned=False):
        self.native, self.owns_native = bridge, owned

    def set_outline_colour(self, colour):
        if self.state == 'CLOSED':
            return
        if self.defer('outline-colour',lambda:self.set_outline_colour(colour)):
            return
        self.outline_colour = colour
        bridge = self.native
        if bridge is not None and getattr(bridge,'outline_colour',None) != colour:
            if hasattr(bridge,'_hide_outline'):
                bridge._hide_outline(close=True)
                bridge.outline_colour = colour

    def attach_observer(self):
        """Observe the standard decorated Main window without replacing its frame."""
        if self.state == 'CLOSED' or self.native is not None:
            return
        from src.ui.native_resize import WindowsSizing
        rect = (self.window.winfo_x(), self.window.winfo_y(), *self._client())
        bridge = WindowsSizing(self.window, rect=rect, minimum=(1, 1), bounds=None,
            on_rect=lambda _rect: self.observe(), on_start=lambda *_args: self.begin(),
            on_end=self.end, custom_frame=False, outline=True, outline_colour=self.outline_colour)
        try:
            if bridge.attach():
                self.bind_native(bridge, owned=True)
        except (OSError, tk.TclError):
            bridge.close(restore=False)
            logging.exception('[Resize] could not attach the Main sizing observer')

    def _client(self):
        return max(1, self.window.winfo_width()), max(1, self.window.winfo_height())

    def _cancel(self, name):
        job = getattr(self, name)
        if job is not None:
            cancel_window_callback(self.root, self.window, job)
            setattr(self, name, None)

    def begin(self):
        if self.state in ('CLOSED', 'RESIZING'):
            return
        self._cancel('_commit_job')
        self._cancel('_restore_job')
        self.generation += 1
        self.state = 'RESIZING'
        self._scroll = []
        for editor in self.editors():
            if editor is not None:
                try:
                    self._scroll.append((weakref.ref(editor), editor.index('@0,0'), editor.xview()[0]))
                except (AttributeError, tk.TclError):
                    pass
        if self.on_start:
            self.on_start()
        if self.native is not None and self.native.running:
            self._watch_job = schedule_window_callback(self.root, self.window, 50, self._watch_end)

    def _watch_end(self):
        """A gesture-only fallback for a final native message without another Tk expose."""
        self._watch_job = None
        if self.state != 'RESIZING' or self.native is None:
            return
        if not self.native.running:
            self.native._tk_event(SimpleNamespace(widget=self.window))
            self.end()
        else:
            self._watch_job = schedule_window_callback(self.root, self.window, 50, self._watch_end)

    def observe(self):
        if self.state == 'CLOSED':
            return
        if self.holding:
            if self.state != 'RESIZING':
                self.begin()
            return
        size = self._client()
        if size != self._last_client and self.state == 'IDLE':
            self._schedule_commit()

    def _configured(self, event):
        if event.widget is self.window:
            self.observe()

    def end(self):
        if self.state != 'RESIZING':
            return
        self._cancel('_watch_job')
        self.state = 'COMMITTING'
        self._schedule_commit()

    def _schedule_commit(self):
        if self._commit_job is None and self.state != 'CLOSED':
            self._commit_job = schedule_window_callback(self.root, self.window, 'idle', self._commit)

    def sync_size(self, width, height):
        """Initial/programmatic placement can set the host before entering the event loop."""
        if self.state == 'CLOSED' or self.holding:
            return
        self._last_client = max(1, int(width)), max(1, int(height))
        self._place(self._last_client)

    def defer(self, key, callback, *, survive_close=False):
        if not self.holding:
            return False
        self.pending[key] = callback, survive_close
        return True

    def _commit(self):
        self._commit_job = None
        if self.state == 'CLOSED':
            return
        if self.holding:
            return
        was_gesture = self.state == 'COMMITTING'
        self.state = 'COMMITTING'
        generation = self.generation
        size = self._client()
        changed = size != self._last_client
        self._last_client = size
        self._place(size)
        updates, self.pending = self.pending, OrderedDict()
        if changed or was_gesture:
            self.commit_count += 1
            if self.on_commit:
                self.on_commit(*size)
        remaining = list(updates.values())
        for index, (callback, _survive) in enumerate(remaining):
            if self.state == 'CLOSED' or generation != self.generation:
                self._dispatch_survivors(remaining[index:])
                return
            try:
                callback()
            except Exception:
                logging.exception('[Resize] deferred view update failed')
        if self.state == 'CLOSED' or generation != self.generation:
            return
        self.state = 'IDLE'
        if updates:
            self._scroll.clear()
        self._restore_job = schedule_window_callback(self.root, self.window, 35, self._settled)

    def _settled(self):
        self._restore_job = None
        if self.state != 'IDLE' or self.holding:
            return
        for reference, top, horizontal in self._scroll:
            editor = reference()
            try:
                if editor is not None and editor.winfo_exists():
                    editor.yview(top)
                    editor.xview_moveto(horizontal)
            except tk.TclError:
                pass
        self._scroll.clear()
        if self.on_settled:
            self.on_settled()

    def _destroyed(self, event):
        if event.widget is self.window:
            self.close(destroying=True)

    def _dispatch_survivors(self, updates):
        for callback, survive in updates:
            if survive:
                try:
                    self.root.after(0, callback)
                except tk.TclError:
                    pass

    def close(self, *, destroying=False):
        if self.state == 'CLOSED':
            return
        self.state = 'CLOSED'
        self.generation += 1
        for name in ('_commit_job', '_watch_job', '_restore_job'):
            self._cancel(name)
        if self.owns_native and self.native is not None:
            self.native.close(restore=False, destroying=destroying)
        self.native = None
        for sequence, binding in self.bindings:
            if destroying and sequence == '<Destroy>':
                continue
            try:
                self.window.unbind(sequence, binding)
            except tk.TclError:
                pass
        self.bindings.clear()
        _owners.discard(self)
        pending, self.pending = self.pending, OrderedDict()
        self._scroll.clear()
        self._dispatch_survivors(pending.values())
        self.on_start = self.on_commit = self.on_settled = None
