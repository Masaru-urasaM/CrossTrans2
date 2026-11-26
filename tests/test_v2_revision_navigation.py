"""The bottom History/link pair stays adjacent when actions wrap."""
import pytest
from tests.test_source_choice import main_window, settle


@pytest.mark.parametrize('width', [716, 1000, 1216])
def test_history_and_link_are_adjacent_on_the_same_row(main_window, width):
    app = main_window('A draft to retain.')
    app.popup.geometry(f'{width}x800')
    app.root.update()
    history = app._view.history_btn
    linked = app._main_linked.button
    assert history.winfo_rooty() == linked.winfo_rooty()
    assert 0 <= linked.winfo_rootx() - history.winfo_rootx() - history.winfo_width() <= 20
    assert history.cget('text') == 'History'
    assert not hasattr(app._view, 'settings_btn')


def test_hiding_fix_grammar_keeps_history_and_link_available(main_window):
    app = main_window('A draft to retain.')
    app._view.set_fix_grammar_visible(False)
    app.root.update_idletasks()
    assert app._view.history_btn.winfo_ismapped()
    assert app._main_linked.button.winfo_ismapped()
    assert app.original_text.get('1.0', 'end-1c') == 'A draft to retain.'


def test_inline_readings_survive_the_late_input_debounce(main_window):
    import time
    app = main_window('私は毎日日本語を勉強しています。', 'I study Japanese every day.')
    deadline = time.monotonic() + .5
    while time.monotonic() < deadline:
        app.root.update()
        time.sleep(.005)
    inline = app._inline_reading
    assert inline.has_ruby and inline.labels
    assert all(label.winfo_manager() == 'place' for label in inline.labels)
    assert app.original_text.get('1.0', 'end-1c') == inline.snapshot
