"""Window-owned pointer sampling for a nonactivating client resize gesture."""
import logging
from typing import NamedTuple

from src.utils.ui_helpers import schedule_window_callback, cancel_window_callback


class PointerState(NamedTuple):
    x: int
    y: int
    pressed: bool
    escape: bool = False


class PointerResize:
    """Read pointer state only while held; never inject input or change focus."""
    def __init__(self, root, window, reader, *, on_move, on_release, on_cancel,
                 interval=16):
        self.root, self.window, self.reader = root, window, reader
        self.on_move, self.on_release, self.on_cancel = on_move, on_release, on_cancel
        self.interval = interval
        self.active = False
        self.job = None
        self.position = None

    def start(self):
        state = self.reader()
        if state is None or not state.pressed or state.escape:
            return False
        self.active = True
        self._schedule()
        return True

    def _schedule(self):
        if self.active:
            self.job = schedule_window_callback(self.root, self.window, self.interval, self._tick)

    def _tick(self):
        self.job = None
        if not self.active:
            return
        try:
            state = self.reader()
            if state is None or state.escape:
                self.stop()
                self.on_cancel()
                return
            position = state.x, state.y
            if position != self.position:
                self.position = position
                self.on_move(*position)
            if not self.active:
                return
            if not state.pressed:
                self.stop()
                self.on_release()
                return
            self._schedule()
        except Exception:
            self.stop()
            logging.exception('[Resize] pointer tracking failed')
            self.on_cancel()

    def stop(self):
        self.active = False
        if self.job is not None:
            cancel_window_callback(self.root, self.window, self.job)
            self.job = None
