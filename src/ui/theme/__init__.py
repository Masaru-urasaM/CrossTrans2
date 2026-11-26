"""
The design system's single source of style (Decision 46, TODO.md DoD1).

    tokens    the named light/dark palettes, spacing, radii, motion - pure data
    contrast  WCAG arithmetic
    scale     px(): a size at 100 % -> pixels on this screen
    fonts     the type ramp and font resolution with explicit fallbacks
    manager   which named palette is in force (or follows Windows), subscribers, Windows detection
    windows   title bar, corners, window icon (DWM)

Import the module you need (`from src.ui.theme import tokens`); this package imports nothing
from the application, so `src.constants` can use it without a cycle.
"""
