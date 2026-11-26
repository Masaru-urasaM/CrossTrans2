"""
Tests for tools/ui_gallery.py (TODO.md U0).

The pure helpers are tested here directly. One integration test runs the tool for real, in
its own process (it makes its own private desktop and its own Tk interpreter - a test must
not make a second one in-process), on two small surfaces: it proves the run is quiet and that
the gallery sees the defects TODO.md section 5 recorded - a gallery that cannot see a known
bug is not a test.
"""
import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

import quiet_session

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import ui_gallery as gallery  # noqa: E402

_APPDATA_BEFORE_IMPORT = os.environ.get('APPDATA')


@pytest.mark.parametrize('overflow', [None, 'width', 'height'])
@pytest.mark.parametrize('hidden', [False, True])
def test_viewport_bounds_check_the_host_without_flagging_scroll_content(tk_root, overflow, hidden):
    import tkinter as tk
    window = tk.Toplevel(tk_root)
    window.geometry('400x260')
    host = tk.Frame(window, width=220, height=120)
    host.pack(anchor='nw')
    host.pack_propagate(False)
    host.canvas = tk.Canvas(host, highlightthickness=0, borderwidth=0)
    host.canvas.place(x=0, y=0, width=260 if overflow == 'width' else 220,
                      height=160 if overflow == 'height' else 120)
    content = tk.Frame(host.canvas, width=220, height=900)
    host.canvas.create_window((0, 0), window=content, anchor='nw')
    window.update()
    if hidden:
        host.pack_forget()
        window.update()
    findings = gallery.viewport_overflow(window)
    assert [row.kind for row in findings] == (['viewport_overflow'] if overflow and not hidden else [])
    window.destroy()


@pytest.mark.parametrize('pending', [False, True])
@pytest.mark.parametrize('overflow', [False, True])
def test_settings_settle_checks_visible_search_results_and_keeps_pending_layout_failures(tk_root, monkeypatch, pending, overflow):
    import itertools
    import tkinter as tk
    window = tk.Toplevel(tk_root)
    window.geometry('420x260')
    hidden = tk.Frame(window)
    hidden_label = tk.Label(hidden, text='Intentionally hidden General page')
    hidden_label.pack()
    results = tk.Frame(window)
    results.pack(fill='both', expand=True)
    results.canvas = tk.Canvas(results, width=300, height=180)
    if overflow:
        results.canvas.place(x=0, y=0, width=600, height=180)
    else:
        results.canvas.pack(fill='both', expand=True)
    results.content = tk.Frame(results.canvas)
    results.canvas.create_window((0, 0), window=results.content, anchor='nw')
    tk.Label(results.content, text='Visible search result').pack()
    results._layout_job = 'pending-layout' if pending else None
    results._bar_job = None
    row = SimpleNamespace(visible=True, title_label=hidden_label, description_label=None,
                          control=hidden)
    page = SimpleNamespace(rows={'general': row})
    settings = SimpleNamespace(current='general', pages={'general': page}, _areas={}, _results=results)
    context = gallery.Context(SimpleNamespace(settings_window=settings), tk_root, 100)
    tk_root.update()
    assert not hidden_label.winfo_ismapped() and results.winfo_ismapped()
    context.pump = lambda seconds: tk_root.update()
    ticks = itertools.count(step=.1)
    monkeypatch.setattr(gallery.time, 'perf_counter', lambda: next(ticks))
    try:
        context.settle_settings()
        if pending or overflow:
            assert len(context.extra) == 1
            assert context.extra[0]['state'] == 'search-results'
            assert context.extra[0]['kind'] == 'unsettled_layout'
        else:
            assert context.extra == []
    finally:
        window.destroy()


class TestSafety:
    def test_it_refuses_to_run_without_a_private_desktop(self):
        with pytest.raises(SystemExit, match="refusing to draw on the screen"):
            gallery.require_private_desktop(None, "CreateDesktopW failed")

    def test_on_the_private_desktop_it_carries_on(self):
        assert gallery.require_private_desktop('crosstrans-tests-1', None) is None

    def test_importing_it_changes_nothing(self):
        assert os.environ.get('APPDATA') == _APPDATA_BEFORE_IMPORT

    def test_settings_go_to_a_scratch_folder(self, monkeypatch):
        monkeypatch.setenv('APPDATA', r'C:\Users\someone\AppData\Roaming')
        scratch = gallery.isolate_settings()
        try:
            assert os.environ['APPDATA'] == scratch
            assert 'ui-gallery-' in os.path.basename(scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


class TestScales:
    def test_a_list_of_percent_values(self):
        assert gallery.parse_scales('100,150') == [100, 150]
        assert gallery.parse_scales(' 200 ') == [200]

    @pytest.mark.parametrize('text', ['', '110', '100,abc', '0'])
    def test_anything_else_is_refused(self, text):
        with pytest.raises((SystemExit, ValueError)):
            gallery.parse_scales(text)

    def test_tk_scaling_follows_the_windows_scale(self):
        assert gallery.scaling_for(4 / 3, 100) == pytest.approx(4 / 3)
        assert gallery.scaling_for(4 / 3, 150) == pytest.approx(2.0)
        assert gallery.scaling_for(4 / 3, 200) == pytest.approx(8 / 3)


class TestColour:
    def test_black_on_white_is_21_to_1(self):
        assert gallery.contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)

    def test_the_order_does_not_matter(self):
        a, b = (0, 188, 140), (255, 255, 255)
        assert gallery.contrast_ratio(a, b) == gallery.contrast_ratio(b, a)

    def test_todo_md_measured_the_translate_button_at_2_45(self):
        # white on #00bc8c - Appendix B of TODO.md
        ratio = gallery.contrast_ratio(gallery.hex_to_rgb('#ffffff'), gallery.hex_to_rgb('#00bc8c'))
        assert ratio == pytest.approx(2.45, abs=0.01)

    def test_short_hex_is_expanded(self):
        assert gallery.hex_to_rgb('#abc') == (170, 187, 204)


class TestGeometry:
    def test_negative_sizes_are_pixels_and_positive_ones_points(self):
        assert gallery.font_px(-16, 1.333) == 16.0
        assert gallery.font_px(9, 4 / 3) == pytest.approx(12.0)

    def test_inside_and_outside(self):
        assert gallery.rect_inside((10, 10, 50, 50), (0, 0, 100, 100))
        assert not gallery.rect_inside((10, 10, 150, 50), (0, 0, 100, 100))

    def test_a_window_that_is_exactly_the_work_area_fits(self):
        assert gallery.fits_work_area(1366, 728, 1366, 728)
        assert not gallery.fits_work_area(1367, 728, 1366, 728)

    def test_the_translator_does_not_fit_a_small_laptop(self):
        _, width, height = gallery.SMALL_LAPTOP
        assert not gallery.fits_work_area(1416, 889, width, height)


class TestReport:
    def rows(self):
        return [
            {'surface': 'dialog.api_error', 'state': 'dialog', 'scale': 100,
             'kind': 'clipped_control', 'widget': 'TButton', 'detail': 'x'},
            {'surface': 'translator', 'state': 'empty', 'scale': 150, 'kind': 'tiny_text_box',
             'widget': 'Text', 'detail': 'x'},
            {'surface': 'translator', 'state': 'empty', 'scale': 100, 'kind': 'tiny_text_box',
             'widget': 'Text', 'detail': 'x'},
            {'surface': 'translator', 'state': 'empty', 'scale': 100, 'kind': 'low_contrast',
             'widget': "TButton 'Translate -> Vietnamese'", 'detail': '2.45:1'},
        ]

    def test_summary_counts_by_kind_most_first(self):
        assert list(gallery.summarise(self.rows()).items())[0] == ('tiny_text_box', 2)

    def test_known_defects_are_matched_to_where_they_appear(self):
        found = gallery.match_known_defects(self.rows())
        assert found['V11-api-dialog-clipped'] == ['dialog.api_error.dialog@100']
        assert found['V11-translation-box-squeezed-at-150'] == ['translator.empty@150']
        assert found['V29-translate-button-contrast'] == ['translator.empty@100']
        assert found['V3-toasts-alike'] == []

    def test_every_known_defect_is_unseen_on_an_empty_run(self):
        assert all(v == [] for v in gallery.match_known_defects([]).values())

    def test_a_tiny_text_box_at_100_percent_is_not_the_150_percent_defect(self):
        rows = [r for r in self.rows() if r['scale'] == 100]
        assert gallery.match_known_defects(rows)['V11-translation-box-squeezed-at-150'] == []


class TestSurfaces:
    def test_every_surface_has_a_builder_and_a_unique_name(self):
        assert len(gallery.SURFACES) == len(set(gallery.SURFACES))
        assert all(callable(b) for b in gallery.SURFACES.values())

    def test_the_issue_list_surfaces_are_all_there(self):
        # TODO.md U0: Translator, Settings, History, Dictionary, dialogs, toasts, update
        # notice, Expanded view, screenshot overlay (and the popup)
        for name in ('translator', 'settings', 'history', 'dictionary', 'dialog.api_error',
                     'toasts', 'update_notice', 'expanded', 'screenshot_overlay', 'quick_popup'):
            assert name in gallery.SURFACES

    def test_dialogs_stands_for_every_dialog(self):
        chosen = gallery.select_surfaces('dialogs')
        assert chosen and all(name.startswith('dialog.') for name in chosen)

    def test_no_surfaces_means_all(self):
        assert gallery.select_surfaces(None) == list(gallery.SURFACES)

    def test_a_typo_is_refused(self):
        with pytest.raises(SystemExit, match="no surface"):
            gallery.select_surfaces('translater')

    def test_a_name_is_a_safe_file_name(self):
        assert gallery.slug('API Key') == 'api_key'
        assert gallery.slug('  Shortcuts & help ') == 'shortcuts_help'


class TestWindowIconDetector:
    """has_window_icon() on a stand-in for user32: the three ways a window can have an icon, and
    the one way it has only Tk's feather."""

    class Fake:
        def __init__(self, small=0, big=0, class_icon=0):
            self.user32 = self
            self.small, self.big, self.class_icon = small, big, class_icon
            self.GetClassLongPtrW = lambda hwnd, index: self.class_icon

        def SendMessageW(self, hwnd, message, which, lparam):
            assert message == 0x007F                                # WM_GETICON
            return self.small if which == 0 else self.big

    def check(self, monkeypatch, **icons):
        fake = self.Fake(**icons)
        monkeypatch.setattr(gallery.ctypes, 'windll', fake)
        monkeypatch.setattr(gallery, 'tk_feather_icon', lambda: 1234)
        return gallery.has_window_icon(1)

    def test_an_icon_set_on_the_window_counts(self, monkeypatch):
        assert self.check(monkeypatch, small=77) is True
        assert self.check(monkeypatch, big=77) is True

    def test_a_class_wide_icon_that_is_not_the_feather_counts(self, monkeypatch):
        assert self.check(monkeypatch, class_icon=999) is True

    def test_only_the_feather_does_not(self, monkeypatch):
        assert self.check(monkeypatch, class_icon=1234) is False

    def test_no_icon_at_all_does_not(self, monkeypatch):
        assert self.check(monkeypatch, class_icon=0) is False

    def test_the_feather_is_found_in_tks_own_dll(self):
        feather = gallery.tk_feather_icon()
        assert feather is None or isinstance(feather, int)


class TestFindingsOnRealWidgets:
    """The detectors on widgets whose colours the test chooses (in-process, on the shared root)."""

    @pytest.fixture
    def window(self, tk_root):
        import tkinter as tk
        from src.ui.theme import compat
        compat.protect_explicit_colours()      # as the application does: colours given are kept
        win = tk.Toplevel(tk_root)
        win.geometry('+0+0')
        yield win
        win.destroy()
        compat.unprotect_explicit_colours()

    def style(self):
        import ttkbootstrap
        return ttkbootstrap.Style()

    def test_pale_grey_text_on_grey_is_low_contrast(self, window):
        import tkinter as tk
        tk.Label(window, text='Faint', fg='#777777', bg='#808080').pack()
        window.update()
        found = gallery.low_contrast(window, self.style())
        assert [f.kind for f in found] == ['low_contrast'] and 'Faint' in found[0].widget
        assert float(found[0].detail.split(':')[0]) < 4.5

    def test_black_on_white_is_fine(self, window):
        import tkinter as tk
        tk.Label(window, text='Clear', fg='#000000', bg='#ffffff').pack()
        window.update()
        assert gallery.low_contrast(window, self.style()) == []

    def test_a_disabled_or_empty_label_is_exempt(self, window):
        import tkinter as tk
        tk.Button(window, text='Off', fg='#777777', bg='#808080', state='disabled').pack()
        tk.Label(window, text='', fg='#777777', bg='#808080').pack()
        window.update()
        assert gallery.low_contrast(window, self.style()) == []

    def test_a_two_row_list_of_many_items_is_tiny(self, window):
        import tkinter as tk
        box = tk.Listbox(window, height=2)
        for n in range(20):
            box.insert('end', f'item {n}')
        box.pack()
        window.update()
        found = gallery.tiny_lists(window)
        assert [f.kind for f in found] == ['tiny_list'] and '2 of 20' in found[0].detail

    def test_a_short_text_box_is_flagged_unless_it_is_ignored(self, window):
        import tkinter as tk
        box = tk.Text(window, height=1)
        box.pack()
        window.update()
        assert [f.kind for f in gallery.tiny_text_boxes(window, window)] == ['tiny_text_box']
        assert gallery.tiny_text_boxes(window, window, ignore={box}) == []

    def test_a_popup_is_not_checked_for_squeezed_boxes(self, window, tk_root):
        """A popup measures its boxes to their content (Decision 34): one line is right for one
        line. Only a window sized by its own code can squeeze a box (V11)."""
        import tkinter as tk
        popup = tk.Toplevel(tk_root)
        popup.overrideredirect(True)
        popup.geometry('+0+0')
        tk.Text(popup, height=1).pack()
        popup.update()
        try:
            kinds = [f['kind'] for f in gallery.window_metrics(popup, tk_root, 100,
                                                               self.style())['findings']]
        finally:
            popup.destroy()
        assert 'tiny_text_box' not in kinds
        tk.Text(window, height=1).pack()
        window.update()
        framed = [f['kind'] for f in gallery.window_metrics(window, tk_root, 100,
                                                            self.style())['findings']]
        assert 'tiny_text_box' in framed

    def test_a_language_pairs_arrow_is_text_but_a_tick_is_a_symbol(self, window):
        import tkinter as tk
        tk.Label(window, text='Japanese ' + chr(0x2192) + ' English').pack()
        window.update()
        assert gallery.symbols_in_text(window) == []
        tk.Label(window, text=chr(0x2713) + ' Done').pack()
        window.update()
        assert len(gallery.symbols_in_text(window)) == 1

    def test_symbols_in_a_label_are_found(self, window):
        import tkinter as tk
        tk.Label(window, text='✓ Done').pack()
        tk.Label(window, text='Plain').pack()
        window.update()
        assert [f.kind for f in gallery.symbols_in_text(window)] == ['symbol_in_text']

    def test_a_scrolling_canvas_with_no_scroll_bar_is_flagged(self, window):
        import tkinter as tk
        frame = tk.Frame(window)
        frame.pack()
        canvas = tk.Canvas(frame, height=50, width=100, scrollregion=(0, 0, 100, 400))
        canvas.pack()
        window.update()
        assert [f.kind for f in gallery.scroll_without_scrollbar(window)] == ['scroll_without_scrollbar']

    def test_a_scroll_bar_beside_it_clears_the_flag(self, window):
        import tkinter as tk
        from tkinter import ttk
        frame = tk.Frame(window)
        frame.pack()
        canvas = tk.Canvas(frame, height=50, width=100, scrollregion=(0, 0, 100, 400))
        canvas.pack(side='left')
        ttk.Scrollbar(frame, command=canvas.yview).pack(side='right', fill='y')
        window.update()
        assert gallery.scroll_without_scrollbar(window) == []


class TestNoVietnamese:
    def test_the_tool_holds_no_vietnamese(self):
        import re
        letters = re.compile('[\u1ea0-\u1ef9\u0110\u0111\u01a0\u01a1\u01af\u01b0]')
        with open(os.path.join(REPO, 'tools', 'ui_gallery.py'), encoding='utf-8') as fh:
            assert not letters.search(fh.read())


@pytest.mark.skipif(not quiet_session.DESKTOP_NAME, reason=quiet_session.skip_reason())
class TestRealRun:
    """The tool, as a user runs it, on two small surfaces at 100 %."""

    LABEL = 'test_ui_gallery_run'

    @pytest.fixture(scope='class')
    def result(self):
        out = os.path.join(gallery.OUT_ROOT, self.LABEL)
        shutil.rmtree(out, ignore_errors=True)
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO, 'tools', 'ui_gallery.py'), '--label', self.LABEL,
             '--scales', '100', '--surfaces', 'dialog.api_error,toasts', '--no-shots'],
            capture_output=True, text=True, env=env, timeout=240, cwd=REPO)
        report = os.path.join(gallery.OUT_ROOT, f'{self.LABEL}.json')
        data = None
        if os.path.exists(report):
            with open(report, encoding='utf-8') as fh:
                data = json.load(fh)
        yield proc, data
        shutil.rmtree(out, ignore_errors=True)
        if os.path.exists(report):
            os.remove(report)

    def test_the_run_succeeds_and_is_quiet(self, result):
        proc, data = result
        assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
        assert data['blocked'] == [] and data['windows_on_user_desktop'] == 0
        assert data['errors'] == []

    def test_it_captures_every_state_asked_for(self, result):
        _, data = result
        assert data['captures'] == 1 + 5          # the API error dialog + five toasts

    def test_repaired_api_dialog_and_toasts_do_not_regress(self, result):
        _, data = result
        assert not data['known_defects']['V11-api-dialog-clipped']
        assert 'V11-api-dialog-clipped' in data['engine_fixed']
        # V3 (toasts that look alike) was ttkbootstrap 1.x overwriting their colours; on 2.x it is
        # gone, and seeing it again would be a regression (Decision 45)
        assert data['ttkbootstrap_major'] >= 2
        assert 'V3-toasts-alike' in data['engine_fixed']
        assert 'V29-translate-button-contrast' in data['engine_fixed']
        assert 'V9-feather-icon' in data['engine_fixed']
        assert data['known_defects']['V9-feather-icon'] == []
        assert data['known_defects']['V3-toasts-alike'] == []

    def test_it_reads_the_icon_and_title_bar_of_the_dialog(self, result):
        _, data = result
        with open(os.path.join(gallery.OUT_ROOT, self.LABEL, 's100.json'), encoding='utf-8') as fh:
            scale = json.load(fh)
        dialog = next(c for c in scale['captures'] if c['surface'] == 'dialog.api_error')
        kinds = {f['kind'] for f in dialog['findings']}
        assert not ({'clipped_control', 'light_title_bar'} & kinds)
        assert 'no_window_icon' not in kinds                   # since U5 the app's own icon is there
        assert dialog['framed'] and dialog['has_icon'] is True        # since U5: the app's own icon
        assert dialog['outer'][2] > 0 and dialog['build_ms'] >= 0

    def test_a_surface_it_was_not_asked_for_is_not_run(self, result):
        _, data = result
        assert data['surfaces'] == ['dialog.api_error', 'toasts']


@pytest.mark.skipif(not quiet_session.DESKTOP_NAME, reason=quiet_session.skip_reason())
def test_full_translator_gallery_keeps_its_viewport_inside_the_host_after_stacking():
    label = 'test_ui_gallery_stacked_viewport'
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    proc = subprocess.run(
        [sys.executable, os.path.join(REPO, 'tools', 'ui_gallery.py'), '--label', label,
         '--scales', '100', '--surfaces', 'translator', '--no-shots', '--strict'],
        capture_output=True, text=True, env=env, timeout=120, cwd=REPO)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    with open(os.path.join(gallery.OUT_ROOT, label, 's100.json'), encoding='utf-8') as file:
        report = json.load(file)
    assert not report['errors'] and not report['blocked']
    assert report['windows_on_user_desktop'] == 0
    assert not report['findings']
    stacked = next(row for row in report['captures'] if row['state'] == 'stacked')
    assert stacked['editor_geometry'] and stacked['body_viewport']['height'] > 0


@pytest.mark.skipif(not quiet_session.DESKTOP_NAME, reason=quiet_session.skip_reason())
def test_japanese_large_text_popup_gallery_is_measured_and_unclipped():
    label = 'test_ui_gallery_dense_popup'
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    proc = subprocess.run(
        [sys.executable, os.path.join(REPO, 'tools', 'ui_gallery.py'), '--label', label,
         '--scales', '200', '--surfaces', 'quick_popup', '--no-shots', '--ui-language', 'ja',
         '--text-scale', '1.5', '--work-area', '1366x728'],
        capture_output=True, text=True, env=env, timeout=240, cwd=REPO)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    with open(os.path.join(gallery.OUT_ROOT, label + '.json'), encoding='utf-8') as file:
        report = json.load(file)
    assert report['captures'] == 10
    assert report['errors'] == [] and report['blocked'] == []
    assert report['windows_on_user_desktop'] == 0
    assert report['findings_by_kind'] == {}, report['findings_by_kind']


def test_product_gallery_excludes_the_developer_demo_and_roundtrip_probe():
    assert set(gallery.select_surfaces('product')) == set(gallery.SURFACES) - {'components', 'theme_switching'}
    assert gallery.parse_scales('100,125,150,175,200') == [100,125,150,175,200]
    assert gallery.parse_args(['--label', 'strict', '--strict']).strict is True


def test_gallery_pump_is_bounded_even_when_events_keep_arriving():
    import time
    from types import SimpleNamespace
    calls = []
    class Tcl:
        def dooneevent(self, flags):
            calls.append(flags)
            return 1
    context = object.__new__(gallery.Context)
    context.root = SimpleNamespace(tk=Tcl(), update=lambda: pytest.fail('update may never return'))
    started = time.perf_counter()
    context.pump(0.02)
    assert calls and time.perf_counter() - started < 0.5


def test_parent_records_a_timed_out_child_as_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(gallery, 'OUT_ROOT', str(tmp_path))
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get('timeout', 180))
    monkeypatch.setattr(gallery.subprocess, 'run', timeout)
    args = gallery.parse_args(['--label', 'timeout', '--scales', '100', '--surfaces', 'dialogs', '--strict'])
    assert gallery.parent_main(args) == 1
    report = json.loads((tmp_path / 'timeout.json').read_text(encoding='utf-8'))
    assert report['errors'] and 'timed out' in report['errors'][0]['error']


def test_vertical_scroll_canvas_does_not_hide_horizontal_clipping(tk_root):
    import tkinter as tk
    from src.ui.theme.compat import NO_AUTOSTYLE
    window = tk.Toplevel(tk_root)
    window.geometry('300x160')
    canvas = tk.Canvas(window, width=200, height=100, **NO_AUTOSTYLE)
    canvas.pack(anchor='w')
    content = tk.Frame(canvas, width=400, height=400, **NO_AUTOSTYLE)
    canvas.create_window((0, 0), window=content, anchor='nw')
    bad = tk.Button(content, text='Visible clipped control', **NO_AUTOSTYLE)
    bad.place(x=170, y=10, width=150, height=30)
    below = tk.Button(content, text='Below the vertical fold', **NO_AUTOSTYLE)
    below.place(x=10, y=300, width=150, height=30)
    tk_root.update()
    findings = gallery.clipped_controls(window)
    assert any('Visible clipped control' in finding.widget and 'horizontal' in finding.detail for finding in findings)
    assert not any('Below the vertical fold' in finding.widget for finding in findings)
    window.destroy()


def test_oversized_scroll_viewport_cannot_hide_clipping_outside_the_window(tk_root):
    import tkinter as tk
    from src.ui.theme.compat import NO_AUTOSTYLE
    window = tk.Toplevel(tk_root)
    window.geometry('300x160')
    canvas = tk.Canvas(window, **NO_AUTOSTYLE)
    canvas.place(x=0, y=0, width=600, height=150)
    content = tk.Frame(canvas, width=600, height=400, **NO_AUTOSTYLE)
    canvas.create_window((0, 0), window=content, anchor='nw')
    hidden = tk.Button(content, text='Outside the client area', **NO_AUTOSTYLE)
    hidden.place(x=400, y=10, width=150, height=30)
    tk_root.update()
    assert any('Outside the client area' in finding.widget and 'horizontal' in finding.detail
               for finding in gallery.clipped_controls(window))
    window.destroy()


def test_gallery_uses_the_foreground_of_the_selected_widget(tk_root):
    import tkinter as tk
    from tkinter import ttk
    window = tk.Toplevel(tk_root)
    style = ttk.Style(window)
    style.configure('GalleryState.TRadiobutton', foreground='white', background='black')
    style.map('GalleryState.TRadiobutton', foreground=[('selected', 'black')], background=[('selected', 'yellow')])
    button = ttk.Radiobutton(window, text='Selected state', style='GalleryState.TRadiobutton')
    button.pack()
    button.state(['selected'])
    tk_root.update()
    assert gallery.widget_colours(button, style)[0] == (0, 0, 0)
    button.state(['!selected'])
    assert gallery.widget_colours(button, style)[0] == (255, 255, 255)
    window.destroy()


def test_repeated_native_captures_release_gdi_bitmaps(tk_root, tmp_path):
    import ctypes
    import tkinter as tk
    from ctypes import wintypes as w
    window = tk.Toplevel(tk_root)
    window.geometry('300x180')
    tk_root.update()
    destination = str(tmp_path / 'capture.png')
    handle = int(window.wm_frame(), 16)
    assert gallery.capture(handle, destination)
    get_resources = ctypes.windll.user32.GetGuiResources
    get_resources.argtypes, get_resources.restype = [w.HANDLE, w.DWORD], w.DWORD
    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = w.HANDLE
    before = get_resources(get_process(), 0)
    for _ in range(20):
        assert gallery.capture(handle, destination)
    after = get_resources(get_process(), 0)
    assert after <= before + 2, (before, after)
    from PIL import Image
    with Image.open(destination) as screenshot:
        assert screenshot.width >= 300 and screenshot.height >= 180
    window.destroy()


@pytest.mark.skipif(not quiet_session.DESKTOP_NAME, reason=quiet_session.skip_reason())
@pytest.mark.parametrize('kind', ['python', 'tcl'])
def test_callback_exception_is_a_failed_real_gallery(tmp_path, kind):
    script = r"""
import sys
import tkinter as tk
from tools import ui_gallery as gallery
gallery.OUT_ROOT = sys.argv[1]
def fault(ctx):
    window = tk.Toplevel(ctx.root)
    window.geometry('320x180')
    def fail():
        raise RuntimeError('fixture callback failure')
    if sys.argv[2] == 'tcl':
        ctx.root.tk.call('after', 0, 'error {fixture Tcl background failure}')
    else:
        ctx.root.after(0, fail)
    yield 'shown', window
gallery.SURFACES['callback_fault'] = fault
args = gallery.parse_args(['--label', 'callback-failure', '--surfaces', 'callback_fault', '--child', '100', '--no-shots'])
raise SystemExit(gallery.child_main(args))
"""
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    proc = subprocess.run([sys.executable, '-c', script, str(tmp_path), kind], cwd=REPO,
                          capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 1, proc.stdout[-2000:] + proc.stderr[-2000:]
    report = json.loads((tmp_path / 'callback-failure' / 's100.json').read_text(encoding='utf-8'))
    expected = 'tk_callback' if kind == 'python' else 'tcl_background'
    assert any(row['surface'] == expected and 'fixture' in row['error']
               for row in report['errors']), report['errors']


def test_floating_picker_is_not_clipped_by_its_owners_scroll_canvas(tk_root):
    import tkinter as tk
    window = tk.Toplevel(tk_root)
    window.geometry('600x300+0+0')
    canvas = tk.Canvas(window, width=120, height=120)
    canvas.pack()
    content = tk.Frame(canvas, width=180, height=120)
    canvas.create_window((0, 0), window=content, anchor='nw')
    popup = tk.Toplevel(content)
    popup.geometry('340x140+240+80')
    field = tk.Entry(popup, width=30)
    field.pack(fill='x')
    tk_root.update()
    assert not any('horizontal content' in finding.detail for finding in gallery.clipped_controls(popup))
    assert not any('horizontal content' in finding.detail for finding in gallery.clipped_controls(window))
    window.destroy()


def test_floating_picker_still_reports_its_own_squeezed_controls(tk_root):
    import tkinter as tk
    window = tk.Toplevel(tk_root)
    window.geometry('300x200+0+0')
    canvas = tk.Canvas(window, width=120, height=120)
    canvas.pack()
    content = tk.Frame(canvas, width=180, height=120)
    canvas.create_window((0, 0), window=content, anchor='nw')
    popup = tk.Toplevel(content)
    popup.geometry('140x100+240+80')
    field = tk.Entry(popup, width=60)
    field.pack(fill='x')
    tk_root.update()
    assert any(finding.kind == 'clipped_control' for finding in gallery.clipped_controls(popup))
    assert not gallery.in_canvas(field) and not gallery.canvas_clips(field)
    window.destroy()

def test_content_sized_fields_are_allowed_only_when_their_whole_content_fits(tk_root):
    import tkinter as tk
    window = tk.Toplevel(tk_root)
    box = tk.Text(window, height=1, pady=0, bd=0, highlightthickness=0)
    box.fits_to_content = True
    box.insert('1.0', 'One complete dictionary field.')
    box.pack()
    tk_root.update()
    assert gallery.tiny_text_boxes(window, tk_root) == []
    box.insert('end', '\nA second line that does not fit.')
    tk_root.update()
    assert any(finding.kind == 'tiny_text_box' for finding in gallery.tiny_text_boxes(window, tk_root))
    window.destroy()
