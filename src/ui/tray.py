"""
System Tray Manager for CrossTrans.
Handles system tray icon and menu.
"""
import os
import sys
import webbrowser
from typing import Callable, Optional

from pystray import Icon, MenuItem, Menu
from PIL import Image, ImageDraw

from src.constants import VERSION, FEEDBACK_URL
from src.ui.theme import brand
from src.ui.theme.manager import palette
from src.ui.i18n import tr


def get_resource_path(relative_path: str) -> str:
    """Get absolute path to resource, works for dev and PyInstaller bundle.

    Args:
        relative_path: Path relative to the app root

    Returns:
        Absolute path to the resource
    """
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    else:
        # Running as script - go up from src/ui/ to project root
        base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base_path, relative_path)


class TrayManager:
    """Manages system tray icon and menu."""

    def __init__(self, config):
        """Initialize tray manager.

        Args:
            config: Config object for reading hotkey settings
        """
        self.config = config
        self.tray_icon: Optional[Icon] = None

        # Callbacks
        self._on_show_main_window: Optional[Callable[[], None]] = None
        self._on_show_settings: Optional[Callable[[], None]] = None
        self._on_quit: Optional[Callable[[], None]] = None
        self._on_update: Optional[Callable[[], None]] = None
        self._on_open_logs: Optional[Callable[[], None]] = None
        self._on_audio_panel: Optional[Callable[[], None]] = None
        self._update_version = None

    def configure_callbacks(self,
                            on_show_main_window: Optional[Callable[[], None]] = None,
                            on_show_settings: Optional[Callable[[], None]] = None,
                            on_quit: Optional[Callable[[], None]] = None,
                            on_update: Optional[Callable[[], None]] = None,
                            on_open_logs: Optional[Callable[[], None]] = None,
                            on_audio_panel: Optional[Callable[[], None]] = None):
        """Configure callback functions for tray menu actions.

        Args:
            on_show_main_window: Called when user clicks Open Translator
            on_show_settings: Called when user clicks Settings
            on_quit: Called when user clicks Quit
            on_open_logs: Called when user clicks Open Logs
            on_audio_panel: Called when user clicks Audio panel (shown while audio translation is on)
        """
        self._on_show_main_window = on_show_main_window
        self._on_show_settings = on_show_settings
        self._on_quit = on_quit
        self._on_update = on_update
        self._on_open_logs = on_open_logs
        self._on_audio_panel = on_audio_panel

    def _create_icon_image(self) -> Image.Image:
        """The tray icon: the CrossTrans mark drawn at the size this display's notification area
        uses (16 / 20 / 24 / 32 px) - never the logo squashed into a square (TODO.md V16)."""
        try:
            return brand.tray_image(brand.system_tray_size())
        except Exception:
            # Fallback: a plain badge if drawing fails for any reason
            image = Image.new('RGB', (64, 64), color=palette().accent)
            draw = ImageDraw.Draw(image)
            draw.text((18, 18), "CT", fill=palette().on_accent)
            return image

    def _build_menu_items(self) -> list:
        """Build menu items list from config.

        Returns:
            List of MenuItem objects
        """
        menu_items = [
            MenuItem(tr('Open Translator'), lambda: self._on_show_main_window() if self._on_show_main_window else None, default=True),
            MenuItem(tr('Settings'), lambda: self._on_show_settings() if self._on_show_settings else None),
            Menu.SEPARATOR,
        ]
        if self._update_version:
            menu_items.insert(2, MenuItem(
                tr('Update available: v{0}', self._update_version),
                lambda: self._on_update() if self._on_update else None))
        getter = getattr(self.config, 'get', None)
        if self._on_audio_panel and getter is not None and getter('audio_enabled', True) is True:
            menu_items.insert(1, MenuItem(tr('Audio panel'), lambda: self._on_audio_panel()))

        # Add all hotkeys (default + custom) from config
        shortcuts_enabled = getattr(self.config, 'get_shortcuts_enabled', lambda: True)() is not False
        all_hotkeys = self.config.get_all_hotkeys() if shortcuts_enabled else {}
        for language, hotkey in all_hotkeys.items():
            if not hotkey:
                continue
            # Format hotkey for display (e.g., "win+alt+v" -> "Win+Alt+V")
            display_hotkey = '+'.join(part.capitalize() for part in hotkey.split('+'))
            menu_items.append(
                MenuItem(f'{display_hotkey} \u2192 {tr(language)}', lambda: None, enabled=False)
            )

        # Add screenshot hotkey if vision capability is available
        if shortcuts_enabled and self.config.has_any_vision_capable():
            screenshot_hotkey = self.config.get_screenshot_hotkey()
            if screenshot_hotkey:
                display_hotkey = '+'.join(part.capitalize() for part in screenshot_hotkey.split('+'))
                menu_items.append(
                    MenuItem(f'{display_hotkey} \u2192 {tr("Screenshot translation")}', lambda: None, enabled=False)
                )

        # Add Fix Grammar hotkey hint only when the global hotkey is enabled
        if shortcuts_enabled and self.config.get_fix_grammar_hotkey_enabled():
            fix_grammar_hotkey = self.config.get_fix_grammar_hotkey()
            if fix_grammar_hotkey:
                display_hotkey = '+'.join(part.capitalize() for part in fix_grammar_hotkey.split('+'))
                menu_items.append(
                    MenuItem(f'{display_hotkey} \u2192 {tr("Fix grammar")}', lambda: None, enabled=False)
                )

        menu_items.extend([
            Menu.SEPARATOR,
            MenuItem(tr('Open Logs'), self._open_logs_action),
            MenuItem(tr('Send Feedback'), lambda: webbrowser.open(FEEDBACK_URL)),
            MenuItem(tr('Quit'), lambda: self._on_quit() if self._on_quit else None)
        ])

        return menu_items

    def _open_logs_action(self) -> None:
        if self._on_open_logs:
            self._on_open_logs()
        else:
            from src.utils.diagnostics import open_logs_folder
            open_logs_folder()

    def create(self) -> Icon:
        """Create and return the system tray icon.

        Returns:
            The pystray Icon object
        """
        image = self._create_icon_image()
        menu_items = self._build_menu_items()
        menu = Menu(*menu_items)

        self.tray_icon = Icon("CrossTrans", image,
                              f"CrossTrans v{VERSION}", menu)
        return self.tray_icon

    def refresh_menu(self):
        """Refresh tray menu to reflect updated hotkeys."""
        if self.tray_icon:
            menu_items = self._build_menu_items()
            self.tray_icon.menu = Menu(*menu_items)

    def set_update_available(self, version):
        self._update_version = version
        self.refresh_menu()

    def stop(self):
        """Stop and cleanup the tray icon."""
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
