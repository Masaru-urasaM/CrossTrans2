"""Stable CrossTrans 2 identities, independent of UI and runtime configuration."""

import os

APP_ID = "CrossTrans2"
DISPLAY_NAME = "CrossTrans 2"
REPOSITORY = "Masaru-urasaM/CrossTrans2"
INSTANCE_PORT = 47824
KEY_ENTROPY = b"CrossTrans2_SecureKey"


def profile_dir():
    """Writable per-user state for CrossTrans2."""
    return os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), APP_ID)
