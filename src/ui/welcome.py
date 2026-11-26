"""First-run setup: appearance, user keys, language packs, general preferences and Home."""
import tkinter as tk
from types import SimpleNamespace

from src.constants import LANGUAGES
from src.core.onboarding import OnboardingState, SAMPLE, STEPS
from src.ui import components as ui
from src.ui.components.dialog import work_area
from src.ui.components.theme_picker import ThemePicker
from src.ui.settings.page_providers import ProvidersPage
from src.ui.settings.page_dictionary import DictionaryPage
from src.ui.settings.pages_basic import GeneralPage
from src.ui.theme import engine
from src.ui.translator_layout import window_size, window_position


class SetupGeneralPage(GeneralPage):
    def populate(self):
        self._autostart_job = None
        self._autostart_wanted = None
        group = self.group()
        cfg = self.config
        self.toggle(group, 'autostart', cfg.is_autostart_enabled, self._queue_autostart)
        self.toggle(group, 'auto_update', cfg.get_auto_check_updates, self._set_auto_update)
        self.toggle(group, 'history_saving', lambda: cfg.get('history_enabled', True),
                    lambda value: cfg.set('history_enabled', value))
        self.body.bind('<Destroy>', self._on_destroy, add='+')


class SetupHost:
    def __init__(self, owner):
        self.owner = owner
        self.config = owner.config
        self.window = owner.window
        self.pages = {}
        self.update_manager = owner.update_manager
        self.key_source_factory = None

    def changed(self, key):
        if self.owner.on_changed:
            self.owner.on_changed(key)

    def set_recording(self, active):
        pass

    def shortcut_status(self):
        return {}

    def api_changed(self):
        if self.owner.on_api_change:
            self.owner.on_api_change()

    def adopt(self, key):
        self.owner.scroll.adopt()

    def reveal(self, key, widget):
        self.owner.scroll.reveal(widget)

    def refresh_pages(self):
        for page in self.pages.values():
            page.refresh()


class WelcomeManager:
    def __init__(self, root, config, *, on_try, on_settings, toast, on_complete=None,
                 on_api_change=None, on_changed=None, update_manager=None):
        self.root, self.config = root, config
        self.state = OnboardingState(config)
        self.on_try, self.on_settings, self.toast = on_try, on_settings, toast
        self.on_complete, self.on_api_change, self.on_changed = on_complete, on_api_change, on_changed
        self.update_manager = update_manager or SimpleNamespace(auto_check_changed=lambda: None)
        self.window = None
        self.step = 0
        self.target = config.get_default_target_language()
        self.autostart = tk.BooleanVar(root, value=config.get_autostart())
        self._startup_job = None
        self.frames = {}
        self.warning = None
        self._no_key_acknowledged = False

    def schedule(self):
        if self.state.should_show and self._startup_job is None:
            self._startup_job = self.root.after(350, self._first_run)

    def _first_run(self):
        self._startup_job = None
        if self.state.should_show:
            self.show()

    def show(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return self.window
        ctx = ui.get()
        self.target = self.config.get_default_target_language()
        self.autostart.set(self.config.get_autostart())
        self.step = self.state.resume_step if self.state.should_show else 0
        self.frames = {}
        self.warning = None
        self._no_key_acknowledged = False
        self.window = tk.Toplevel(self.root)
        self.window.withdraw()
        self.window.title('Set up CrossTrans 2')
        self.window.sizes_from_work_area = True
        engine.attach(self.window)
        size = window_size(work_area(), scale=ctx.scale, default=(900, 720), minimum=(500, 440))
        x, y = window_position(size, work_area())
        self.window.geometry(f'{size.width}x{size.height}+{x}+{y}')
        self.window.minsize(size.min_width, size.min_height)
        body = ui.Frame(self.window, padding=ctx.px(20))
        body.pack(fill='both', expand=True)
        footer = ui.Frame(body)
        footer.pack(side='bottom', fill='x', pady=(ctx.px(16), 0))
        self.next_button = ui.Button(footer, 'Next', self.next, kind='accent')
        self.next_button.pack(side='right')
        self.back_button = ui.Button(footer, 'Back', self.back, kind='subtle')
        self.back_button.pack(side='right', padx=ctx.px(8))
        self.skip_button = ui.Button(footer, 'Skip for now', self.skip, kind='subtle')
        self.skip_button.pack(side='left')
        self.progress = ui.Label(body, '', kind='caption')
        self.progress.pack(side='bottom', pady=(ctx.px(8), 0))
        self.scroll = ui.ScrollArea(body)
        self.scroll.pack(fill='both', expand=True)
        self.host = SetupHost(self)
        self.window.protocol('WM_DELETE_WINDOW', self.skip)
        self.window.bind('<Escape>', self._escape)
        self.window.bind('<Destroy>', self._destroyed, add='+')
        self._render()
        self.window.deiconify()
        self.window.lift()
        self.next_button.focus_set()
        return self.window

    def _render(self):
        titles = ('Appearance and language', 'API keys', 'Dictionary language packs',
                  'General preferences', 'Your setup is ready')
        self.progress.configure(text=f'Step {self.step + 1} of {STEPS}')
        self.back_button.configure(state='disabled' if self.step == 0 else 'normal')
        self.next_button.configure(text='Open Home' if self.step == STEPS - 1 else 'Next')
        for frame in self.frames.values():
            frame.pack_forget()
        if self.step not in self.frames:
            frame = ui.Frame(self.scroll.content)
            frame.bind('<Configure>', lambda event, host=frame: self._wrap_page(host, event.width), add='+')
            self.frames[self.step] = frame
            ui.Label(frame, titles[self.step], kind='subtitle', wraplength=ui.get().px(600)).pack(anchor='w', pady=(0, ui.get().px(12)))
            if self.step == 0:
                language = ui.Dropdown(frame, [('en', 'English'), ('ja', '日本語')],
                                       self.config.get('ui_language', 'en'), on_change=self._choose_ui_language,
                                       label='Interface language')
                language.pack(anchor='w', pady=(0, ui.get().px(12)))
                self.theme_picker = ThemePicker(frame, self.config)
                self.theme_picker.pack(fill='x')
                ui.Label(frame, 'Skipping without a theme choice follows Windows: Pink + White or Black.',
                          kind='muted', wraplength=ui.get().px(600), justify='left').pack(fill='x', pady=ui.get().px(8))
            elif self.step in (1, 2, 3):
                page_type = {1: ProvidersPage, 2: DictionaryPage, 3: SetupGeneralPage}[self.step]
                page = page_type(self.host)
                self.host.pages[page.key] = page
                if self.step == 1:
                    ui.InfoBar(frame, 'Translation requires your own API key. Add one here, or explicitly continue without a key.',
                               kind='warning').pack(fill='x', pady=(0, ui.get().px(12)))
                    self.api_page = page
                elif self.step == 2:
                    ui.Label(frame, 'Packs are optional. Install only the languages you need; you can do this later.',
                              kind='muted', wraplength=ui.get().px(600), justify='left').pack(fill='x')
                else:
                    ui.LanguagePicker(frame, LANGUAGES, self.target, on_change=self._choose_target,
                                       label='Translate into').pack(anchor='w', pady=(0, ui.get().px(12)))
                page.build(frame).pack(fill='x')
            else:
                ui.Label(frame, 'Your choices are saved. Open Home to start translating.',
                          wraplength=ui.get().px(600), justify='left').pack(fill='x')
                ui.Button(frame, 'Try a translation', self.try_sample, kind='soft').pack(anchor='w', pady=ui.get().px(12))
                ui.Label(frame, 'Trying the sample sends it to your translation service.', kind='caption',
                          wraplength=ui.get().px(600)).pack(anchor='w')
        self.frames[self.step].pack(fill='both', expand=True)
        self.scroll.adopt()
        self.scroll.scroll_to_top()

    @staticmethod
    def _wrap_page(frame, width):
        for child in frame.winfo_children():
            if isinstance(child, ui.Label) and int(float(child.cget('wraplength') or 0)):
                child.configure(wraplength=max(1, width - ui.get().px(8)))

    def _choose_ui_language(self, value):
        self.config.set('ui_language', value)
        ui.get().manager.set_ui_language(value)

    def _choose_target(self, language):
        self.target = language
        self.config.set_default_target_language(language)

    def _flush(self):
        page = self.host.pages.get('providers')
        if page is not None:
            page._cancel_save()
            page._save_if_changed()
            sec = getattr(page, 'audio_section', None)
            if sec is not None and hasattr(sec, 'shortcut_editor'):
                sec.shortcut_editor.cancel_recording()
            for card in page.cards:
                card.set_revealed(False)
            page._authenticated = False
        general = self.host.pages.get('general')
        if general is not None and general._autostart_job is not None:
            general._cancel_autostart()
            general._apply_autostart()

    def _has_key(self):
        return any(item.get('api_key', '').strip() for item in self.config.get_api_keys())

    def _warn_without_key(self, action):
        if self.warning is not None and self.warning.window.winfo_exists():
            self.warning.window.lift()
            return self.warning
        dialog = ui.Dialog(self.window, 'An API key is required to translate',
                           'You can open Home without a key, but translation will not work until you add your own key in Settings.')
        dialog.add_button('Add a key', self.add_key, default=True)
        def accept():
            self._no_key_acknowledged = True
            action()
        dialog.add_button('Continue without a key', accept)
        dialog.show()
        self.warning = dialog
        return dialog

    def next(self):
        self._flush()
        if self.step == 1 and not self._has_key() and not self._no_key_acknowledged:
            return self._warn_without_key(self._advance)
        if self.step == STEPS - 1:
            if not self._has_key() and not self._no_key_acknowledged:
                return self._warn_without_key(self._finish)
            return self._finish()
        self._advance()

    def _advance(self):
        self.step = min(STEPS - 1, self.step + 1)
        self.config.set('setup_step', self.step)
        self._render()

    def back(self):
        self._flush()
        if self.step > 0:
            self.step -= 1
            self.config.set('setup_step', self.step)
            self._render()

    def _escape(self, event=None):
        self.skip()
        return 'break'

    def skip(self):
        self._flush()
        if not self._has_key() and not self._no_key_acknowledged:
            return self._warn_without_key(self._finish)
        self._finish()

    def _finish(self):
        packs = self.host.pages.get('dictionary')
        if packs is not None and packs.busy:
            self.step = 2
            self._render()
            packs.show_notice('warning', 'Wait for the language-pack operation to finish before closing setup.')
            return False
        self._flush()
        self.state.finish()
        self.window.destroy()
        if self.on_complete:
            self.on_complete()
        return True

    def add_key(self):
        self.step = 1
        self.config.set('setup_step', self.step)
        self._render()

    def try_sample(self):
        self._flush()
        if not self._has_key():
            return self._warn_without_key(self._finish)
        if not self._finish():
            return
        self.on_try(SAMPLE, self.config.get_default_target_language())

    def note_success(self, text, failed=False):
        if self.state.first_success(text, failed=failed):
            self.toast.show_success('Your first translation is ready. You can copy it or replace the original.')

    def _destroyed(self, event):
        if event.widget is self.window:
            self.frames.clear()
            self.host.pages.clear()

    def close(self):
        if self._startup_job:
            self.root.after_cancel(self._startup_job)
            self._startup_job = None
        if self.window is not None and self.window.winfo_exists():
            self._flush()
            self.window.destroy()
