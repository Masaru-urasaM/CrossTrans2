"""Bounded local audio decoding, capture and temporary-file ownership. No Tk calls."""
from dataclasses import dataclass
from pathlib import Path
import logging
import math
import shutil
import tempfile
import threading
import time
import wave

from src.utils.log_events import log_event


MEDIA_EXTENSIONS = ('.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus',
                    '.webm', '.mp4', '.mkv', '.mov', '.avi', '.mpeg', '.mpga')
RATE = 16000
QUICK_LIMIT = 180
FILE_LIMIT = 1800
CHUNK_SECONDS = 180


class AudioError(Exception):
    """An expected audio failure whose English message is localized by the UI."""


class AudioCancelled(AudioError):
    pass


def is_cancelled(cancelled) -> bool:
    if cancelled is None:
        return False
    if callable(cancelled):
        return bool(cancelled())
    if hasattr(cancelled, 'is_set'):
        return bool(cancelled.is_set())
    return bool(cancelled)


def check_cancel(cancelled):
    if is_cancelled(cancelled):
        raise AudioCancelled('Audio translation was cancelled.')


@dataclass(frozen=True)
class AudioTrack:
    path: str
    source: str
    offset: float = 0.0


class AudioFiles:
    """Own only app-created files; uploaded originals are never removed."""
    def __init__(self):
        self.directory = Path(tempfile.mkdtemp(prefix='CrossTrans2-audio-'))
        self.tracks = []
        self.recorded = False

    def cleanup(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def save(self, destination):
        """Export synchronized PCM: PC left and microphone right for Both."""
        import numpy as np
        arrays = [read_pcm(track.path) for track in self.tracks]
        if not arrays:
            raise AudioError('There is no recording to save.')
        length = max(len(data) + round(track.offset * RATE)
                     for data, track in zip(arrays, self.tracks))
        output = np.zeros((length, len(arrays)), dtype=np.int16)
        for channel, (data, track) in enumerate(zip(arrays, self.tracks)):
            start = round(track.offset * RATE)
            output[start:start + len(data), channel] = data
        write_pcm(destination, output, channels=len(arrays))


def write_pcm(path, samples, channels=1):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(samples.astype('<i2').tobytes())


def read_pcm(path):
    import numpy as np
    with wave.open(str(path), 'rb') as stream:
        if (stream.getframerate(), stream.getsampwidth(), stream.getnchannels()) != (RATE, 2, 1):
            raise AudioError('The audio could not be decoded.')
        return np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2').copy()


def decode_media(path, destination, cancelled, limit=FILE_LIMIT, progress=None):
    """Decode the first audio stream to bounded 16 kHz mono without loading video frames."""
    try:
        import av
    except ImportError as error:
        raise AudioError('Audio support is not installed. Install the application audio dependencies.') from error
    if not Path(path).is_file():
        raise AudioError('File no longer exists.')
    try:
        with av.open(str(path)) as container, wave.open(str(destination), 'wb') as output:
            output.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
            streams = list(container.streams.audio)
            if not streams:
                raise AudioError('This file has no audio track.')
            stream = streams[0]
            duration = float(stream.duration * stream.time_base) if stream.duration is not None else None
            if duration is not None and duration > limit + 0.05:
                raise AudioError('Audio and video files must be 30 minutes or shorter.')
            resampler = av.AudioResampler(format='s16', layout='mono', rate=RATE)
            count = 0

            def append(frame):
                nonlocal count
                count += frame.samples
                if count > limit * RATE:
                    raise AudioError('Audio and video files must be 30 minutes or shorter.')
                output.writeframesraw(bytes(frame.planes[0])[:frame.samples * 2])

            for frame in container.decode(stream):
                check_cancel(cancelled)
                for converted in resampler.resample(frame):
                    append(converted)
                if progress and count % (RATE * 5) < frame.samples:
                    progress('decoding', count / RATE, duration or limit)
            for converted in resampler.resample(None):
                append(converted)
            if count == 0:
                raise AudioError('This file has no audio track.')
            return count / RATE
    except AudioError:
        raise
    except Exception as error:
        raise AudioError('The audio could not be decoded.') from error


def reduce_noise(samples):
    """Conservative spectral attenuation; never used to terminate a recording."""
    import numpy as np
    if len(samples) < 512:
        return samples
    size, hop = 512, 256
    signal = samples.astype(np.float64)
    padded = np.pad(signal, (size, size))
    window = np.hanning(size)
    frames = np.lib.stride_tricks.sliding_window_view(padded, size)[::hop].copy()
    spectrum = np.fft.rfft(frames * window, axis=1)
    magnitude = np.abs(spectrum)
    floor = np.percentile(magnitude, 15, axis=0)
    gain = np.maximum(0.35, 1 - 0.8 * floor / np.maximum(magnitude, 1))
    gain[:, :2] = 0.2
    restored = np.fft.irfft(spectrum * gain, n=size, axis=1) * window
    result = np.zeros(len(padded))
    weights = np.zeros(len(padded))
    for index, frame in enumerate(restored):
        start = index * hop
        result[start:start + size] += frame
        weights[start:start + size] += window * window
    result = (result / np.maximum(weights, 1e-8))[size:size + len(signal)]
    return np.clip(result, -32768, 32767).astype(np.int16)


def resolve_wasapi_loopback(audio, selection=None):
    """Resolve a WASAPI loopback device safely for the given selection or system default."""
    if selection is not None:
        index = selection.get('index') if isinstance(selection, dict) else selection
        target_name = selection.get('name') if isinstance(selection, dict) else None
        if index is not None:
            try:
                device = audio.get_device_info_by_index(index)
                if target_name is None or device.get('name') == target_name:
                    if device.get('isLoopbackDevice'):
                        return device
                    if hasattr(audio, 'get_wasapi_loopback_analogue_by_index'):
                        try:
                            return audio.get_wasapi_loopback_analogue_by_index(index)
                        except Exception:
                            pass
            except Exception:
                pass

        # If index shifted or name differed, search all devices by target_name
        if target_name and hasattr(audio, 'get_device_count'):
            for i in range(audio.get_device_count()):
                try:
                    d = audio.get_device_info_by_index(i)
                    if d.get('name') == target_name:
                        if d.get('isLoopbackDevice'):
                            return d
                        if hasattr(audio, 'get_wasapi_loopback_analogue_by_index'):
                            try:
                                return audio.get_wasapi_loopback_analogue_by_index(i)
                            except Exception:
                                pass
                except Exception:
                    continue

    if hasattr(audio, 'get_default_wasapi_loopback'):
        try:
            return audio.get_default_wasapi_loopback()
        except Exception:
            pass

    # Fallback when default WASAPI device index is -1 or not configured
    try:
        wasapi_info = audio.get_host_api_info_by_type(13)  # paWASAPI = 13
        wasapi_host_index = wasapi_info['index']
    except Exception:
        wasapi_host_index = None

    loopbacks = []
    wasapi_outputs = []
    if hasattr(audio, 'get_device_count'):
        for i in range(audio.get_device_count()):
            try:
                d = audio.get_device_info_by_index(i)
                if wasapi_host_index is not None and d.get('hostApi') != wasapi_host_index:
                    continue
                if d.get('isLoopbackDevice') and d.get('maxInputChannels', 0) > 0:
                    loopbacks.append(d)
                elif d.get('maxOutputChannels', 0) > 0:
                    wasapi_outputs.append(d)
            except Exception:
                continue

    if not loopbacks:
        if wasapi_outputs and hasattr(audio, 'get_wasapi_loopback_analogue_by_index'):
            for out in wasapi_outputs:
                try:
                    lb = audio.get_wasapi_loopback_analogue_by_index(out['index'])
                    if lb and lb.get('isLoopbackDevice'):
                        return lb
                except Exception:
                    continue
        return None

    candidate_names = []
    try:
        def_out = audio.get_default_output_device_info()
        name = def_out.get('name', '').strip().lower()
        if name and 'sound mapper' not in name:
            candidate_names.append(name)
    except Exception:
        pass

    if hasattr(audio, 'get_host_api_count'):
        try:
            for api_idx in range(audio.get_host_api_count()):
                try:
                    api_info = audio.get_host_api_info_by_index(api_idx)
                    out_idx = api_info.get('defaultOutputDevice', -1)
                    if out_idx >= 0:
                        dev = audio.get_device_info_by_index(out_idx)
                        dev_name = dev.get('name', '').strip().lower()
                        if dev_name and 'sound mapper' not in dev_name and 'primary sound' not in dev_name:
                            candidate_names.append(dev_name)
                except Exception:
                    continue
        except Exception:
            pass

    for cand in candidate_names:
        for lb in loopbacks:
            clean_lb = lb['name'].replace('[Loopback]', '').strip().lower()
            if clean_lb and (clean_lb in cand or cand in clean_lb):
                return lb
            if '(' in cand and ')' in cand:
                paren = cand[cand.find('(') + 1:cand.rfind(')')].strip()
                if paren and paren in clean_lb:
                    return lb

    for out in wasapi_outputs:
        out_name = out.get('name', '').strip().lower()
        for lb in loopbacks:
            clean_lb = lb['name'].replace('[Loopback]', '').strip().lower()
            if clean_lb and clean_lb in out_name:
                return lb

    return loopbacks[0]


def list_devices():
    """Called explicitly by a worker, never at module import or in a gallery."""
    try:
        import pyaudiowpatch as pa
    except ImportError as error:
        raise AudioError('Audio support is not installed. Install the application audio dependencies.') from error
    with pa.PyAudio() as audio:
        return [dict(index=int(item['index']), name=item['name'],
                     loopback=bool(item.get('isLoopbackDevice', False)))
                for item in audio.get_device_info_generator() if item.get('maxInputChannels', 0) > 0]


def device_channels(device) -> int:
    """Channel count to open `device` with.

    A WASAPI loopback endpoint only accepts its own mix format, so a 5.1 or 7.1 playback device (many gaming
    headsets) must be opened with all of its channels: asking for one or two fails with PortAudio error -9996
    "Invalid device". Microphones are opened with at most two channels.
    """
    native = int(device['maxInputChannels'])
    return native if device.get('isLoopbackDevice') else min(2, native)


# A headset microphone's name: English and Japanese Windows ("ヘッドセット (soundcore Sleep A20)")
HEADSET_MARKS = ('headset', 'hands-free', 'handsfree', 'hands free', 'ヘッドセット', 'ハンズフリー')
PROFILE_SUFFIXES = ('hands-free ag audio', 'hands-free', 'handsfree', 'hands free', 'stereo')
WASAPI_HOST_TYPE = 13            # paWASAPI


def hands_free_format(audio, device) -> bool:
    """Whether the microphone's WASAPI endpoint has the Bluetooth hands-free format, mono at 16 kHz or less, in
    any Windows language. The default input is an MME device (its name cut to 31 characters, its rate always
    44.1 kHz), so its WASAPI twin is found by name."""
    try:
        wasapi = audio.get_host_api_info_by_type(WASAPI_HOST_TYPE)['index']
    except Exception:
        return False
    name = device.get('name') or ''
    twins = [device] if device.get('hostApi') == wasapi else []
    if not twins and name and hasattr(audio, 'get_device_count'):
        for index in range(audio.get_device_count()):
            try:
                other = audio.get_device_info_by_index(index)
            except Exception:
                continue
            if (other.get('hostApi') == wasapi and not other.get('isLoopbackDevice')
                    and other.get('maxInputChannels', 0) > 0 and (other.get('name') or '').startswith(name)):
                twins.append(other)
    return any(int(twin.get('maxInputChannels', 0)) == 1 and float(twin.get('defaultSampleRate') or 0) <= 16000
               for twin in twins)


def same_headset(playback_name: str, microphone_name: str, hands_free: bool = False) -> bool:
    """Whether the microphone is the headset that plays the PC audio, so that PC + Microphone leaves it out:
    opening a Bluetooth headset's microphone switches its playback from stereo (A2DP) to the hands-free profile.

    The two endpoints share the device name in brackets, less the profile Windows 10 appends ("Headphones
    (WH-1000XM4 Stereo)" and "Headset (WH-1000XM4 Hands-Free AG Audio)"). So do the speakers and the microphone
    of one sound card ("Speakers (Realtek(R) Audio)" and "Microphone Array (Realtek(R) Audio)"), which record
    together without trouble, so the microphone must also be named as a headset or have the hands-free format
    (`hands_free_format`) (Decision 89)."""
    pc_name, mic_name = (playback_name or '').lower(), (microphone_name or '').lower()
    if not hands_free and not any(mark in mic_name for mark in HEADSET_MARKS):
        return False
    for name in (pc_name, mic_name):
        if '(' in name and ')' in name:
            inside = name[name.find('(') + 1:name.rfind(')')].strip()
            for suffix in PROFILE_SUFFIXES:
                if inside.endswith(' ' + suffix):
                    inside = inside[:-len(suffix)].strip()
                    break
            if len(inside) > 3 and inside in pc_name and inside in mic_name:
                return True
    return False


def stored_channels(capture_channels: int) -> int:
    """Channels written to the raw capture WAV: mono or stereo stay as they are, anything wider becomes mono."""
    return capture_channels if capture_channels <= 2 else 1


def mix_to_stored(data: bytes, capture_channels: int, stored: int) -> bytes:
    """Average interleaved int16 frames of `capture_channels` down to `stored` channels (only mono is produced)."""
    if capture_channels == stored:
        return data
    import numpy as np
    frames = np.frombuffer(data, dtype='<i2').reshape(-1, capture_channels)
    return frames.astype(np.int32).mean(axis=1).astype('<i2').tobytes()


class CaptureSession:
    """PortAudio callbacks enqueue PCM only; a worker owns native streams and disk I/O."""
    def __init__(self, source='pc', microphone=None, playback=None, limit=QUICK_LIMIT,
                 backend=None, clock=time.monotonic):
        self.source, self.microphone, self.playback = source, microphone, playback
        self.limit, self.backend, self.clock = limit, backend, clock
        self.stop_event, self.cancelled = threading.Event(), threading.Event()
        self.files = AudioFiles()
        self.files.recorded = True
        self.started = None
        self.elapsed = 0.0
        self.level = 0.0
        self.levels = {}         # role -> (level of its latest packet, when): one meter per device
        self.limit_reached = False
        self.sid = None          # the log session this recording belongs to (AudioController)
        self.skipped = []        # devices left out, and why: logged with `recording.stop`

    def current_levels(self, window=0.3):
        """Each recording device's level, 0 for one that sent nothing lately: WASAPI loopback sends no
        packets while nothing plays, so its last level would otherwise stay on the meter."""
        now = self.clock()
        return {role: (level if now - at <= window else 0.0) for role, (level, at) in list(self.levels.items())}

    def stop(self):
        self.stop_event.set()

    def cancel(self):
        self.cancelled.set()
        self.stop()

    def run(self, progress):
        import queue
        try:
            import av  # noqa: F401 - import probe
            import numpy as np
            if self.backend is None:
                import pyaudiowpatch as pa
            else:
                pa = self.backend
        except ImportError as error:
            self.files.cleanup()
            raise AudioError('Audio support is not installed. Install the application audio dependencies.') from error
        streams, writers, layouts = [], [], []
        successful = False
        packets = queue.Queue(maxsize=600)
        failed = threading.Event()
        offsets = {}
        try:
            with pa.PyAudio() as audio:
                choices = []

                def selected_device(selection, default):
                    if selection is None:
                        try:
                            return default()
                        except Exception:
                            pass
                        if hasattr(audio, 'get_device_count'):
                            candidates = []
                            for i in range(audio.get_device_count()):
                                try:
                                    d = audio.get_device_info_by_index(i)
                                    if not d.get('isLoopbackDevice') and d.get('maxInputChannels', 0) > 0:
                                        candidates.append(d)
                                except Exception:
                                    continue
                            for d in candidates:
                                low = d.get('name', '').strip().lower()
                                if 'sound mapper' not in low and 'primary sound' not in low:
                                    return d
                            if candidates:
                                return candidates[0]
                        raise AudioError('Could not open the microphone. Check device permissions or select another recording device.')
                    index = selection.get('index') if isinstance(selection, dict) else selection
                    target_name = selection.get('name') if isinstance(selection, dict) else None
                    if index is not None:
                        try:
                            device = audio.get_device_info_by_index(index)
                            if target_name is None or device.get('name') == target_name:
                                return device
                        except Exception:
                            pass
                    # If index shifted or name differed, search all devices by target_name
                    if target_name and hasattr(audio, 'get_device_count'):
                        for i in range(audio.get_device_count()):
                            try:
                                d = audio.get_device_info_by_index(i)
                                if d.get('name') == target_name:
                                    return d
                            except Exception:
                                continue
                    # Fallback to system default
                    try:
                        return default()
                    except Exception:
                        pass
                    raise AudioError('The selected audio device could not be opened. Refresh devices in Settings > API keys.')

                if self.source not in ('pc', 'microphone', 'both'):
                    raise AudioError('Choose a recording source in Settings > API keys.')
                if self.source in ('pc', 'both'):
                    device = resolve_wasapi_loopback(audio, self.playback)
                    if device is not None and device.get('isLoopbackDevice'):
                        choices.append(('PC', device))
                    elif self.source == 'pc':
                        raise AudioError('Choose a PC playback loopback device.')
                    else:
                        self.skipped.append({'role': 'PC', 'reason': 'no_loopback'})
                if self.source in ('microphone', 'both'):
                    try:
                        device = selected_device(self.microphone, audio.get_default_input_device_info)
                        if device.get('isLoopbackDevice'):
                            if self.source == 'microphone':
                                raise AudioError('Choose a microphone input device.')
                            self.skipped.append({'role': 'Microphone', 'name': device.get('name'), 'reason': 'is_loopback'})
                        else:
                            # The playing Bluetooth headset's own microphone would end its stereo playback
                            is_clashing_bluetooth = (self.source == 'both' and bool(choices)
                                                     and same_headset(choices[0][1].get('name', ''),
                                                                      device.get('name', ''),
                                                                      hands_free_format(audio, device)))
                            if not is_clashing_bluetooth:
                                choices.append(('Microphone', device))
                            else:
                                self.skipped.append({'role': 'Microphone', 'name': device.get('name'),
                                                     'reason': 'bluetooth_clash'})
                    except Exception as error:
                        if self.source == 'microphone':
                            raise AudioError('Could not open the microphone. Check device permissions or select another recording device.') from error
                        self.skipped.append({'role': 'Microphone', 'reason': 'unavailable', 'error': type(error).__name__})
                if not choices:
                    logging.warning('[Audio] No active audio playback or microphone device found (source=%s)', self.source)
                    raise AudioError('No active audio playback or microphone device was found. Connect speakers, headphones, or a microphone, or check Audio devices in Settings > API keys.')
                active_choices, rates, stats = [], [], []
                for index, (label, device) in enumerate(choices):
                    check_cancel(self.cancelled)
                    if self.stop_event.is_set():
                        raise AudioCancelled('Recording stopped before the device was ready.')
                    channels = device_channels(device)
                    stored = stored_channels(channels)
                    rate = int(device['defaultSampleRate'])
                    file_idx = len(active_choices)
                    path = self.files.directory / f'{file_idx}.wav'
                    writer = wave.open(str(path), 'wb')
                    writer.setparams((stored, 2, rate, 0, 'NONE', 'not compressed'))

                    def callback(data, frames, timing, status, channel=file_idx, sample_rate=rate):
                        if self.stop_event.is_set():
                            return None, pa.paComplete
                        try:
                            packets.put_nowait((channel, data, max(0, self.clock() - self.started - frames / sample_rate)))
                        except queue.Full:
                            failed.set()
                        return None, pa.paContinue

                    try:
                        stream = audio.open(format=pa.paInt16, channels=channels, rate=rate,
                                            input=True, input_device_index=int(device['index']),
                                            frames_per_buffer=max(128, rate // 50),
                                            stream_callback=callback, start=False)
                        streams.append(stream)
                        writers.append(writer)
                        layouts.append((channels, stored))
                        rates.append(rate)
                        stats.append({'frames': 0, 'peak': 0.0, 'squares': 0.0, 'samples': 0})
                        active_choices.append((label, device))
                    except Exception as err:
                        logging.warning('[Audio] Could not open %s device %r (index=%s, channels=%s, rate=%s): %s: %s',
                                        label, device.get('name'), device.get('index'), channels, rate,
                                        type(err).__name__, err)
                        writer.close()
                        try:
                            path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        self.skipped.append({'role': label, 'name': device.get('name'), 'reason': 'open_failed',
                                             'error': type(err).__name__})
                        if len(choices) == 1:
                            if label == 'Microphone':
                                raise AudioError('Could not open the microphone. Check device permissions or select another recording device.') from err
                            raise AudioError('Could not open the recording device. Check the device and microphone permissions.') from err
                if not active_choices:
                    raise AudioError('Could not open the recording device. Check the device and microphone permissions.')
                self.started = self.clock()
                self.levels = {label: (0.0, self.started) for label, _ in active_choices}
                for stream in streams:
                    check_cancel(self.cancelled)
                    if self.stop_event.is_set():
                        raise AudioCancelled('Recording stopped before the device was ready.')
                    stream.start_stream()
                progress('recording', 0, 0)
                last_progress = self.clock()
                while not self.stop_event.is_set() or not packets.empty():
                    check_cancel(self.cancelled)
                    self.elapsed = min(self.limit, self.clock() - self.started)
                    if self.elapsed >= self.limit:
                        self.limit_reached = True
                        self.stop()
                    if failed.is_set():
                        raise AudioError('Audio capture lost samples. Check the device and try again.')
                    try:
                        index, data, offset = packets.get(timeout=0.05)
                        offsets.setdefault(index, offset)
                        data = mix_to_stored(data, *layouts[index])
                        writers[index].writeframesraw(data)
                        values = np.frombuffer(data, dtype='<i2').astype(np.float64)
                        self.level = min(1.0, math.sqrt(float(np.mean(values * values))) / 6000) if len(values) else 0
                        self.levels[active_choices[index][0]] = (self.level, self.clock())
                        if len(values):
                            stat = stats[index]
                            stat['frames'] += len(values) // layouts[index][1]
                            stat['peak'] = max(stat['peak'], float(np.max(np.abs(values))))
                            stat['squares'] += float(np.sum(values * values))
                            stat['samples'] += len(values)
                    except queue.Empty:
                        if not self.stop_event.is_set() and streams and all(not item.is_active() for item in streams):
                            raise AudioError('The recording device disconnected or stopped.')
                    if self.clock() - last_progress >= 0.1:
                        progress('recording', self.elapsed, self.level)
                        last_progress = self.clock()
                for stream in streams:
                    stream.stop_stream()
                    stream.close()
                streams.clear()
                for writer in writers:
                    writer.close()
                writers.clear()
                if self.elapsed < 0.25:
                    self._log_stop(active_choices, layouts, rates, stats)
                    raise AudioError('The recording was too short. Hold the shortcut a little longer.')
                for index, (label, device) in enumerate(active_choices):
                    src_wav = self.files.directory / f'{index}.wav'
                    mono_path = self.files.directory / f'{index}-mono.wav'
                    try:
                        if src_wav.exists() and src_wav.stat().st_size > 44:
                            decode_media(src_wav, mono_path, self.cancelled, self.limit + 1)
                            self.files.tracks.append(AudioTrack(str(mono_path), label, offsets.get(index, 0)))
                        else:
                            self.skipped.append({'role': label, 'name': device.get('name'), 'reason': 'no_audio'})
                    except AudioError as err:
                        self.skipped.append({'role': label, 'name': device.get('name'), 'reason': 'decode_failed',
                                             'error': type(err).__name__})
                        if len(active_choices) == 1:
                            self._log_stop(active_choices, layouts, rates, stats)
                            raise
                    except Exception as err:
                        self.skipped.append({'role': label, 'name': device.get('name'), 'reason': 'decode_failed',
                                             'error': type(err).__name__})
                        if len(active_choices) == 1:
                            self._log_stop(active_choices, layouts, rates, stats)
                            raise AudioError('The audio could not be decoded.') from err
                self._log_stop(active_choices, layouts, rates, stats)
                if not self.files.tracks:
                    raise AudioError('No speech was detected. Check the source and try again.')
                successful = True
                return self.files
        except AudioError:
            self.files.cleanup()
            raise
        except Exception as error:
            logging.warning('[Audio] Recording setup failed (source=%s): %s: %s', self.source, type(error).__name__, error)
            self.files.cleanup()
            raise AudioError('Could not open the recording device. Check the device and microphone permissions.') from error
        finally:
            for stream in streams:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            for writer in writers:
                writer.close()
            if not successful:
                self.files.cleanup()

    def _log_stop(self, active_choices, layouts, rates, stats):
        """What each device delivered: enough to tell a silent microphone from one that was never opened."""
        kept = {track.source for track in self.files.tracks}
        devices = []
        for index, (label, device) in enumerate(active_choices):
            stat = stats[index]
            rms = math.sqrt(stat['squares'] / stat['samples']) if stat['samples'] else 0.0
            devices.append({'role': label, 'name': device.get('name'), 'channels': layouts[index][0],
                            'stored': layouts[index][1], 'rate': rates[index], 'frames': stat['frames'],
                            'peak': round(stat['peak'] / 32768, 3), 'rms': round(rms / 32768, 4),
                            'track': label in kept})
        log_event('Audio', 'recording.stop', sid=self.sid, seconds=round(self.elapsed, 2),
                  limit_reached=self.limit_reached, devices=devices, skipped=self.skipped,
                  tracks=len(self.files.tracks))
