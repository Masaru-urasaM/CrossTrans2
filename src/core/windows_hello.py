"""Window-owned desktop Windows Hello using the documented WinRT interop ABI.

Only the operating system can produce a successful verification. No credentials,
Tk objects or Python WinRT projection packages enter the native worker.
"""
from contextlib import contextmanager
import ctypes as C
import os
import sys
import threading
import time
import uuid

RUNTIME_CLASS = 'Windows.Security.Credentials.UI.UserConsentVerifier'
STATICS_IID = 'af4f3f91-564c-4ddc-b8b5-973447627c65'
INTEROP_IID = '39e050c3-4e74-441a-8dc0-b81104df949c'
ASYNC_INFO_IID = '00000036-0000-0000-c000-000000000046'
WINRT_NAMESPACE = uuid.UUID('11f47ad5-7b73-42c0-abae-878b1e16adee')
RESULT_OPERATION_IID = str(uuid.uuid5(WINRT_NAMESPACE,
    'pinterface({9fc2b0bb-e446-44e2-aa61-9cab8f636af2};'
    'enum(Windows.Security.Credentials.UI.UserConsentVerificationResult;i4))'))
HRESULT = C.c_int32
POINTER = C.c_void_p


class HelloError(OSError):
    def __init__(self, result):
        self.hresult = int(result) & 0xffffffff
        super().__init__(self.hresult, 'Windows Hello could not complete the operation.')


class GUID(C.Structure):
    _fields_ = [('data1', C.c_uint32), ('data2', C.c_uint16), ('data3', C.c_uint16),
                ('data4', C.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def checked(result):
    if C.c_int32(result).value < 0:
        raise HelloError(result)


def method(pointer, slot, result_type, *argument_types):
    if not pointer:
        raise HelloError(0x80004003)
    table = C.cast(pointer, C.POINTER(C.POINTER(POINTER))).contents
    return C.WINFUNCTYPE(result_type, POINTER, *argument_types)(table[slot])


def release(pointer):
    if pointer:
        method(pointer, 2, C.c_uint32)(pointer)


def wait_result(operation, cancelled=None, timeout=120.0):
    """Take ownership of an IAsyncOperation<enum>; release every interface on all paths."""
    cancelled = cancelled or threading.Event()
    info = POINTER()
    status = C.c_int32(0)
    try:
        iid = GUID.parse(ASYNC_INFO_IID)
        checked(method(operation, 0, HRESULT, C.POINTER(GUID), C.POINTER(POINTER))(
            operation, C.byref(iid), C.byref(info)))
        deadline = time.monotonic() + timeout
        while True:
            checked(method(info, 7, HRESULT, C.POINTER(C.c_int32))(info, C.byref(status)))
            if cancelled.is_set() or time.monotonic() >= deadline:
                checked(method(info, 9, HRESULT)(info))
                return None
            if status.value != 0:
                break
            cancelled.wait(0.02)
        if status.value != 1:
            return None
        value = C.c_int32()
        checked(method(operation, 8, HRESULT, C.POINTER(C.c_int32))(operation, C.byref(value)))
        return value.value if not cancelled.is_set() else None
    finally:
        if info:
            if status.value != 0:
                method(info, 10, HRESULT)(info)
            release(info)
        release(operation)


class DesktopHello:
    def __init__(self):
        if sys.platform != 'win32':
            raise HelloError(0x80004001)
        self.runtime = C.WinDLL('combase', use_last_error=True)
        self.user = C.WinDLL('user32', use_last_error=True)
        declarations = (
            (self.runtime.RoInitialize, [C.c_uint32], HRESULT),
            (self.runtime.RoUninitialize, [], None),
            (self.runtime.WindowsCreateString, [C.c_wchar_p, C.c_uint32, C.POINTER(POINTER)], HRESULT),
            (self.runtime.WindowsDeleteString, [POINTER], HRESULT),
            (self.runtime.RoGetActivationFactory, [POINTER, C.POINTER(GUID), C.POINTER(POINTER)], HRESULT),
            (self.user.GetWindowThreadProcessId, [POINTER, C.POINTER(C.c_uint32)], C.c_uint32),
        )
        for function, arguments, result in declarations:
            function.argtypes, function.restype = arguments, result

    @contextmanager
    def string(self, text):
        handle = POINTER()
        checked(self.runtime.WindowsCreateString(text, len(text.encode('utf-16-le')) // 2, C.byref(handle)))
        try:
            yield handle
        finally:
            self.runtime.WindowsDeleteString(handle)

    @contextmanager
    def factory(self, iid_text):
        result = self.runtime.RoInitialize(1)
        if (int(result) & 0xffffffff) == 0x80010106:
            result = self.runtime.RoInitialize(0)
        checked(result)
        pointer = POINTER()
        try:
            with self.string(RUNTIME_CLASS) as name:
                iid = GUID.parse(iid_text)
                checked(self.runtime.RoGetActivationFactory(name, C.byref(iid), C.byref(pointer)))
                yield pointer
        finally:
            release(pointer)
            self.runtime.RoUninitialize()

    def availability(self):
        # Feature-detect the desktop entry point before offering Hello.
        with self.factory(INTEROP_IID):
            pass
        with self.factory(STATICS_IID) as factory:
            operation = POINTER()
            checked(method(factory, 6, HRESULT, C.POINTER(POINTER))(factory, C.byref(operation)))
            return wait_result(operation, timeout=2.0)

    def _verify(self, hwnd, message, cancelled=None):
        cancelled = cancelled or threading.Event()
        process = C.c_uint32()
        if (not hwnd or cancelled.is_set()
                or not self.user.GetWindowThreadProcessId(POINTER(hwnd), C.byref(process))
                or process.value != os.getpid()):
            return False
        with self.factory(INTEROP_IID) as factory, self.string(message) as text:
            operation = POINTER()
            iid = GUID.parse(RESULT_OPERATION_IID)
            checked(method(factory, 6, HRESULT, POINTER, POINTER, C.POINTER(GUID), C.POINTER(POINTER))(
                factory, POINTER(hwnd), text, C.byref(iid), C.byref(operation)))
            return wait_result(operation, cancelled) == 0


def request_verification(hwnd, message, cancelled=None):
    """The single production entry point which may show an OS authentication prompt."""
    return DesktopHello()._verify(hwnd, message, cancelled)
