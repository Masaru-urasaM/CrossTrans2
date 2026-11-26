"""
CrossTrans's component set (TODO.md U4b): the building blocks every surface is made of, drawn from
the design tokens and rebuilt for each theme.

    from src.ui import components as ui
    card = ui.Card(window)
    ui.Label(card, 'Hello', kind='title').pack()
    ui.Button(card, 'Translate', kind='accent', command=...).pack()

`install()` (called by the application after the theme engine) builds the styles; every component
asks `context.get()` for sizes, fonts, palette and icons.
"""
from src.ui.components.composite import (EmptyState, Keycap, Keys, NavigationView, ScrollArea)
from src.ui.components.context import PLANES, Context, get, install, reset
from src.ui.components.dialog import Dialog
from src.ui.components.feedback import InfoBar, Tooltip
from src.ui.components.language_picker import LanguagePicker, SourceLanguagePicker
from src.ui.components.mascots import load as mascot
from src.ui.components.model_picker import ModelPicker
from src.ui.components.menu import Menu
from src.ui.components.settings import Dropdown, KeySource, SettingGroup, SettingRow, ShortcutEditor
from src.ui.components.widgets import (ActionBar, AutoScrollbar, Button, Card, Chip, Choice, Frame, IconButton, Label,
                                       Progress, SegmentedControl, Separator, Switch, TextField,
                                       ThinScrollbar, Toggle)

__all__ = ['ActionBar', 'Menu', 'PLANES', 'Context', 'get', 'install', 'reset', 'Dialog', 'EmptyState', 'InfoBar', 'mascot',
           'Keycap', 'Keys', 'SourceLanguagePicker', 'LanguagePicker', 'ModelPicker', 'NavigationView', 'ScrollArea', 'Tooltip', 'AutoScrollbar', 'Button', 'Card', 'Chip',
           'Choice', 'Frame', 'IconButton', 'Label', 'Progress', 'SegmentedControl', 'Separator',
           'Switch', 'TextField', 'ThinScrollbar', 'Toggle', 'Dropdown', 'KeySource', 'SettingGroup',
           'SettingRow', 'ShortcutEditor']
