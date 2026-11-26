"""
Tests for src/core/guide_content.py (TODO.md U8, step 6): the in-app help as data. No Tk.

The old guide had the page names written into its sentences, and they went stale the day the tabs were
reorganised ("Settings -> Hotkeys" is no tab any more). These tests are what keeps that from happening again:
every link names a real page, and no sentence names a tab that is gone.
"""
import re

import pytest

from src.core import guide_content as G
from src.core import settings_index as S

PLACEHOLDER = re.compile(r'\{(\w+)\}')


def texts(topic):
    return [topic.title, topic.summary, *topic.steps, *topic.notes, *topic.keywords,
            *( [topic.link[1]] if topic.link else [] )]


class TestTheData:
    def test_there_are_topics(self):
        assert len(G.TOPICS) >= 8

    def test_keys_are_unique_and_prefixed(self):
        keys = [t.key for t in G.TOPICS]
        assert len(set(keys)) == len(keys) and all(key.startswith('help_') for key in keys)

    def test_titles_are_unique(self):
        titles = [t.title for t in G.TOPICS]
        assert len(set(titles)) == len(titles)

    @pytest.mark.parametrize('topic', G.TOPICS, ids=lambda t: t.key)
    def test_a_topic_has_words(self, topic):
        assert topic.title.strip() and topic.summary.strip().endswith('.')
        assert topic.steps or topic.notes, 'a topic with neither steps nor notes says nothing'
        for line in (*topic.steps, *topic.notes):
            assert line.strip() and line == line.strip()

    @pytest.mark.parametrize('topic', G.TOPICS, ids=lambda t: t.key)
    def test_only_plain_ascii_the_japanese_interface_translates_it_later(self, topic):
        for text in texts(topic):
            assert all(ord(ch) < 128 for ch in text), (topic.key, text)

    @pytest.mark.parametrize('topic', G.TOPICS, ids=lambda t: t.key)
    def test_every_placeholder_is_one_fill_knows(self, topic):
        for text in texts(topic):
            for name in PLACEHOLDER.findall(text):
                assert name in ('languages', 'providers', 'trial_limit', 'history_limit'), (topic.key, name)

    def test_history_help_follows_the_runtime_retention_limit(self, monkeypatch):
        from src.core.history import HistoryManager
        monkeypatch.setattr(HistoryManager, 'MAX_HISTORY', 213)
        message = G.topic('help_translator').notes[-1]
        assert G.fill(message, 4, 3) == 'History keeps your last 213 translations and can be searched.'

    @pytest.mark.parametrize('topic', G.TOPICS, ids=lambda t: t.key)
    def test_every_link_names_a_real_page_and_a_button(self, topic):
        if topic.link is None:
            return
        page, text = topic.link
        assert page in {p.key for p in S.PAGES} and text.strip()

    @pytest.mark.parametrize('topic', G.TOPICS, ids=lambda t: t.key)
    def test_no_sentence_names_a_tab_that_is_gone(self, topic):
        for text in texts(topic):
            for stale in ('Hotkeys', 'API Key', 'Settings ->', 'tab '):
                assert stale not in text, (topic.key, stale, text)

    def test_the_pages_the_help_names_exist_by_their_titles(self):
        titles = {p.title for p in S.PAGES}
        for topic in G.TOPICS:
            if topic.link:
                button = topic.link[1]
                named = [title for title in titles if title in button]
                assert named or 'settings' in button.lower(), (topic.key, button)


class TestLookupAndFill:
    def test_lookup_by_key(self):
        assert G.topic('help_start').title == 'Getting started'
        with pytest.raises(KeyError):
            G.topic('nonsense')

    def test_fill_puts_the_numbers_in(self):
        assert G.fill('{languages} languages, {providers} providers, {trial_limit} a day', 104, 14, 20) == \
            '104 languages, 14 providers, 20 a day'

    def test_text_without_a_placeholder_is_unchanged(self):
        assert G.fill('Press Win + Alt + V.', 1, 2, 3) == 'Press Win + Alt + V.'

    @pytest.mark.parametrize('topic', G.TOPICS, ids=lambda t: t.key)
    def test_every_line_of_every_topic_fills_cleanly(self, topic):
        for line in (*topic.steps, *topic.notes):
            filled = G.fill(line, 104, 14, 20)
            assert '{' not in filled and '}' not in filled

    def test_the_numbers_that_change_do_appear_somewhere(self):
        everything = ' '.join(line for topic in G.TOPICS for line in (*topic.steps, *topic.notes))
        for name in ('languages', 'providers'):
            assert '{' + name + '}' in everything


class TestSearch:
    def test_the_topics_are_in_the_settings_index(self):
        in_index = {item.key for item in S.entries_of('help')}
        assert in_index == {t.key for t in G.TOPICS}

    def test_an_entry_says_what_the_topic_says(self):
        for topic in G.TOPICS:
            item = S.entry(topic.key)
            assert (item.title, item.description, item.keywords) == (topic.title, topic.summary, topic.keywords)

    def test_a_search_for_hotkey_finds_the_setting_and_the_help(self):
        keys = [item.key for item in S.search('hotkey')]
        assert 'language_shortcuts' in keys and 'help_shortcuts' in keys

    def test_a_search_for_xbox_finds_the_grammar_shortcut_and_the_help(self):
        keys = [item.key for item in S.search('xbox')]
        assert 'fix_grammar_shortcut' in keys and 'help_shortcuts' in keys

    def test_a_search_for_a_topic_s_title_finds_it_first(self):
        assert S.search('Getting started')[0].key == 'help_start'

    def test_problems_finds_the_troubleshooting_topic(self):
        assert 'help_trouble' in [item.key for item in S.search('problem')]
