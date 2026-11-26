"""
Tests for the Help page (TODO.md U8, step 6, Decision 51): one card per topic of `core/guide_content.py`.

The words are `test_guide_content`'s business; these tests are about what the page does with them: a card for
each topic, the steps numbered and the notes bulleted, the numbers that change put in, a button to the page a
topic talks about, wrapping to the card, and the bullets following the theme.
"""
import pytest

from src.constants import LANGUAGES
from src.core import guide_content as guide
from src.core import settings_index as index
from src.ui.settings.page_help import HelpPage
from tests.settings_support import Host, SettingsConfig
from tests.test_components import env, holder, light, shown  # noqa: F401  (shared fixtures)

TOPICS = guide.TOPICS


@pytest.fixture
def make(shown, light):
    """Build the page in a mapped window: `make()` -> (page, host)."""
    shown.geometry('760x900+0+0')

    def build(width=None):
        if width is not None:
            shown.geometry(f'{width}x900+0+0')
        host = Host(shown, SettingsConfig())
        page = HelpPage(host)
        page.build(shown)
        page.body.pack(fill='both', expand=True, padx=20, pady=20)
        shown.update()
        return page, host
    return build


def lines_of(topic):
    return len(topic.steps) + len(topic.notes)


class TestTheCards:
    def test_every_entry_of_the_page_has_a_row(self, make):
        page, host = make()
        for each in index.entries_of('help'):
            assert page.row_for(each.key) is not None, each.key

    def test_a_row_says_what_its_topic_says(self, make):
        page, host = make()
        for topic in TOPICS:
            row = page.row_for(topic.key)
            assert row.title_label.cget('text') == topic.title
            assert row.description_label.cget('text') == topic.summary

    def test_there_is_a_line_for_every_step_and_note(self, make):
        page, host = make()
        assert len(page.texts) == sum(lines_of(topic) for topic in TOPICS)

    def test_the_lines_come_in_order_within_each_topic(self, make):
        page, host = make()
        shown = [label.cget('text') for label in page.texts]
        expected = [guide.fill(line, **page.values) for topic in TOPICS for line in (*topic.steps, *topic.notes)]
        assert shown == expected

    def test_the_numbers_that_change_are_put_in(self, make):
        page, host = make()
        everything = ' '.join(label.cget('text') for label in page.texts)
        assert '{' not in everything and '}' not in everything
        assert f'{len(LANGUAGES)} languages are supported.' in everything

    def test_help_has_provider_metadata_without_trial_quota(self, make):
        page, host = make()
        assert page.values['languages'] == len(LANGUAGES)
        assert page.values['providers'] > 0
        assert 'trial_limit' not in page.values


class TestStepsAndBullets:
    def markers(self, page, topic):
        """The marker label of each line of a topic, in order (the label before the text in its line)."""
        control = page.row_for(topic.key).control
        out = []
        for line in control.winfo_children():
            labels = [w for w in line.winfo_children() if w.winfo_class() in ('TLabel', 'Label')]
            if len(labels) == 2:
                out.append(labels[0])
        return out

    def test_steps_are_numbered_from_one(self, make):
        page, host = make()
        topic = guide.topic('help_start')
        marks = self.markers(page, topic)
        assert [m.cget('text') for m in marks[:len(topic.steps)]] == ['1.', '2.', '3.', '4.']

    def test_notes_are_a_round_icon_not_a_typed_symbol(self, make):
        page, host = make()
        topic = guide.topic('help_popup')
        marks = self.markers(page, topic)
        assert len(marks) == len(topic.notes) and all(m.cget('text') == '' and str(m.cget('image')) for m in marks)

    def test_there_is_one_bullet_icon_per_note(self, make):
        page, host = make()
        assert len(page.bullets) == sum(len(topic.notes) for topic in TOPICS)

    def test_a_topic_with_no_steps_has_no_numbers(self, make):
        page, host = make()
        topic = guide.topic('help_trouble')
        assert not any(m.cget('text').endswith('.') and m.cget('text')[:-1].isdigit()
                       for m in self.markers(page, topic))


class TestLinks:
    def test_a_topic_with_a_link_has_a_button_that_says_so(self, make):
        page, host = make()
        for topic in TOPICS:
            if topic.link:
                assert page.links[topic.key].cget('text') == topic.link[1]
            else:
                assert topic.key not in page.links

    def test_a_button_opens_the_page_it_names(self, make):
        page, host = make()
        for topic in TOPICS:
            if topic.link:
                page.links[topic.key].invoke()
        assert host.opened == [topic.link[0] for topic in TOPICS if topic.link]

    def test_each_button_keeps_its_own_target(self, make):
        """A loop that closes over its variable sends every button to the last page."""
        page, host = make()
        targets = {topic.link[0] for topic in TOPICS if topic.link}
        assert len(targets) > 1
        page.links['help_shortcuts'].invoke()
        assert host.opened == ['shortcuts']


class TestWrapping:
    def test_text_wraps_to_the_card_not_to_its_own_length(self, make):
        page, host = make(width=760)
        wide = [int(float(str(label.cget('wraplength')))) for label in page.texts]
        assert min(wide) > 0
        assert max(wide) <= page.body.winfo_width()

    def test_a_narrower_window_wraps_the_same_text_tighter(self, make):
        page, host = make(width=900)
        before = max(int(float(str(label.cget('wraplength')))) for label in page.texts)
        page.body.master.geometry('560x900+0+0')
        page.body.update()
        after = max(int(float(str(label.cget('wraplength')))) for label in page.texts)
        assert after < before


class TestTheme:
    def image_names(self, page):
        return [str(marker.cget('image')) for marker in page.bullets]

    def test_the_bullets_follow_the_theme(self, make, light):
        page, host = make()
        before = self.image_names(page)
        light.manager.set_mode('dark')
        page.body.update()
        after = self.image_names(page)
        assert before != after and all(after)

    def test_the_page_stops_listening_when_it_goes(self, make, light):
        page, host = make()
        assert page._retheme in light.manager._subscribers
        page.body.destroy()
        assert page._retheme not in light.manager._subscribers

    def test_a_theme_change_after_the_page_went_does_nothing(self, make, light):
        page, host = make()
        page.body.destroy()
        light.manager.set_mode('dark')
        light.manager.set_mode('light')
