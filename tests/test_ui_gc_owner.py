"""Workers request GC through the UI owner without making Tcl calls themselves."""
import threading

from tests.final_ui_helpers import design, pump
from src.utils import gc_owner


def test_worker_collection_runs_on_the_ui_thread(design, monkeypatch):
    original = gc_owner.gc.collect
    seen = []
    def collect(generation=2):
        seen.append(threading.get_ident())
        return original(generation)
    monkeypatch.setattr(gc_owner.gc, 'collect', collect)
    finished = threading.Event()
    def worker():
        gc_owner.collect()
        finished.set()
    thread = threading.Thread(target=worker)
    thread.start()
    pump(design.root, finished.is_set, timeout=3)
    thread.join(0.1)
    assert seen and set(seen) == {threading.get_ident()}


def test_installing_context_again_keeps_one_collector(design):
    first = gc_owner.install(design.root)
    assert gc_owner.install(design.root) is first
    assert first.thread == threading.get_ident()
    assert not gc_owner.gc.isenabled()
