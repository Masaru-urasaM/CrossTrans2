"""Persist collection and phase results even if a Tk test cannot return."""
import json
from pathlib import Path

_path = None


def pytest_addoption(parser):
    parser.addoption('--dr-event-log', help='Durable deferred-resize validation events')
    parser.addoption('--dr-select-nodes', help='Exact retained node IDs for an interrupted-run continuation')


def pytest_configure(config):
    global _path
    value = config.getoption('--dr-event-log')
    if value:
        _path = Path(value)
        _path.parent.mkdir(parents=True, exist_ok=True)
        _path.write_text('', encoding='utf-8')


def _write(event):
    if _path is not None:
        with _path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(event) + '\n')


def pytest_collection_modifyitems(config, items):
    selection = config.getoption('--dr-select-nodes')
    if not selection:
        return
    import pytest
    expected = set(json.loads(Path(selection).read_text(encoding='utf-8'))['nodeids'])
    actual = {item.nodeid for item in items}
    if not expected or expected-actual:
        raise pytest.UsageError('Interrupted-run selection is empty or contains missing current node IDs')
    kept = [item for item in items if item.nodeid in expected]
    removed = [item for item in items if item.nodeid not in expected]
    config.hook.pytest_deselected(items=removed)
    items[:] = kept


def pytest_collection_finish(session):
    _write(dict(event='collection', nodeids=[item.nodeid for item in session.items]))


def pytest_runtest_logreport(report):
    _write(dict(event='report', nodeid=report.nodeid, when=report.when,
                outcome=report.outcome, duration=report.duration,
                longrepr=str(report.longrepr) if report.longrepr else None))
