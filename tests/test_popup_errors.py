"""
Tests for src/ui/popup_errors.py (TODO.md U6, V22): a failed translation in plain words.

The popup used to print "Error: API key invalid or quota exceeded (401)" in red; now it says what
happened and what to do, and keeps the raw notice as "Details". These pin the classes of failure
the application really produces (the strings below are copied from src/core/translation.py and
the API manager), the shape of every sentence, and that nothing is ever left without one.
"""
import os
import re
import sys

import pytest

from src.ui import popup_errors
from src.ui.components import styles
from src.ui.popup_errors import DANGER, FIX_NONE, FIX_RETRY, FIX_SETTINGS, INFO, WARNING, explain

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REAL_NOTICES = {
    'invalid key': ("Error: Invalid API key. Please check your API key in Settings.", FIX_SETTINGS),
    'no key': ("Error: No API key configured.\n\nPlease add your AI API key in Settings.\n\n"
               "Go to Settings > Guide tab for instructions on getting a free API key.",
               FIX_SETTINGS),
    'all keys failed': ("Error: All API keys failed.\nKey #1: 429 Too Many Requests - quota "
                        "exceeded for gemini-2.5-flash\nKey #2: 401 Unauthorized - invalid API key",
                        FIX_SETTINGS),
    'quota': ("Error: quota exceeded", FIX_SETTINGS),
    'rate limit': ("Error: Rate limit reached for requests", FIX_RETRY),
    'trial used up': ("Error: Trial quota exhausted for today", FIX_SETTINGS),
    'timeout': ("Error: The request timed out after 20 seconds", FIX_RETRY),
    'connection': ("Error: HTTPSConnectionPool: Max retries exceeded (Connection refused)",
                   FIX_RETRY),
    'dns': ("Error: getaddrinfo failed", FIX_RETRY),
    'server': ("Error: API request failed (503)", FIX_RETRY),
    'overloaded': ("Error: The model is overloaded. Please try again later.", FIX_RETRY),
    'no selection': ("No text selected. Please select text and try again.", FIX_NONE),
    'nothing to redo': ("No text to re-translate.", FIX_NONE),
    'no speech': ("Error: No speech was detected. Check the source and try again.", FIX_RETRY),
    'no audio device': ("Error: No active audio playback or microphone device was found. Connect speakers, "
                        "headphones, or a microphone, or check Audio devices in Settings > API keys.", FIX_SETTINGS),
    'recording source': ("Error: Choose a recording source in Settings > API keys.", FIX_SETTINGS),
    'manual audio key': ("Error: Your manual audio key failed or is unsupported. Replace it or switch Audio to "
                         "Auto in Settings > API keys.", FIX_SETTINGS),
    'speech 401': ("Error: The speech provider did not accept this API key.", FIX_SETTINGS),
    'speech 403': ("Error: This API key does not have permission to use speech recognition.", FIX_SETTINGS),
    'speech 404': ("Error: Speech recognition model \"whisper-large-v3\" was not found or is unavailable.", FIX_SETTINGS),
    'something else': ("Error: unexpected response shape", FIX_RETRY),
}


class TestWhatHelpsFirst:
    @pytest.mark.parametrize('name', REAL_NOTICES)
    def test_each_real_notice_points_at_the_right_fix(self, name):
        raw, fix = REAL_NOTICES[name]
        assert explain(raw).fix == fix, (name, explain(raw))

    def test_a_rejected_key_beats_a_timeout_mentioned_in_the_same_notice(self):
        raw = "Error: All API keys failed.\nKey #1: timed out\nKey #2: 401 Unauthorized"
        assert explain(raw).fix == FIX_SETTINGS

    def test_a_failure_with_no_key_at_all_asks_for_one(self):
        assert "no api key" in explain(REAL_NOTICES['no key'][0]).message.lower()

    def test_no_captured_text_is_actionable_and_redo_remains_information(self):
        assert explain(REAL_NOTICES['no selection'][0]).kind == WARNING
        assert explain(REAL_NOTICES['nothing to redo'][0]).kind == INFO


class TestEverySentence:
    @pytest.mark.parametrize('name', REAL_NOTICES)
    def test_it_is_plain_words(self, name):
        message = explain(REAL_NOTICES[name][0]).message
        assert message and message[0].isupper() and message.endswith('.')
        assert not message.lower().startswith('error')
        assert len(message) <= 420

    @pytest.mark.parametrize('name', REAL_NOTICES)
    def test_its_kind_is_one_an_info_bar_has(self, name):
        assert explain(REAL_NOTICES[name][0]).kind in styles.INFO_KINDS

    @pytest.mark.parametrize('raw', ['', None, 'x', 'Error:', '\n\n', 'ERROR: ' + 'a' * 5000])
    def test_nothing_is_left_without_a_sentence(self, raw):
        help_ = explain(raw)
        assert help_.message and help_.kind in (INFO, WARNING, DANGER)
        assert help_.fix in (FIX_NONE, FIX_RETRY, FIX_SETTINGS)

    def test_the_same_notice_always_gets_the_same_answer(self):
        raw = REAL_NOTICES['all keys failed'][0]
        assert explain(raw) == explain(raw)

    def test_case_does_not_matter(self):
        assert explain("ERROR: INVALID API KEY") == explain("error: invalid api key")

    def test_a_legacy_trial_notice_cannot_offer_free_translations(self):
        message = explain(REAL_NOTICES['trial used up'][0]).message.lower()
        assert 'midnight' not in message and 'free' not in message
        assert explain(REAL_NOTICES['trial used up'][0]).fix == FIX_SETTINGS


class TestTheModule:
    def test_it_is_pure_logic_with_no_tk(self):
        with open(popup_errors.__file__, encoding='utf-8') as fh:
            text = fh.read()
        assert 'tkinter' not in text and 'import tk' not in text

    def test_it_holds_no_vietnamese(self):
        letters = re.compile('[' + chr(0x1EA0) + '-' + chr(0x1EF9) + chr(0x110) + chr(0x111) + chr(0x1A0) + chr(0x1A1) + chr(0x1AF) + chr(0x1B0) + ']')
        with open(popup_errors.__file__, encoding='utf-8') as fh:
            assert not letters.search(fh.read())
