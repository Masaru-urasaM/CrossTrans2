"""
Tests for tools/prune_legacy.py (TODO.md U3-U13): the bridge module `legacy` loses a constant when
the last `L.NAME` that read it goes, and this tool is how - not hand edits of a generated file.
"""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import prune_legacy as tool  # noqa: E402

LEGACY_TEXT = """\"\"\"docstring\"\"\"

# --- Surfaces ---

PANEL = '#2b2b2b'   # x3: a.py
OLD_RED = '#ff0000'   # x1: b.py
C_ABCDEF = '#abcdef'   # x1: c.py
UI_9 = ('Segoe UI', 9)   # x2: d.py
UI_99 = ('Segoe UI', 99)   # x0

HIGHLIGHT_COLORS = (
    '#f4a261', '#2ec4b6',
)
"""

SOURCE = "color = L.PANEL\nfont = L.UI_9\nother = L.PANEL_EXTRA\n"


def lines(text):
    return text.split('\n')


class TestFindingOrphans:
    def test_a_constant_nothing_reads_is_an_orphan(self):
        found = tool.orphans(lines(LEGACY_TEXT), SOURCE)
        assert [name for _number, name in found] == ['OLD_RED', 'C_ABCDEF', 'UI_99']

    def test_a_constant_that_is_read_stays(self):
        names = [name for _n, name in tool.orphans(lines(LEGACY_TEXT), SOURCE)]
        assert 'PANEL' not in names and 'UI_9' not in names

    def test_a_longer_name_does_not_keep_a_shorter_one_alive(self):
        # L.PANEL_EXTRA is read; PANEL is read separately; UI_99 is not kept by UI_9
        names = [name for _n, name in tool.orphans(lines(LEGACY_TEXT), "x = L.UI_9\n")]
        assert 'UI_99' in names

    def test_the_highlight_palette_is_never_pruned(self):
        names = [name for _n, name in tool.orphans(lines(LEGACY_TEXT), "")]
        assert 'HIGHLIGHT_COLORS' not in names

    def test_comments_and_the_docstring_are_not_constants(self):
        text = "# NOT_A_CONSTANT = 1\n\"\"\"X = 2\"\"\"\n"
        assert tool.orphans(lines(text), "") == []

    def test_the_line_numbers_point_at_the_lines(self):
        all_lines = lines(LEGACY_TEXT)
        for number, name in tool.orphans(all_lines, SOURCE):
            assert all_lines[number].startswith(name + ' = ')


class TestApplying:
    @pytest.fixture
    def legacy_file(self, tmp_path, monkeypatch):
        path = tmp_path / 'legacy.py'
        path.write_bytes(LEGACY_TEXT.encode('utf-8'))
        monkeypatch.setattr(tool, 'LEGACY', str(path))
        monkeypatch.setattr(tool, 'source_text', lambda: SOURCE)
        return path

    def test_a_report_changes_nothing(self, legacy_file, capsys):
        before = legacy_file.read_bytes()
        assert tool.main([]) == 0
        assert legacy_file.read_bytes() == before
        assert '3 orphan' in capsys.readouterr().out

    def test_apply_removes_exactly_the_orphans(self, legacy_file):
        tool.main(['--apply'])
        text = legacy_file.read_text(encoding='utf-8')
        assert 'OLD_RED' not in text and 'C_ABCDEF' not in text and 'UI_99' not in text
        assert 'PANEL = ' in text and 'UI_9 = ' in text and 'HIGHLIGHT_COLORS' in text
        assert 'docstring' in text and '# --- Surfaces ---' in text

    def test_a_second_run_finds_nothing(self, legacy_file, capsys):
        tool.main(['--apply'])
        capsys.readouterr()
        tool.main([])
        assert '0 orphan' in capsys.readouterr().out

    def test_the_line_endings_are_kept(self, tmp_path, monkeypatch):
        path = tmp_path / 'legacy.py'
        path.write_bytes(LEGACY_TEXT.replace('\n', '\r\n').encode('utf-8'))
        monkeypatch.setattr(tool, 'LEGACY', str(path))
        monkeypatch.setattr(tool, 'source_text', lambda: SOURCE)
        tool.main(['--apply'])
        data = path.read_bytes()
        assert b'\r\n' in data and b'\n' not in data.replace(b'\r\n', b'')


class TestTheRealModule:
    def test_the_real_source_text_reads_the_theme_manager_but_not_the_rest_of_the_theme(self):
        text = tool.source_text()
        assert 'class QuickTranslateManager' in text
        assert 'class Palette' not in text          # tokens.py is in src/ui/theme, left out
