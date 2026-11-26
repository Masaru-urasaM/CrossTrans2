"""Tests for src/core/model_names.py (TODO.md U8, step 4): the model list and the search over it."""
import pytest

from src.core import model_names as mn

MODEL_MAP = {
    'Google': ['gemini-2.5-flash', 'Gemini-2.5-Pro', 'gemma-3'],
    'Groq': ['llama-3.3-70b', 'gemma-3', 'openai/gpt-oss-120b'],
}


class TestModelsFor:
    def test_auto_comes_first_then_a_provider_s_models_a_to_z(self):
        assert mn.models_for('Groq', MODEL_MAP) == ['Auto', 'gemma-3', 'llama-3.3-70b', 'openai/gpt-oss-120b']

    def test_sorting_ignores_case(self):
        assert mn.models_for('Google', MODEL_MAP)[1:] == ['gemini-2.5-flash', 'Gemini-2.5-Pro', 'gemma-3']

    def test_auto_provider_offers_every_model_once(self):
        names = mn.models_for('Auto', MODEL_MAP)
        assert names[0] == 'Auto' and names.count('gemma-3') == 1 and len(names) == 1 + 5

    def test_no_provider_is_auto_too(self):
        assert mn.models_for('', MODEL_MAP) == mn.models_for('Auto', MODEL_MAP)
        assert mn.models_for(None, MODEL_MAP) == mn.models_for('Auto', MODEL_MAP)

    def test_a_provider_the_map_lacks_offers_only_auto(self):
        assert mn.models_for('Nobody', MODEL_MAP) == ['Auto']

    def test_a_model_called_auto_is_not_listed_twice(self):
        assert mn.models_for('X', {'X': ['Auto', 'a']}) == ['Auto', 'a']

    def test_blank_names_are_dropped(self):
        assert mn.models_for('X', {'X': ['', 'a']}) == ['Auto', 'a']

    def test_names_differing_only_in_case_are_one(self):
        assert mn.models_for('X', {'X': ['Foo', 'foo']}) == ['Auto', 'Foo']

    def test_the_maps_own_lists_are_left_alone(self):
        original = list(MODEL_MAP['Groq'])
        mn.models_for('Groq', MODEL_MAP)
        assert MODEL_MAP['Groq'] == original


class TestScore:
    @pytest.mark.parametrize('name,query,expected', [
        ('gemini-2.5-flash', 'gem', 3),
        ('gemini-2.5-flash', 'flash', 2),
        ('openai/gpt-oss-120b', 'gpt', 2),
        ('openai/gpt-oss-120b', 'oss-1', 1),
        ('gemini-2.5-flash', 'xyz', 0),
        ('Gemini-2.5-Pro', 'GEMINI', 3),
        ('anything', '', 1),
        ('anything', '   ', 1),
    ])
    def test_the_grades(self, name, query, expected):
        assert mn.score(name, query) == expected


class TestRank:
    MODELS = ['Auto', 'llama-3.3-70b', 'gemini-2.5-flash', 'gemma-3', 'mega-gem-1', 'flash-lite']

    def test_a_blank_query_keeps_the_list(self):
        assert mn.rank(self.MODELS, '') == self.MODELS
        assert mn.rank(self.MODELS, '  ') == self.MODELS

    def test_a_prefix_beats_a_word_beats_a_substring(self):
        assert mn.rank(self.MODELS, 'gem') == ['gemini-2.5-flash', 'gemma-3', 'mega-gem-1']

    def test_a_word_match_beats_a_substring_match(self):
        assert mn.rank(['xflashx', 'a-flash'], 'flash') == ['a-flash', 'xflashx']

    def test_the_lists_order_holds_within_a_grade(self):
        assert mn.rank(['b-x', 'a-x', 'c-x'], 'x') == ['b-x', 'a-x', 'c-x']

    def test_nothing_matching_is_an_empty_list(self):
        assert mn.rank(self.MODELS, 'zzz') == []

    def test_case_does_not_matter(self):
        assert mn.rank(self.MODELS, 'LLAMA') == ['llama-3.3-70b']

    def test_auto_can_be_found_too(self):
        assert mn.rank(self.MODELS, 'aut') == ['Auto']


class TestOffersCustom:
    def test_a_name_not_in_the_list_is_offered(self):
        assert mn.offers_custom(['a', 'b'], 'my-model') is True

    def test_a_name_already_there_is_not_offered_whatever_its_case(self):
        assert mn.offers_custom(['Gemma-3'], 'gemma-3') is False

    def test_a_blank_query_offers_nothing(self):
        assert mn.offers_custom(['a'], '') is False and mn.offers_custom(['a'], '  ') is False

    def test_surrounding_spaces_are_ignored(self):
        assert mn.offers_custom(['a'], ' a ') is False
