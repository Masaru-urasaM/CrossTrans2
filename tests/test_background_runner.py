"""
Tests for src/ui/settings/background.py (TODO.md U8, step 4): background work whose results reach the UI
thread through a queue the UI thread drains - and whose worker never touches Tk (Decision 43).
"""
import logging
import threading
import time
import tkinter as tk

import pytest

from src.ui.settings.background import BackgroundRunner


def pump(root, predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        root.update()
        time.sleep(0.003)
    return predicate()


@pytest.fixture
def frame(tk_root):
    widget = tk.Frame(tk_root)
    widget.pack()
    yield widget
    if widget.winfo_exists():
        widget.destroy()


@pytest.fixture
def runner(frame):
    return BackgroundRunner(frame, poll_ms=5)


@pytest.fixture
def held_runner(tk_root):
    from src.ui.deferred_resize import DeferredContentResize
    window = tk.Toplevel(tk_root)
    window.geometry('600x400')
    owner = DeferredContentResize(window,background='#ffffff',size=(600,400))
    frame = tk.Frame(owner.host)
    frame.pack(fill='both',expand=True)
    label = tk.Label(frame,text='Original status')
    label.pack()
    runner = BackgroundRunner(frame,poll_ms=5)
    tk_root.update()
    owner.begin()
    try:
        yield owner,frame,label,runner
    finally:
        if window.winfo_exists():
            window.destroy()
        tk_root.update()


@pytest.mark.parametrize('with_done',[False,True])
def test_held_progress_is_coalesced_and_completed_work_does_not_keep_polling(held_runner,tk_root,with_done):
    owner,_frame,label,runner = held_runner
    completed = threading.Event()
    events = []
    def work(progress,cancelled):
        for step in range(100):
            progress(str(step))
        completed.set()
        return 'Ready'
    def progress(value):
        label.configure(text=value)
        events.append(value)
    runner.start('probe',work,on_progress=progress,
        on_done=(lambda value,error:events.append(value)) if with_done else None)
    assert pump(tk_root,lambda:completed.is_set() and runner._jobs['probe'].waiting_done)
    assert events == [] and label.cget('text') == 'Original status'
    assert runner._pump_job is None and len(owner.pending)==2
    owner.end()
    assert pump(tk_root,lambda:not runner.busy())
    assert events == (['99','Ready'] if with_done else ['99']) and label.cget('text')=='99'


def test_replaced_held_job_cannot_publish_its_old_progress_or_result(held_runner,tk_root):
    owner,_frame,_label,runner = held_runner
    events = []
    runner.start('probe',lambda progress,cancelled:progress('old') or 'old result',
        on_progress=events.append,on_done=lambda value,error:events.append(value))
    assert pump(tk_root,lambda:runner._jobs['probe'].waiting_done)
    runner.start('probe',lambda progress,cancelled:'new result',
        on_done=lambda value,error:events.append(value))
    assert pump(tk_root,lambda:runner._jobs['probe'].waiting_done)
    owner.end()
    assert pump(tk_root,lambda:not runner.busy())
    assert events == ['new result']


def test_destroyed_settings_frame_discards_held_result_and_releases_job(held_runner,tk_root):
    owner,frame,_label,runner = held_runner
    events = []
    runner.start('probe',lambda progress,cancelled:'obsolete',
        on_done=lambda value,error:events.append(value))
    assert pump(tk_root,lambda:runner._jobs['probe'].waiting_done)
    frame.destroy()
    owner.end()
    assert pump(tk_root,lambda:owner.state=='IDLE')
    assert events == [] and not runner.busy() and runner._pump_job is None


class TestRunning:
    def test_the_work_runs_off_the_ui_thread_and_the_callbacks_on_it(self, runner, tk_root):
        owner = threading.get_ident()
        seen = {}

        def work(progress, cancelled):
            seen['worker'] = threading.get_ident()
            progress(1, 2)
            return 'result'
        events = []
        runner.start('a', work,
                     on_progress=lambda *a: events.append(('progress', a, threading.get_ident())),
                     on_done=lambda r, e: events.append(('done', (r, e), threading.get_ident())))
        assert pump(tk_root, lambda: len(events) == 2)
        assert seen['worker'] != owner
        assert events == [('progress', (1, 2), owner), ('done', ('result', None), owner)]

    def test_the_worker_never_calls_tk_after(self, runner, tk_root, monkeypatch):
        owner = threading.get_ident()
        original = tk.Misc.after

        def ui_after(self, *args, **kwargs):
            assert threading.get_ident() == owner, 'a worker called Tk.after'
            return original(self, *args, **kwargs)
        monkeypatch.setattr(tk.Misc, 'after', ui_after)
        done = []
        runner.start('a', lambda progress, cancelled: [progress(i) for i in range(5)] and 'ok',
                     on_done=lambda r, e: done.append(r))
        assert pump(tk_root, lambda: done) and done == ['ok']

    def test_progress_arrives_in_order_before_done(self, runner, tk_root):
        log = []

        def work(progress, cancelled):
            for i in range(6):
                progress(i)
            return 'end'
        runner.start('a', work, on_progress=lambda i: log.append(i), on_done=lambda r, e: log.append(r))
        assert pump(tk_root, lambda: log and log[-1] == 'end')
        assert log == [0, 1, 2, 3, 4, 5, 'end']

    def test_an_exception_in_the_work_is_delivered_not_raised(self, runner, tk_root, caplog):
        got = []
        with caplog.at_level(logging.ERROR):
            runner.start('a', lambda progress, cancelled: 1 / 0, on_done=lambda r, e: got.append((r, e)))
            assert pump(tk_root, lambda: got)
        assert got[0][0] is None and isinstance(got[0][1], ZeroDivisionError)
        assert "background work 'a' failed" in caplog.text

    def test_two_jobs_with_different_tags_both_finish(self, runner, tk_root):
        done = []
        runner.start('a', lambda p, c: 'A', on_done=lambda r, e: done.append(r))
        runner.start('b', lambda p, c: 'B', on_done=lambda r, e: done.append(r))
        assert pump(tk_root, lambda: len(done) == 2) and sorted(done) == ['A', 'B']

    def test_busy_is_true_only_while_a_job_runs(self, runner, tk_root):
        release = threading.Event()
        done = []
        assert not runner.busy() and not runner.busy('a')
        runner.start('a', lambda p, c: release.wait(2) or 'x', on_done=lambda r, e: done.append(r))
        assert runner.busy() and runner.busy('a') and not runner.busy('b')
        release.set()
        assert pump(tk_root, lambda: done)
        assert not runner.busy() and not runner.busy('a')

    def test_the_timer_stops_when_nothing_is_left(self, runner, tk_root):
        done = []
        runner.start('a', lambda p, c: 1, on_done=lambda r, e: done.append(r))
        assert pump(tk_root, lambda: done)
        pump(tk_root, lambda: False, 0.05)
        assert runner._pump_job is None

    def test_a_job_without_callbacks_is_fine(self, runner, tk_root):
        runner.start('a', lambda p, c: 1)
        assert pump(tk_root, lambda: not runner.busy())


class TestCancelling:
    def test_starting_the_same_tag_again_drops_the_first_ones_callbacks(self, runner, tk_root):
        release_first = threading.Event()
        first_started = threading.Event()
        log = []

        def first(progress, cancelled):
            first_started.set()
            release_first.wait(2)
            progress('first')
            return 'first'
        runner.start('row', first, on_progress=lambda *a: log.append(a), on_done=lambda r, e: log.append(r))
        assert first_started.wait(2)
        runner.start('row', lambda p, c: 'second', on_done=lambda r, e: log.append(r))
        release_first.set()
        assert pump(tk_root, lambda: 'second' in log)
        pump(tk_root, lambda: False, 0.1)
        assert log == ['second']

    def test_starting_the_same_tag_again_the_first_one_is_told_to_stop(self, runner, tk_root):
        started, release, told = threading.Event(), threading.Event(), []

        def first(progress, cancelled):
            started.set()
            release.wait(2)
            told.append(cancelled())
        runner.start('row', first)
        assert started.wait(2)
        runner.start('row', lambda progress, cancelled: None)
        release.set()
        assert pump(tk_root, lambda: bool(told))
        assert told == [True]

    def test_a_job_cancelled_while_it_was_being_read_calls_back_no_more(self, frame, tk_root):
        """A progress callback may cancel its own job: what is queued behind it is not delivered."""
        runner = BackgroundRunner(frame, poll_ms=1000)       # pumped by hand, once everything is queued
        log = []

        def work(progress, cancelled):
            for step in range(3):
                progress(step)
            return 'done'

        def on_progress(step):
            log.append(step)
            runner.cancel('a')
        runner.start('a', work, on_progress=on_progress, on_done=lambda result, error: log.append(result))
        runner._jobs['a'].thread.join(2)
        runner._pump()
        assert log == [0] and not runner.busy('a')

    def test_the_work_is_told_it_was_cancelled(self, runner, tk_root):
        started, flags, finished = threading.Event(), [], threading.Event()

        def work(progress, cancelled):
            started.set()
            for _ in range(200):
                if cancelled():
                    flags.append(True)
                    break
                time.sleep(0.005)
            finished.set()
        runner.start('a', work)
        assert started.wait(2)
        runner.cancel('a')
        assert finished.wait(2) and flags == [True]

    def test_cancel_by_tag_leaves_the_others(self, runner, tk_root):
        release = threading.Event()
        done = []
        runner.start('a', lambda p, c: (release.wait(2), 'A')[1], on_done=lambda r, e: done.append(r))
        runner.start('b', lambda p, c: (release.wait(2), 'B')[1], on_done=lambda r, e: done.append(r))
        runner.cancel('a')
        release.set()
        assert pump(tk_root, lambda: done)
        pump(tk_root, lambda: False, 0.1)
        assert done == ['B']

    def test_cancel_without_a_tag_cancels_every_job(self, runner, tk_root):
        release = threading.Event()
        done = []
        for tag in 'ab':
            runner.start(tag, lambda p, c: release.wait(2) or 1, on_done=lambda r, e: done.append(r))
        runner.cancel()
        release.set()
        pump(tk_root, lambda: False, 0.15)
        assert done == [] and not runner.busy()

    def test_cancelling_an_unknown_tag_is_harmless(self, runner):
        runner.cancel('nothing')

    def test_destroying_the_widget_cancels_everything_and_stops_the_timer(self, frame, tk_root):
        runner = BackgroundRunner(frame, poll_ms=5)
        release = threading.Event()
        done, flags = [], []

        def work(progress, cancelled):
            release.wait(2)
            flags.append(cancelled())
            return 'x'
        runner.start('a', work, on_done=lambda r, e: done.append(r))
        frame.destroy()
        release.set()
        pump(tk_root, lambda: bool(flags), 1)
        assert flags == [True] and done == [] and runner._pump_job is None and not runner.busy()

    def test_nothing_starts_after_the_widget_is_gone(self, frame, tk_root):
        runner = BackgroundRunner(frame, poll_ms=5)
        frame.destroy()
        ran = []
        runner.start('a', lambda p, c: ran.append(1))
        pump(tk_root, lambda: False, 0.05)
        assert ran == [] and not runner.busy()

    def test_destroying_a_child_does_not_stop_the_runner(self, frame, tk_root):
        runner = BackgroundRunner(frame, poll_ms=5)
        child = tk.Frame(frame)
        child.pack()
        child.destroy()
        done = []
        runner.start('a', lambda p, c: 1, on_done=lambda r, e: done.append(r))
        assert pump(tk_root, lambda: done)


class TestCallbacksThatFail:
    def test_a_callback_that_raises_does_not_stop_the_other_jobs(self, runner, tk_root, caplog):
        done = []

        def bad(result, error):
            raise RuntimeError('boom')
        with caplog.at_level(logging.ERROR):
            runner.start('a', lambda p, c: 1, on_done=bad)
            runner.start('b', lambda p, c: 2, on_done=lambda r, e: done.append(r))
            assert pump(tk_root, lambda: done)
        assert done == [2] and 'could not be shown' in caplog.text

    def test_a_widget_that_vanished_is_not_an_error(self, runner, tk_root, caplog):
        def gone(result, error):
            raise tk.TclError('invalid command name')
        with caplog.at_level(logging.ERROR):
            runner.start('a', lambda p, c: 1, on_done=gone)
            assert pump(tk_root, lambda: not runner.busy())
        assert 'could not be shown' not in caplog.text
