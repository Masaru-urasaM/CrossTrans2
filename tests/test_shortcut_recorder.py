"""
Tests for src/core/shortcut_recorder.py - key events in, one shortcut out (TODO.md U8). No Tk, no
keyboard hook: the events are a list.
"""
import pytest

from src.core import shortcut_recorder as rec
from src.core.shortcut_recorder import CANCELLED, DOWN, UP, ShortcutRecorder


def run(*events):
    recorder = ShortcutRecorder()
    return recorder, recorder.feed_all(list(events))


class TestFinishing:
    def test_modifiers_then_a_key_finish_the_combination(self):
        _, result = run((DOWN, 'left windows'), (DOWN, 'alt'), (DOWN, 'e'))
        assert result == 'win+alt+e'

    def test_the_result_is_normalised_whatever_order_the_keys_went_down_in(self):
        _, result = run((DOWN, 'shift'), (DOWN, 'ctrl'), (DOWN, 'f5'))
        assert result == 'ctrl+shift+f5'

    def test_the_names_the_keyboard_library_uses_are_understood(self):
        _, result = run((DOWN, 'right ctrl'), (DOWN, 'left alt'), (DOWN, 'page up'))
        assert result == 'ctrl+alt+pageup'

    def test_one_modifier_is_enough(self):
        _, result = run((DOWN, 'ctrl'), (DOWN, 'k'))
        assert result == 'ctrl+k'

    def test_a_modifier_pressed_twice_counts_once(self):
        _, result = run((DOWN, 'ctrl'), (DOWN, 'ctrl'), (DOWN, 'ctrl'), (DOWN, 'k'))
        assert result == 'ctrl+k'

    def test_a_held_modifier_that_repeats_is_let_go_by_one_release(self):
        # Windows repeats the key-down of a held key; one release must be enough to clear it
        recorder = ShortcutRecorder()
        for event in ((DOWN, 'ctrl'), (DOWN, 'ctrl'), (DOWN, 'ctrl'), (UP, 'ctrl')):
            recorder.feed(*event)
        assert recorder.live == ''
        assert recorder.feed(DOWN, 'k') is None and not recorder.done

    def test_it_is_done_after_the_key(self):
        recorder, result = run((DOWN, 'ctrl'), (DOWN, 'k'))
        assert recorder.done
        assert recorder.feed(DOWN, 'j') is None


class TestWhileItIsBeingMade:
    def test_the_modifiers_held_are_live(self):
        recorder = ShortcutRecorder()
        assert recorder.live == ''
        recorder.feed(DOWN, 'left windows')
        recorder.feed(DOWN, 'alt')
        assert recorder.live == 'win+alt'

    def test_letting_a_modifier_go_removes_it(self):
        recorder = ShortcutRecorder()
        recorder.feed(DOWN, 'ctrl')
        recorder.feed(DOWN, 'alt')
        recorder.feed(UP, 'ctrl')
        assert recorder.live == 'alt'
        recorder.feed(DOWN, 'k')
        assert recorder.done

    def test_a_modifier_let_go_is_not_part_of_the_result(self):
        recorder, result = run((DOWN, 'ctrl'), (DOWN, 'alt'), (UP, 'ctrl'), (DOWN, 'k'))
        assert result == 'alt+k'

    def test_a_plain_key_with_nothing_held_is_ignored_and_it_keeps_listening(self):
        recorder = ShortcutRecorder()
        assert recorder.feed(DOWN, 'x') is None and not recorder.done
        assert recorder.feed(DOWN, 'ctrl') is None
        assert recorder.feed(DOWN, 'x') == 'ctrl+x'

    def test_letting_go_of_a_key_never_finishes_anything(self):
        recorder = ShortcutRecorder()
        recorder.feed(DOWN, 'ctrl')
        assert recorder.feed(UP, 'k') is None and not recorder.done

    def test_letting_go_of_a_modifier_that_was_never_down_is_harmless(self):
        recorder = ShortcutRecorder()
        assert recorder.feed(UP, 'alt') is None and recorder.live == ''


class TestCancelling:
    def test_esc_by_itself_cancels(self):
        recorder, result = run((DOWN, 'esc'))
        assert result is CANCELLED and rec.cancelled(result) and not rec.finished(result) and recorder.done

    def test_escape_spelt_out_cancels_too(self):
        assert run((DOWN, 'escape'))[1] is CANCELLED

    def test_esc_with_a_modifier_held_is_a_key_like_any_other(self):
        _, result = run((DOWN, 'ctrl'), (DOWN, 'esc'))
        assert result == 'ctrl+escape' and rec.finished(result)


class TestIgnored:
    @pytest.mark.parametrize('event', [(DOWN, ''), (DOWN, None), ('hold', 'ctrl'), ('', 'ctrl')])
    def test_nonsense_events_change_nothing(self, event):
        recorder = ShortcutRecorder()
        assert recorder.feed(*event) is None and recorder.live == '' and not recorder.done


class TestReset:
    def test_reset_forgets_everything(self):
        recorder, _ = run((DOWN, 'ctrl'), (DOWN, 'k'))
        recorder.reset()
        assert not recorder.done and recorder.live == ''
        assert recorder.feed_all([(DOWN, 'alt'), (DOWN, 'j')]) == 'alt+j'

    def test_reset_drops_held_modifiers(self):
        recorder = ShortcutRecorder()
        recorder.feed(DOWN, 'ctrl')
        recorder.reset()
        assert recorder.feed(DOWN, 'k') is None


class TestFeedAll:
    def test_no_result_when_the_events_do_not_finish_it(self):
        assert ShortcutRecorder().feed_all([(DOWN, 'ctrl'), (DOWN, 'alt')]) is None

    def test_it_stops_at_the_first_result(self):
        recorder = ShortcutRecorder()
        assert recorder.feed_all([(DOWN, 'ctrl'), (DOWN, 'k'), (DOWN, 'alt'), (DOWN, 'j')]) == 'ctrl+k'
