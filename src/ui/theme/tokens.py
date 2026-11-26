"""
Design tokens (Decision 46): the colours, spacing, radii and motion every surface draws from.

Pure data - no Tk, no application imports - so it can be tested without a display and imported
by `src.constants`. Direction A "Soft Fluent" with kawaii touches; every text pair is at least
4.5:1 and every boundary, focus ring and accent-on-surface at least 3:1 in both themes
(`tests/test_theme_tokens.py` asserts it; TODO.md 6.2 explains the values).
"""
from dataclasses import dataclass, fields, replace
from typing import Dict, Tuple

LIGHT_MODE = 'light'
DARK_MODE = 'dark'
SYSTEM_MODE = 'system'          # a user setting, never a palette: it resolves to light or dark
PINK_MODE, BLUE_MODE, YELLOW_MODE, GREEN_MODE = 'pink', 'blue', 'yellow', 'green'
LAVENDER_MODE, TEAL_MODE, PEACH_MODE, COFFEE_MODE = 'lavender', 'teal', 'peach', 'coffee'
MIDNIGHT_MODE, FOREST_MODE, PLUM_MODE, GRAPHITE_MODE = 'midnight', 'forest', 'plum', 'graphite'
THEME_OPTIONS = (('system', 'Follow Windows'), ('pink', 'Pink + White'),
                 ('blue', 'Ocean Blue + White'), ('yellow', 'Pale Yellow + White'),
                 ('green', 'Pale Green + White'), ('lavender', 'Lavender'),
                 ('teal', 'Teal'), ('peach', 'Peach'), ('coffee', 'Coffee'),
                 ('light', 'White'), ('dark', 'Black'), ('midnight', 'Midnight Blue'),
                 ('forest', 'Forest Night'), ('plum', 'Plum Night'), ('graphite', 'Graphite'))
MODES = tuple(key for key, _label in THEME_OPTIONS)


@dataclass(frozen=True)
class Palette:
    """One theme's colours, by role. Roles, not values: a surface says `surface`, never `#fff`."""
    mode: str
    bg: str             # the window
    surface: str        # a card, a popup, a dialog
    alt: str            # a quiet fill on a surface: chips, inset fields, hover
    border: str         # decorative hairlines
    strong: str         # a control's boundary: >= 3:1 against surface and bg
    text: str
    muted: str          # secondary text, still >= 4.5:1
    accent: str         # the one interactive colour
    on_accent: str
    hover: str          # the accent, hovered or pressed
    soft: str           # tonal button and chip fill
    soft_text: str
    warm: str           # the brand amber: moments (welcome, success), never an action
    on_warm: str
    success: str        # as text or icon on a surface
    warning: str
    danger: str
    focus: str          # the focus ring
    selection: str      # selected text
    ruby: str           # a furigana reading
    link: str           # the wash behind a sentence Linked reading points at (text and ruby stay >= 4.5:1)

    def as_dict(self) -> Dict[str, str]:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != 'mode'}

    @property
    def selection_text(self):
        return self.on_accent if self.selection == self.accent else self.text


LIGHT = Palette(
    mode=LIGHT_MODE, bg='#F7F5F2', surface='#FFFFFF', alt='#F2EFEB', border='#E3DED7',
    strong='#8A847C', text='#1F2328', muted='#5C636E', accent='#0F6CBD', on_accent='#FFFFFF',
    hover='#0C5A9E', soft='#E5F0FA', soft_text='#0B5394', warm='#F5A623', on_warm='#3B2600',
    success='#1B7A4A', warning='#8A5A00', danger='#C42B3C', focus='#0F6CBD', selection='#CFE3F6',
    ruby='#2563C9', link='#FBE7B0')

DARK = Palette(
    mode=DARK_MODE, bg='#1B1C20', surface='#25272C', alt='#2E3036', border='#3A3D45',
    strong='#7A808A', text='#ECEDEF', muted='#A7ACB5', accent='#5EA8E8', on_accent='#08192A',
    hover='#7DBAF0', soft='#22364B', soft_text='#A9D1F6', warm='#F7B347', on_warm='#2A1A00',
    success='#5CC08A', warning='#F0C04C', danger='#FF7B86', focus='#7DBAF0', selection='#2C4A6B',
    ruby='#80B8FF', link='#35434B')            # the dark reading colour is Decision 9's

PINK = replace(LIGHT, bg='#FFF4F8', alt='#FBEAF1', border='#EAC9D8', strong='#94717F',
               accent='#A02C61', hover='#84204D', soft='#F8DCE9', soft_text='#7C214A', focus='#A02C61',
               selection='#F4D7E5')
BLUE = replace(LIGHT, bg='#F0F8FF', alt='#E7F2FC', border='#C9DEEF', strong='#68829A')
YELLOW = replace(LIGHT, bg='#FFFCEE', alt='#F9F2D4', border='#E4D9AF', strong='#8D8060',
                 accent='#7D5A10', hover='#654609', soft='#F8EAB7', soft_text='#66460B',
                 focus='#7D5A10', selection='#F1E1A7')
GREEN = replace(LIGHT, bg='#F1FBF4', alt='#E5F4E9', border='#C7DECE', strong='#698571',
                accent='#246B46', hover='#195334', soft='#DCEFE1', soft_text='#1C5736',
                focus='#246B46', selection='#D1E8D8')
LAVENDER = replace(LIGHT, bg='#F7F3FF', alt='#EEE7F8', border='#D9CCE9', strong='#80708F',
                   accent='#6E42A8', hover='#573286', soft='#E9DDF8', soft_text='#512C81',
                   focus='#6E42A8', selection='#E4D5F5')
TEAL = replace(LIGHT, bg='#F0FAF8', alt='#DFF2ED', border='#C2DDD5', strong='#597F75',
               accent='#086F63', hover='#07594F', soft='#D0EDE6', soft_text='#075247',
               focus='#086F63', selection='#C9E6DE')
PEACH = replace(LIGHT, bg='#FFF5EF', alt='#FBE7DA', border='#E8CDBB', strong='#98755F',
                accent='#A34720', hover='#833719', soft='#F9DDCA', soft_text='#793115',
                focus='#A34720', selection='#F0D2BD')
COFFEE = replace(LIGHT, bg='#FAF6F0', alt='#EFE5DA', border='#D9C9B6', strong='#8B7966',
                 accent='#805332', hover='#674227', soft='#EBDAC6', soft_text='#604027',
                 focus='#805332', selection='#E5D3BD')
MIDNIGHT = replace(DARK, bg='#121D31', surface='#1B2B43', alt='#253953', border='#344B68',
                   strong='#7D96B5', muted='#B0BDD0', accent='#8AB9FF', on_accent='#0B1F3A',
                   hover='#A8CAFF', soft='#263E60', soft_text='#C5DCFF', focus='#A8CAFF',
                   selection='#284C7C', link='#304256')
FOREST = replace(DARK, bg='#14241F', surface='#1F322A', alt='#2A4035', border='#3D5749',
                 strong='#85A794', muted='#ADBCB1', accent='#7BCDA2', on_accent='#0C281B',
                 hover='#9EDCB9', soft='#264C39', soft_text='#B7E7CB', focus='#9EDCB9',
                 selection='#28513E', link='#30443B')
PLUM = replace(DARK, bg='#271B2D', surface='#35243E', alt='#452F50', border='#5A4068',
               strong='#B194BD', muted='#C1ACC8', accent='#D9A1ED', on_accent='#32133F',
               hover='#E7BFF5', soft='#4C315A', soft_text='#F0CEF9', focus='#E7BFF5',
               selection='#5C3B6B', link='#43364F')
GRAPHITE = replace(DARK, bg='#1D1F23', surface='#2A2D32', alt='#353940', border='#494E58',
                   strong='#969EA9', muted='#B4BAC4', accent='#C0C7D1', on_accent='#1F252D',
                   hover='#D7DCE4', soft='#3B424D', soft_text='#D7DEE9', focus='#D7DCE4',
                   selection='#424C5B', link='#39404B')
PALETTES = {PINK_MODE: PINK, BLUE_MODE: BLUE, YELLOW_MODE: YELLOW, GREEN_MODE: GREEN,
            LAVENDER_MODE: LAVENDER, TEAL_MODE: TEAL, PEACH_MODE: PEACH, COFFEE_MODE: COFFEE,
            LIGHT_MODE: LIGHT, DARK_MODE: DARK, MIDNIGHT_MODE: MIDNIGHT, FOREST_MODE: FOREST,
            PLUM_MODE: PLUM, GRAPHITE_MODE: GRAPHITE}

# (foreground role, background role) pairs the design promises to keep readable.
TEXT_PAIRS: Tuple[Tuple[str, str], ...] = (
    ('text', 'bg'), ('text', 'surface'), ('text', 'alt'),
    ('muted', 'bg'), ('muted', 'surface'), ('muted', 'alt'),
    ('on_accent', 'accent'), ('on_accent', 'hover'), ('soft_text', 'soft'),
    ('on_warm', 'warm'), ('success', 'surface'), ('warning', 'surface'),
    ('danger', 'surface'), ('ruby', 'surface'), ('text', 'selection'), ('text', 'link'),
    ('ruby', 'link'),
)
BOUNDARY_PAIRS: Tuple[Tuple[str, str], ...] = (
    ('strong', 'surface'), ('strong', 'bg'), ('focus', 'surface'), ('focus', 'bg'),
    ('accent', 'surface'), ('accent', 'bg'),
)

# Spacing on Fluent's 4 px grid; radii in px at 100 %; chips are pills.
SPACING: Tuple[int, ...] = (4, 8, 12, 16, 24, 32)
RADIUS = {'window': 8, 'dialog': 8, 'card': 8, 'popup': 8, 'control': 6}
PILL = 'pill'
CONTROL_HEIGHT = 32           # px at 100 %; icon buttons are 32 x 32, every target >= 24 x 24
MIN_TARGET = 24

# The colour a window makes transparent (`-transparentcolor`): the popup's resize outline is a frame
# whose inside is this colour. Near-black but not black, so nothing else is ever this exact value.
COLOUR_KEY = '#000001'

# Motion (UQ14: subtle). Milliseconds; all of it off when Windows animation effects are off.
MOTION_MS = {'fast': 100, 'normal': 150, 'slow': 200}
