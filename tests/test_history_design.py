"""U9a: History's filters, safe actions, theme, and responsive layout."""
import tkinter as tk
import gc
import weakref

import pytest

from src.ui import components as ui
from src.ui.theme import tokens
from tests.test_components import env  # noqa: F401
from tests.test_history_dialog import FakeHistoryManager, finish, make_entries, rows_of
from src.ui.history_dialog import HistoryDialog, entry_pair, filter_entries, pair_label


class TestHistoryFiltering:
    @pytest.mark.parametrize('field', ['original', 'translated', 'target_lang', 'source_lang'])
    def test_every_searchable_field_is_case_insensitive(self, field):
        rows = [{field: 'A Needle in text'}, {field: 'Something else'}]
        assert filter_entries(rows, '  nEEdLe  ') == rows[:1]

    @pytest.mark.parametrize('query', ['[', '.*', '+', '(text)', '\\'])
    def test_queries_are_literal_text(self, query):
        rows = [{'original': 'literal ' + query}, {'original': 'ordinary text'}]
        assert filter_entries(rows, query) == rows[:1]

    def test_missing_labels_and_fields_are_searchable_safely(self):
        assert entry_pair({}) == ('Unknown', 'Unknown')
        assert entry_pair({'source_lang': None}) == ('Unknown', 'Unknown')
        assert filter_entries([{}, {'original': None}], 'hello') == []
        assert filter_entries([{}]) == [{}]
        assert pair_label(('Unknown', 'French')) == 'To French'


@pytest.fixture
def history(env, tk_root):
    opened = []

    def make(entries=None):
        store = FakeHistoryManager(make_entries(5) if entries is None else entries)
        loaded = []
        dialog = HistoryDialog(tk_root, store, loaded.append)
        opened.append(dialog)
        finish(dialog)
        return dialog, store, loaded

    yield make
    for dialog in opened:
        if dialog.window.winfo_exists():
            dialog.window.destroy()


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def search(dialog, text):
    dialog.search_entry.set(text)
    finish(dialog)


class TestHistoryDesign:
    def test_closed_view_is_released(self, env, tk_root):
        dialog = HistoryDialog(tk_root, FakeHistoryManager([]), lambda item: None)
        reference = weakref.ref(dialog)
        dialog.window.destroy()
        del dialog
        gc.collect()
        assert reference() is None

    def test_typing_filters_before_focus_leaves(self, history):
        dialog, _, _ = history()
        dialog.search_entry.focus_force()
        dialog.window.update()
        dialog.search_entry.insert(0, 'number 3')
        dialog.search_entry.event_generate('<KeyRelease>', keysym='3')
        finish(dialog)
        assert len(dialog.visible_entries) == 1
        assert dialog.visible_entries[0]['id'] == 'id-3'

    def test_leaving_search_does_not_rebuild_rows_or_reset_scrolling(self, history):
        dialog, _, _ = history(make_entries(20))
        dialog.search_entry.set('source')
        finish(dialog)
        first = rows_of(dialog)[0]
        dialog.canvas.yview_moveto(0.5)
        before = dialog.canvas.yview()
        dialog.search_entry.focus_force()
        dialog.window.update()
        dialog.more_button.focus_force()
        dialog.window.update()
        assert rows_of(dialog)[0] is first
        assert dialog.canvas.yview() == before

    def test_hidden_parent_does_not_hide_history(self, history, tk_root):
        tk_root.withdraw()
        dialog, _, _ = history()
        assert dialog.window.winfo_viewable()
        assert not str(dialog.window.transient())
        assert dialog.window.grab_current() is None

    def test_search_is_a_real_placeholder(self, history):
        dialog, _, _ = history()
        assert isinstance(dialog.search_entry, ui.TextField)
        assert dialog.search_var.get() == dialog.search_entry.get() == ''

    def test_language_filter_and_search_intersect(self, history):
        entries = make_entries(3)
        entries[1]['target_lang'] = 'French'
        dialog, _, _ = history(entries)
        dialog.set_pair(('Japanese', 'English'))
        search(dialog, 'number 1')
        assert dialog.visible_entries == []
        search(dialog, 'number 2')
        assert dialog.visible_entries == [entries[2]]

    def test_pair_filters_follow_first_occurrence(self, history):
        entries = make_entries(4)
        entries[1]['target_lang'] = 'French'
        dialog, _, _ = history(entries)
        assert dialog.pairs == [('Japanese', 'English'), ('Japanese', 'French')]

    def test_deleting_last_entry_in_a_pair_returns_to_all(self, history):
        entries = make_entries(2)
        entries[1]['target_lang'] = 'French'
        dialog, _, _ = history(entries)
        dialog.set_pair(('Japanese', 'French'))
        dialog._delete_item(entries[1])
        finish(dialog)
        assert dialog.selected_pair is None
        assert dialog.visible_entries == entries[:1]

    def test_rows_are_cards_with_keyboard_actions(self, history):
        dialog, _, _ = history()
        row = rows_of(dialog)[0]
        assert isinstance(row, ui.Card)
        actions = [w.label for w in descendants(row) if isinstance(w, ui.IconButton)]
        assert actions == ['Copy translation', 'Open in Translator', 'Delete translation']
        assert all(str(w.cget('takefocus')) == '1' for w in descendants(row)
                   if isinstance(w, ui.IconButton))

    def test_clear_all_waits_for_confirmation(self, history):
        dialog, store, _ = history()
        question = dialog._clear_all()
        assert store.entries
        assert question._default.cget('text') == 'Cancel'
        assert question.window.grab_current() is None
        question.cancel()
        assert store.entries

    def test_confirmed_clear_removes_all_and_updates_empty_state(self, history):
        dialog, store, _ = history()
        question = dialog._clear_all()
        next(b for b in question._button_list if b.cget('text') == 'Clear all').invoke()
        finish(dialog)
        assert store.entries == dialog.all_history == dialog.visible_entries == []
        assert isinstance(dialog.empty_state, ui.EmptyState)

    def test_empty_state_returns_to_translator(self, history, tk_root):
        dialog, _, _ = history([])
        assert dialog.empty_state.button.cget('text') == 'Open the Translator'
        dialog.empty_state.button.invoke()
        assert not dialog.window.winfo_exists()

    def test_search_escape_clears_before_closing(self, history):
        dialog, _, _ = history()
        search(dialog, 'number 1')
        dialog._escape()
        assert dialog.window.winfo_exists() and dialog.search_var.get() == ''
        dialog._escape()
        assert not dialog.window.winfo_exists()

    def test_copy_uses_exact_plain_text_without_opening(self, history, monkeypatch):
        dialog, store, loaded = history()
        copied = []
        monkeypatch.setattr(dialog.window, 'clipboard_clear', lambda: None)
        monkeypatch.setattr(dialog.window, 'clipboard_append', copied.append)
        dialog._copy_item(store.entries[0])
        assert copied == [store.entries[0]['translated']]
        assert loaded == [] and dialog.window.winfo_exists()

    @pytest.mark.parametrize('mode', ['light', 'dark'])
    def test_theme_follows_while_open(self, history, env, mode):
        dialog, _, _ = history()
        env.manager.set_mode(mode)
        finish(dialog)
        assert dialog.canvas.cget('bg').lower() == tokens.PALETTES[mode].bg.lower()
        assert all(isinstance(row, ui.Card) for row in rows_of(dialog))

    def test_overflow_menu_does_not_post_until_requested(self, history):
        dialog, _, _ = history()
        assert dialog.more_button.cget('text') == 'Clear all history'
        assert dialog.more_button.winfo_ismapped()
        assert not hasattr(dialog, 'show_menu')

    def test_more_languages_keeps_every_pair_reachable(self, history, monkeypatch):
        entries = make_entries(5)
        for index, item in enumerate(entries):
            item['target_lang'] = ['English', 'French', 'German', 'Japanese', 'Korean'][index]
        dialog, _, _ = history(entries)
        monkeypatch.setattr(tk.Menu, 'post', lambda *args: None)
        dialog.show_pairs()
        assert dialog.menu.index('end') == 4
        dialog.menu.invoke(4)
        finish(dialog)
        assert dialog.visible_entries == entries[4:]
        assert dialog.pair_variable.get() == 4
        assert any(str(w.cget('text')) == pair_label(dialog.pairs[4])
                   for w in dialog._filters)

    @pytest.mark.parametrize('index', [0, 1, 2])
    def test_clicking_a_pair_pill_selects_that_pair(self, history, index):
        entries = make_entries(3)
        for item, language in zip(entries, ['French', 'German', 'Japanese']):
            item['target_lang'] = language
        dialog, _, _ = history(entries)
        dialog._filters[index + 1].invoke()
        finish(dialog)
        assert dialog.visible_entries == [entries[index]]

    def test_no_results_action_clears_both_filters(self, history):
        dialog, _, _ = history()
        dialog.set_pair(dialog.pairs[0])
        search(dialog, 'not a translation')
        dialog.empty_state.button.invoke()
        finish(dialog)
        assert dialog.selected_pair is None and dialog.search_var.get() == ''
        assert len(dialog.visible_entries) == 5

    def test_escape_clears_pair_before_closing(self, history):
        dialog, _, _ = history()
        dialog.set_pair(dialog.pairs[0])
        dialog._escape()
        assert dialog.window.winfo_exists() and dialog.selected_pair is None

    def test_closing_unsubscribes_the_window(self, history, env):
        dialog, _, _ = history()
        assert dialog._retheme in env.manager._subscribers
        dialog.window.destroy()
        assert dialog._retheme not in env.manager._subscribers

    def test_plain_lines_recolour_while_open(self, history, env):
        dialog, _, _ = history()
        for mode in ['dark', 'light']:
            env.manager.set_mode(mode)
            for row in rows_of(dialog):
                assert str(row.lines[0][0].cget('foreground')).lower() == env.palette.muted.lower()
                assert str(row.lines[1][0].cget('foreground')).lower() == env.palette.text.lower()

    def test_default_enter_in_clear_question_keeps_history(self, history):
        dialog, store, _ = history()
        question = dialog._clear_all()
        question.window.focus_force()
        question.window.update()
        question.window.event_generate('<Return>')
        dialog.window.update()
        assert store.entries and not question.exists

    def test_clear_question_is_single_instance(self, history):
        dialog, _, _ = history()
        assert dialog._clear_all() is dialog._clear_all()

    def test_row_actions_reach_the_correct_entry(self, history):
        dialog, store, loaded = history()
        row = rows_of(dialog)[2]
        row.actions[1].invoke()
        assert loaded == [store.entries[2]]

    def test_delete_button_only_deletes_its_entry(self, history):
        dialog, store, loaded = history()
        expected = store.entries[2]
        rows_of(dialog)[2].actions[2].invoke()
        finish(dialog)
        assert expected not in store.entries and len(store.entries) == 4
        assert loaded == []

    def test_hover_and_keyboard_focus_reveal_the_actions(self, history):
        dialog, _, _ = history()
        row = rows_of(dialog)[0]
        assert all(not str(button.cget('image')) for button in row.actions)
        row.event_generate('<Enter>')
        assert all(str(button.cget('image')) for button in row.actions)
        for button in row.actions:
            button.hide()
        row.actions[0].focus_force()
        dialog.window.update()
        assert all(str(button.cget('image')) for button in row.actions)

    def test_theme_keeps_hidden_actions_hidden(self, history, env):
        dialog, _, _ = history()
        row = rows_of(dialog)[0]
        assert all(not str(button.cget('image')) for button in row.actions)
        mode = 'dark' if env.palette.mode == 'light' else 'light'
        env.manager.set_mode(mode)
        assert all(not str(button.cget('image')) for button in row.actions)

    def test_search_retains_reusable_rows_and_closing_releases_them(self, history):
        dialog, _, _ = history()
        reference = weakref.ref(rows_of(dialog)[0])
        search(dialog, 'missing')
        gc.collect()
        assert reference() is not None
        assert reference().entry is None
        assert not reference().winfo_manager()
        dialog.window.destroy()
        gc.collect()
        assert reference() is None

    def test_resize_refits_readings_without_recreating_the_canvas(self, history, monkeypatch):
        from src.core.furigana import RubySegment
        from src.ui import history_dialog as module
        from src.ui.ruby_text import RubyLine
        monkeypatch.setattr(module.furigana, 'should_annotate', lambda *args: True)
        monkeypatch.setattr(module.furigana, 'annotate',
                            lambda *args: (RubySegment('Tokyo', 'tokyo'),) * 25)
        dialog, _, _ = history(make_entries(1))
        dialog.furigana_enabled = True
        dialog._filter_and_display()
        finish(dialog)
        line = next(w for w in descendants(dialog.scrollable_frame) if isinstance(w, RubyLine))
        old_width = line.winfo_reqwidth()
        dialog.window.geometry('500x450')
        finish(dialog)
        dialog._refit()
        assert line.winfo_reqwidth() < old_width
        assert line.winfo_reqwidth() <= dialog._line_px()
        assert line.get_plain().endswith('...')

    def test_theme_recolours_readings_and_keeps_the_scroll_position(self, history, env, monkeypatch):
        from src.core.furigana import RubySegment
        from src.ui import history_dialog as module
        from src.ui.ruby_text import RubyLine
        monkeypatch.setattr(module.furigana, 'should_annotate', lambda *args: True)
        monkeypatch.setattr(module.furigana, 'annotate', lambda *args: (RubySegment('Tokyo', 'tokyo'),))
        dialog, _, _ = history(make_entries(20))
        dialog.furigana_enabled = True
        dialog._filter_and_display()
        finish(dialog)
        dialog.canvas.yview_moveto(0.5)
        before = dialog.canvas.yview()
        env.manager.set_mode('dark')
        finish(dialog)
        line = next(w for w in descendants(dialog.scrollable_frame) if isinstance(w, RubyLine))
        assert line.cget('bg').lower() == env.palette.surface.lower()
        assert line.itemcget(line.find_withtag('reading')[0], 'fill').lower() == env.palette.ruby.lower()
        assert dialog.canvas.yview() == before

    def test_resizing_refits_long_lines(self, history):
        entries = make_entries(1)
        entries[0]['original'] = 'A long sentence to wrap. ' * 20
        dialog, _, _ = history(entries)
        dialog.window.geometry('500x450')
        finish(dialog)
        dialog._refit()
        labels = [w for w in rows_of(dialog)[0].winfo_children()
                  if isinstance(w, ui.Label) and 'long sentence' in str(w.cget('text'))]
        assert labels and labels[0].winfo_reqwidth() <= dialog._line_px()
