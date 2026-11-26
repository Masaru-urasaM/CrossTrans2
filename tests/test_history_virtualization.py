"""History retention, demand paging, recycled row identity and keyboard continuity."""
import copy
import tkinter as tk
import pytest

from src.core.history import HistoryManager
from src.ui.history_dialog import HistoryDialog, FIRST_ROWS, ROW_BATCH, MAX_LIVE_ROWS
from tests.test_components import env  # noqa: F401
from tests.test_history_dialog import FakeHistoryManager, make_entries, finish, rows_of
from tests.test_translation_cache import FakeConfig


@pytest.fixture
def virtual_history(env, tk_root):
    windows = []

    def create(*, quick=False, embedded=False, entries=None, ruby=False):
        parent = tk.Toplevel(tk_root)
        parent.geometry('760x650')
        windows.append(parent)
        container = tk.Frame(parent) if embedded else None
        if container is not None:
            container.pack(fill='both', expand=True)
        store = FakeHistoryManager(make_entries(200) if entries is None else entries)
        loaded = []
        dialog = HistoryDialog(parent, store, loaded.append, container=container,
                               quick_pick=quick, furigana_enabled=ruby)
        windows.append(dialog.window)
        return dialog, store, loaded

    yield create
    for window in reversed(windows):
        if window.winfo_exists():
            window.destroy()


def position(dialog, index):
    dialog.canvas.yview_moveto(index / dialog._loaded_count)
    finish(dialog)


def test_retention_keeps_two_hundred_newest_and_cache_reaches_oldest_retained():
    config = FakeConfig()
    history = HistoryManager(config)
    for index in range(205):
        history.add_entry(f'Source {index}', f'Result {index}', 'Japanese', source_lang='English')
    saved = history.get_history()
    assert HistoryManager.MAX_HISTORY == len(saved) == 200
    assert [row['original'] for row in saved] == [f'Source {index}' for index in range(204, 4, -1)]
    assert history.find_cached('Source 5', 'Japanese') == 'Result 5'
    assert history.find_cached('Source 4', 'Japanese') is None


def test_two_hundred_records_survive_real_config_reload(tmp_path, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, 'CONFIG_DIR', str(tmp_path))
    monkeypatch.setattr(Config, 'CONFIG_FILE', str(tmp_path / 'fixture.json'))
    config = Config()
    config._config['history'] = copy.deepcopy(make_entries(200))
    history = HistoryManager(config)
    history.add_entry('Newest source', 'Newest result', 'English', source_lang='Japanese')
    reloaded = HistoryManager(Config()).get_history()
    assert len(reloaded) == 200
    assert reloaded[0]['original'] == 'Newest source'
    assert reloaded[-1]['id'] == 'id-198'


@pytest.mark.parametrize('quick,embedded', [(False, False), (False, True), (True, True)])
def test_initial_page_prefetch_stops_at_twenty_four_until_thirteenth_row(virtual_history, quick, embedded):
    dialog, store, _ = virtual_history(quick=quick, embedded=embedded)
    assert len(dialog._row_pool) == FIRST_ROWS == 12
    finish(dialog)
    assert dialog._loaded_count == 24
    assert len(dialog._row_pool) == 24
    position(dialog, 8)
    assert dialog._loaded_count == 24
    position(dialog, 12)
    assert dialog._loaded_count == 36
    assert [r.entry for r in rows_of(dialog)] == store.entries[:36]
    finish(dialog)
    assert dialog._loaded_count == 36


def test_scroll_to_every_page_and_back_reuses_at_most_thirty_six_cards(virtual_history):
    dialog, store, _ = virtual_history()
    finish(dialog)
    for index in range(12, 193, 12):
        position(dialog, index)
        assert dialog._loaded_count == min(200, index + 24)
        assert len(dialog._row_pool) <= MAX_LIVE_ROWS == 36
        assert all(row.entry is store.entries[row.entry_index] for row in rows_of(dialog))
    dialog._focus_index(199)
    finish(dialog)
    identities = {str(row) for row in dialog._row_pool}
    assert dialog._row_map[199].entry is store.entries[-1]
    last_row = dialog._row_map[199]
    assert last_row.winfo_rooty() >= dialog.canvas.winfo_rooty()
    assert (last_row.winfo_rooty() + last_row.winfo_height()
            <= dialog.canvas.winfo_rooty() + dialog.canvas.winfo_height())
    dialog._focus_index(0)
    finish(dialog)
    assert {str(row) for row in dialog._row_pool} == identities
    assert dialog._row_map[0].entry is store.entries[0]


def test_search_includes_unrendered_record_and_cancels_the_old_prefetch(virtual_history):
    dialog, store, _ = virtual_history()
    old_job = dialog._row_job
    dialog.search_entry.set('number 199')
    finish(dialog)
    assert dialog.visible_entries == [store.entries[-1]]
    assert rows_of(dialog)[0].entry is store.entries[-1]
    assert dialog._row_job is None
    assert old_job not in dialog.root.tk.call('after', 'info')
    dialog._clear_search()
    finish(dialog)
    assert dialog._loaded_count == 24
    assert rows_of(dialog)[0].entry is store.entries[0]


@pytest.mark.parametrize('action', ['copy', 'open', 'delete'])
def test_recycled_actions_always_use_the_new_bound_record(virtual_history, monkeypatch, action):
    dialog, store, loaded = virtual_history()
    finish(dialog)
    original_cards = {str(row) for row in dialog._row_pool}
    dialog._focus_index(199)
    finish(dialog)
    row = next(row for row in rows_of(dialog) if str(row) in original_cards)
    record = row.entry
    assert row.entry_index >= 164
    if action == 'copy':
        copied = []
        monkeypatch.setattr(dialog.window, 'clipboard_clear', lambda: None)
        monkeypatch.setattr(dialog.window, 'clipboard_append', copied.append)
        row.actions[0].invoke()
        assert copied == [record['translated']]
    elif action == 'open':
        row.actions[1].invoke()
        assert loaded == [record]
    else:
        row.actions[2].invoke()
        finish(dialog)
        assert record not in store.entries
        assert len(store.entries) == 199


def test_embedded_recycled_click_opens_the_correct_detail(virtual_history):
    dialog, store, _ = virtual_history(embedded=True)
    finish(dialog)
    dialog._focus_index(199)
    finish(dialog)
    dialog._row_map[199].event_generate('<Button-1>')
    finish(dialog)
    assert [box.get_plain() for box in dialog.detail_boxes] == [store.entries[-1]['original'], store.entries[-1]['translated']]
    dialog.show_list()
    finish(dialog)
    assert dialog._row_map[199].entry is store.entries[-1]


def test_quick_picker_keyboard_crosses_page_and_pool_boundaries(virtual_history):
    dialog, store, loaded = virtual_history(quick=True, embedded=True)
    finish(dialog)
    dialog.window.winfo_toplevel().focus_force()
    dialog._focus_index(23)
    finish(dialog)
    for index in range(24, 42):
        dialog._quick_move(1)
        finish(dialog)
        focused = dialog.window.focus_get()
        assert focused.entry is store.entries[index]
    focused.event_generate('<End>')
    finish(dialog)
    focused = dialog.window.focus_get()
    assert focused.entry is store.entries[-1]
    focused.event_generate('<Return>')
    assert loaded == [store.entries[-1]]


def test_closing_cancels_both_prefetch_and_viewport_without_callback_errors(virtual_history, monkeypatch):
    dialog, _, _ = virtual_history()
    errors = []
    monkeypatch.setattr(dialog.root, 'report_callback_exception', lambda *args: errors.append(args))
    jobs = set(dialog.window._ct_window_callback_jobs)
    dialog.window.destroy()
    dialog.root.update()
    assert not (jobs & set(dialog.root.tk.call('after', 'info')))
    assert not errors


def test_nonempty_search_reuses_cards_and_empty_search_releases_them(virtual_history):
    dialog, store, _ = virtual_history()
    finish(dialog)
    cards = {str(row) for row in dialog._row_pool}
    dialog.search_entry.set('number 199')
    finish(dialog)
    assert str(rows_of(dialog)[0]) in cards
    assert len(dialog._row_pool) <= 36
    dialog.search_entry.set('no matching fixture')
    finish(dialog)
    assert dialog._row_pool == []
    assert dialog.empty_state is not None


def test_recycled_preview_switches_between_ruby_and_plain_without_stale_clicks(virtual_history, monkeypatch):
    from src.core.furigana import RubySegment
    from src.ui import history_dialog as module
    from src.ui.ruby_text import RubyLine
    records = make_entries(200)
    for index, record in enumerate(records):
        record['original'] = f'{index} 日本語' if index % 5 == 0 else f'English source {index}'
        record['source_verdict'] = 'Japanese' if index % 5 == 0 else 'English'
    monkeypatch.setattr(module.furigana, 'should_annotate', lambda text, hint: '日本語' in text)
    monkeypatch.setattr(module.furigana, 'annotate', lambda text, hint: (RubySegment(text, 'よみ'),))
    dialog, store, _ = virtual_history(embedded=True, entries=records, ruby=True)
    finish(dialog)
    assert isinstance(dialog._row_map[0].lines[0][0], RubyLine)
    first_card = dialog._row_map[0]
    dialog._focus_index(199)
    finish(dialog)
    assert dialog._row_map[164] is first_card
    widget = first_card.lines[0][0]
    assert not isinstance(widget, RubyLine)
    assert str(widget.cget('text')) == records[164]['original']
    widget.event_generate('<Button-1>')
    finish(dialog)
    assert dialog.detail_boxes[0].get_plain() == records[164]['original']


@pytest.mark.parametrize('locale,text_scale', [('en', 1.0), ('en', 1.5), ('ja', 1.0), ('ja', 1.5)])
def test_oldest_stays_in_view_after_search_reset_resize_and_theme_changes(virtual_history, env, locale, text_scale):
    previous = env.manager.ui_language, env.manager.text_scale, env.manager.mode
    dialog = None
    try:
        env.manager.set_ui_language(locale)
        env.manager.set_text_scale(text_scale)
        dialog, store, _ = virtual_history(embedded=True)
        finish(dialog)
        dialog._focus_index(199)
        finish(dialog)
        dialog.search_entry.set('number 199')
        finish(dialog)
        dialog._clear_search()
        finish(dialog)
        for dimensions in ('800x620', '1400x900'):
            dialog.window.winfo_toplevel().geometry(dimensions)
            for theme in ('pink', 'blue', 'yellow', 'green', 'light', 'dark', 'system'):
                env.manager.set_mode(theme)
                finish(dialog)
                dialog._focus_index(199)
                finish(dialog)
                row = dialog._row_map[199]
                assert row.entry is store.entries[-1]
                assert row.winfo_rooty() >= dialog.canvas.winfo_rooty()
                assert row.winfo_rooty() + row.winfo_height() <= dialog.canvas.winfo_rooty() + dialog.canvas.winfo_height()
                assert len(dialog._row_pool) <= 36
    finally:
        if dialog is not None and dialog.window.winfo_exists():
            dialog.window.destroy()
        env.manager.set_ui_language(previous[0])
        env.manager.set_text_scale(previous[1])
        env.manager.set_mode(previous[2])
