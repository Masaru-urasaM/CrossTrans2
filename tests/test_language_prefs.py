"""
Tests for src/core/language_prefs.py - which languages the Translator's picker offers first
(TODO.md U7, UQ8). No Tk, no config.
"""
import pytest

from src.constants import LANGUAGES, TRANSLATOR_RECENT_LANGUAGES
from src.core import language_prefs as P

SAMPLE = [("Vietnamese", "vi", "Ti" + chr(0x1EBF) + "ng Vi" + chr(0x1EC7) + "t"),
          ("English", "en", "English"),
          ("Japanese", "ja", chr(0x65E5) + chr(0x672C) + chr(0x8A9E)),
          ("Chinese Simplified", "zh-CN", chr(0x4E2D) + chr(0x6587)),
          ("Chinese Traditional", "zh-TW", chr(0x4E2D) + chr(0x6587)),
          ("Korean", "ko", chr(0xD55C) + chr(0xAD6D) + chr(0xC5B4)),
          ("French", "fr", "Fran" + chr(0xE7) + "ais"),
          ("German", "de", "Deutsch")]


def names(languages):
    return [language[0] for language in languages]


class TestPushRecent:
    def test_the_newest_choice_comes_first(self):
        assert P.push_recent(["English", "French"], "Japanese") == ["Japanese", "English", "French"]

    def test_a_language_chosen_again_moves_to_the_front_once(self):
        assert P.push_recent(["English", "French", "Korean"], "French") == ["French", "English", "Korean"]

    def test_the_list_is_bounded(self):
        recent = []
        for name in ("A", "B", "C", "D", "E", "F", "G"):
            recent = P.push_recent(recent, name)
        assert len(recent) == TRANSLATOR_RECENT_LANGUAGES
        assert recent[0] == "G" and "A" not in recent

    def test_a_limit_can_be_given(self):
        assert P.push_recent(["a", "b", "c"], "d", limit=2) == ["d", "a"]

    def test_a_blank_name_changes_nothing(self):
        assert P.push_recent(["English"], "") == ["English"]

    def test_stale_junk_in_the_stored_list_is_dropped(self):
        assert P.push_recent(["English", None, 7, ""], "French") == ["French", "English"]

    def test_the_input_is_not_modified(self):
        stored = ["English"]
        P.push_recent(stored, "French")
        assert stored == ["English"]


class TestPinned:
    def test_the_hotkey_languages_in_order_once_each(self):
        assert P.pinned_names(["Vietnamese", "English", "English", "Japanese"]) == \
            ["Vietnamese", "English", "Japanese"]

    def test_blanks_are_dropped(self):
        assert P.pinned_names(["", "English", None]) == ["English"]

    def test_nothing_pinned(self):
        assert P.pinned_names([]) == []


class TestMatches:
    def test_the_english_name_starting_with_the_query_is_the_best_match(self):
        assert P.matches(SAMPLE[6], "fre") == 3

    def test_a_word_of_the_native_name_or_the_code_is_second_best(self):
        assert P.matches(SAMPLE[6], "fra") == 2          # Fran(c)ais
        assert P.matches(SAMPLE[3], "zh-c") == 2         # the code
        assert P.matches(SAMPLE[3], "simpl") == 2        # a word of the English name

    def test_anywhere_else_is_a_weak_match(self):
        assert P.matches(SAMPLE[6], "ench") == 1

    def test_no_match(self):
        assert P.matches(SAMPLE[6], "klingon") == 0

    def test_case_and_surrounding_spaces_do_not_matter(self):
        assert P.matches(SAMPLE[1], "  ENG ") == 3

    def test_the_native_name_is_searched(self):
        assert P.matches(SAMPLE[2], chr(0x65E5)) == 2

    def test_a_query_inside_a_native_word_is_a_weak_match(self):
        # "ran" is in neither "French" nor "fr", only inside the native "Fran(c)ais"
        assert P.matches(SAMPLE[6], "ran") == 1

    def test_a_blank_query_matches_everything_weakly(self):
        assert P.matches(SAMPLE[0], "   ") == 1


class TestSearch:
    def test_best_matches_first_then_the_lists_own_order(self):
        # Chinese begins with it; French and German ("Deutsch") only contain it
        found = P.search(SAMPLE, "ch")
        assert names(found) == ["Chinese Simplified", "Chinese Traditional", "French", "German"]

    def test_a_stronger_match_later_in_the_list_comes_first(self):
        # Malay only contains "al"; Albanian begins with it, though it comes later in the list
        malay = ("Malay", "ms", "Bahasa Melayu")
        albanian = ("Albanian", "sq", "Shqip")
        assert names(P.search([malay, albanian], "al")) == ["Albanian", "Malay"]

    def test_the_english_name_beats_a_code(self):
        # "ko" is Korean's code, "ko" begins Korean's name too - and nothing else
        assert names(P.search(SAMPLE, "ko")) == ["Korean"]

    def test_a_query_with_no_match_returns_nothing(self):
        assert P.search(SAMPLE, "zzz") == []

    def test_a_blank_query_returns_everything_in_order(self):
        assert names(P.search(SAMPLE, "")) == names(SAMPLE)

    def test_the_real_list_finds_japanese_by_its_native_name(self):
        native = [language for language in LANGUAGES if language[0] == "Japanese"][0][2]
        assert "Japanese" in names(P.search(LANGUAGES, native))


class TestSections:
    def test_no_query_lists_recent_then_pinned_then_all(self):
        shown = P.sections(SAMPLE, "", recent=["French"], pinned=["English", "Japanese"])
        assert [s.title for s in shown] == [P.RECENT, P.PINNED, P.ALL]
        assert names(shown[0].languages) == ["French"]
        assert names(shown[1].languages) == ["English", "Japanese"]
        assert names(shown[2].languages) == names(SAMPLE)

    def test_empty_sections_are_left_out(self):
        shown = P.sections(SAMPLE, "", recent=[], pinned=[])
        assert [s.title for s in shown] == [P.ALL]

    def test_a_stale_name_is_ignored(self):
        shown = P.sections(SAMPLE, "", recent=["Klingon", "French"], pinned=["Elvish"])
        assert [s.title for s in shown] == [P.RECENT, P.ALL]
        assert names(shown[0].languages) == ["French"]

    def test_a_query_gives_one_results_section_over_every_language(self):
        shown = P.sections(SAMPLE, "ger", recent=["French"], pinned=["English"])
        assert [s.title for s in shown] == [P.RESULTS]
        assert names(shown[0].languages) == ["German"]

    def test_a_query_with_no_match_shows_no_sections(self):
        assert P.sections(SAMPLE, "zzz", recent=["French"], pinned=["English"]) == []

    def test_a_blank_query_is_no_query(self):
        shown = P.sections(SAMPLE, "   ", recent=["French"], pinned=[])
        assert [s.title for s in shown] == [P.RECENT, P.ALL]


class TestFlatten:
    def test_every_row_in_order_is_what_the_arrow_keys_walk(self):
        shown = P.sections(SAMPLE, "", recent=["French"], pinned=["English"])
        flat = P.flatten(shown)
        assert names(flat)[:2] == ["French", "English"]
        assert len(flat) == 2 + len(SAMPLE)

    def test_nothing_shown_nothing_to_walk(self):
        assert P.flatten([]) == []


class TestTheRealList:
    def test_every_hotkey_language_the_app_ships_is_in_the_list(self):
        from config import Config
        known = {language[0] for language in LANGUAGES}
        for name in Config.DEFAULT_HOTKEYS:
            assert name in known, f"{name} is pinned by default but is not a language"


class TestTargetName:
    """What the swap button puts in the picker: only a language `lang_id` has settled."""

    def test_japanese(self):
        assert P.target_name('Japanese') == 'Japanese'

    @pytest.mark.parametrize('spelling', ['Chinese (Simplified)', 'Chinese Simplified', 'zh-CN'])
    def test_simplified_chinese_in_every_spelling_the_app_meets(self, spelling):
        assert P.target_name(spelling) == 'Chinese Simplified'

    def test_traditional_chinese(self):
        assert P.target_name('Chinese (Traditional)') == 'Chinese Traditional'

    @pytest.mark.parametrize('language', [None, '', 'Unknown', 'French', 'English', 'Klingon'])
    def test_anything_else_is_not_named(self, language):
        assert P.target_name(language) is None

    def test_every_name_it_returns_is_a_language_of_the_list(self):
        known = {language[0] for language in LANGUAGES}
        for settled in ('Japanese', 'Chinese (Simplified)', 'Chinese (Traditional)'):
            assert P.target_name(settled) in known
