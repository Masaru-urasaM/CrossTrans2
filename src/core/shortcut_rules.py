"""
Which key combinations CrossTrans can register, and what is wrong with one it cannot (TODO.md U8).

Pure logic, no Tk and no keyboard hook: the Settings window records a combination with the
`keyboard` library, whose names (`esc`, `page up`, `left windows`) are not the ones
`HotkeyManager` registers with `RegisterHotKey` (`escape`, `pageup`, `win`). A recording the manager
cannot register used to be saved anyway and fail silently at the next start; this module normalises
what was recorded, says in plain words what is wrong, and tells a Windows-owned combination (an error)
from one the Xbox Game Bar also wants (a warning - the user may still choose it).

The order a shortcut is written in is Win, Ctrl, Alt, Shift, then the key, so the defaults
(`win+alt+e`) are already canonical.
"""
from typing import Dict, List, Mapping, NamedTuple, Optional, Tuple

from src.core.hotkey import HotkeyManager

MODIFIERS: Tuple[str, ...] = ('win', 'ctrl', 'alt', 'shift')

_MODIFIER_NAMES: Dict[str, str] = {
    'win': 'win', 'windows': 'win', 'left windows': 'win', 'right windows': 'win', 'cmd': 'win',
    'super': 'win', 'ctrl': 'ctrl', 'control': 'ctrl', 'left ctrl': 'ctrl', 'right ctrl': 'ctrl',
    'alt': 'alt', 'left alt': 'alt', 'right alt': 'alt', 'alt gr': 'alt',
    'shift': 'shift', 'left shift': 'shift', 'right shift': 'shift',
}
_KEY_NAMES: Dict[str, str] = {
    'esc': 'escape', 'return': 'enter', 'del': 'delete', 'ins': 'insert', 'page up': 'pageup',
    'page down': 'pagedown', 'spacebar': 'space', 'pgup': 'pageup', 'pgdn': 'pagedown',
}

# What a key is called on screen, where the key's own name would not do.
KEY_LABELS: Dict[str, str] = {
    'win': 'Win', 'ctrl': 'Ctrl', 'alt': 'Alt', 'shift': 'Shift', 'escape': 'Esc', 'pageup': 'Page Up',
    'pagedown': 'Page Down', 'space': 'Space', 'enter': 'Enter', 'tab': 'Tab', 'backspace': 'Backspace',
    'delete': 'Delete', 'insert': 'Insert', 'home': 'Home', 'end': 'End', 'left': 'Left', 'up': 'Up',
    'right': 'Right', 'down': 'Down',
}

# Combinations Windows keeps for itself (RegisterHotKey fails or the system wins).
RESERVED = frozenset({
    'alt+f4', 'ctrl+alt+delete', 'ctrl+escape', 'alt+tab', 'alt+escape', 'ctrl+shift+escape',
    'alt+space', 'win+l', 'win+d', 'win+tab',
})

# Combinations the Xbox Game Bar also uses by default: a warning, not a refusal.
GAME_BAR: Dict[str, str] = {
    'win+alt+g': 'record the last moments',
    'win+alt+r': 'start or stop recording',
    'win+alt+b': 'turn HDR on or off',
    'win+alt+m': 'turn the microphone on or off',
    'win+alt+t': 'show or hide the recording timer',
}

OK, WARNING, ERROR = 'ok', 'warning', 'error'


class Verdict(NamedTuple):
    kind: str                 # OK, WARNING or ERROR
    message: str              # plain words, '' when there is nothing to say
    normalized: str           # the combination as it would be stored

    @property
    def ok(self) -> bool:
        """A warning is still a combination the user may keep."""
        return self.kind != ERROR


def normalize(text: Optional[str]) -> str:
    """`text` as the manager registers it: lower case, one name per key, modifiers first in the
    order Win, Ctrl, Alt, Shift, each once. Anything it cannot read is returned cleaned, not
    guessed at - `validate` is what judges it."""
    if not text:
        return ''
    modifiers = set()
    keys: List[str] = []
    for raw in str(text).lower().split('+'):
        part = ' '.join(raw.split())
        if not part:
            continue
        if part in _MODIFIER_NAMES:
            modifiers.add(_MODIFIER_NAMES[part])
        else:
            keys.append(_KEY_NAMES.get(part, part))
    ordered = [name for name in MODIFIERS if name in modifiers]
    return '+'.join(ordered + keys)


def parts(text: Optional[str]) -> Tuple[List[str], List[str]]:
    """(modifiers, keys) of a normalised combination."""
    modifiers, keys = [], []
    for part in normalize(text).split('+'):
        if not part:
            continue
        (modifiers if part in MODIFIERS else keys).append(part)
    return modifiers, keys


def display(text: Optional[str]) -> List[str]:
    """The keycaps to draw for `text`: ['Win', 'Alt', 'E']."""
    out = []
    for part in normalize(text).split('+'):
        if part:
            out.append(KEY_LABELS.get(part) or (part.upper() if len(part) <= 3 else part.capitalize()))
    return out


def is_supported_key(key: str) -> bool:
    return key in HotkeyManager.VK_CODES


def validate(text: Optional[str], owner: str = '', others: Optional[Mapping[str, str]] = None) -> Verdict:
    """Judge `text` for `owner` against what the other owners already hold (`others`: owner label ->
    combination; the owner's own entry, if present, is ignored)."""
    combo = normalize(text)
    if not combo:
        return Verdict(ERROR, "Press the keys you want to use.", '')
    modifiers, keys = parts(combo)
    shown = ' + '.join(display(combo))
    if not keys:
        return Verdict(ERROR, "Add a letter, a number or a function key after the modifiers.", combo)
    if len(keys) > 1:
        return Verdict(ERROR, "A shortcut has one key besides Win, Ctrl, Alt and Shift.", combo)
    if not modifiers:
        return Verdict(ERROR, "Start with Ctrl, Alt, Shift or Win, or it would type into the app you are in.",
                       combo)
    if not is_supported_key(keys[0]):
        return Verdict(ERROR, f"CrossTrans cannot use {display(keys[0])[0]} in a shortcut. "
                              "Use a letter, a number, F1 to F12, or a key such as Space or Enter.", combo)
    if combo in RESERVED:
        return Verdict(ERROR, f"{shown} belongs to Windows. Choose another.", combo)
    if modifiers == ['win']:
        return Verdict(ERROR, f"Windows keeps Win + key shortcuts for itself. Add Alt, Ctrl or Shift to {shown}.",
                       combo)
    for other, held in (others or {}).items():
        if other != owner and normalize(held) == combo:
            return Verdict(ERROR, f"{shown} is already used for {other}.", combo)
    if combo in GAME_BAR:
        return Verdict(WARNING, f"The Xbox Game Bar can also use {shown} to {GAME_BAR[combo]}. If it "
                                "does not work, turn that shortcut off in Windows Settings > Gaming > "
                                "Xbox Game Bar, or choose another.", combo)
    return Verdict(OK, '', combo)


def conflicts(held: Mapping[str, str]) -> Dict[str, str]:
    """Owner -> message for every owner in `held` whose combination is unusable or shared; for showing
    a problem that is already saved (an old config, a hand edit). Warnings are included."""
    found = {}
    for owner, combo in held.items():
        if not combo:
            continue
        verdict = validate(combo, owner, held)
        if verdict.kind != OK:
            found[owner] = verdict.message
    return found


def as_registered(held: Mapping[str, str]) -> Dict[str, str]:
    """`held` with every combination normalised, blanks dropped - what to hand the manager."""
    return {owner: normalize(combo) for owner, combo in held.items() if normalize(combo)}
