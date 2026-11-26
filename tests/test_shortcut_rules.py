"""
Tests for src/core/shortcut_rules.py - which key combinations can be registered and what is wrong
with one that cannot (TODO.md U8). No Tk, no keyboard hook.
"""
import pytest

import src.core  # noqa: F401  (must precede `config`: see project CLAUDE.md)
from config import Config
from src.core import shortcut_rules as R
from src.core.hotkey import HotkeyManager


class TestNormalize:
    @pytest.mark.parametrize('recorded, stored', [
        ('Windows+Alt+E', 'win+alt+e'),
        ('left windows + alt + e', 'win+alt+e'),
        ('alt+win+e', 'win+alt+e'),
        ('shift+ctrl+F5', 'ctrl+shift+f5'),
        ('right ctrl+left alt+x', 'ctrl+alt+x'),
        ('control+alt gr+z', 'ctrl+alt+z'),
        ('ctrl+ctrl+a', 'ctrl+a'),
        ('ctrl+esc', 'ctrl+escape'),
        ('alt+page up', 'alt+pageup'),
        ('alt+Page Down', 'alt+pagedown'),
        ('ctrl+del', 'ctrl+delete'),
        ('ctrl+return', 'ctrl+enter'),
        ('  win + alt + v  ', 'win+alt+v'),
    ])
    def test_what_the_keyboard_library_records_becomes_what_the_manager_registers(self, recorded, stored):
        assert R.normalize(recorded) == stored

    @pytest.mark.parametrize('empty', [None, '', '   ', '+', '++'])
    def test_nothing_is_empty(self, empty):
        assert R.normalize(empty) == ''

    def test_the_defaults_are_already_canonical(self):
        for combo in list(Config.DEFAULT_HOTKEYS.values()) + [Config.SCREENSHOT_HOTKEY_DEFAULT,
                                                              Config.FIX_GRAMMAR_HOTKEY_DEFAULT]:
            assert R.normalize(combo) == combo

    def test_it_is_idempotent(self):
        for text in ('Windows+Alt+E', 'shift+ctrl+f5', 'ctrl+esc', 'print screen+alt'):
            once = R.normalize(text)
            assert R.normalize(once) == once

    def test_an_unknown_key_is_kept_not_guessed(self):
        assert R.normalize('ctrl+print screen') == 'ctrl+print screen'


class TestDisplay:
    def test_keycaps_in_the_order_written(self):
        assert R.display('win+alt+e') == ['Win', 'Alt', 'E']

    @pytest.mark.parametrize('combo, caps', [
        ('ctrl+escape', ['Ctrl', 'Esc']), ('ctrl+f5', ['Ctrl', 'F5']), ('ctrl+f12', ['Ctrl', 'F12']),
        ('ctrl+pageup', ['Ctrl', 'Page Up']), ('alt+space', ['Alt', 'Space']),
        ('ctrl+shift+a', ['Ctrl', 'Shift', 'A']),
    ])
    def test_keys_are_named_as_on_the_keyboard(self, combo, caps):
        assert R.display(combo) == caps

    def test_a_recording_is_normalised_first(self):
        assert R.display('Windows+Alt+E') == ['Win', 'Alt', 'E']

    def test_nothing_shows_no_keycaps(self):
        assert R.display('') == [] and R.display(None) == []


class TestParts:
    def test_modifiers_and_keys_are_separated(self):
        assert R.parts('alt+win+e') == (['win', 'alt'], ['e'])

    def test_only_modifiers_has_no_key(self):
        assert R.parts('win+alt') == (['win', 'alt'], [])


class TestValidate:
    @pytest.mark.parametrize('combo', ['win+alt+e', 'ctrl+shift+f9', 'ctrl+alt+space', 'ctrl+alt+1', 'windows+alt+j'])
    def test_a_usable_combination_is_fine(self, combo):
        verdict = R.validate(combo)
        assert verdict.kind == R.OK and verdict.ok and verdict.message == ''
        assert verdict.normalized == R.normalize(combo)

    def test_nothing_recorded(self):
        verdict = R.validate('')
        assert verdict.kind == R.ERROR and not verdict.ok and 'Press the keys' in verdict.message

    def test_only_modifiers(self):
        verdict = R.validate('win+alt')
        assert verdict.kind == R.ERROR and 'letter' in verdict.message

    def test_a_key_alone_would_type_into_the_app(self):
        verdict = R.validate('e')
        assert verdict.kind == R.ERROR and 'Ctrl, Alt, Shift or Win' in verdict.message

    def test_a_function_key_alone_is_still_refused(self):
        assert R.validate('f9').kind == R.ERROR

    def test_two_keys_are_not_a_shortcut(self):
        verdict = R.validate('ctrl+a+b')
        assert verdict.kind == R.ERROR and 'one key' in verdict.message

    @pytest.mark.parametrize('combo', ['ctrl+print screen', 'alt+;', 'ctrl+caps lock', 'ctrl+num lock'])
    def test_a_key_the_manager_cannot_register_is_refused_and_named(self, combo):
        verdict = R.validate(combo)
        assert verdict.kind == R.ERROR and 'cannot use' in verdict.message

    @pytest.mark.parametrize('combo', ['alt+f4', 'ctrl+alt+delete', 'ctrl+esc', 'alt+tab', 'windows+l',
                                       'win+d', 'ctrl+shift+esc', 'win+tab', 'alt+space'])
    def test_what_windows_keeps_is_refused(self, combo):
        verdict = R.validate(combo)
        assert verdict.kind == R.ERROR and 'belongs to Windows' in verdict.message

    @pytest.mark.parametrize('combo', ['win+q', 'win+1', 'windows+f5'])
    def test_win_and_a_key_belong_to_windows(self, combo):
        verdict = R.validate(combo)
        assert verdict.kind == R.ERROR and 'Windows keeps Win + key' in verdict.message

    def test_a_combination_another_owner_holds_is_refused_by_name(self):
        verdict = R.validate('win+alt+e', 'Japanese', {'English': 'win+alt+e'})
        assert verdict.kind == R.ERROR and 'English' in verdict.message

    def test_the_same_combination_written_another_way_is_still_a_duplicate(self):
        assert R.validate('Windows+Alt+E', 'Japanese', {'English': 'win+alt+e'}).kind == R.ERROR
        assert R.validate('alt+win+e', 'Japanese', {'English': 'windows+alt+e'}).kind == R.ERROR

    def test_the_owners_own_entry_is_not_a_duplicate(self):
        assert R.validate('win+alt+e', 'English', {'English': 'win+alt+e'}).kind == R.OK

    def test_a_blank_entry_of_another_owner_conflicts_with_nothing(self):
        assert R.validate('win+alt+e', 'Japanese', {'English': ''}).kind == R.OK

    def test_the_game_bars_keys_are_a_warning_the_user_may_keep(self):
        verdict = R.validate('win+alt+g', 'Fix grammar')
        assert verdict.kind == R.WARNING and verdict.ok
        assert 'Xbox Game Bar' in verdict.message and 'Win + Alt + G' in verdict.message

    def test_an_error_beats_a_warning(self):
        verdict = R.validate('win+alt+g', 'Screenshot', {'Fix grammar': 'win+alt+g'})
        assert verdict.kind == R.ERROR

    def test_the_verdict_carries_the_stored_form(self):
        assert R.validate('Windows+Alt+E').normalized == 'win+alt+e'
        assert R.validate('alt+f4').normalized == 'alt+f4'


class TestTheManagerCanRegisterWhatItAccepts:
    """The point of the module: a combination it calls fine is one `HotkeyManager` can parse."""

    @pytest.fixture(scope='class')
    def manager(self):
        return HotkeyManager(config=None, callback=lambda language: None)

    @pytest.mark.parametrize('key', sorted(set(HotkeyManager.VK_CODES) - {'delete'}))
    def test_every_key_the_manager_knows_is_accepted_after_ctrl_alt(self, key):
        assert R.validate(f'ctrl+alt+{key}').ok, key

    @pytest.mark.parametrize('combo', ['Windows+Alt+E', 'shift+ctrl+F5', 'left windows+left alt+j',
                                       'ctrl+alt+esc', 'ctrl+alt+page up'])
    def test_a_normalised_combination_parses(self, manager, combo):
        verdict = R.validate(combo)
        assert verdict.ok
        modifiers, vk = manager._parse_hotkey(verdict.normalized)
        assert modifiers is not None and vk is not None

    def test_a_refused_unknown_key_is_one_the_manager_does_not_parse(self, manager):
        assert R.validate('ctrl+alt+print screen').kind == R.ERROR
        assert manager._parse_hotkey('ctrl+alt+print screen') == (None, None)

    def test_every_default_the_app_ships_is_usable(self):
        for language, combo in Config.DEFAULT_HOTKEYS.items():
            assert R.validate(combo, language).kind == R.OK, language
        assert R.validate(Config.SCREENSHOT_HOTKEY_DEFAULT).kind == R.OK

    def test_the_fix_grammar_default_is_the_game_bar_warning(self):
        assert R.validate(Config.FIX_GRAMMAR_HOTKEY_DEFAULT).kind == R.WARNING


class TestConflicts:
    def test_a_clean_set_has_none(self):
        assert R.conflicts({'English': 'win+alt+e', 'Japanese': 'win+alt+j'}) == {}

    def test_both_owners_of_a_shared_combination_are_named(self):
        found = R.conflicts({'English': 'win+alt+e', 'Japanese': 'Windows+Alt+E', 'French': 'win+alt+f'})
        assert set(found) == {'English', 'Japanese'}

    def test_a_saved_warning_or_error_is_reported(self):
        found = R.conflicts({'Fix grammar': 'win+alt+g', 'Screenshot': 'alt+f4'})
        assert set(found) == {'Fix grammar', 'Screenshot'}
        assert 'Game Bar' in found['Fix grammar'] and 'Windows' in found['Screenshot']

    def test_blank_entries_are_ignored(self):
        assert R.conflicts({'English': '', 'Japanese': None}) == {}


class TestAsRegistered:
    def test_every_combination_is_normalised_and_blanks_dropped(self):
        assert R.as_registered({'English': 'Windows+Alt+E', 'Japanese': '', 'French': 'shift+ctrl+f5'}) == {
            'English': 'win+alt+e', 'French': 'ctrl+shift+f5'}
