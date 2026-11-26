"""Frozen audio and Tcl/Tk runtime self-check. Never opens a device, profile, browser or network connection."""
import json
from pathlib import Path
import sys
import threading


def run(destination):
    from src.core.audio_media import AudioFiles, decode_media, read_pcm, RATE
    result = {'frozen': bool(getattr(sys, 'frozen', False)), 'errors': []}
    files = AudioFiles()
    try:
        import tkinter
        import av
        import numpy
        import pyaudiowpatch as pa
        tcl = tkinter.Tcl()
        tcl_library = tcl.eval('info library')
        result.update(av=av.__version__, numpy=numpy.__version__,
                      tcl_library=tcl_library,
                      portaudio_sample_bytes=pa.get_sample_size(pa.paInt16))
        sample = Path(__file__).resolve().parents[1] / 'assets' / 'audio_probe.wav'
        output = files.directory / 'decoded.wav'
        result['seconds'] = decode_media(sample, output, threading.Event())
        result['samples'] = len(read_pcm(output))
        result['codecs'] = sorted({'aac', 'mp3', 'flac', 'opus', 'pcm_s16le'} & av.codecs_available)
        if result['samples'] != 4 * RATE or result['portaudio_sample_bytes'] != 2 or len(result['codecs']) != 5:
            result['errors'].append('Audio runtime data or codecs are incomplete.')
        if not tcl_library:
            result['errors'].append('Tcl library initialization returned an empty path.')
    except Exception as error:
        result['errors'].append(str(error))
    finally:
        files.cleanup()
    Path(destination).write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 1 if result['errors'] else 0
