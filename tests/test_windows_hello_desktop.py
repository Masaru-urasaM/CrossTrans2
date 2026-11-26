"""Desktop Hello owner handles, ABI/resource ownership, cancellation and queue delivery."""
from contextlib import contextmanager
import ctypes as C
import os
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

from src.core import auth
from src.core import windows_hello as hello
from tests.final_ui_helpers import design  # noqa: F401


class FakeOperation:
    """A real-shaped in-process COM vtable, with no OS authentication calls."""

    def __init__(self, result=0, statuses=(1,), query_error=0, result_error=0):
        self.callbacks = []
        self.statuses = list(statuses)
        self.result, self.query_error, self.result_error = result, query_error, result_error
        self.released = []
        self.cancelled = self.closed = 0
        self.operation_table = (C.c_void_p * 9)()
        self.info_table = (C.c_void_p * 11)()
        self.operation_storage = C.pointer(C.cast(self.operation_table, C.POINTER(C.c_void_p)))
        self.info_storage = C.pointer(C.cast(self.info_table, C.POINTER(C.c_void_p)))
        self.operation = C.cast(self.operation_storage, C.c_void_p)
        self.info = C.cast(self.info_storage, C.c_void_p)

        def query(this, iid, output):
            if self.query_error:
                return self.query_error
            output[0] = self.info
            return 0

        def status(this, output):
            output[0] = self.statuses[0]
            if len(self.statuses) > 1:
                self.statuses.pop(0)
            return 0

        def result_value(this, output):
            output[0] = self.result
            return self.result_error

        def cancel(this):
            self.cancelled += 1
            return 0

        def close(this):
            self.closed += 1
            return 0

        self.bind(self.operation_table, 0, hello.HRESULT, query, C.POINTER(hello.GUID), C.POINTER(C.c_void_p))
        self.bind(self.operation_table, 2, C.c_uint32, lambda this: self.released.append('operation') or 0)
        self.bind(self.operation_table, 8, hello.HRESULT, result_value, C.POINTER(C.c_int32))
        self.bind(self.info_table, 2, C.c_uint32, lambda this: self.released.append('info') or 0)
        self.bind(self.info_table, 7, hello.HRESULT, status, C.POINTER(C.c_int32))
        self.bind(self.info_table, 9, hello.HRESULT, cancel)
        self.bind(self.info_table, 10, hello.HRESULT, close)

    def bind(self, table, slot, result, callback, *arguments):
        function = C.WINFUNCTYPE(result, C.c_void_p, *arguments)(callback)
        self.callbacks.append(function)
        table[slot] = C.cast(function, C.c_void_p).value


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows COM ABI')
@pytest.mark.parametrize('result', range(7))
def test_desktop_request_passes_full_owner_handle_and_only_verified_grants_access(result):
    operation = FakeOperation(result=result)
    factory_table = (C.c_void_p * 7)()
    factory_storage = C.pointer(C.cast(factory_table, C.POINTER(C.c_void_p)))
    factory_pointer = C.cast(factory_storage, C.c_void_p)
    calls = []

    def request(this, hwnd, text, iid, output):
        calls.append((hwnd, str(uuid.UUID(bytes_le=bytes(iid.contents)))))
        output[0] = operation.operation
        return 0

    operation.bind(factory_table, 6, hello.HRESULT, request, C.c_void_p, C.c_void_p,
                   C.POINTER(hello.GUID), C.POINTER(C.c_void_p))
    client = hello.DesktopHello.__new__(hello.DesktopHello)

    def owner_process(hwnd, output):
        C.cast(output, C.POINTER(C.c_uint32))[0] = os.getpid()
        return 1

    client.user = SimpleNamespace(GetWindowThreadProcessId=owner_process)

    @contextmanager
    def factory(iid):
        calls.append(iid)
        yield factory_pointer

    @contextmanager
    def string(text):
        yield C.c_void_p(1)

    client.factory, client.string = factory, string
    owner = 0x123456789 if C.sizeof(C.c_void_p) == 8 else 0x1234567
    assert client._verify(owner, 'Verify this fixture') is (result == 0)
    assert calls == [hello.INTEROP_IID, (owner, hello.RESULT_OPERATION_IID)]
    assert operation.closed == 1 and operation.released == ['info', 'operation']


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows COM ABI')
@pytest.mark.parametrize('status', [2, 3])
def test_cancelled_or_failed_async_status_never_becomes_verified(status):
    operation = FakeOperation(result=0, statuses=(status,))
    assert hello.wait_result(operation.operation) is None
    assert operation.closed == 1
    assert operation.released == ['info', 'operation']


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows COM ABI')
@pytest.mark.parametrize('cancel', [True, False])
def test_cancel_or_timeout_cancels_the_os_operation_and_releases_references(cancel):
    operation = FakeOperation(statuses=(0,))
    event = threading.Event()
    if cancel:
        event.set()
    assert hello.wait_result(operation.operation, event, timeout=0 if not cancel else 120) is None
    assert operation.cancelled == 1
    assert operation.released == ['info', 'operation']


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows COM ABI')
@pytest.mark.parametrize('query_error,result_error,released', [
    (0x80004002, 0, ['operation']), (0, 0x80070005, ['info', 'operation'])])
def test_hresult_failure_releases_every_acquired_interface(query_error, result_error, released):
    operation = FakeOperation(query_error=query_error, result_error=result_error)
    with pytest.raises(hello.HelloError):
        hello.wait_result(operation.operation)
    assert operation.released == released


@pytest.mark.parametrize('owner,pid,cancel', [(0, os.getpid(), False), (1, os.getpid()+1, False), (1, os.getpid(), True)],
                         ids=['missing-owner', 'foreign-process', 'cancelled'])
def test_invalid_foreign_or_cancelled_owner_does_not_start_verification(owner, pid, cancel):
    client = hello.DesktopHello.__new__(hello.DesktopHello)
    def owner_process(hwnd, output):
        C.cast(output, C.POINTER(C.c_uint32))[0] = pid
        return 1
    client.user = SimpleNamespace(GetWindowThreadProcessId=owner_process)
    client.factory = Mock(side_effect=AssertionError('must not request a prompt'))
    event = threading.Event()
    if cancel:
        event.set()
    assert not client._verify(owner, 'Fixture', event)
    client.factory.assert_not_called()


@pytest.mark.parametrize('outcome,cancel', [(True, False), (False, False), (True, True), (RuntimeError('fixture'), False)])
def test_verification_worker_has_only_a_numeric_owner_and_delivers_one_fail_closed_result(monkeypatch, outcome, cancel):
    main = threading.get_ident()
    seen, received = [], []
    event = threading.Event()
    def request(hwnd, message, cancelled):
        seen.append((threading.get_ident(), hwnd, cancelled))
        if isinstance(outcome, Exception):
            raise outcome
        if cancel:
            event.set()
        return outcome
    monkeypatch.setattr(auth, 'HAS_WINDOWS_HELLO', True)
    monkeypatch.setattr(auth, 'request_verification', request)
    worker = auth.WindowsHelloAuth.verify_async(received.append, owner_hwnd=123, cancelled=event)
    worker.join(3)
    assert not worker.is_alive()
    assert len(seen) == 1 and seen[0] == (worker.ident, 123, event) and worker.ident != main
    assert received == [outcome is True and not cancel]


def test_hello_dialog_supplies_its_wrapper_and_applies_queue_result_on_ui_thread(design, monkeypatch):
    seen = []
    def start(callback, message, **kwargs):
        seen.append(kwargs)
        callback(True)
    monkeypatch.setattr(auth.WindowsHelloAuth, 'verify_async', start)
    assert auth._run_windows_hello(design.root)
    assert seen[0]['owner_hwnd'] > 0
    assert seen[0]['cancelled'].is_set()


def test_destroyed_owner_cancels_hello_and_does_not_open_password_fallback(design, monkeypatch):
    import tkinter as tk
    parent = tk.Toplevel(design.root)
    parent.update_idletasks()
    cancelled = []
    def start(callback, message, **kwargs):
        cancelled.append(kwargs['cancelled'])
        design.root.after(0, parent.destroy)
    monkeypatch.setattr(auth, 'verify_password', lambda value: False)
    monkeypatch.setattr(auth.WindowsHelloAuth, 'is_available', lambda: True)
    monkeypatch.setattr(auth.WindowsHelloAuth, 'verify_async', start)
    fallback = Mock(side_effect=AssertionError('destroyed parent must not create a dialog'))
    monkeypatch.setattr(auth, 'PasswordDialog', fallback)
    assert not auth.require_auth(parent)
    assert cancelled[0].is_set()
    fallback.assert_not_called()
