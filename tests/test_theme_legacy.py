"""Retirement guards for the completed design-system migration (U13, DoD1)."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src import constants
from src.ui.theme import tokens

REPO = str(Path(__file__).resolve().parents[1])


class TestRetirement:
    def test_the_compatibility_palette_is_retired(self):
        assert not (Path(REPO) / 'src/ui/theme/legacy.py').exists()

    def test_the_obsolete_icon_generator_is_retired(self):
        assert not (Path(REPO) / 'src/assets/generate_icons.py').exists()

    @pytest.mark.parametrize('name', ['add_btn_gray.png', 'add_btn_blue.png',
                                      'add_btn_light.png', 'add_btn_light_blue.png'])
    def test_the_unused_bitmap_buttons_are_retired(self, name):
        assert not (Path(REPO) / 'src/assets' / name).exists()

    @pytest.mark.parametrize('name,role', [
        ('FURIGANA_DEFAULT_BG', 'surface'), ('FURIGANA_BASE_FG', 'text'),
        ('FURIGANA_KANJI_FG', 'text'), ('FURIGANA_RUBY_FG', 'ruby'), ('FURIGANA_RUBY_BG', 'surface')])
    def test_standalone_ruby_defaults_come_from_the_design_palette(self, name, role):
        assert getattr(constants, name) == getattr(tokens.DARK, role)

    @pytest.mark.parametrize('group,key', [('colours', 'outside_theme'), ('fonts', 'outside_theme'),
                                           ('legacy_references', 'outside_theme')])
    def test_no_old_style_consumer_can_return(self, group, key):
        sys.path.insert(0, os.path.join(REPO, 'tools'))
        import ui_audit
        assert ui_audit.scan()[group][key] == 0


class TestImports:
    """Each import order, in a fresh interpreter: `src.constants` imports the theme, and the
    theme must not import anything that imports `src.constants` back."""

    def run(self, code):
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        done = subprocess.run([sys.executable, '-W', 'ignore', '-c', code], cwd=REPO,
                              capture_output=True, text=True, env=env, timeout=120)
        assert done.returncode == 0, done.stderr[-1500:]

    def test_the_theme_alone(self):
        self.run("import src.ui.theme.tokens, src.ui.theme.fonts, src.ui.theme.manager")

    def test_constants_first(self):
        self.run("import src.constants; import src.ui.theme.tokens")

    def test_core_before_config_as_the_app_does(self):
        self.run("import src.core; import config; import src.ui.theme.manager")

    def test_a_ui_module_first(self):
        self.run("from src.ui import popup_layout; import src.ui.quick_translate")

    def test_authentication_can_be_imported_before_settings(self):
        self.run("import src.core.auth; from src.ui.settings import SettingsWindow")

    def test_the_lazy_exports_still_resolve(self):
        self.run("import src.core; import src.ui; "
                 "assert src.ui.SettingsWindow.__name__ == 'SettingsWindow'; "
                 "assert src.ui.APIErrorDialog.__name__ == 'APIErrorDialog'")

    def test_an_unknown_attribute_of_src_ui_is_an_attribute_error(self):
        self.run("import src.ui\n"
                 "try:\n    src.ui.nope\nexcept AttributeError:\n    pass\n"
                 "else:\n    raise SystemExit('no AttributeError')")
