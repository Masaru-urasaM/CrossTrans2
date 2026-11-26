"""One-step mode translates every chunk, not only the last one (CHANGELOG Phase 27).

`AudioRecognizer` used to overwrite `one_step_translation` per chunk, so a file longer than three minutes, or PC +
Microphone with speech on both, showed the whole transcript next to the translation of its last chunk only. Now
the pieces are joined in time order when every chunk answered with one, and otherwise the joined transcript is
translated as text (two steps). Fake Gemini replies; no network.
"""
import threading

import pytest

from src.core import audio_media as media, audio_service as speech
from tests.test_audio_workflow import signal, track

GEMINI = [dict(id='g', api_key='fixture-gemini', provider='Google', model_name='gemini-2.5-flash', is_audio_key=True)]


def replies(monkeypatch, *answers):
    queue = iter(answers)
    monkeypatch.setattr(speech, 'execute_gemini_audio_request', lambda *args: next(queue))


def recognizer():
    return speech.AudioRecognizer(GEMINI, model='gemini-2.5-flash', mode='one_step', target_language='Vietnamese')


def run(rec, tracks):
    return rec.transcribe(tracks, threading.Event(), lambda *args: None)


def test_every_chunk_of_a_long_file_is_translated_in_order(monkeypatch, tmp_path):
    replies(monkeypatch, '<TRANSCRIPT>first part</TRANSCRIPT><TRANSLATION>phan dau</TRANSLATION>',
            '<TRANSCRIPT>second part</TRANSCRIPT><TRANSLATION>phan sau</TRANSLATION>')
    rec = recognizer()
    transcript = run(rec, [track(tmp_path, 185)])
    assert 'first part' in transcript and 'second part' in transcript
    assert rec.one_step_translation == 'phan dau\n\nphan sau'
    assert (rec.mode_used, rec.mode_reason) == ('one_step', '')


def test_a_chunk_without_its_translation_sends_the_session_to_two_steps(monkeypatch, tmp_path):
    replies(monkeypatch, '<TRANSCRIPT>first part</TRANSCRIPT><TRANSLATION>phan dau</TRANSLATION>',
            '<TRANSCRIPT>second part</TRANSCRIPT>')
    rec = recognizer()
    run(rec, [track(tmp_path, 185)])
    assert rec.one_step_translation == ''
    assert (rec.mode_used, rec.mode_reason) == ('two_step', 'partial_one_step_reply')


def test_pc_and_microphone_are_translated_from_the_joined_transcript(monkeypatch, tmp_path):
    replies(monkeypatch, '<TRANSCRIPT>from the call</TRANSCRIPT><TRANSLATION>tu cuoc goi</TRANSLATION>',
            '<TRANSCRIPT>my answer</TRANSCRIPT><TRANSLATION>cau tra loi</TRANSLATION>')
    pc, mic = track(tmp_path, source='PC'), track(tmp_path, source='Microphone')
    media.write_pcm(mic.path, signal(frequency=500))
    rec = recognizer()
    run(rec, [pc, mic])
    assert rec.one_step_translation == ''
    assert (rec.mode_used, rec.mode_reason) == ('two_step', 'several_sources')


def test_a_retry_from_cache_never_reuses_the_previous_translation(monkeypatch, tmp_path):
    replies(monkeypatch, '<TRANSCRIPT>hello</TRANSCRIPT><TRANSLATION>xin chao</TRANSLATION>')
    rec = recognizer()
    audio = track(tmp_path)
    run(rec, [audio])
    assert rec.one_step_translation == 'xin chao'
    run(rec, [audio])                      # every chunk cached: no audio call, so no fresh translation
    assert rec.one_step_translation == ''
    assert (rec.mode_used, rec.mode_reason) == ('two_step', 'cached_transcript')


@pytest.mark.parametrize('reply', ['<TRANSCRIPT>hello</TRANSCRIPT><TRANSLATION>   </TRANSLATION>',
                                   '<TRANSCRIPT>hello</TRANSCRIPT>'])
def test_an_empty_or_missing_translation_is_not_a_translation(monkeypatch, tmp_path, reply):
    replies(monkeypatch, reply)
    rec = recognizer()
    run(rec, [track(tmp_path)])
    assert rec.one_step_translation == '' and rec.mode_reason == 'no_translation_in_reply'
