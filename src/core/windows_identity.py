"""Windows password verification against the process user's actual SID, never environment hints."""
import ctypes
from ctypes import wintypes as W
import sys


class WindowsAccounts:
    def __init__(self):
        self.security = ctypes.WinDLL('advapi32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.names = ctypes.WinDLL('secur32', use_last_error=True)
        declarations = (
            (self.security.OpenProcessToken, [W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)], W.BOOL),
            (self.security.GetTokenInformation,
             [W.HANDLE, ctypes.c_int, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD)], W.BOOL),
            (self.security.EqualSid, [W.LPVOID, W.LPVOID], W.BOOL),
            (self.security.LogonUserW,
             [W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, W.DWORD, W.DWORD, ctypes.POINTER(W.HANDLE)], W.BOOL),
            (self.kernel.GetCurrentProcess, [], W.HANDLE),
            (self.kernel.CloseHandle, [W.HANDLE], W.BOOL),
            (self.names.GetUserNameExW, [ctypes.c_int, W.LPWSTR, ctypes.POINTER(W.ULONG)], W.BOOL),
        )
        for function, arguments, result in declarations:
            function.argtypes, function.restype = arguments, result

    def account_name(self):
        # SAM-compatible names include the account authority; UPN is the domain fallback.
        for name_format in (2, 8):
            length = W.ULONG(1024)
            buffer = ctypes.create_unicode_buffer(length.value)
            if self.names.GetUserNameExW(name_format, buffer, ctypes.byref(length)):
                return buffer.value
        raise OSError(ctypes.get_last_error(), 'The current Windows account could not be identified.')

    def _user(self, token):
        length = W.DWORD()
        self.security.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        if not length.value:
            raise OSError(ctypes.get_last_error(), 'Token identity is unavailable.')
        buffer = ctypes.create_string_buffer(length.value)
        if not self.security.GetTokenInformation(token, 1, buffer, length, ctypes.byref(length)):
            raise OSError(ctypes.get_last_error(), 'Token identity is unavailable.')
        return buffer

    def verify(self, password, account=None):
        account = account or self.account_name()
        if '\\' in account:
            domain, username = account.split('\\', 1)
        else:
            username, domain = account, None if '@' in account else '.'
        current, authenticated = W.HANDLE(), W.HANDLE()
        password_buffer = ctypes.create_unicode_buffer(password)
        try:
            if not self.security.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, ctypes.byref(current)):
                return False
            if not self.security.LogonUserW(username, domain, password_buffer, 2, 0,
                                            ctypes.byref(authenticated)):
                return False
            current_user, authenticated_user = self._user(current), self._user(authenticated)
            current_sid = ctypes.cast(current_user, ctypes.POINTER(W.LPVOID))[0]
            authenticated_sid = ctypes.cast(authenticated_user, ctypes.POINTER(W.LPVOID))[0]
            return bool(self.security.EqualSid(current_sid, authenticated_sid))
        finally:
            ctypes.memset(ctypes.addressof(password_buffer), 0, ctypes.sizeof(password_buffer))
            for token in (authenticated, current):
                if token:
                    self.kernel.CloseHandle(token)


def current_account():
    if sys.platform != 'win32':
        return ''
    try:
        return WindowsAccounts().account_name()
    except (OSError, AttributeError):
        return ''


def verify_current_password(password, account=None):
    if sys.platform != 'win32':
        return False
    try:
        return WindowsAccounts().verify(password, account)
    except (OSError, AttributeError, ValueError):
        return False
