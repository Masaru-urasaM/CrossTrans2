"""Simulated audio hardware and speech providers for end-to-end tests of the audio panel (CHANGELOG Phase 33).

`SimulatedPortAudio` stands in for pyaudiowpatch on a typical Windows laptop: a Realtek codec with speakers and a
microphone array. Its device list has the shape pyaudiowpatch gives on a real machine (read from the developer's
on 2026-11-17): every endpoint once under MME, with the name cut to 31 characters and a rate of 44.1 kHz, and once
under WASAPI, with the full name, the endpoint's own format and a "[Loopback]" twin for each playback device. The
default input is the MME microphone; the default loopback is the WASAPI one. Every stream runs on its own thread
and delivers 20 ms packets in real time, as PortAudio does. A loopback sends no packets while nothing plays (as
WASAPI does); a microphone always sends packets, silent ones when nobody speaks.

Each device "speaks" with its own voice: a syllable-rate envelope over a fundamental and its octave.
`SimulatedCloud` takes the place of the network under `urllib.request`. It reads the WAV the app sends (multipart
for a transcription endpoint, base64 JSON for Gemini) and finds the voice by its fundamental. It answers with that
voice's line, so a test can tell which device's audio reached which request. Nothing leaves the machine.
"""
from dataclasses import dataclass
import base64
import email.message
import io
import json
import re
import threading
import time
import urllib.error
import wave

import numpy as np

SPEAKERS = 'Speakers (Realtek(R) Audio)'
LOOPBACK = SPEAKERS + ' [Loopback]'
MICROPHONE = 'Microphone Array (Realtek(R) Audio)'
MME, WASAPI = 0, 2               # host API indexes, as on the developer's machine (DirectSound is 1)
MME_NAME_LENGTH = 31


@dataclass(frozen=True)
class Voice:
    frequency: float
    line: str                       # what the simulated provider hears in this voice
    amplitude: int = 6000

    def samples(self, start, frames, rate):
        t = np.arange(start, start + frames) / rate
        envelope = 0.55 + 0.45 * np.sin(2 * np.pi * 3.0 * t)      # about three syllables a second
        tone = np.sin(2 * np.pi * self.frequency * t) + 0.3 * np.sin(2 * np.pi * 2 * self.frequency * t)
        return (self.amplitude * envelope * tone / 1.3).astype('<i2')


PC_VOICE = Voice(240.0, 'The harbor opens at nine')
MIC_VOICE = Voice(640.0, 'The ferry leaves at ten')
FILE_VOICE = Voice(440.0, 'The museum closes at five')
VOICES = (PC_VOICE, MIC_VOICE, FILE_VOICE)


class SimulatedStream:
    def __init__(self, backend, device, voice, kwargs):
        self.backend, self.device, self.voice = backend, device, voice
        self.callback = kwargs['stream_callback']
        self.channels, self.rate = kwargs['channels'], kwargs['rate']
        self.frames = kwargs['frames_per_buffer']
        self.produced = self.packets = 0
        self.closed = False
        self._stop = threading.Event()
        self._thread = None

    def start_stream(self):
        self._thread = threading.Thread(target=self._produce, daemon=True, name='SimulatedAudioDevice')
        self._thread.start()

    def _produce(self):
        period = self.frames / self.rate
        due = time.monotonic()
        while not self._stop.is_set():
            due += period
            if self.voice is not None or not self.device['isLoopbackDevice']:
                if self.voice is None:
                    data = np.zeros(self.frames * self.channels, dtype='<i2').tobytes()
                else:
                    data = np.repeat(self.voice.samples(self.produced, self.frames, self.rate), self.channels).tobytes()
                self.produced += self.frames
                self.packets += 1
                _, flag = self.callback(data, self.frames, {}, 0)
                if flag == SimulatedPortAudio.paComplete:
                    return
            self._stop.wait(max(0.0, due - time.monotonic()))

    def is_active(self):
        return self._thread is not None and self._thread.is_alive()

    def stop_stream(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def close(self):
        self.stop_stream()
        self.closed = True


class SimulatedPortAudio:
    """A fake pyaudiowpatch module: `with backend.PyAudio() as audio` like the real one."""
    paInt16, paComplete, paContinue = 8, 1, 0

    def __init__(self, pc_voice=PC_VOICE, mic_voice=MIC_VOICE, *, speakers=SPEAKERS, loopback_channels=2,
                 loopback_rate=48000, microphone=MICROPHONE, mic_channels=2, mic_rate=48000):
        self.pc_voice, self.mic_voice = pc_voice, mic_voice

        def device(name, host, inputs, outputs, rate, loopback=False):
            return dict(name=name, hostApi=host, maxInputChannels=inputs, maxOutputChannels=outputs,
                        defaultSampleRate=float(rate), isLoopbackDevice=loopback)
        self.devices = [
            device('Microsoft Sound Mapper - Input', MME, 2, 0, 44100),
            device(microphone[:MME_NAME_LENGTH], MME, 2, 0, 44100),
            device(speakers[:MME_NAME_LENGTH], MME, 0, 2, 44100),
            device(speakers, WASAPI, 0, loopback_channels, loopback_rate),
            device(microphone, WASAPI, mic_channels, 0, mic_rate),
            device(speakers + ' [Loopback]', WASAPI, loopback_channels, 0, loopback_rate, loopback=True),
        ]
        for index, entry in enumerate(self.devices):
            entry['index'] = index
        self.default_input, self.default_loopback = 1, 5
        self.streams = []
        backend = self

        class PyAudio:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def get_default_wasapi_loopback(self):
                return dict(backend.devices[backend.default_loopback])

            def get_default_input_device_info(self):
                return dict(backend.devices[backend.default_input])

            def get_host_api_info_by_type(self, kind):
                if kind != 13:
                    raise OSError(-9979, 'Host API not found')
                return dict(index=WASAPI, type=13, name='Windows WASAPI')

            def get_device_count(self):
                return len(backend.devices)

            def get_device_info_by_index(self, index):
                return dict(backend.devices[index])

            def open(self, **kwargs):
                device = backend.devices[kwargs['input_device_index']]
                assert kwargs['input'] and kwargs['format'] == backend.paInt16 and not kwargs.get('start', True)
                if device['isLoopbackDevice'] and kwargs['channels'] != device['maxInputChannels']:
                    raise OSError(-9996, 'Invalid device')     # WASAPI loopback: only its own mix format
                voice = backend.pc_voice if device['isLoopbackDevice'] else backend.mic_voice
                stream = SimulatedStream(backend, device, voice, kwargs)
                backend.streams.append(stream)
                return stream

        self.PyAudio = PyAudio

    def all_closed(self):
        return all(stream.closed and not stream.is_active() for stream in self.streams)


def wav_samples(wav):
    with wave.open(io.BytesIO(wav), 'rb') as reader:
        shape = (reader.getframerate(), reader.getnchannels(), reader.getsampwidth())
        return shape, np.frombuffer(reader.readframes(reader.getnframes()), dtype='<i2')


def voice_in(wav):
    """The voice whose fundamental is the strongest tone in `wav`, or None for silence."""
    (rate, _, _), data = wav_samples(wav)
    if len(data) < rate // 10 or float(np.sqrt(np.mean(data.astype(np.float64) ** 2))) < 50:
        return None
    spectrum = np.abs(np.fft.rfft(data * np.hanning(len(data))))
    frequencies = np.fft.rfftfreq(len(data), 1 / rate)
    band = (frequencies > 80) & (frequencies < 4000)
    peak = float(frequencies[band][np.argmax(spectrum[band])])
    voice = min(VOICES, key=lambda item: abs(item.frequency - peak))
    return voice if abs(voice.frequency - peak) < 20 else None


def one_step_translation(target, line):
    return f'[{target}, one step] {line}'


class SimulatedCloud:
    """Answers the speech requests the app builds; `fail` queues HTTP errors for the next requests."""
    def __init__(self):
        self.requests = []
        self.fail = []              # (status, retry_after or None), one per request, oldest first
        self.lock = threading.Lock()

    def build_opener(self, *handlers):
        return self

    def open(self, request, timeout=None):
        url = request.full_url
        headers = {name.lower(): value for name, value in request.header_items()}
        if '/audio/transcriptions' in url:
            kind, key = 'transcription', headers.get('authorization', '').removeprefix('Bearer ')
            fields = self._multipart(request.data, headers['content-type'])
            wav, model, prompt = fields['file'], fields['model'].decode(), ''
        elif url.startswith('https://generativelanguage.googleapis.com/'):
            kind, key = 'gemini', headers.get('x-goog-api-key', '')
            parts = json.loads(request.data)['contents'][0]['parts']
            prompt = next(part['text'] for part in parts if 'text' in part)
            wav = base64.b64decode(next(part['inline_data']['data'] for part in parts if 'inline_data' in part))
            model = re.search(r'/models/([^:]+):', url).group(1)
        else:
            raise AssertionError(f'the simulation has no provider at {url}')
        (rate, channels, width), data = wav_samples(wav)
        voice = voice_in(wav)
        with self.lock:
            failure = self.fail.pop(0) if self.fail else None
            self.requests.append(dict(kind=kind, url=url, key=key, model=model, prompt=prompt, rate=rate,
                                      channels=channels, width=width, seconds=len(data) / rate, voice=voice,
                                      status=failure[0] if failure else 200, timeout=timeout))
        if failure:
            status, retry_after = failure
            message = email.message.Message()
            if retry_after is not None:
                message['Retry-After'] = str(retry_after)
            raise urllib.error.HTTPError(url, status, 'Simulated failure', message, io.BytesIO(b'{}'))
        line = voice.line if voice else ''
        if kind == 'transcription':
            reply = {'text': line, 'segments': [{'start': 0.0, 'end': len(data) / rate, 'text': line}] if line else []}
        else:
            target = re.search(r'translate it into (.+?)\. Provide', prompt)
            text = f'<TRANSCRIPT>{line}</TRANSCRIPT>'
            if target and line:
                text += f'<TRANSLATION>{one_step_translation(target.group(1), line)}</TRANSLATION>'
            reply = {'candidates': [{'content': {'parts': [{'text': text}]}}]}
        return io.BytesIO(json.dumps(reply).encode())

    @staticmethod
    def _multipart(body, content_type):
        boundary = content_type.split('boundary=', 1)[1].encode()
        fields = {}
        for part in body.split(b'--' + boundary):
            head, separator, value = part.partition(b'\r\n\r\n')
            name = re.search(rb'name="([^"]+)"', head)
            if separator and name:
                fields[name.group(1).decode()] = value[:-2] if value.endswith(b'\r\n') else value
        return fields
