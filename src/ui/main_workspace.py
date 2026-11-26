"""Primary HOME/SETTINGS navigation in the existing main application window."""
from src.ui import components as ui
from src.ui.components import context as C


class MainWorkspace:
    def __init__(self, window, translation_frame, on_open_settings, *, container=None):
        self.window = window
        self.settings = None
        self.primary = 'home'
        self.home_page = 'translate'
        self.on_open_settings = on_open_settings
        parent = container if container is not None else window
        px = C.get().px
        if translation_frame is not None:
            translation_frame.pack_forget()
        self.tabs = ui.SegmentedControl(parent, [('home', 'Home'), ('settings', 'Settings')],
                                        'home', on_change=self.request_primary, icons={'home': 'house', 'settings': 'gear'},
                                        padding=(px(16), px(10)))
        self.tabs.pack(side='top', fill='x')
        self.content = ui.Frame(parent, on='bg')
        self.content.pack(fill='both', expand=True)
        self.home_host = ui.Frame(self.content, on='bg')
        self.settings_host = ui.Frame(self.content, on='bg')
        self.home_host.pack(fill='both', expand=True)
        self.home_sidebar = ui.Frame(self.home_host, on='alt', padding=px(12))
        self.home_sidebar.pack(side='left', fill='y')
        self.home_navigation = ui.NavigationView(
            self.home_sidebar, [('translate', 'Translate', 'translate')], value='translate',
            on_select=self.select_home, on='alt')
        self.home_navigation.pack(fill='x')
        self.translate_host = ui.Frame(self.home_host, on='bg')
        self.translate_host.pack(side='left', fill='both', expand=True)
        if translation_frame is not None:
            translation_frame.pack(in_=self.translate_host, fill='both', expand=True)
            translation_frame.lift()
        self.pages = {'translate': self.translate_host}
        self.controllers = {}
        self.factories = {}

    def request_primary(self, primary):
        if primary == 'settings':
            self.on_open_settings()
        else:
            self.show_primary('home')

    def show_primary(self, primary):
        if primary not in ('home', 'settings'):
            raise ValueError(primary)
        if self.primary == 'settings' and primary != 'settings' and self.settings is not None:
            self.settings.deactivate()
        self.home_host.pack_forget()
        self.settings_host.pack_forget()
        selected = self.home_host if primary == 'home' else self.settings_host
        selected.pack(fill='both', expand=True)
        self.primary = primary
        self.tabs.value = primary

    def select_home(self, key):
        if key not in self.pages:
            if key not in self.factories:
                raise ValueError(key)
            host = ui.Frame(self.home_host, on='bg')
            self.controllers[key] = self.factories[key](host)
            self.pages[key] = host
        self.show_primary('home')
        for name, page in self.pages.items():
            if name != key:
                page.pack_forget()
        self.pages[key].pack(side='left', fill='both', expand=True)
        self.home_page = key
        self.home_navigation.value = key
        controller = self.controllers.get(key)
        if controller is not None and hasattr(controller, 'activate'):
            controller.activate()
        return controller

    def configure_home_pages(self, dictionary, history):
        self.factories.update(dictionary=dictionary, history=history)
        self.home_navigation.destroy()
        self.home_navigation = ui.NavigationView(self.home_sidebar,
            [('translate', 'Translate', 'translate'), ('dictionary', 'Dictionary', 'book'),
             ('history', 'History', 'clock-history')], value=self.home_page,
             on_select=self.select_home, on='alt')
        self.home_navigation.pack(fill='x')

    def primary_action(self):
        if self.primary == 'settings':
            return True
        if self.home_page == 'dictionary':
            self.controllers['dictionary'].lookup_phrase()
            return True
        return self.home_page != 'translate'

    def focus_search(self, event=None):
        if self.primary == 'home' and self.home_page == 'history':
            self.controllers['history'].search_entry.focus_set()
            return 'break'
