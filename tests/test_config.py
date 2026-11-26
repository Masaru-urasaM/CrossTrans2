"""
Unit tests for config.py - Configuration management.
"""
import copy
import os
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock, Mock

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.core  # noqa: F401  (must precede `config`: see project CLAUDE.md)
from config import Config


class TestConfigInit:
    """Tests for Config initialization."""

    def test_default_config_values(self, temp_config_dir):
        """Test that default values are set correctly."""
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()

                assert config.get_api_keys() == []
                assert config.get_autostart() == False
                assert config.get_theme() == 'darkly'
                assert config.get_check_updates() == False

    def test_load_existing_config(self, temp_config_dir, sample_config_json):
        """Test loading existing configuration file."""
        config_file = os.path.join(temp_config_dir, 'config.json')

        # Write sample config
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(sample_config_json, f)

        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', config_file):
                config = Config()

                api_keys = config.get_api_keys()
                assert len(api_keys) == 1
                assert api_keys[0]['model_name'] == 'gemini-2.0-flash'


class TestApiKeyManagement:
    """Tests for API key management."""

    def test_get_api_keys_empty(self, temp_config_dir):
        """Test getting API keys when none are set."""
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()
                assert config.get_api_keys() == []

    def test_set_and_get_api_keys(self, temp_config_dir):
        """Test setting and retrieving API keys."""
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()

                test_keys = [
                    {'model_name': 'gpt-4', 'api_key': 'sk-test123', 'provider': 'openai'},
                    {'model_name': 'claude-3', 'api_key': 'sk-ant-test', 'provider': 'anthropic'}
                ]
                config.set_api_keys(test_keys)

                retrieved = config.get_api_keys()
                assert len(retrieved) == 2
                assert retrieved[0]['model_name'] == 'gpt-4'
                assert retrieved[1]['api_key'] == 'sk-ant-test'

    def test_get_api_key_backward_compat(self, temp_config_dir):
        """Test backward compatibility method get_api_key()."""
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()

                test_keys = [{'model_name': 'test', 'api_key': 'first-key'}]
                config.set_api_keys(test_keys)

                # get_api_key() should return first key
                assert config.get_api_key() == 'first-key'


class TestHotkeyManagement:
    """Tests for hotkey management."""

    def test_get_default_hotkeys(self, temp_config_dir):
        """Test getting default hotkeys."""
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()

                hotkeys = config.get_hotkeys()
                assert 'Vietnamese' in hotkeys
                assert 'English' in hotkeys
                assert hotkeys['Vietnamese'] == 'win+alt+v'

    def test_set_custom_hotkey(self, temp_config_dir):
        """Test setting custom hotkey."""
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()

                config.set_custom_hotkey('Korean', 'win+alt+k')
                custom = config.get_custom_hotkeys()

                assert 'Korean' in custom
                assert custom['Korean'] == 'win+alt+k'

    def test_get_all_hotkeys_merges(self, temp_config_dir):
        """Test that get_all_hotkeys merges default and custom."""
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()

                config.set_custom_hotkey('Korean', 'win+alt+k')
                all_hotkeys = config.get_all_hotkeys()

                # Should have both default and custom
                assert 'Vietnamese' in all_hotkeys
                assert 'Korean' in all_hotkeys

    def test_max_custom_hotkeys(self, temp_config_dir):
        """Test that max custom hotkeys limit is enforced."""
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()

                # Add max number of custom hotkeys
                for i in range(Config.MAX_CUSTOM_HOTKEYS + 2):
                    config.set_custom_hotkey(f'Lang{i}', f'win+alt+{i}')

                custom = config.get_custom_hotkeys()
                assert len(custom) <= Config.MAX_CUSTOM_HOTKEYS


class TestThemeAndSettings:
    """Tests for theme and other settings."""

    def test_set_and_get_theme(self, temp_config_dir):
        """Test setting and getting theme."""
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()

                config.set_theme('superhero')
                assert config.get_theme() == 'superhero'

    def test_restore_defaults(self, temp_config_dir):
        """Test restoring default settings preserves API keys."""
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()

                # Set custom values
                test_keys = [{'model_name': 'test', 'api_key': 'preserve-me'}]
                config.set_api_keys(test_keys)
                config.set_theme('superhero')
                config.set_custom_hotkey('Korean', 'win+alt+k')

                # Restore defaults
                config.restore_defaults()

                # API keys should be preserved
                assert len(config.get_api_keys()) == 1
                assert config.get_api_keys()[0]['api_key'] == 'preserve-me'

                # Theme should be reset
                assert config.get_theme() == 'darkly'

                # Custom hotkeys should be cleared
                assert config.get_custom_hotkeys() == {}

    def test_audio_records_pc_audio_by_default_and_after_restore(self, temp_config_dir):
        """The developer's choice of 2026-11-17: PC audio only, so the microphone is not opened unasked."""
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()
                assert config.get('audio_source') == 'pc'

                config.set('audio_source', 'both')
                config.restore_defaults()
                assert config.get('audio_source') == 'pc'

    def test_a_saved_recording_source_is_kept(self, temp_config_dir):
        """Changing the default does not rewrite a source the user already chose."""
        config_file = os.path.join(temp_config_dir, 'config.json')
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump({'audio_source': 'both'}, f)
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', config_file):
                assert Config().get('audio_source') == 'both'


class TestApiCapabilities:
    """Tests for API capability management."""

    def test_update_api_capabilities(self, temp_config_dir):
        """Test updating API capabilities."""
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()

                test_keys = [{'model_name': 'gpt-4o', 'api_key': 'test-key'}]
                config.set_api_keys(test_keys)

                config.update_api_capabilities('test-key', 'gpt-4o', True, True)

                api_keys = config.get_api_keys()
                assert api_keys[0].get('vision_capable') == True
                assert api_keys[0].get('file_capable') == True

    def test_has_any_vision_capable(self, temp_config_dir):
        """Test checking for vision capability."""
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()

                # No APIs = no vision
                assert config.has_any_vision_capable() == False

                # Add vision-capable API
                test_keys = [{'model_name': 'gpt-4o', 'api_key': 'test', 'vision_capable': True}]
                config._config['api_keys'] = test_keys

                assert config.has_any_vision_capable() == True


class TestConfigPersistence:
    """Tests for config file persistence."""

    def test_save_creates_file(self, temp_config_dir):
        """Test that save creates the config file."""
        config_file = os.path.join(temp_config_dir, 'config.json')

        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', config_file):
                config = Config()
                config.set_theme('cyborg')

                # File should exist after save
                assert os.path.exists(config_file)

                # Content should be valid JSON
                with open(config_file, 'r') as f:
                    data = json.load(f)
                    assert data['theme'] == 'cyborg'

    def test_secure_save(self, temp_config_dir):
        """Test secure save overwrites file content."""
        config_file = os.path.join(temp_config_dir, 'config.json')

        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', config_file):
                config = Config()

                # Write initial config
                config.set_api_keys([{'model_name': 'test', 'api_key': 'secret-key'}])

                # Secure save should still work
                config.save(secure=True)

                assert os.path.exists(config_file)


class TestQuickPopupSize:
    """The size the user dragged the quick-translate popup to (Decision 34, P4)."""

    @staticmethod
    def _patched(temp_config_dir):
        return (patch.object(Config, 'CONFIG_DIR', temp_config_dir),
                patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')))

    def test_automatic_until_the_user_drags_it(self, temp_config_dir):
        directory, file = self._patched(temp_config_dir)
        with directory, file:
            assert Config().get_quick_popup_size() == (None, None)

    def test_a_dragged_size_survives_a_restart(self, temp_config_dir):
        directory, file = self._patched(temp_config_dir)
        with directory, file:
            Config().set_quick_popup_size(480, 300)
            assert Config().get_quick_popup_size() == (480, 300)

    def test_none_returns_it_to_automatic(self, temp_config_dir):
        directory, file = self._patched(temp_config_dir)
        with directory, file:
            config = Config()
            config.set_quick_popup_size(480, 300)
            config.set_quick_popup_size(None, None)
            assert Config().get_quick_popup_size() == (None, None)


class TestTranslatorWindowMemory:
    """What the Translator remembers between runs (TODO.md U7): its size and the recent targets."""

    @staticmethod
    def _patched(temp_config_dir):
        return (patch.object(Config, 'CONFIG_DIR', temp_config_dir),
                patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')))

    def test_the_size_is_automatic_until_the_user_resizes(self, temp_config_dir):
        directory, file = self._patched(temp_config_dir)
        with directory, file:
            assert Config().get_main_window_size() == (None, None)

    def test_a_size_survives_a_restart_and_none_returns_to_automatic(self, temp_config_dir):
        directory, file = self._patched(temp_config_dir)
        with directory, file:
            Config().set_main_window_size(1100, 700)
            assert Config().get_main_window_size() == (1100, 700)
            Config().set_main_window_size(None, None)
            assert Config().get_main_window_size() == (None, None)

    def test_no_recent_languages_at_first(self, temp_config_dir):
        directory, file = self._patched(temp_config_dir)
        with directory, file:
            assert Config().get_recent_languages() == []

    def test_recent_languages_are_newest_first_once_each_and_survive_a_restart(self, temp_config_dir):
        directory, file = self._patched(temp_config_dir)
        with directory, file:
            config = Config()
            for name in ("English", "French", "English"):
                config.push_recent_language(name)
            assert Config().get_recent_languages() == ["English", "French"]

    def test_the_class_default_list_is_never_shared_between_instances(self, temp_config_dir):
        directory, file = self._patched(temp_config_dir)
        with directory, file:
            Config().push_recent_language("English")
            assert Config.DEFAULT_CONFIG['recent_languages'] == []

    def test_a_damaged_value_reads_as_empty(self, temp_config_dir):
        directory, file = self._patched(temp_config_dir)
        with directory, file:
            config = Config()
            config._config['recent_languages'] = "English"
            assert config.get_recent_languages() == []


class TestThemeMode:
    """The user's theme choice (Decision 46): system by default, light or dark on request."""

    def make(self, temp_config_dir):
        with patch.object(Config, 'CONFIG_DIR', temp_config_dir):
            with patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')):
                config = Config()
                config.CONFIG_DIR = temp_config_dir
                config.CONFIG_FILE = os.path.join(temp_config_dir, 'config.json')
                return config

    def test_the_default_is_to_follow_windows(self, temp_config_dir):
        assert self.make(temp_config_dir).get_theme_mode() == 'system'

    def test_a_choice_is_kept(self, temp_config_dir):
        config = self.make(temp_config_dir)
        config.set_theme_mode('dark')
        assert config.get_theme_mode() == 'dark'

    def test_an_unknown_value_in_the_file_reads_as_system(self, temp_config_dir):
        config = self.make(temp_config_dir)
        config._config['theme_mode'] = 'plaid'
        assert config.get_theme_mode() == 'system'

    def test_an_unknown_value_is_refused_on_write(self, temp_config_dir):
        config = self.make(temp_config_dir)
        with pytest.raises(ValueError):
            config.set_theme_mode('plaid')
        assert config.get_theme_mode() == 'system'

    def test_a_config_written_before_the_key_existed_reads_as_system(self, temp_config_dir):
        config = self.make(temp_config_dir)
        config._config.pop('theme_mode', None)
        assert config.get_theme_mode() == 'system'

    def test_the_manager_can_use_the_config_as_its_store(self, temp_config_dir):
        from src.ui.theme.manager import ThemeManager
        config = self.make(temp_config_dir)
        manager = ThemeManager(config, detect=lambda: 'light')
        manager.set_mode('dark')
        assert config.get_theme_mode() == 'dark'


class TestSettingsWindowSize:
    def make(self, **overrides):
        cfg = Config.__new__(Config)
        cfg._config = dict(overrides)
        cfg.save = Mock()
        return cfg

    def test_it_is_unset_by_default(self):
        assert self.make().get_settings_window_size() == (None, None)
        assert Config.DEFAULT_CONFIG['settings_window_width'] is None

    def test_it_round_trips_and_saves(self):
        cfg = self.make()
        cfg.set_settings_window_size(900, 640)
        assert cfg.get_settings_window_size() == (900, 640)
        cfg.save.assert_called_once()

    def test_none_returns_it_to_the_default(self):
        cfg = self.make(settings_window_width=900, settings_window_height=640)
        cfg.set_settings_window_size(None, None)
        assert cfg.get_settings_window_size() == (None, None)

    @pytest.mark.parametrize('bad', ['wide', -5, 0, [], {}, 'x1'])
    def test_a_value_that_is_not_a_size_reads_as_unset(self, bad):
        cfg = self.make(settings_window_width=bad, settings_window_height=700)
        assert cfg.get_settings_window_size() == (None, 700)

    def test_it_is_not_one_of_the_restored_settings(self):
        assert 'settings_window_width' not in Config.RESTORABLE


class TestSettingsRedesign:
    """What the redesigned Settings window needs from the config (TODO.md U8)."""

    @staticmethod
    def _patched(temp_config_dir):
        return (patch.object(Config, 'CONFIG_DIR', temp_config_dir),
                patch.object(Config, 'CONFIG_FILE', os.path.join(temp_config_dir, 'config.json')),
                patch.object(Config, '_update_registry_autostart'))     # never the real registry

    def test_the_translator_starts_in_vietnamese_as_it_always_has(self, temp_config_dir):
        a, b, c = self._patched(temp_config_dir)
        with a, b, c:
            assert Config().get_default_target_language() == 'Vietnamese'

    def test_a_chosen_default_language_survives_a_restart(self, temp_config_dir):
        a, b, c = self._patched(temp_config_dir)
        with a, b, c:
            Config().set_default_target_language('Japanese')
            assert Config().get_default_target_language() == 'Japanese'

    @pytest.mark.parametrize('stored', ['Klingon', '', None, 5, ['English']])
    def test_a_name_that_is_not_a_language_reads_as_the_default(self, temp_config_dir, stored):
        a, b, c = self._patched(temp_config_dir)
        with a, b, c:
            config = Config()
            config._config['default_target_language'] = stored
            assert config.get_default_target_language() == 'Vietnamese'

    @pytest.mark.parametrize('bad', ['', None, 5])
    def test_a_default_language_must_be_a_name(self, temp_config_dir, bad):
        a, b, c = self._patched(temp_config_dir)
        with a, b, c:
            with pytest.raises(ValueError):
                Config().set_default_target_language(bad)

    def test_language_hotkeys_are_written_together_and_the_old_custom_store_is_emptied(self, temp_config_dir):
        a, b, c = self._patched(temp_config_dir)
        with a, b, c:
            config = Config()
            config._config['custom_hotkeys'] = {'French': 'win+alt+f'}
            config.set_language_hotkeys({'English': 'win+alt+e', 'French': 'win+alt+f', 'German': ''})
            again = Config()
            assert again.get_hotkeys() == {'English': 'win+alt+e', 'French': 'win+alt+f', 'German': ''}
            assert again.get_custom_hotkeys() == {} and again.get_all_hotkeys() == again.get_hotkeys()

    def test_every_restorable_setting_is_a_setting_with_a_shipped_value(self):
        for key in Config.RESTORABLE:
            assert key in Config.DEFAULT_CONFIG, key

    def test_secrets_and_data_are_never_restorable(self):
        for key in ('api_keys', 'history', 'nlp_installed', 'nlp_basic_mode', 'provider_health',
                    'trial_mode_forced', 'update_reminders'):
            assert key not in Config.RESTORABLE

    def test_restoring_defaults_puts_the_shipped_values_back_and_keeps_the_rest(self, temp_config_dir):
        a, b, c = self._patched(temp_config_dir)
        with a, b, c:
            config = Config()
            config.set_api_keys([{'model_name': 'm', 'api_key': 'k', 'provider': 'Google'}])
            config._config['nlp_installed'] = ['Japanese']
            config._config['history'] = [{'original': 'a'}]
            config.set_language_hotkeys({'English': 'ctrl+alt+x', 'French': 'ctrl+alt+f'})
            config.set_screenshot_hotkey('ctrl+alt+s')
            config.set_screenshot_target_language('French')
            config.set_fix_grammar_hotkey('ctrl+alt+g')
            config.set_fix_grammar_hotkey_enabled(True)
            config.set_fix_grammar_enabled(False)
            config.set_auto_check_updates(False)
            config.set_quick_replace(True)
            config.set_furigana_enabled(False)
            config.set_linked_reading_enabled(False)
            config.set_theme_mode('dark')
            config.set_default_target_language('French')
            config.restore_defaults()
            again = Config()
            for key in Config.RESTORABLE:
                assert again._config[key] == Config.DEFAULT_CONFIG[key], key
            assert again.get_api_keys() and again._config['nlp_installed'] == ['Japanese']
            assert again._config['history'] == [{'original': 'a'}]

    def test_restored_values_are_copies_not_the_class_defaults(self, temp_config_dir):
        a, b, c = self._patched(temp_config_dir)
        with a, b, c:
            config = Config()
            config.restore_defaults()
            config.get_hotkeys()['English'] = 'ctrl+alt+z'
            assert Config.DEFAULT_CONFIG['hotkeys']['English'] == 'win+alt+e'

    def test_a_setter_never_changes_the_shipped_defaults(self, temp_config_dir):
        a, b, c = self._patched(temp_config_dir)
        with a, b, c:
            shipped = copy.deepcopy(Config.DEFAULT_CONFIG)
            config = Config()
            config.set_custom_hotkey('French', 'win+alt+f')
            config.push_recent_language('English')
            config.get_hotkeys()['English'] = 'ctrl+alt+z'
            config.get_api_keys().append({'model_name': 'x'})
            assert Config.DEFAULT_CONFIG == shipped
            assert Config.DEFAULT_HOTKEYS['English'] == 'win+alt+e'

    def test_a_second_config_in_the_same_process_starts_clean(self, temp_config_dir, tmp_path):
        a, b, c = self._patched(temp_config_dir)
        with a, b, c:
            Config().set_custom_hotkey('French', 'win+alt+f')
        other = tmp_path / 'other'
        other.mkdir()
        with patch.object(Config, 'CONFIG_DIR', str(other)),                 patch.object(Config, 'CONFIG_FILE', str(other / 'config.json')):
            assert Config().get_custom_hotkeys() == {}

    def test_a_damaged_file_also_starts_from_copies(self, temp_config_dir):
        path = os.path.join(temp_config_dir, 'config.json')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('{not json')
        a, b, c = self._patched(temp_config_dir)
        with a, b, c:
            config = Config()
            config.set_custom_hotkey('French', 'win+alt+f')
            assert Config.DEFAULT_CONFIG['custom_hotkeys'] == {}

    def test_a_key_missing_from_an_old_file_is_merged_as_a_copy(self, temp_config_dir):
        path = os.path.join(temp_config_dir, 'config.json')
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump({'autostart': False}, handle)
        a, b, c = self._patched(temp_config_dir)
        with a, b, c:
            config = Config()
            config.set_custom_hotkey('French', 'win+alt+f')
            assert Config.DEFAULT_CONFIG['custom_hotkeys'] == {}
