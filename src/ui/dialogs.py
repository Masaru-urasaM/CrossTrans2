"""Application notices on the shared, nonmodal design system."""
import tkinter as tk

from src.core.remote_config import get_config  # noqa: F401 - kept: tests may patch this module attribute
from src.ui import components as ui


class _SettingsPrompt:
    def _open_settings(self):
        self.view.close()
        if self.on_open_settings_tab:
            self.on_open_settings_tab('API Key')
        elif self.on_open_settings:
            self.on_open_settings()

    def _open_help(self):
        self.view.close()
        if self.on_open_settings_tab:
            self.on_open_settings_tab('Guide')

    def _settings_actions(self):
        if self.on_open_settings or self.on_open_settings_tab:
            self.view.add_button('Open API keys', self._open_settings, default=True)
        self.view.add_button('Done')
        if self.on_open_settings_tab:
            ui.Button(self.view.body, 'How to get a key', command=self._open_help,
                      kind='subtle', icon='question-circle').pack(anchor='w', pady=(ui.get().px(12), 0))


class APIErrorDialog(_SettingsPrompt):
    def __init__(self, parent, error_message='', on_open_settings=None,
                 on_open_settings_tab=None):
        self.on_open_settings, self.on_open_settings_tab = on_open_settings, on_open_settings_tab
        message = ('Translation requires your own API key. Add a key in API keys, or check '
                   'the configured key and try again.')
        self.view = ui.Dialog(parent, "Let's connect a translation service", message, icon='cloud-plus')
        self.window = self.view.window
        if error_message:
            ui.InfoBar(self.view.body, error_message, kind='danger').pack(fill='x', pady=(ui.get().px(12), 0))
        self._settings_actions()
        self.view.show()






class UpdateFailedDialog:
    """Preserve the caller's synchronous result contract without a modal grab."""

    def __init__(self, parent, error_msg, pending_path=None, current_exe=None):
        self.result, self.pending_path, self.current_exe = 'cancel', pending_path, current_exe
        self.view = ui.Dialog(parent, "The update couldn't be installed",
                              'Choose another way to update, or try again later from Settings.',
                              icon='cloud-slash', on_cancel=self._on_update_cancel)
        self.window = self.view.window
        px = ui.get().px
        self.details = ui.InfoBar(self.view.body, error_msg, kind='danger')
        self.details.pack(fill='x', pady=(px(12), px(16)))
        self.option_var = tk.StringVar(self.window, value='reboot' if pending_path else 'manual')
        self.choices = []
        options = [('reboot', 'Install when Windows restarts')] if pending_path else []
        options += [('manual', 'Download manually from GitHub'), ('cancel', 'Try again later')]
        for value, text in options:
            choice = ui.Choice(self.view.body, text, self.option_var, value)
            choice.pack(anchor='w', pady=px(4))
            self.choices.append(choice)
        ui.Label(self.view.body, 'Details are saved in %TEMP%\\crosstrans_update.log.', kind='caption',
                  wraplength=px(440), justify='left').pack(anchor='w', pady=(px(12), 0))
        self.view.add_button('Continue', self._on_update_continue, close=False)
        self.view.add_button('Cancel', self._on_update_cancel, default=True, close=False)
        self.view.show()
        self.window.wait_window()

    def _on_update_continue(self):
        self.result = self.option_var.get()
        self.view.close()

    def _on_update_cancel(self):
        self.result = 'cancel'
        self.view.close()
