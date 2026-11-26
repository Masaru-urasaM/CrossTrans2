"""U9b acceptance: complete Dictionary flow, presentation fidelity, theme and lifecycle."""
import gc
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
import weakref
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.furigana import RubySegment
from src.ui import components as ui
from src.ui.custom_word_boxes import CustomWordBoxesFrame, MAX_BOXES
from src.ui.dictionary_mode import WordButtonFrame
from src.ui.dictionary_pill import WordPill
from src.ui.dictionary_popup import DictionaryPopup
from src.ui.dictionary_render import DictRun, split_dictionary_text
from src.ui.dictionary_result import DictionaryResultWindow, parse_entries, take_runs
from tests.test_components import env  # noqa: F401


RESULT = ('## [勉強]\n\n1. **Translation**: study\n2. **Source Language**: Japanese\n'
          '3. **Definition**: the act of studying\n4. **Word Type**: noun\n'
          '5. **Pronunciation**: べんきょう\n6. **Synonyms** (if any): 学習 → learning\n'
          '7. **Antonyms** (if any): None\n8. **Examples**:\n'
          '   - 毎日勉強します。 → I study every day.\n')


def pump(root, predicate=lambda: False, timeout=0.15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root.update()
        if predicate():
            break
        time.sleep(0.005)


@pytest.fixture
def views(env, tk_root):
    created = []

    def make(text=RESULT, **kw):
        view = DictionaryResultWindow(tk_root, text, kw.pop('target', 'English'),
                                      area=(0, 0, 1600, 1000), position=(70, 60), **kw)
        created.append(view)
        pump(tk_root)
        return view

    make.created = created
    yield make
    for view in created:
        if view.window.winfo_exists():
            view.window.destroy()


@pytest.fixture
def picker(env, tk_root, monkeypatch):
    monkeypatch.setattr(WordButtonFrame, '_tokenize_text', lambda self, text: text.split())
    top = tk.Toplevel(tk_root)
    top.geometry('760x560+60+60')
    frame = WordButtonFrame(top, 'one two three four', lambda value: None)
    frame.pack(fill='both', expand=True)
    boxes = CustomWordBoxesFrame(frame.frame, language='English', furigana_enabled=False)
    frame.insert_custom_widget(boxes.frame)
    frame.set_drop_target(boxes)
    pump(tk_root)
    yield top, frame, boxes
    if top.winfo_exists():
        top.destroy()


def model(text=RESULT, annotate=False, source=None):
    runs = split_dictionary_text(text, 'English', ['勉強'], ['accent'], annotate=annotate, source_lang=source)
    return parse_entries(text, runs)


class TestCardModel:
    def test_all_eight_fields_and_decorated_labels_are_present(self):
        entry, = model()
        assert ''.join(r.base for r in entry.heading) == '勉強'
        assert [f.number for f in entry.fields] == list(range(1, 9))
        assert [f.label for f in entry.fields] == ['Translation', 'Source Language', 'Definition',
                                                  'Word Type', 'Pronunciation', 'Synonyms', 'Antonyms', 'Examples']
        assert entry.fields[5].text == '学習 → learning'
        assert entry.fields[-1].text == '- 毎日勉強します。 → I study every day.'

    @pytest.mark.parametrize('index', range(8))
    def test_each_field_value_survives(self, index):
        values = ['value ' + str(i) for i in range(8)]
        labels = ['Translation', 'Source Language', 'Definition', 'Word Type',
                  'Pronunciation', 'Synonyms', 'Antonyms', 'Examples']
        text = '## word\n' + '\n'.join(f'{i+1}. **{label}**: {values[i]}' for i, label in enumerate(labels))
        assert model(text)[0].fields[index].text == values[index]

    def test_entries_have_independent_sources_and_values(self):
        entries = model(RESULT + '\n---\n\n' + RESULT.replace('勉強', '会議').replace('study', 'meeting'))
        assert len(entries) == 2
        assert entries[0].fields[0].text == 'study'
        assert entries[1].fields[0].text == 'meeting'
        assert ''.join(r.base for r in entries[1].heading) == '会議'

    @pytest.mark.parametrize('text', ['unstructured reply', 'a preamble\n\n## word\nbody',
                                    '## word\n1. **Odd field**: keep this\ncontinued',
                                    '## [word]\n2. **Translation**: answer'])
    def test_unrecognised_content_is_still_visible(self, text):
        entries = model(text)
        visible = ' '.join(''.join(r.base for r in e.heading) + ' ' + ' '.join(f.text for f in e.fields)
                           for e in entries)
        for value in ['unstructured reply', 'a preamble', 'body', 'keep this', 'continued', 'answer']:
            if value in text:
                assert value in visible

    @pytest.mark.parametrize('text', ['', '\n', '  \n  '])
    def test_empty_replies_have_no_fake_entry(self, text):
        assert model(text) == ()

    def test_whole_ruby_pairs_survive_presentation_slices(self):
        runs = (DictRun('## ['), DictRun('勉強', 'べんきょう', 'accent'), DictRun(']\n'))
        assert take_runs(runs, 4, 6) == (runs[1],)

    def test_readings_are_generated_before_removing_protocol_labels(self):
        entries = model(annotate=True, source='Japanese')
        assert any(run.ruby for run in entries[0].heading)
        assert not any(run.ruby for run in entries[0].fields[4].runs)
        assert any(run.ruby for run in entries[0].fields[5].runs)
        assert any(run.ruby for run in entries[0].fields[7].runs)


class TestDictionaryCards:
    def test_copy_keeps_the_exact_model_response(self, views, monkeypatch):
        copied = []
        monkeypatch.setattr('pyperclip.copy', copied.append)
        view = views(words=['勉強'], source_lang='Japanese')
        view.copy_button.invoke()
        assert copied == [RESULT]
        assert view.copy_button.cget('text') == 'Copied'
        assert view.copy_button.icon_name == 'check2'

    def test_closed_copy_feedback_cancels_its_timer(self, views, tk_root, monkeypatch):
        monkeypatch.setattr('pyperclip.copy', lambda value: None)
        view = views()
        view.copy()
        job = view._copy_job
        view.window.destroy()
        assert job not in tk_root.tk.call('after', 'info')

    def test_theme_switch_preserves_text_readings_and_highlighting(self, views, env):
        view = views(words=['勉強'], source_lang='Japanese')
        before = [box.get_plain() for box in view.boxes]
        bases = [[box.pair_base_text(frame) for frame in box._frames] for box in view.boxes]
        env.manager.set_mode('dark')
        assert [box.get_plain() for box in view.boxes] == before
        assert [[box.pair_base_text(frame) for frame in box._frames] for box in view.boxes] == bases
        found = []
        for box in view.boxes:
            assert box.cget('bg') == env.palette.surface
            for frame in box._frames:
                if box.pair_base_text(frame) == '勉強':
                    found.append(box.pair_labels(frame)[1][0].cget('fg'))
        assert found and set(found) == {env.palette.accent}

    def test_multiple_entries_use_distinct_cards(self, views):
        view = views(RESULT + '\n' + RESULT.replace('勉強', '会議'))
        assert len(view.cards) == 2
        assert all(isinstance(card, ui.Card) for card in view.cards)

    def test_no_result_uses_the_empty_state(self, views):
        view = views('')
        assert not view.cards
        assert any(isinstance(child, ui.EmptyState) for child in view.scroll.content.winfo_children())

    def test_resize_reflows_values_without_inner_scrolling(self, views, tk_root):
        view = views(RESULT.replace('the act of studying', 'a longer explanation ' * 25))
        view.window.geometry('450x500')
        pump(tk_root)
        assert all(box.yview() == (0.0, 1.0) for box in view.boxes)
        assert view.scroll.canvas.yview()[1] < 1.0

    def test_escape_closes_the_window(self, views, tk_root):
        view = views()
        view.window.focus_force()
        tk_root.update()
        view.window.event_generate('<Escape>')
        tk_root.update()
        assert not view.window.winfo_exists()

    def test_closed_view_is_released(self, views):
        view = views()
        reference = weakref.ref(view)
        view.window.destroy()
        views.created.clear()
        del view
        gc.collect()
        assert reference() is None


class TestWordAndPhrasePills:
    def test_space_and_shift_click_select_words(self, picker, tk_root):
        top, frame, boxes = picker
        first = frame.word_labels[0].widget
        first.focus_force()
        tk_root.update()
        first.event_generate('<space>')
        assert frame.get_selected_words() == ['one']
        frame._on_word_click(2, SimpleNamespace(state=1))
        assert frame.get_selected_words() == ['one', 'two', 'three']
        frame.clear_btn.invoke()
        assert frame.get_selected_words() == []

    def test_selected_pills_use_the_soft_accent_in_both_themes(self, picker, env):
        top, frame, boxes = picker
        frame._toggle_word(0)
        env.manager.set_mode('dark')
        pill = frame.word_labels[0].widget
        assert isinstance(pill, WordPill)
        assert pill.label.cget('bg') == env.palette.soft
        assert pill.label.cget('fg') == env.palette.soft_text
        assert pill.cget('takefocus') == '1'
        pill._hover(True)
        assert pill.label.cget('bg') == env.palette.soft

    def test_lookup_is_single_flight_until_result(self, picker):
        top, frame, boxes = picker
        calls = []
        frame.set_lookup_callback(calls.append)
        frame._toggle_word(0)
        frame.lookup_btn.invoke()
        frame._trigger_lookup()
        assert calls == [['one']]
        assert str(frame.lookup_btn.cget('state')) == 'disabled'
        frame.stop_lookup_animation()
        assert str(frame.lookup_btn.cget('state')) == 'normal'

    def test_enter_keeps_existing_tags_and_joins_typed_content(self, picker):
        top, frame, boxes = picker
        box = boxes._boxes[0]
        box.add_word_tag('ice')
        box.text_widget.insert('end', 'cream')
        box.text_widget.mark_set('insert', 'end-1c')
        box._on_enter(None)
        assert box.get_content() == 'ice cream'
        assert [tag.word for tag in box._tags] == ['ice', 'cream']

    def test_twenty_phrases_reach_lookup_and_keep_first_box_protected(self, picker):
        top, frame, boxes = picker
        for i in range(25):
            boxes._on_add_clicked(len(boxes._boxes) - 1)
        assert len(boxes._boxes) == MAX_BOXES == 20
        phrases = [f'Phrase {index + 1}' for index in range(20)]
        for box, phrase in zip(boxes._boxes, phrases):
            box.text_widget.insert('end', phrase)
        assert boxes.get_all_phrases() == phrases
        assert boxes._boxes[0].remove_btn is None
        assert all(not box.add_btn.winfo_manager() for box in boxes._boxes)
        top.update_idletasks()
        boxes.box_scroll.reveal(boxes._boxes[-1].text_widget)
        assert boxes.box_scroll.canvas.yview()[0] > 0
        assert frame.lookup_btn.winfo_ismapped()
        boxes._on_remove_clicked(1)
        assert len(boxes._boxes) == MAX_BOXES - 1
        assert all(box.add_btn.winfo_manager() for box in boxes._boxes)
        boxes._on_add_clicked(len(boxes._boxes) - 1)
        assert len(boxes._boxes) == 20

    def test_drag_is_bound_to_this_window_and_destroy_removes_it(self, picker, tk_root):
        top, frame, boxes = picker
        baseline = tk_root.bind_all('<B1-Motion>')
        frame._start_drag_to_box('one', SimpleNamespace(x_root=100, y_root=100))
        assert frame._drag_ghost is None
        frame._drag_motion(SimpleNamespace(x_root=120, y_root=120))
        ghost = frame._drag_ghost
        assert top.bind('<B1-Motion>')
        assert tk_root.bind_all('<B1-Motion>') == baseline
        frame.destroy()
        assert not ghost.winfo_exists()
        assert not top.bind('<B1-Motion>')

    def test_tag_delete_removes_the_embedded_window_and_plain_content(self, picker):
        top, frame, boxes = picker
        box = boxes._boxes[0]
        box.add_word_tag('keep')
        box.add_word_tag('remove')
        box._tags[-1]._remove()
        assert box.get_content() == 'keep'
        assert len(box.text_widget.dump('1.0', 'end', window=True)) == 1


class TestLanguageDialogs:
    @pytest.mark.parametrize('locale,text_scale', [('en', 1.0), ('en', 1.5), ('ja', 1.0), ('ja', 1.5)])
    def test_twenty_phrase_guidance_fits_narrow_wide_and_live_themes(self, env, tk_root, monkeypatch,
                                                                   locale, text_scale):
        from src.ui.i18n import tr
        previous = env.manager.ui_language, env.manager.text_scale, env.manager.mode
        config = MagicMock()
        config.get_furigana_enabled.return_value = False
        flow = DictionaryPopup(tk_root, config, MagicMock(), MagicMock(), MagicMock())
        monkeypatch.setattr(flow, '_work_area', lambda *args: (0, 0, 1600, 1000))
        monkeypatch.setattr(WordButtonFrame, '_tokenize_text', lambda self, text: text.split())
        callback_errors = []
        monkeypatch.setattr(tk_root, 'report_callback_exception', lambda *args: callback_errors.append(args))
        window = None

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        try:
            env.manager.set_ui_language(locale)
            env.manager.set_text_scale(text_scale)
            window = flow._open_with_language('one two three', 'English', target_language='Japanese')
            for _ in range(19):
                flow.custom_boxes._on_add_clicked(0)
            message = tr('Click words to select them. Shift-click selects a range.')
            guidance = next(widget for widget in descendants(window)
                            if widget.winfo_class() == 'TLabel' and str(widget.cget('text')) == message)
            for dimensions in ('500x560', '800x760'):
                window.geometry(dimensions)
                for mode in ('pink', 'blue', 'yellow', 'green', 'light', 'dark', 'system'):
                    env.manager.set_mode(mode)
                    window.update_idletasks()
                    assert guidance.winfo_reqwidth() <= guidance.winfo_width()
                    assert flow.word_frame.lookup_btn.winfo_ismapped()
                    assert (flow.word_frame.lookup_btn.winfo_rooty() + flow.word_frame.lookup_btn.winfo_height()
                            <= window.winfo_rooty() + window.winfo_height())
            assert not callback_errors
        finally:
            if window is not None and window.winfo_exists():
                window.destroy()
            env.manager.set_ui_language(previous[0])
            env.manager.set_text_scale(previous[1])
            env.manager.set_mode(previous[2])

    @pytest.mark.parametrize('first_succeeds', [True, False])
    def test_concurrent_windows_only_release_their_own_lookup(self, env, tk_root, monkeypatch,
                                                            first_succeeds):
        from src.ui.quick_translate import QuickTranslateManager
        monkeypatch.setattr(WordButtonFrame, '_tokenize_text', lambda self, text: text.split())
        config, service = MagicMock(), MagicMock()
        config.get_furigana_enabled.return_value = False
        service.get_trial_info.return_value = None
        first, second = threading.Event(), threading.Event()

        def lookup(words, *args, **kwargs):
            (first if words == ['first'] else second).wait(5)
            if words == ['first'] and not first_succeeds:
                raise RuntimeError('first lookup failed')
            return '## word\n1. **Translation**: answer'

        service.dictionary_lookup.side_effect = lookup
        quick = QuickTranslateManager(tk_root, config)
        flow = DictionaryPopup(tk_root, config, service, MagicMock(), quick)
        monkeypatch.setattr(flow, '_work_area', lambda *args: (0, 0, 1600, 1000))
        before = set(tk_root.winfo_children())
        try:
            flow._open_with_language('first', 'English', target_language='French')
            a = flow.word_frame
            a._toggle_word(0)
            a._trigger_lookup()
            flow._open_with_language('second', 'English', target_language='French')
            b = flow.word_frame
            b._toggle_word(0)
            b._trigger_lookup()
            first.set()
            pump(tk_root, lambda: not a._lookup_animation_running, timeout=5)
            assert not a._lookup_animation_running
            assert str(a.lookup_btn.cget('state')) == 'normal'
            assert b._lookup_animation_running
            assert str(b.lookup_btn.cget('state')) == 'disabled'
            second.set()
            pump(tk_root, lambda: not b._lookup_animation_running, timeout=5)
            assert not b._lookup_animation_running
            assert str(b.lookup_btn.cget('state')) == 'normal'
        finally:
            first.set()
            second.set()
            for window in set(tk_root.winfo_children()) - before:
                window.destroy()

    @pytest.mark.parametrize('how', ['close', 'destroy'])
    def test_dialog_timer_belongs_to_root_and_dies_with_its_window(self, env, tk_root, how):
        flow = DictionaryPopup(tk_root, MagicMock(), MagicMock(), MagicMock(), MagicMock())
        dialog = flow._show_nlp_not_installed_dialog()
        job = dialog._front_job
        command = tk_root.tk.call('after', 'info', job)[0]
        try:
            assert command in tk_root._tclCommands
            assert command not in (dialog.window._tclCommands or [])
        finally:
            dialog.close() if how == 'close' else dialog.window.destroy()
        assert job not in tk_root.tk.call('after', 'info')

    def test_destroying_a_dialog_child_keeps_its_topmost_timer(self, env, tk_root):
        dialog = ui.Dialog(tk_root, 'Dictionary test').show()
        job = dialog._front_job
        child = ui.Label(dialog.body, 'temporary')
        child.destroy()
        assert job in tk_root.tk.call('after', 'info')
        dialog.close()

    @pytest.mark.parametrize('area', [(0, 0, 1366, 728), (0, 0, 1024, 600),
                                     (0, 0, 800, 480), (1920, 0, 3840, 1040)])
    def test_picker_fits_small_and_secondary_work_areas(self, env, tk_root, monkeypatch, area):
        monkeypatch.setattr(WordButtonFrame, '_tokenize_text', lambda self, text: text.split())
        config, service = MagicMock(), MagicMock()
        config.get_furigana_enabled.return_value = False
        service.get_trial_info.return_value = None
        flow = DictionaryPopup(tk_root, config, service, MagicMock(), MagicMock())
        monkeypatch.setattr(flow, '_work_area', lambda *args: area)
        window = flow._open_with_language('one two three', 'English', target_language='French')
        try:
            pump(tk_root)
            text = flow.word_frame.text_widget
            line = tkfont.Font(font=text.cget('font')).metrics('linespace')
            assert text.winfo_height() >= 2 * line
            assert window.winfo_width() < area[2] - area[0]
            assert window.winfo_height() + 40 <= area[3] - area[1]
            for button in [flow.word_frame.lookup_btn, flow.word_frame.clear_btn,
                           flow.word_frame.expand_btn, flow.word_frame.exit_btn]:
                assert button.winfo_ismapped()
                assert button.winfo_height() >= button.winfo_reqheight()
        finally:
            window.destroy()

    @pytest.mark.parametrize('case', ['none', 'kanji', 'missing', 'unknown'])
    def test_every_dialog_is_a_nonmodal_component(self, env, tk_root, monkeypatch, case):
        flow = DictionaryPopup(tk_root, MagicMock(), MagicMock(), MagicMock(), MagicMock())
        monkeypatch.setattr('src.ui.dictionary_popup.nlp_manager.get_installed_languages',
                            lambda: ['English', 'Japanese', 'Chinese (Simplified)'])
        before = tk_root.grab_current()
        if case == 'none':
            dialog = flow._show_nlp_not_installed_dialog()
        else:
            dialog = flow._show_language_selection_dialog('勉強', 'Korean' if case == 'missing' else None,
                                                          case == 'missing', kanji_only=case == 'kanji')
        try:
            assert isinstance(dialog, ui.Dialog)
            assert dialog.window.winfo_viewable()
            assert not str(dialog.window.transient())
            assert tk_root.grab_current() is before
            if case == 'kanji':
                assert [v for v, label in dialog.language_picker.options][:2] == ['Japanese', 'Chinese (Simplified)']
        finally:
            dialog.close()

    def test_worker_never_calls_tk_and_delivery_runs_on_ui_thread(self, env, tk_root, monkeypatch):
        main_thread = threading.get_ident()
        service, quick = MagicMock(), MagicMock()
        service.dictionary_lookup.side_effect = lambda *a, **k: threading.get_ident()
        service.get_trial_info.return_value = None
        flow = DictionaryPopup(tk_root, MagicMock(), service, MagicMock(), quick)
        calls = []
        flow._show_result = lambda result, *a: calls.append((result, threading.get_ident()))
        flow._do_lookup(['one'], 'English')
        pump(tk_root, lambda: bool(calls), timeout=5)
        assert calls and calls[0][0] != main_thread
        assert calls[0][1] == main_thread
