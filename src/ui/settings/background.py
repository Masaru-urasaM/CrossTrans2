"""
Runs slow work (a key test, a language pack install) off the UI thread (TODO.md U8, steps 4-5).

The old Test button called `window.update()` from inside a loop that talked to the network, and the
trial check called `window.after()` from a worker thread. Here a job runs on its own thread and only
puts messages on a queue; the UI thread drains the queue on a timer and calls the page back. A worker
never touches Tk (Decision 43).

    runner = BackgroundRunner(widget)
    runner.start('row-1', lambda progress, cancelled: work(...), on_progress=show, on_done=finish)

`work(progress, cancelled)` returns the result; `progress(*args)` and `cancelled()` are safe to call from
the worker. `on_progress(*args)` and `on_done(result, error)` run on the UI thread - `error` is the
exception the work raised (then `result` is None). A job that was cancelled, or whose widget was
destroyed, never calls back.
"""
import logging
import gc
import queue
import threading
import tkinter as tk
from typing import Any, Callable, Dict, Optional

POLL_MS = 50


class _Job:
    def __init__(self, on_progress, on_done):
        self.messages: 'queue.Queue' = queue.Queue()
        self.cancel = threading.Event()
        self.on_progress, self.on_done = on_progress, on_done
        self.thread: Optional[threading.Thread] = None
        self.waiting_done = False


class BackgroundRunner:
    def __init__(self, widget, poll_ms: int = POLL_MS):
        self.widget, self.poll_ms = widget, poll_ms
        self._ui_thread = threading.get_ident()
        self._jobs: Dict[str, _Job] = {}
        self._pump_job: Optional[str] = None
        self._closed = False
        widget.bind('<Destroy>', self._on_destroy, add='+')

    # --- starting and stopping -------------------------------------------------------------------
    def start(self, tag: str, work: Callable[[Callable, Callable[[], bool]], Any],
              on_progress: Optional[Callable] = None, on_done: Optional[Callable] = None) -> None:
        """Start `work` under `tag`. A job already running under the tag is cancelled first (its
        callbacks never run) - Test pressed twice is one test, the second."""
        if self._closed:
            return
        if threading.get_ident() != self._ui_thread:
            raise RuntimeError('BackgroundRunner.start must run on the UI thread')
        # Release unreachable Tk cycles on their owning thread before allocating on a worker.
        gc.collect()
        self.cancel(tag)
        job = _Job(on_progress, on_done)

        def progress(*args):
            job.messages.put(('progress', args))

        def run():
            try:
                job.messages.put(('done', (work(progress, job.cancel.is_set), None)))
            except Exception as error:
                logging.exception(f"[Settings] background work '{tag}' failed")
                job.messages.put(('done', (None, error)))

        job.thread = threading.Thread(target=run, daemon=True, name=f'Work-{tag}')
        self._jobs[tag] = job
        job.thread.start()
        self._schedule()

    def cancel(self, tag: Optional[str] = None) -> None:
        """Ask a job (every job, without a tag) to stop and forget its callbacks."""
        for name in ([tag] if tag is not None else list(self._jobs)):
            job = self._jobs.pop(name, None)
            if job is not None:
                job.cancel.set()

    def busy(self, tag: Optional[str] = None) -> bool:
        return tag in self._jobs if tag is not None else bool(self._jobs)

    # --- the UI thread's side --------------------------------------------------------------------
    def _schedule(self) -> None:
        if (self._pump_job is None and not self._closed
                and any(not job.waiting_done for job in self._jobs.values())):
            self._pump_job = self.widget.after(self.poll_ms, self._pump)

    def _pump(self) -> None:
        self._pump_job = None
        for tag, job in list(self._jobs.items()):
            if job.waiting_done:
                continue
            while True:
                try:
                    kind, payload = job.messages.get_nowait()
                except queue.Empty:
                    break
                if self._jobs.get(tag) is not job:
                    break                                    # cancelled while we were reading
                if kind == 'progress':
                    self._deliver(tag, job, 'progress', job.on_progress, payload)
                else:
                    self._deliver(tag, job, 'done', job.on_done, payload)
                    break
        self._schedule()

    def _deliver(self, tag, job, kind, callback, payload):
        owner = self.widget
        def apply():
            if self._closed or self._jobs.get(tag) is not job:
                return
            try:
                exists = owner is not None and owner.winfo_exists()
            except tk.TclError:
                exists = False
            if not exists:
                self.cancel(tag)
                return
            if kind == 'done':
                self._jobs.pop(tag, None)
            self._call(callback, *payload)
        if callback is not None or (kind == 'done' and job.on_progress is not None):
            from src.ui.deferred_resize import defer_update
            if defer_update(owner, ('background', id(self), tag, kind), apply):
                if kind == 'done':
                    job.waiting_done = True
                return
        apply()

    @staticmethod
    def _call(callback: Optional[Callable], *args) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except tk.TclError:
            pass                                             # the widget it drew on is gone
        except Exception:
            logging.exception("[Settings] a background result could not be shown")

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self.widget:
            return
        self._closed = True
        self.cancel()
        if self._pump_job is not None:
            try:
                self.widget.after_cancel(self._pump_job)
            except (tk.TclError, ValueError):
                pass
            self._pump_job = None
