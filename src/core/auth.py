"""
Windows Authentication Module for CrossTrans.
Supports Windows Hello (PIN/Fingerprint/Face) with password fallback.
"""
import logging
import queue
import sys
import threading
import tkinter as tk
from typing import Optional, Callable

from src.ui import components as ui
from src.ui.i18n import tr
from src.ui.settings.background import BackgroundRunner
from src.core.windows_identity import current_account, verify_current_password
from src.core.windows_hello import DesktopHello, request_verification
from src.utils.ui_helpers import schedule_window_callback, cancel_window_callback

# Desktop Hello uses the OS ABI; optional Python WinRT projections are not required.
HAS_WINDOWS_HELLO = sys.platform == 'win32'

# Constants
LOGON32_LOGON_INTERACTIVE = 2
LOGON32_PROVIDER_DEFAULT = 0
MAX_ATTEMPTS = 5


class WindowsHelloAuth:
    """Windows Hello authentication using native API."""

    @staticmethod
    def is_available() -> bool:
        """Check if Windows Hello is available on this system."""
        if not HAS_WINDOWS_HELLO:
            return False
        try:
            return DesktopHello().availability() == 0
        except (OSError, AttributeError):
            return False

    @staticmethod
    def verify_async(callback: Callable[[bool], None], message: str = "Verify your identity", *,
                     owner_hwnd: int = 0, cancelled=None):
        """
        Show Windows Hello dialog in background thread.
        Calls callback(True) on success, callback(False) on failure/cancel.
        """
        message = tr(message)
        cancelled = cancelled or threading.Event()

        def run_verification():
            success = False
            try:
                if HAS_WINDOWS_HELLO and not cancelled.is_set():
                    success = bool(request_verification(owner_hwnd, message, cancelled))
            except Exception as error:
                logging.warning('Windows Hello verification failed (%s).',
                                hex(error.hresult) if hasattr(error, 'hresult') else type(error).__name__)
            callback(success and not cancelled.is_set())

        thread = threading.Thread(target=run_verification, daemon=True)
        thread.start()
        return thread


def verify_password(password: str, account: Optional[str] = None) -> bool:
    """Verify Windows password using LogonUserW API.

    Note: If the Windows account has no password, empty string will succeed.
    """
    return verify_current_password(password, account)


class PasswordDialog:
    """Fallback password dialog with rate limiting."""

    def __init__(self, parent, title: str = "Enter Password"):
        self.result = False
        self.parent = parent
        self.failed_attempts = 0

        self._create_dialog(title)

    def _create_dialog(self, title):
        self.view = ui.Dialog(self.parent, 'Verify your identity',
                              'Enter your Windows account password to view protected API keys.',
                              icon='shield-lock', on_cancel=self._cancel)
        self.dialog = self.view.window
        self._create_widgets()
        self.runner = BackgroundRunner(self.dialog)
        self.verify_button = self.view.add_button('Verify', self._verify, default=True, close=False)
        self.view.add_button('Cancel', self._cancel, close=False)
        self.view.show()
        self.password_entry.focus_set()
        self.dialog.wait_window()

    def _create_widgets(self):
        px = ui.get().px
        self._account = current_account()
        ui.Label(self.view.body, 'Windows account', kind='strong').pack(anchor='w', pady=(px(8), px(4)))
        self.account_entry = ui.TextField(self.view.body)
        self.account_entry.set(self._account)
        self.account_entry.pack(fill='x')
        ui.Label(self.view.body, 'Use DOMAIN\\user or MicrosoftAccount\\email for your signed-in account.',
                 kind='muted', wraplength=px(420), justify='left').pack(anchor='w', pady=(px(4), px(8)))
        ui.Label(self.view.body, 'Use your account password, not your Windows Hello PIN.',
                 kind='muted', wraplength=px(420), justify='left').pack(anchor='w', pady=(0, px(8)))
        self.password_var = tk.StringVar(self.dialog)
        self.password_entry = ui.TextField(self.view.body, textvariable=self.password_var, show='*')
        self.password_entry.pack(fill='x')
        self.password_entry.bind('<Return>', lambda e: self._verify() or 'break')
        self.error_label = ui.Label(self.view.body, '', kind='muted', wraplength=px(420), justify='left')
        self.error_label.pack(anchor='w', pady=(px(8), 0))
        self.attempts_label = ui.Label(self.view.body,
                                       f'Attempts remaining: {MAX_ATTEMPTS}/{MAX_ATTEMPTS}', kind='caption')
        self.attempts_label.pack(anchor='w', pady=(px(4), 0))

    def _verify(self):
        if self.runner.busy() or self.failed_attempts >= MAX_ATTEMPTS:
            return
        password = self.password_var.get()
        account = self.account_entry.get().strip()
        self.password_var.set('')
        self.password_entry.set('')
        self.error_label.configure(text='Verifying...')
        self.verify_button.configure(state='disabled')

        def work(progress, cancelled):
            return verify_password(password, account) if account != self._account else verify_password(password)

        def done(verified, error):
            if verified and error is None:
                self.result = True
                self.view.close()
                return
            self.failed_attempts += 1
            remaining = MAX_ATTEMPTS - self.failed_attempts
            self.attempts_label.configure(text=f'Attempts remaining: {remaining}/{MAX_ATTEMPTS}')
            if remaining <= 0:
                self.result = False
                self.view.close()
                notice = ui.Dialog(self.parent, 'Too many attempts',
                                    'The password could not be verified. Close this notice and try again later.',
                                    icon='shield-exclamation')
                notice.add_button('Done', default=True)
                notice.show()
            else:
                self.error_label.configure(text='That password was not accepted. Try again.')
                self.password_entry.invalid(True)
                self.verify_button.configure(state='normal')
                self.password_entry.focus_set()

        self.runner.start('password', work, on_done=done)

    def _cancel(self):
        self.result = False
        self.view.close()


class WaitingDialog:
    """Dialog shown while waiting for Windows Hello."""

    def __init__(self, parent, message: str = "Waiting for Windows Hello...", on_cancel: Callable = None):
        self.parent = parent
        self.on_cancel = on_cancel
        self.cancelled = False

        self.view = ui.Dialog(parent, 'Windows Hello', message,
                              icon='shield-lock', on_cancel=self._cancel)
        self.dialog = self.view.window
        ui.Label(self.view.body, 'Use your PIN, fingerprint or face.', kind='muted').pack(anchor='w')
        self.progress = ui.Progress(self.view.body, mode='indeterminate')
        self.progress.pack(fill='x', pady=ui.get().px(12))
        self.progress.start()
        self.view.add_button('Cancel', self._cancel, close=False)
        self.view.show()

    def _cancel(self):
        """Handle cancel button click."""
        self.cancelled = True
        if self.on_cancel:
            self.on_cancel()
        self.close()

    def hide_for_windows_hello(self):
        """Hide this dialog so Windows Hello can appear on top."""
        if self.dialog.winfo_exists():
            self.dialog.iconify()

    def show(self):
        """Show the dialog again."""
        if self.dialog.winfo_exists():
            self.dialog.deiconify()
            self.dialog.lift()
            self.dialog.focus_force()

    def close(self):
        if self.dialog.winfo_exists():
            self.dialog.destroy()


def _run_windows_hello(parent) -> bool:
    """Run Windows Hello authentication flow.

    Returns:
        True if verified successfully, False if failed/cancelled.
    """
    results = queue.Queue()
    verified = [False]
    timer = [None]
    cancelled = threading.Event()
    view = ui.Dialog(parent, 'Verify your identity',
                     'Use Windows Hello to continue, or cancel to use your account password.',
                     icon='shield-lock')

    def close():
        cancelled.set()
        if timer[0] is not None:
            cancel_window_callback(parent._root(), view.window, timer[0])
            timer[0] = None
        view.close()

    def poll():
        timer[0] = None
        try:
            verified[0] = bool(results.get_nowait())
        except queue.Empty:
            timer[0] = schedule_window_callback(parent._root(), view.window, 50, poll)
        else:
            close()

    view._on_cancel = close
    view.add_button('Cancel', close, default=True, close=False)
    view.show()
    view.window.update_idletasks()
    owner_hwnd = int(view.window.wm_frame(), 16)
    view.window.bind('<Destroy>', lambda event: cancelled.set() if event.widget is view.window else None, add='+')
    WindowsHelloAuth.verify_async(results.put, 'CrossTrans - Verify your identity to view API keys',
                                 owner_hwnd=owner_hwnd, cancelled=cancelled)
    timer[0] = schedule_window_callback(parent._root(), view.window, 50, poll)
    view.window.wait_window()
    return verified[0]



def require_auth(parent) -> bool:
    """
    Authenticate user via PIN/Fingerprint/Face or password.

    Flow:
    1. If no password on account → auto-authenticate
    2. If PIN/Fingerprint/Face available → use automatically
    3. If that fails/cancelled → fall through to password dialog

    Returns:
        True if authenticated successfully
    """
    if hasattr(parent, 'winfo_exists') and not parent.winfo_exists():
        return False
    # Check if account has no password - auto-authenticate
    if verify_password(""):
        logging.info("Account has no password - auto-authenticated")
        return True

    # Auto-use PIN/Fingerprint/Face if available
    if WindowsHelloAuth.is_available():
        if _run_windows_hello(parent):
            return bool(getattr(parent, 'winfo_exists', lambda: True)())
        # Failed/cancelled → fall through to password

    # Fallback: Password dialog
    if hasattr(parent, 'winfo_exists') and not parent.winfo_exists():
        return False
    dialog = PasswordDialog(parent)
    return dialog.result


# Backward compatibility aliases
def verify_credential(credential: str) -> bool:
    """Backward compatibility: verify password."""
    return verify_password(credential)

def verify_windows_credential(credential: str) -> bool:
    """Backward compatibility: verify password."""
    return verify_password(credential)

def verify_windows_password(password: str) -> bool:
    """Backward compatibility: verify password."""
    return verify_password(password)

def has_windows_pin() -> bool:
    """Check if Windows Hello is available (renamed for clarity)."""
    return WindowsHelloAuth.is_available() if HAS_WINDOWS_HELLO else False


# Legacy class alias
class AuthDialog(PasswordDialog):
    """Legacy alias for PasswordDialog."""
    pass

class EnhancedAuthDialog(PasswordDialog):
    """Legacy alias for PasswordDialog."""
    pass
