"""CrossTrans2 owns its application identity and per-user profile."""
import json
from pathlib import Path

import src.core
from config import Config
from src import constants, product
from src.core import crypto, remote_config
from src.utils import logging_setup


def test_all_local_storage_uses_the_v2_identity(monkeypatch, tmp_path):
    assert Config.APP_NAME == product.APP_ID == constants.APP_NAME == 'CrossTrans2'
    assert Path(Config.CONFIG_DIR).name == 'CrossTrans2'
    assert Path(remote_config.CACHE_DIR).name == 'CrossTrans2'
    monkeypatch.setenv('APPDATA', str(tmp_path))
    monkeypatch.setattr(logging_setup, 'is_frozen', lambda: True)
    assert Path(logging_setup.resolve_log_dir()) == tmp_path / 'CrossTrans2' / 'logs'


def test_fresh_profile_does_not_import_unrelated_application_data(monkeypatch, tmp_path):
    old = tmp_path / 'UnrelatedApp'
    old.mkdir()
    old_file = old / 'config.json'
    old_data = {'api_keys': [{'api_key': 'fixture-unrelated-only'}], 'history': [{'id': 'old'}]}
    old_file.write_text(json.dumps(old_data), encoding='utf-8')
    new = tmp_path / 'CrossTrans2'
    monkeypatch.setattr(Config, 'CONFIG_DIR', str(new))
    monkeypatch.setattr(Config, 'CONFIG_FILE', str(new / 'config.json'))
    config = Config()
    assert config.get_api_keys() == []
    assert config.get('history') == []
    assert json.loads(old_file.read_text(encoding='utf-8')) == old_data


def test_locks_release_channel_and_key_protection_use_product_identity():
    assert constants.LOCK_PORT == product.INSTANCE_PORT == 47824
    assert constants.GITHUB_REPO == product.REPOSITORY == 'Masaru-urasaM/CrossTrans2'
    assert crypto.SecureStorage.ENTROPY == product.KEY_ENTROPY


def test_language_pack_directories_use_product_identity(monkeypatch, tmp_path):
    from src.core import nlp_manager
    monkeypatch.setenv('APPDATA', str(tmp_path))
    assert Path(nlp_manager.get_custom_packages_dir()) == tmp_path / 'CrossTrans2' / 'nlp_packages'
    assert Path(nlp_manager.get_udpipe_model_dir()) == tmp_path / 'CrossTrans2' / 'udpipe_models'
