"""
Recording from a PC-playback (WASAPI loopback) device that has more than two channels.

A WASAPI loopback endpoint only accepts the endpoint's own mix format: a 7.1 headset (for example a CORSAIR HS80
reporting 8 input channels) answers PortAudio error -9996 "Invalid device" when opened with 1 or 2 channels. The
capture must open such a device with its native channel count, mix the samples down before they reach the WAV
file, and log why a device could not be opened. Fake PortAudio backend; no real device is touched.
"""
import logging
import math
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import audio_media as media

NATIVE_8 = 8


def interleaved(channels, frames=4800, rate=48000, hz=440.0, amplitude=8000):
    """`frames` frames of a sine wave repeated on every channel, as int16 little-endian interleaved bytes."""
    samples = [int(amplitude * math.sin(2 * math.pi * hz * n / rate)) for n in range(frames)]
    return b''.join(struct.pack('<' + 'h' * channels, *([value] * channels)) for value in samples)


class Backend:
    """Fake pyaudiowpatch: one loopback and one input device; `strict` mimics WASAPI's mix-format rule."""

    paInt16, paComplete, paContinue = 8, 1, 0

    def __init__(self, loopback_channels, mic_channels=2, strict=True, fail_all=False):
        self.loopback_channels, self.mic_channels = loopback_channels, mic_channels
        self.strict, self.fail_all = strict, fail_all
        self.opened = []
        backend = self

        class PyAudio:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def get_default_wasapi_loopback(self):
                return dict(index=28, name='Headphone (Gamer 7.1) [Loopback]', maxInputChannels=backend.loopback_channels,
                            defaultSampleRate=48000, isLoopbackDevice=True)

            def get_default_input_device_info(self):
                return dict(index=22, name='Microphone (Desk Mic)', maxInputChannels=backend.mic_channels,
                            defaultSampleRate=48000, isLoopbackDevice=False)

            def open(self, **kwargs):
                backend.opened.append(kwargs)
                native = backend.loopback_channels if kwargs['input_device_index'] == 28 else backend.mic_channels
                if backend.fail_all or (backend.strict and kwargs['input_device_index'] == 28 and kwargs['channels'] != native):
                    raise OSError(-9996, 'Invalid device')
                return Stream(kwargs['stream_callback'], kwargs['channels'])

        class Stream:
            def __init__(self, callback, channels):
                self.callback, self.channels = callback, channels

            def start_stream(self):
                self.callback(interleaved(self.channels), 4800, {}, 0)

            def stop_stream(self):
                pass

            def close(self):
                pass

            def is_active(self):
                return True

        self.PyAudio = PyAudio


def record(source, backend):
    clock = [0.0]

    def now():
        clock[0] += .05
        return clock[0]
    capture = media.CaptureSession(source, limit=1, backend=backend, clock=now)
    return capture.run(lambda *args: None)


def wav_channels(path):
    import wave
    with wave.open(str(path), 'rb') as handle:
        return handle.getnchannels(), handle.getframerate(), handle.getnframes()


class TestMultiChannelLoopback:
    def test_an_eight_channel_loopback_is_opened_with_its_native_channel_count(self):
        backend = Backend(NATIVE_8)
        files = record('pc', backend)
        try:
            loopback = [kw for kw in backend.opened if kw['input_device_index'] == 28]
            assert [kw['channels'] for kw in loopback] == [NATIVE_8]
            assert len(files.tracks) == 1 and Path(files.tracks[0].path).exists()
        finally:
            files.cleanup()

    def test_samples_are_mixed_down_so_the_recording_stays_small(self):
        backend = Backend(NATIVE_8)
        files = record('pc', backend)
        try:
            # the track handed to recognition is decoded mono; the raw capture is never 8-channel on disk
            raw = files.directory / '0.wav'
            channels, rate, frames = wav_channels(raw)
            assert channels == 1 and rate == 48000 and frames >= 4800
            assert wav_channels(files.tracks[0].path)[0] == 1
        finally:
            files.cleanup()

    def test_a_stereo_loopback_still_opens_and_is_stored_as_stereo(self):
        backend = Backend(2)
        files = record('pc', backend)
        try:
            assert [kw['channels'] for kw in backend.opened] == [2]
            assert wav_channels(files.directory / '0.wav')[0] == 2
        finally:
            files.cleanup()

    def test_a_microphone_is_still_opened_with_at_most_two_channels(self):
        backend = Backend(NATIVE_8, mic_channels=8, strict=False)
        files = record('microphone', backend)
        try:
            assert [kw['channels'] for kw in backend.opened] == [2]
        finally:
            files.cleanup()

    def test_pc_and_microphone_together_record_both_tracks(self):
        backend = Backend(NATIVE_8)
        files = record('both', backend)
        try:
            assert [t.source for t in files.tracks] == ['PC', 'Microphone']
        finally:
            files.cleanup()


class TestMixdown:
    def test_equal_channels_pass_through_unchanged(self):
        data = interleaved(2, frames=10)
        assert media.mix_to_stored(data, 2, 2) is data

    def test_eight_channels_average_to_one(self):
        data = struct.pack('<8h', 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000) + struct.pack('<8h', 800, 0, 0, 0, 0, 0, 0, 0)
        mixed = struct.unpack('<2h', media.mix_to_stored(data, 8, 1))
        assert mixed == (1000, 100)

    def test_loud_channels_cannot_overflow(self):
        data = struct.pack('<8h', *([32767] * 8))
        assert struct.unpack('<h', media.mix_to_stored(data, 8, 1)) == (32767,)

    @pytest.mark.parametrize('native,loopback,expected', [
        (8, True, 8), (6, True, 6), (2, True, 2), (1, True, 1), (8, False, 2), (2, False, 2), (1, False, 1)])
    def test_channels_to_open_a_device_with(self, native, loopback, expected):
        device = dict(maxInputChannels=native, isLoopbackDevice=loopback)
        assert media.device_channels(device) == expected

    @pytest.mark.parametrize('capture,expected', [(8, 1), (6, 1), (3, 1), (2, 2), (1, 1)])
    def test_stored_channels(self, capture, expected):
        assert media.stored_channels(capture) == expected


class TestFailuresAreExplainedInTheLog:
    def test_the_underlying_error_is_logged_when_no_device_can_be_opened(self, caplog):
        backend = Backend(NATIVE_8, fail_all=True)
        with caplog.at_level(logging.WARNING):
            with pytest.raises(media.AudioError, match='Could not open the recording device'):
                record('pc', backend)
        text = '\n'.join(rec.getMessage() for rec in caplog.records)
        assert '-9996' in text and 'Invalid device' in text
        assert 'Headphone (Gamer 7.1) [Loopback]' in text and 'channels=8' in text
