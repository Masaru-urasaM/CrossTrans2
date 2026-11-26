"""Collect cycles on the Tcl-owning thread while a GUI interpreter is alive."""
import gc
import queue
import threading
import time

_owner = None


class Collector:
    def __init__(self, root):
        self.root = root
        self.thread = threading.get_ident()
        self.pending = queue.Queue()
        self.enabled_before = gc.isenabled()
        self.closed = False
        self.last_full = time.monotonic()
        gc.disable()
        self.job = root.after(1000, self.tick)
        root.bind('<Destroy>', self.destroyed, add='+')

    def tick(self):
        self.job = None
        try:
            full = self.closed or not self.pending.empty() or time.monotonic() - self.last_full >= 60
            gc.collect(2 if full else 0)
            if full:
                self.last_full = time.monotonic()
        finally:
            while not self.pending.empty():
                try:
                    self.pending.get_nowait().set()
                except queue.Empty:
                    break
            if not self.closed:
                self.job = self.root.after(1000, self.tick)

    def destroyed(self, event):
        global _owner
        if event.widget is not self.root:
            return
        self.closed = True
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None
        self.tick()
        if self.enabled_before:
            gc.enable()
        if _owner is self:
            _owner = None


def install(root):
    global _owner
    if _owner is None or _owner.closed:
        _owner = Collector(root._root())
    return _owner


def collect():
    """Workers request collection without calling Tk; CLI callers collect directly."""
    owner = _owner
    if owner is None or owner.closed or threading.get_ident() == owner.thread:
        return gc.collect()
    finished = threading.Event()
    owner.pending.put(finished)
    finished.wait(2.0)
    return 0
