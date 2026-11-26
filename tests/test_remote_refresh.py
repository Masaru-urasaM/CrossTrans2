"""
Decision 103 (2026-11-20) - a running app fetches the Worker's table again, not only at start-up.

TODO Task 22 found the developer's app, started at 14:55 with a 16-hour-old cache, still on the 2026-11-18 table
eleven hours later, while the Worker had served a newer one since 21:00: the start-up fetch runs only when the
cache is a day old, and nothing fetched again while the app ran. No network here: the fetch is a recorder.
"""
import threading
import time
from types import SimpleNamespace

from src.app import TranslatorApp
from src.constants import REMOTE_CONFIG_REFRESH_SECONDS, REMOTE_CONFIG_RETRY_SECONDS
from src.core import remote_config
from src.core.remote_config import RemoteConfigManager

HOUR = 3600


def manager(age_hours, fetching=False):
    """A manager whose table is `age_hours` old, outside the singleton; `fetches` records each forced fetch."""
    mgr = object.__new__(RemoteConfigManager)
    mgr._initialized = True
    mgr._config = {'_cached_at': time.time() - age_hours * HOUR} if age_hours is not None else {}
    mgr._config_lock = threading.RLock()
    mgr._update_callbacks = []
    mgr._fetching = fetching
    mgr._last_refresh_attempt = 0.0
    mgr.fetches = []
    mgr.fetch_remote_async = lambda force=False: mgr.fetches.append(force)
    return mgr


def test_the_interval_is_shorter_than_the_workers_and_the_retry_shorter_still():
    assert REMOTE_CONFIG_REFRESH_SECONDS <= 12 * HOUR
    assert REMOTE_CONFIG_RETRY_SECONDS < REMOTE_CONFIG_REFRESH_SECONDS


def test_a_table_older_than_the_interval_is_fetched_again_with_force():
    mgr = manager(age_hours=7)
    assert mgr.refresh_if_stale() is True
    assert mgr.fetches == [True]                 # past the 24-hour start-up rule


def test_a_newer_table_is_left_alone():
    mgr = manager(age_hours=1)
    assert mgr.refresh_if_stale() is False and mgr.fetches == []


def test_a_table_without_a_fetch_time_counts_as_old():
    mgr = manager(age_hours=None)                # the hard-coded defaults: the start-up fetch failed
    assert mgr.refresh_if_stale() is True


def test_a_failed_attempt_waits_before_the_next():
    mgr = manager(age_hours=7)
    assert mgr.refresh_if_stale() is True
    assert mgr.refresh_if_stale() is False       # the fetch failed: the table is still old
    mgr._last_refresh_attempt -= REMOTE_CONFIG_RETRY_SECONDS + 1
    assert mgr.refresh_if_stale() is True
    assert mgr.fetches == [True, True]


def test_nothing_starts_while_a_fetch_runs():
    mgr = manager(age_hours=30, fetching=True)
    assert mgr.refresh_if_stale() is False and mgr.fetches == []


def test_the_limits_can_be_given():
    mgr = manager(age_hours=2)
    assert mgr.refresh_if_stale(max_age=HOUR, retry_after=0) is True


def test_the_attempt_is_logged_as_a_measurement(caplog):
    mgr = manager(age_hours=7)
    with caplog.at_level('INFO'):
        mgr.refresh_if_stale()
    assert any('7.0 h old, fetching again' in record.getMessage() for record in caplog.records)


def test_no_update_callback_is_registered_so_the_fetch_thread_never_reaches_tk():
    mgr = remote_config.get_config()
    assert mgr._update_callbacks == []


def test_the_watchdog_asks_for_a_refresh_and_schedules_itself(monkeypatch):
    asked, scheduled = [], []
    monkeypatch.setattr(remote_config, 'get_config', lambda: SimpleNamespace(refresh_if_stale=lambda: asked.append(1)))
    app = SimpleNamespace(running=True, _app_start_time=time.time(), _last_heartbeat=time.time(),
                          _watchdog_check=lambda: None,
                          root=SimpleNamespace(after=lambda ms, callback: scheduled.append(ms)))
    TranslatorApp._watchdog_check(app)
    assert asked == [1] and scheduled == [60000]


def test_a_failing_refresh_does_not_stop_the_watchdog(monkeypatch):
    def boom():
        raise RuntimeError('fixture')
    scheduled = []
    monkeypatch.setattr(remote_config, 'get_config', lambda: SimpleNamespace(refresh_if_stale=boom))
    app = SimpleNamespace(running=True, _app_start_time=time.time(), _last_heartbeat=time.time(),
                          _watchdog_check=lambda: None,
                          root=SimpleNamespace(after=lambda ms, callback: scheduled.append(ms)))
    TranslatorApp._watchdog_check(app)
    assert scheduled == [60000]


def test_a_stopped_app_asks_for_nothing(monkeypatch):
    asked = []
    monkeypatch.setattr(remote_config, 'get_config', lambda: SimpleNamespace(refresh_if_stale=lambda: asked.append(1)))
    TranslatorApp._watchdog_check(SimpleNamespace(running=False))
    assert asked == []
