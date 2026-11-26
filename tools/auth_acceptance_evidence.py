"""Observe fixed-fixture authentication UI without retaining credentials or key values."""
import copy
import ctypes
from dataclasses import dataclass
import queue
from types import SimpleNamespace

FIXTURE_KEYS = frozenset(f'fixture-only-key-{index}' for index in range(2))


@dataclass(frozen=True)
class Verification:
    accepted: bool
    cancelled: bool = False
    method: str = 'injected-fake'
    attempts: int = 0
    account_format: str = 'unknown'
    backend_events: tuple = ()


class PasswordBackendObserver:
    """Pass through the native backend and enqueue only boolean/numeric API outcomes."""

    def __init__(self):
        self.events = queue.Queue()

    def __enter__(self):
        from src.core import windows_identity
        self.module = windows_identity
        self.previous = windows_identity.WindowsAccounts
        observer = self

        class ObservedAccounts(self.previous):
            def __init__(self):
                super().__init__()
                for owner, name in ((self.names, 'GetUserNameExW'), (self.security, 'OpenProcessToken'),
                                    (self.security, 'LogonUserW'), (self.security, 'EqualSid')):
                    native = getattr(owner, name)
                    def observed(*args, function=native, operation=name):
                        result = function(*args)
                        error = ctypes.get_last_error() if not result else 0
                        observer.events.put(dict(operation=operation, accepted=bool(result), error_code=error))
                        return result
                    setattr(owner, name, observed)
        windows_identity.WindowsAccounts = ObservedAccounts
        return self

    def __exit__(self, *args):
        self.module.WindowsAccounts = self.previous

    def drain(self):
        result = []
        while True:
            try:
                result.append(self.events.get_nowait())
            except queue.Empty:
                return tuple(result)


def account_format(value):
    if value.casefold().startswith('microsoftaccount\\'):
        return 'MicrosoftAccount'
    if '\\' in value:
        return 'SAM'
    return 'UPN' if '@' in value else 'unqualified'


class AcceptanceRecorder:
    def __init__(self, page, verifier, *, native=False, mode='injected-fake'):
        self.page, self.verifier = page, verifier
        self.native, self.mode = native, mode
        self.verifications, self.actions, self.errors = [], [], []

    def snapshot(self):
        try:
            cards = self.page.cards
            if not self.page.body.winfo_exists() or len(cards) != len(FIXTURE_KEYS):
                return dict(alive=False, consistent=False, fixtures=False, revealed=[], unmasked=[])
            flags = [bool(card.revealed) for card in cards]
            unmasked = [str(card.field.cget('show')) == '' for card in cards]
            return dict(alive=True, consistent=flags == unmasked,
                        fixtures=frozenset(card.field.get() for card in cards) == FIXTURE_KEYS,
                        revealed=[index for index, value in enumerate(flags) if value],
                        unmasked=[index for index, value in enumerate(unmasked) if value])
        except Exception:
            return dict(alive=False, consistent=False, fixtures=False, revealed=[], unmasked=[])

    def verify(self, parent):
        try:
            outcome = self.verifier(parent)
            if not isinstance(outcome, Verification):
                raise TypeError('A structured verification outcome is required')
        except Exception as error:
            self.errors.append(type(error).__name__)
            outcome = Verification(False, method='verification-error')
        accepted = bool(outcome.accepted) and not outcome.cancelled
        self.verifications.append(dict(accepted=accepted, cancelled=bool(outcome.cancelled),
                                       method=outcome.method, attempts=outcome.attempts,
                                       account_format=outcome.account_format,
                                       backend_events=copy.deepcopy(outcome.backend_events)))
        return accepted

    def run(self, action, operation):
        before, first = self.snapshot(), len(self.verifications)
        try:
            operation()
        except Exception as error:
            self.errors.append(type(error).__name__)
        self.actions.append(dict(action=action, before=before, after=self.snapshot(),
                                 verifications=copy.deepcopy(self.verifications[first:])))

    def report(self):
        checks = dict(show_all_after_one_accept=False, hide_without_reauthentication=False,
                      single_key_reveal=False, cancel_keeps_all_masked=False)
        saw_accept = False
        for action in self.actions:
            before, after, attempts = action['before'], action['after'], action['verifications']
            safe = all(state['alive'] and state['consistent'] and state['fixtures'] for state in (before, after))
            if not safe:
                continue
            accepted = any(result['accepted'] for result in attempts)
            if (action['action'] == 'toggle-all' and not before['revealed']
                    and len(after['revealed']) == 2 and len(attempts) == 1 and accepted):
                checks['show_all_after_one_accept'] = True
                saw_accept = True
            if (action['action'] == 'toggle-all' and before['revealed'] and not after['revealed']
                    and not attempts and saw_accept):
                checks['hide_without_reauthentication'] = True
            if (action['action'] == 'single-key' and not before['revealed']
                    and len(after['revealed']) == 1 and (saw_accept or accepted)):
                checks['single_key_reveal'] = True
            if (not before['revealed'] and not after['revealed'] and len(attempts) == 1
                    and attempts[0]['cancelled'] and not attempts[0]['accepted']):
                checks['cancel_keeps_all_masked'] = True
        safe_actions = all(state['alive'] and state['consistent'] and state['fixtures']
                           for action in self.actions for state in (action['before'], action['after']))
        functional = all(checks.values()) and safe_actions and not self.errors
        def os_verified(result):
            accepted = {event['operation'] for event in result['backend_events'] if event['accepted']}
            return {'OpenProcessToken', 'LogonUserW', 'EqualSid'} <= accepted
        native_password = self.native and any(result['accepted'] and result['method'] == 'native-password-dialog'
                                             and result['attempts'] > 0 and os_verified(result)
                                             for result in self.verifications)
        return dict(schema=2, mode=self.mode, native_run=self.native, fake_keys=True, checks=checks,
                    functional_checks_passed=functional, native_password_verified=native_password,
                    status='native-password-flow-passed' if functional and native_password else 'incomplete',
                    verifications=copy.deepcopy(self.verifications), actions=copy.deepcopy(self.actions),
                    errors=list(self.errors))


def native_password_verifier(parent):
    from src.core import auth

    class ObservedPasswordDialog(auth.PasswordDialog):
        def __init__(self, owner):
            self.cancelled, self.attempts, self.format = False, 0, 'unknown'
            super().__init__(owner)

        def _verify(self):
            if not self.runner.busy() and self.failed_attempts < auth.MAX_ATTEMPTS:
                self.attempts += 1
                self.format = account_format(self.account_entry.get().strip())
            super()._verify()

        def _cancel(self):
            self.cancelled = True
            super()._cancel()

    with PasswordBackendObserver() as backend:
        dialog = ObservedPasswordDialog(parent)
    return Verification(bool(dialog.result), dialog.cancelled, 'native-password-dialog',
                        dialog.attempts, dialog.format, backend.drain())


class AcceptanceView:
    """Production key controls with observed actions and a fixed fixture configuration."""

    def __init__(self, root, config, verifier, *, native=False, mode='injected-fake'):
        from src.ui import components as ui
        from src.ui.settings import page_providers
        from src.ui.theme import engine
        self.root = root
        engine.attach(root)
        self.original_verifier = page_providers.require_auth
        self.closed = False
        self.footer = ui.Frame(root)
        self.footer.pack(side='bottom', fill='x', padx=12, pady=12)
        self.instructions = ui.Label(self.footer,
                 '1. Show all and verify. 2. Hide all. 3. Reveal one eye. '
                 '4. Mask and reset, then Show all and Cancel.', wraplength=900)
        self.instructions.pack(fill='x')
        self.progress = ui.Label(self.footer, '', wraplength=900)
        self.progress.pack(fill='x', pady=6)
        self._wrap_width = None
        self.footer.bind('<Configure>', self._fit_footer, add='+')
        ui.Button(self.footer, 'Mask and reset authentication', command=self.reset).pack(pady=4)
        self.area = ui.ScrollArea(root)
        self.area.pack(fill='both', expand=True)
        host = SimpleNamespace(window=root, config=config, changed=lambda key: None,
                               api_changed=lambda: None, adopt=lambda key: None,
                               reveal=lambda key, widget: None)
        self.page = page_providers.ProvidersPage(host)
        self.page.test_card = lambda card: None
        self.page.build(self.area.content).pack(fill='x')
        self.recorder = AcceptanceRecorder(self.page, verifier, native=native, mode=mode)
        update_controls = self.page._update_add_button
        def fixed_fixture_controls():
            update_controls()
            if self.page.body.winfo_exists():
                self.page.add_btn.configure(state='disabled')
                self.page.delete_all_btn.configure(state='disabled')
        self.page._update_add_button = fixed_fixture_controls
        toggle = self.page.toggle_all_keys
        self.page.show_all_btn.configure(command=lambda: self.run('toggle-all', toggle))
        for card in self.page.cards:
            card.field.configure(state='readonly')
            card.test_btn.configure(state='disabled')
            card.remove_btn.configure(state='disabled')
            card.provider.button.configure(state='disabled')
            card.model.button.configure(state='disabled')
            card.eye.configure(command=lambda item=card: self.run('single-key', lambda: self.page.reveal_clicked(item)))
        self.page.add_btn.configure(state='disabled')
        self.page.delete_all_btn.configure(state='disabled')
        self.area.adopt()
        page_providers.require_auth = self.recorder.verify
        self.update_progress()

    def _fit_footer(self, event):
        width = max(120, event.width)
        if width != self._wrap_width:
            self._wrap_width = width
            self.instructions.configure(wraplength=width)
            self.progress.configure(wraplength=width)

    def run(self, action, operation):
        self.recorder.run(action, operation)
        self.update_progress()

    def reset(self):
        def mask():
            for card in self.page.cards:
                card.set_revealed(False)
            self.page.invalidate_auth()
            self.page._update_add_button()
        self.run('reset', mask)

    def update_progress(self):
        if not self.closed and self.progress.winfo_exists():
            self.page.add_btn.configure(state='disabled')
            self.page.delete_all_btn.configure(state='disabled')
            labels = ('Show all', 'Hide all', 'One key', 'Cancel')
            checks = self.recorder.report()['checks']
            self.progress.configure(text=' | '.join(f'{label}: {"PASS" if passed else "pending"}'
                                                    for label, passed in zip(labels, checks.values())))

    def close(self):
        from src.ui.settings import page_providers
        if not self.closed:
            self.closed = True
            page_providers.require_auth = self.original_verifier
            if self.page.body.winfo_exists():
                self.page.body.destroy()
            if self.area.winfo_exists():
                self.area.destroy()
            if self.footer.winfo_exists():
                self.footer.destroy()
