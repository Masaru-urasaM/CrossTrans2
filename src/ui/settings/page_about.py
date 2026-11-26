"""
About (TODO.md U8, Decision 51): the version and the update check, a way to the Help page, feedback and
the source.

The update check, the offer and the cancellable download are `UpdateManagerMixin`'s, and it drives
widgets by name (`check_update_btn`, `update_status_label`, `update_offer_frame` ...). They are made
here, in the version row, and handed to the host under those names - the one place a page puts widgets
on the host, because the mixin predates pages and its behaviour is covered by its own tests.
"""
import webbrowser

from src.constants import FEEDBACK_URL, GITHUB_REPO, VERSION
from src.ui import components as ui
from src.ui.components import context as C
from src.ui.settings.page import SettingsPage

GITHUB_URL = f'https://github.com/{GITHUB_REPO}'


class AboutPage(SettingsPage):
    key = 'about'

    def populate(self) -> None:
        host = self.host
        px = C.get().px
        self.buttons = {}

        group = self.group()
        row = self.row(group, 'version', stacked=True)
        plane = row.plane
        ui.Label(row.control, f'CrossTrans v{VERSION}', kind='subtitle', on=plane).pack(anchor='w')
        host.update_check_frame = ui.Frame(row.control, on=plane)
        host.update_check_frame.pack(anchor='w', fill='x', pady=(px(8), 0))
        host.check_update_btn = ui.Button(host.update_check_frame, 'Check for updates', kind='soft',
                                          icon='arrow-repeat', on=plane,
                                          command=lambda: host._check_for_updates_click())
        host.check_update_btn.pack(anchor='w')
        host.update_status_label = ui.Label(host.update_check_frame, '', kind='caption', on=plane,
                                            justify='left', wraplength=px(560))
        host.update_status_label.pack(anchor='w', fill='x', pady=(px(8), 0))
        host.update_check_frame.bind('<Configure>',
                                    lambda e: self._wrap(host.update_status_label, e.widget), add='+')
        host.update_offer_frame = ui.Frame(row.control, on=plane)
        host.update_offer_label = ui.Label(host.update_offer_frame, '', kind='strong', on=plane)
        host.update_offer_label.pack(side='left', padx=(0, px(12)))
        host.update_offer_button = ui.Button(host.update_offer_frame, 'View update', kind='accent', on=plane)
        host.update_offer_button.pack(side='left')

        group = self.group()
        row = self.row(group, 'github_update_token', stacked=True)
        ui.Label(row.control,
                 'Optional. Public updates work automatically without a token. Add a fine-grained GitHub token '
                 '(Contents: read) only if you hit GitHub rate limits or use a private fork. Windows encrypts it '
                 'for this user; it is separate from AI keys and GitHub CLI login.',
                 kind='muted', justify='left', wraplength=px(560), on=row.plane).pack(anchor='w', fill='x')
        self.update_token = ui.TextField(row.control, show='*', width=24, on=row.plane)
        self.update_token.pack(fill='x', pady=(px(8), px(8)))
        self.update_token.bind('<Return>', lambda e: self.save_update_token(), add='+')
        actions = ui.Frame(row.control, on=row.plane)
        actions.pack(fill='x')
        self.save_token_button = ui.Button(actions, 'Save update token', kind='soft', on=row.plane,
                                           command=self.save_update_token)
        self.save_token_button.grid(row=0, column=0, sticky='w')
        self.remove_token_button = ui.Button(actions, 'Remove token', kind='soft', on=row.plane,
                                             command=self.remove_update_token)
        self.remove_token_button.grid(row=0, column=1, sticky='w', padx=(px(8), 0))
        actions.bind('<Configure>', self._fit_token_actions, add='+')
        self.token_status = ui.Label(row.control, '', kind='caption', wraplength=px(560),
                                     justify='left', on=row.plane)
        self.token_status.pack(anchor='w', fill='x', pady=(px(8), 0))
        self.token_guide = ui.Button(row.control, 'Token setup guide', kind='soft', on=row.plane,
                                     icon='box-arrow-up-right', command=lambda: webbrowser.open(
                                         'https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens'))
        self.token_guide.pack(anchor='w', pady=(px(8), 0))
        self._token_saved_status()
        for label in (self.token_status, row.control.winfo_children()[0]):
            row.control.bind('<Configure>', lambda e, lb=label: self._wrap(lb, e.widget), add='+')

        group = self.group('Help and feedback')
        self._link(group, 'guide', 'Open Help', 'question-circle', lambda: host.open_page('help'))
        self._link(group, 'keyboard_help', 'Open Shortcuts', 'keyboard', lambda: host.open_page('shortcuts'))
        show_welcome = getattr(host, 'on_show_welcome', None)
        self._link(group, 'welcome', 'Show welcome again', 'emoji-smile', show_welcome)
        if show_welcome is None:
            self.buttons['welcome'].configure(state='disabled')
        self._link(group, 'feedback', 'Send feedback', 'chat-left-text', lambda: webbrowser.open(FEEDBACK_URL))
        self._link(group, 'diagnostics', 'Open diagnostics', 'bug', self.open_diagnostics)
        self.toggle(group, 'debug_logging', self._get_debug_logging, self._set_debug_logging)
        self._link(group, 'source', 'Open GitHub', 'box-arrow-up-right', lambda: webbrowser.open(GITHUB_URL))
        host.refresh_update_state()

    def _get_debug_logging(self) -> bool:
        return bool(self.config.get('debug_logging', False))

    def _set_debug_logging(self, enabled: bool) -> None:
        self.config.set('debug_logging', bool(enabled))
        from src.utils.logging_setup import set_debug_logging
        set_debug_logging(bool(enabled))

    def open_diagnostics(self):
        from src.ui.diagnostics_dialog import DiagnosticsDialog
        parent = getattr(self.host, 'window', None) or self.body.winfo_toplevel()
        status_fn = getattr(self.host, 'shortcut_status', None)
        status = status_fn() if callable(status_fn) else {}
        return DiagnosticsDialog(parent, config=self.config, shortcut_status=status)

    def _fit_token_actions(self, event):
        px = C.get().px
        narrow = event.width < self.save_token_button.winfo_reqwidth() + self.remove_token_button.winfo_reqwidth() + px(8)
        self.remove_token_button.grid_configure(row=1 if narrow else 0, column=0 if narrow else 1,
                                               padx=(0 if narrow else px(8), 0),
                                               pady=(px(8) if narrow else 0, 0))

    def _token_saved_status(self):
        saved = bool(self.config.get('github_update_token_encrypted', ''))
        self.token_status.configure(text='Update token saved. Enter a new token to replace it.' if saved
                                    else 'No update token saved. Public update checks work automatically.')

    def save_update_token(self):
        token = self.update_token.get()
        if not token.strip():
            return True
        try:
            self.config.set_github_update_token(token)
        except (ValueError, OSError):
            self.token_status.configure(text='Could not save the token. Check its format and Windows encryption.')
            return False
        self.update_token.clear()
        self._token_saved_status()
        self._credentials_changed()
        return True

    def remove_update_token(self):
        try:
            self.config.set_github_update_token('')
        except OSError:
            self.token_status.configure(text='Could not remove the token. Check file permissions and try again.')
            return
        self.update_token.clear()
        self._token_saved_status()
        self._credentials_changed()

    def _credentials_changed(self):
        if getattr(self.host, '_download_active', False):
            self.host._cancel_download()
        self.host.update_manager.credentials_changed()
        self.host.check_update_btn.configure(state='normal')
        self.host.update_status_label.configure(text='')
        self.host.changed('github_update_token')

    def _link(self, group, key: str, text: str, icon: str, command) -> None:
        row = self.row(group, key)
        button = ui.Button(row.control, text, command=command, kind='soft', icon=icon, on=row.plane)
        button.pack()
        self.buttons[key] = button

    def refresh(self) -> None:
        super().refresh()
        self.host.refresh_update_state()
