"""
Tests for src/core/tone_prompts.py - the Translator's tone chips write the custom instruction
(TODO.md U7, UQ9). Text in, text out; no Tk.
"""
import pytest

from src.core import tone_prompts as T


class TestTheChips:
    def test_the_four_the_plan_names_in_its_order(self):
        assert [label for _, label, _ in T.TONES] == ['Formal', 'Casual', 'Concise', 'Natural']

    def test_every_chip_writes_a_distinct_sentence_that_is_not_part_of_another(self):
        sentences = [sentence for _, _, sentence in T.TONES]
        assert len(set(sentences)) == 4
        for one in sentences:
            assert all(one not in other for other in sentences if other != one)

    def test_the_sentences_are_plain_english_instructions(self):
        for _, _, sentence in T.TONES:
            assert sentence.endswith('.') and sentence.isascii()


class TestToggle:
    def test_a_chip_writes_its_sentence_into_an_empty_field(self):
        assert T.toggle('', 'formal') == T.SENTENCES['formal']

    def test_pressing_it_again_takes_it_out(self):
        assert T.toggle(T.SENTENCES['formal'], 'formal') == ''

    def test_it_is_added_after_what_the_user_wrote(self):
        assert T.toggle('Keep names as they are.', 'casual') == 'Keep names as they are. ' + T.SENTENCES['casual']

    def test_removing_it_leaves_the_users_own_words_untouched(self):
        text = 'Keep names as they are. ' + T.SENTENCES['casual']
        assert T.toggle(text, 'casual') == 'Keep names as they are.'

    def test_two_tones_can_be_on_together(self):
        both = T.toggle(T.toggle('', 'formal'), 'concise')
        assert T.active(both) == ['formal', 'concise']

    def test_removing_one_of_two_keeps_the_other(self):
        both = T.toggle(T.toggle('', 'formal'), 'concise')
        assert T.toggle(both, 'formal') == T.SENTENCES['concise']

    def test_a_tone_in_the_middle_closes_the_gap(self):
        text = f"A {T.SENTENCES['natural']} B"
        assert T.toggle(text, 'natural') == 'A B'

    def test_trailing_spaces_do_not_pile_up(self):
        assert T.toggle('Hello.   ', 'formal') == 'Hello. ' + T.SENTENCES['formal']

    def test_none_is_an_empty_field(self):
        assert T.toggle(None, 'casual') == T.SENTENCES['casual']

    def test_an_unknown_tone_is_an_error(self):
        with pytest.raises(KeyError):
            T.toggle('', 'sarcastic')

    @pytest.mark.parametrize('key', [key for key, _, _ in T.TONES])
    def test_twice_is_the_start_again_for_every_chip(self, key):
        assert T.toggle(T.toggle('Hello.', key), key) == 'Hello.'


class TestActive:
    def test_nothing_in_an_empty_field(self):
        assert T.active('') == [] and T.active(None) == []

    def test_free_text_lights_nothing(self):
        assert T.active('Make it formal') == []

    def test_a_written_sentence_lights_its_chip_wherever_it_is(self):
        assert T.active('Hello. ' + T.SENTENCES['concise'] + ' Thanks') == ['concise']

    def test_the_order_is_the_chips_not_the_texts(self):
        text = T.SENTENCES['natural'] + ' ' + T.SENTENCES['formal']
        assert T.active(text) == ['formal', 'natural']
