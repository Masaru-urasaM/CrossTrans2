"""
Turns a stream of key events into one shortcut (TODO.md U8).

Pure logic, no Tk and no keyboard hook: the Settings window feeds it `(event type, key name)` pairs
from whatever it listens to (the `keyboard` library in the app, a list in a test). The old recorder
compared strings inside the hook's own thread and showed a message box from it; this one only keeps
the modifiers that are down and says when the combination is finished.

A combination is finished when a key that is not a modifier goes down while at least one modifier
is held. Esc on its own cancels. A key pressed with no modifier held is ignored - a shortcut that
starts with a plain key would type into whatever app is in front.
"""
from typing import List, Tuple

from src.core import shortcut_rules as rules

CANCELLED = object()            # what `feed` returns when the user pressed Esc by itself

DOWN, UP = 'down', 'up'


class ShortcutRecorder:
    def __init__(self):
        self._held: List[str] = []
        self.done = False

    def reset(self) -> None:
        self._held = []
        self.done = False

    @property
    def live(self) -> str:
        """The modifiers held right now, as they would be stored ('win+alt'), '' when none."""
        return rules.normalize('+'.join(self._held))

    def feed(self, event_type: str, name: str):
        """Take one key event. Returns None while the combination is still being made, the finished
        combination (normalised) when a key completes it, or `CANCELLED`."""
        if self.done or not name:
            return None
        key = rules.normalize(name)
        if not key:
            return None
        is_modifier = key in rules.MODIFIERS
        if event_type == UP:
            if is_modifier and key in self._held:
                self._held.remove(key)
            return None
        if event_type != DOWN:
            return None
        if is_modifier:
            if key not in self._held:
                self._held.append(key)
            return None
        if key == 'escape' and not self._held:
            self.done = True
            return CANCELLED
        if not self._held:
            return None
        self.done = True
        return rules.normalize('+'.join(self._held + [key]))

    def feed_all(self, events: List[Tuple[str, str]]):
        """`feed` every event in turn; the first result that is not None, else None."""
        for event_type, name in events:
            result = self.feed(event_type, name)
            if result is not None:
                return result
        return None


def finished(result) -> bool:
    return result is not None and result is not CANCELLED


def cancelled(result) -> bool:
    return result is CANCELLED
