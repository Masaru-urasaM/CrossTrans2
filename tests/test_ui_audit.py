"""
Tests for tools/ui_audit.py (TODO.md U0) and the ratchet built on it.

The ratchet is DoD1: hard-coded colours, fonts and bootstyles outside `src/ui/theme/` may only
go down, ending at zero. `tests/ui_audit_baseline.json` holds today's numbers; a change that
adds a literal fails, and a change that removes one fails too until the baseline is lowered -
so the file always says what the code says.
"""
import json
import os
import sys
import textwrap

import pytest

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(TESTS), 'tools'))
import ui_audit as audit  # noqa: E402

BASELINE_PATH = os.path.join(TESTS, 'ui_audit_baseline.json')


def counts(source):
    return audit.scan_text(textwrap.dedent(source))


class TestScanText:
    def test_hex_colours_are_counted_in_both_lengths_and_lowercased(self):
        found = counts("""
            a = '#FFFFFF'
            b = "#abc"
            c = 'not-a-colour'
            d = '#12345'
        """)
        assert found['colours'] == ['#ffffff', '#abc']

    def test_a_font_tuple_names_a_known_family(self):
        found = counts("""
            f(font=('Segoe UI', 11))
            g(font=('Segoe UI Variable Text', 14, 'bold'))
            h(pady=('padx', 0))
            i(x=('remaining', 3))
        """)
        assert found['fonts'] == ['Segoe UI 11', 'Segoe UI Variable Text 14 bold']

    def test_bootstyles_and_paddings(self):
        found = counts("""
            ttk.Button(root, bootstyle="success-outline", padx=10, pady=(4, 8))
            ttk.Frame(root, padding=12)
        """)
        assert found['bootstyles'] == ['success-outline']
        assert sorted(found['paddings']) == [4, 10, 12]

    def test_symbols_count_only_on_lines_that_look_like_ui_text(self):
        found = counts("""
            ttk.Button(root, text="✓ Done")
            x = '✓'
            ttk.Label(root, text="Plain")
        """)
        assert [line for _, line in found['symbols']] == ['ttk.Button(root, text="✓ Done")']


class TestTkWidgetsWithColour:
    def widgets(self, source):
        return [(w, guarded) for _, w, guarded in counts(source)['tk_coloured']]

    def test_an_explicit_colour_on_a_tk_widget_is_unguarded_by_default(self):
        assert self.widgets("tk.Label(root, bg='#222222')") == [('Label', False)]

    def test_autostyle_false_guards_it(self):
        assert self.widgets("tk.Label(root, bg='#222222', autostyle=False)") == [('Label', True)]

    def test_the_projects_guard_unpacked_into_the_call_guards_it(self):
        assert self.widgets("tk.Frame(root, bg='#222222', **NO_AUTOSTYLE)") == [('Frame', True)]

    def test_a_kwargs_dict_that_carries_the_guard_guards_the_call(self):
        source = """
            options = dict(bg='#222222', **NO_AUTOSTYLE)
            tk.Label(root, fg='#ffffff', **options)
            other = {'autostyle': False}
            tk.Label(root, fg='#ffffff', **other)
            unrelated = dict(bd=0)
            tk.Label(root, fg='#ffffff', **unrelated)
        """
        assert self.widgets(source) == [('Label', True), ('Label', True), ('Label', False)]

    def test_ttk_widgets_and_colourless_tk_widgets_are_not_counted(self):
        assert self.widgets("ttk.Label(root, foreground='#888888')\ntk.Label(root, text='x')") == []

    def test_autostyle_true_is_not_a_guard(self):
        assert self.widgets("tk.Label(root, bg='#222222', autostyle=True)") == [('Label', False)]


class TestLegacyReferences:
    def refs(self, source):
        return counts(source)['legacy_refs']

    def test_each_read_of_a_legacy_constant_is_counted(self):
        source = '''
            from src.ui.theme import legacy as L
            a = L.PANEL
            b = dict(bg=L.WHITE, font=L.UI_10)
        '''
        assert self.refs(source) == 3

    def test_another_alias_and_the_dotted_import_are_followed(self):
        assert self.refs("from src.ui.theme import legacy as old\nx = old.PANEL") == 1
        assert self.refs("import src.ui.theme.legacy as old\nx = old.PANEL") == 1

    def test_other_attributes_called_l_are_not_legacy(self):
        assert self.refs("import popup_layout as L\nx = L.fit(1)") == 0
        assert self.refs("from src.ui.theme import tokens\nx = tokens.LIGHT.bg") == 0


class TestScope:
    def test_only_src_config_and_main_are_scanned(self):
        assert audit.is_scanned('src/ui/toast.py')
        assert audit.is_scanned('config.py') and audit.is_scanned('main.py')
        assert not audit.is_scanned('tests/test_x.py')
        assert not audit.is_scanned('tools/ui_audit.py')
        assert not audit.is_scanned('src/ui/readme.md')

    def test_the_theme_package_is_allowed_to_hold_literals(self):
        assert audit.is_allowed('src/ui/theme/tokens.py')
        assert not audit.is_allowed('src/ui/toast.py')

    def test_every_scanned_file_exists_and_none_is_a_test(self):
        files = audit.source_files()
        assert 'src/app.py' in files and 'config.py' in files
        assert not any(f.startswith(('tests/', 'tools/')) for f in files)


class TestRatchet:
    @pytest.fixture(scope='class')
    def report(self):
        return audit.scan()

    @pytest.fixture(scope='class')
    def baseline(self):
        with open(BASELINE_PATH, encoding='utf-8') as fh:
            return json.load(fh)

    def current(self, report):
        return {'colours_outside_theme': report['colours']['outside_theme'],
                'fonts_outside_theme': report['fonts']['outside_theme'],
                'bootstyles_outside_theme': report['bootstyles']['outside_theme'],
                'unguarded_tk_widgets': report['tk_widgets_with_colour']['unguarded'],
                'symbols_in_ui_strings': report['symbols_in_ui_strings']['count'],
                'legacy_references': report['legacy_references']['outside_theme']}

    def test_no_count_went_up(self, report, baseline):
        now = self.current(report)
        worse = {k: (baseline[k], now[k]) for k in baseline if now[k] > baseline[k]}
        assert not worse, (f"UI literals went up (baseline, now): {worse}. New styling belongs in "
                           f"src/ui/theme/ as a token - TODO.md DoD1.")

    def test_the_baseline_names_what_the_code_says(self, report, baseline):
        now = self.current(report)
        better = {k: (baseline[k], now[k]) for k in baseline if now[k] < baseline[k]}
        assert not better, (f"Fewer literals than the baseline (baseline, now): {better}. Lower "
                            f"tests/ui_audit_baseline.json to {now} so the ratchet keeps hold.")

    def test_the_baseline_covers_exactly_the_counted_things(self, report, baseline):
        assert set(baseline) == set(self.current(report))
