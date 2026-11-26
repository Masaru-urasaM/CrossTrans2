"""
Tests for src/core/settings_index.py - the Settings pages, every setting's words, and the search
(TODO.md U8). No Tk.
"""
import pytest

from src.core import settings_index as S


def keys(entries):
    return [item.key for item in entries]


class TestTheData:
    def test_pages_have_unique_keys_and_titles(self):
        assert len({p.key for p in S.PAGES}) == len(S.PAGES)
        assert len({p.title for p in S.PAGES}) == len(S.PAGES)

    def test_the_pages_are_the_plan_s_in_order(self):
        assert [p.key for p in S.PAGES] == ['general', 'appearance', 'translation', 'shortcuts',
                                            'providers', 'dictionary', 'help', 'about']

    def test_every_entry_belongs_to_a_page_and_every_page_has_entries(self):
        pages = {p.key for p in S.PAGES}
        assert {e.page for e in S.ENTRIES} <= pages
        for page in pages:
            assert S.entries_of(page), page

    def test_entry_keys_are_unique(self):
        assert len({e.key for e in S.ENTRIES}) == len(S.ENTRIES)

    def test_every_entry_has_a_title_and_a_sentence(self):
        for e in S.ENTRIES:
            assert e.title.strip(), e.key
            if e.key == 'capabilities':
                assert e.description == ''
            else:
                assert e.description.strip() and e.description.endswith('.'), e.key

    def test_titles_are_unique_across_the_window(self):
        titles = [e.title for e in S.ENTRIES]
        assert len(set(titles)) == len(titles)

    def test_no_stray_symbols_or_other_scripts_in_the_words(self):
        for e in S.ENTRIES:
            for text in (e.title, e.description, *e.keywords):
                assert all(ord(ch) < 128 for ch in text), (e.key, text)
        for p in S.PAGES:
            assert all(ord(ch) < 128 for ch in p.title)

    def test_the_translation_page_holds_what_used_to_hide_under_hotkeys(self):
        on_translation = keys(S.entries_of('translation'))
        for key in ('furigana', 'replace_mode', 'fix_grammar_button'):
            assert key in on_translation

    def test_lookup_by_key(self):
        assert S.entry('theme').page == 'appearance' and S.page('about').title == 'About'
        with pytest.raises(KeyError):
            S.entry('nonsense')

    def test_every_icon_is_a_real_bootstrap_icon(self):
        import json
        import pathlib
        import ttkbootstrap
        glyphs = json.load(open(pathlib.Path(ttkbootstrap.__file__).parent / 'assets' / 'icons' /
                                'icon_metrics.json', encoding='utf-8'))
        for p in S.PAGES:
            assert p.icon in glyphs, p.icon


class TestPageFor:
    @pytest.mark.parametrize('name, page', [
        ('General', 'general'), ('Hotkeys', 'shortcuts'), ('API Key', 'providers'),
        ('Dictionary', 'dictionary'), ('Guide', 'help'), ('Translation', 'translation'),
        ('API keys', 'providers'), ('about & help', 'about'), ('providers', 'providers'),
        ('  GENERAL ', 'general'), ('api', 'providers'), ('Appearance', 'appearance'),
        ('shortcuts', 'shortcuts'), ('help', 'help'), ('About', 'about'),
    ])
    def test_every_name_the_app_or_a_user_may_use(self, name, page):
        assert S.page_for(name) == page

    @pytest.mark.parametrize('name', [None, '', '   ', 'nonsense', 'zzz'])
    def test_nothing_else(self, name):
        assert S.page_for(name) is None

    def test_a_page_key_maps_to_itself(self):
        for p in S.PAGES:
            assert S.page_for(p.key) == p.key and S.page_for(p.title) == p.key


class TestSearch:
    def test_a_blank_query_finds_nothing(self):
        assert S.search('') == [] and S.search('   ') == []

    def test_a_query_that_matches_nothing(self):
        assert S.search('zzzzqq') == []

    def test_dark_finds_the_theme_first(self):
        assert keys(S.search('dark'))[0] == 'theme'

    def test_update_finds_the_automatic_check_and_the_version_page(self):
        found = keys(S.search('update'))
        assert 'auto_update' in found and 'version' in found

    def test_a_title_start_outranks_a_keyword(self):
        graded = (S.Entry('a', 'general', 'Later thing', 'Something else.', ('dark',)),
                  S.Entry('b', 'general', 'Dark mode', 'Another.', ()))
        assert keys(S.search('dark', graded)) == ['b', 'a']

    def test_a_keyword_outranks_a_mention_in_the_description(self):
        graded = (S.Entry('a', 'general', 'Alpha', 'Mentions dark skies.', ()),
                  S.Entry('b', 'general', 'Beta', 'Nothing here.', ('dark',)))
        assert keys(S.search('dark', graded)) == ['b', 'a']

    def test_within_a_score_the_lists_own_order_holds(self):
        graded = (S.Entry('a', 'general', 'One', 'Has dark.', ()), S.Entry('b', 'general', 'Two', 'Has dark.', ()))
        assert keys(S.search('dark', graded)) == ['a', 'b']

    def test_every_term_must_match(self):
        assert 'fix_grammar_shortcut' in keys(S.search('game bar'))
        assert S.search('game zzzz') == []

    def test_the_page_name_is_searched_too(self):
        found = S.search('appearance')
        assert found and found[0].page == 'appearance'

    def test_case_and_spaces_do_not_matter(self):
        assert keys(S.search('  DARK ')) == keys(S.search('dark'))

    @pytest.mark.parametrize('item', S.ENTRIES, ids=lambda e: e.key)
    def test_every_setting_can_be_found_by_its_own_title(self, item):
        assert item in S.search(item.title)

    def test_furigana_finds_the_setting_before_the_reader(self):
        found = keys(S.search('furi'))
        assert 'furigana' in found and 'furigana_reader' not in found

    def test_several_results_on_one_page_name_the_page_once(self):
        found = S.search('shortcut')
        assert len([e for e in found if e.page == 'shortcuts']) >= 3
        pages = S.pages_with_results(found)
        assert pages.count('shortcuts') == 1 and len(pages) == len(set(pages))

    def test_the_result_pages_in_the_order_of_their_best_result(self):
        found = S.search('update')
        pages = S.pages_with_results(found)
        assert len(pages) == len(set(pages)) and set(pages) == {e.page for e in found}
        assert pages[0] == found[0].page


def test_linked_reading_is_a_primary_translation_feature():
    entry = S.entry('linked_reading')
    assert entry.page == 'translation'
    assert entry.title == 'Show Linked reading'
    assert 'experimental' not in entry.title.lower()
