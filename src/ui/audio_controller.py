"""UI-owned audio workflow. Capture, decoding, recognition and translation run on workers."""
from pathlib import Path
import logging
import queue
import hashlib
import threading
import time
import tkinter as tk
from tkinter import filedialog

from src.core.audio_media import (AudioError, AudioCancelled, AudioFiles, AudioTrack, CaptureSession,
                                 FILE_LIMIT, check_cancel, is_cancelled, decode_media)
from src.core.audio_service import AudioRecognizer, resolve_audio_source_rows, text_chunks
from src.core.translation_task import TranslationTasks, TranslationStopped
from src.ui import components as ui
from src.ui.i18n import tr
from src.ui.settings.audio_settings import SOURCE_OPTIONS
from src.utils.log_events import log_event, new_session_id
from src.utils.ui_helpers import schedule_window_callback


class AudioController:
    def __init__(self, app):
        self.app, self.root, self.config = app, app.root, app.config
        self.events = queue.Queue()
        self.generation = 0
        self.cancelled = threading.Event()
        self.capture = None
        self.busy = False
        self.main_owner = None
        self.files = None
        self.recognizer = None
        self.transcript = ''
        self.target = ''
        self.dialog = None
        self.rendering = False
        self.state = 'idle'
        self.result_mode = 'text'
        self._thread = None
        self._task = None
        self._closed = False
        self._chord = None
        self._released = False
        self._armed = True
        self._playing = False
        self._on_done = None
        self._file_recognizers = {}
        self._translation_cache = {}
        self.text_service = None
        self._pump_scheduled = False
        self._session = None     # the log session of the running recording, file or retry
        self.trigger = 'hold'    # what started the recording: the held shortcut, or the audio panel
        self.watchers = []       # callables(phase, info) - the audio panel follows every recording here
        self.result_anchor = None    # () -> the audio panel's (left, top, width, height), or None when closed
        self.front_window = None     # () -> the open audio panel's window, which questions must open above
        app.quick_translate_manager.audio_close_handler = self.request_close
        self.root.bind('<Destroy>', self._destroyed, add='+')

    def _destroyed(self, event):
        if event.widget is self.root:
            self.shutdown()

    def _open_session(self, kind, recognizer, **fields):
        """Start the log session of one recording, file or retry; its id is on every line it causes."""
        self._close_session('replaced')
        sid = new_session_id()
        recognizer.sid = sid
        self._session = {'sid': sid, 'kind': kind, 'started': time.monotonic(), 'timings': {},
                         'recognizer': recognizer}
        log_event('Audio', 'session.start', sid=sid, kind=kind, mode_requested=getattr(recognizer, 'mode', ''),
                  keys_mode='manual' if getattr(recognizer, 'manual_mode', False) else 'auto',
                  keys=len(getattr(recognizer, 'rows', None) or []), target=self.target, **fields)
        return self._session

    def _close_session(self, outcome, result=None, error=None):
        """One line that says how the session ended, how long each step took and which mode ran."""
        session, self._session = self._session, None
        if not session:
            return
        recognizer = session['recognizer']
        fields = dict(kind=session['kind'], outcome=outcome,
                      total_ms=round((time.monotonic() - session['started']) * 1000), **session['timings'],
                      mode_used=getattr(recognizer, 'mode_used', ''), mode_reason=getattr(recognizer, 'mode_reason', ''))
        if result is not None:
            transcript, translated, attribution = result
            fields.update(transcript_chars=len(transcript or ''), translation_chars=len(str(translated or '')),
                          models=attribution or '')
        if error is not None:
            fields.update(error=type(error).__name__, message=str(error))
        log_event('Audio', 'session.done', level=logging.WARNING if outcome == 'error' else logging.INFO,
                  sid=session['sid'], **fields)

    @property
    def phase(self):
        """What a control can offer now: 'idle', 'preparing', 'recording' or 'processing'."""
        if not self.busy:
            return 'idle'
        if self.capture is not None and self.state in ('preparing', 'recording'):
            return self.state
        return 'processing'

    def _notify(self, phase, **info):
        """Tell the watchers; one that fails must not stop the recording or the others."""
        if self._closed:
            return
        for watcher in list(self.watchers):
            try:
                watcher(phase, info)
            except Exception as error:
                logging.warning('[Audio] A watcher failed: %s', type(error).__name__)

    def stop_recording(self):
        """Stop recording and translate what was recorded (the panel's Stop; the held shortcut stops itself)."""
        if self.capture is not None and self.state in ('preparing', 'recording'):
            self.capture.stop()

    def cancel_from_panel(self):
        """The panel's Cancel: stop the recording or its processing. A recording already made gets the usual
        save question (Settings > Save recordings), as when its status popup is closed."""
        if not self.busy:
            return
        if self.main_owner is not None:
            self.cancel()
        else:
            self.request_close('cancel')

    def toggle_recording(self):
        """Record, or stop a recording; ignored while a recording is being recognized or translated."""
        phase = self.phase
        if phase in ('preparing', 'recording'):
            self.stop_recording()
        elif phase == 'idle':
            self.start_recording('panel')

    def _ensure_pump(self):
        if not self._pump_scheduled and not self._closed:
            self._pump_scheduled = True
            schedule_window_callback(self.root, self.root, 30, self._pump)

    def _recognizer(self):
        from src.core.translation import TranslationService
        text_keys = self.config.get_api_keys()
        if any(k.get('api_key') for k in text_keys):
            self.text_service = getattr(self.app, 'translation_service', None) or TranslationService(self.config)
        else:
            self.text_service = TranslationService(self.config)
        mode_source, source_rows = resolve_audio_source_rows(self.config)
        mode = self.config.get('audio_mode', 'two_step')
        target_lang = self.target or self.config.get('audio_target_language', 'Auto')
        if target_lang == 'Auto':
            target_lang = getattr(self.app, 'selected_language', 'Vietnamese')
        usage_cb = getattr(self.app.translation_service, 'key_usage_events', None).put if hasattr(self.app, 'translation_service') else None
        return AudioRecognizer(
            source_rows,
            self.config.get('audio_model', 'whisper-large-v3'),
            usage=usage_cb,
            config=self.config,
            mode=mode,
            target_language=target_lang,
            manual_mode=(mode_source == 'manual'),
        )

    def start_recording(self, trigger='hold'):
        """Record from the configured source. `trigger` 'hold' follows the held shortcut and stops when every key
        is released; 'panel' records until the audio panel's Stop, the 3-minute limit or Cancel."""
        if trigger == 'panel':
            self._armed = True
        if not self._armed:
            if not self.busy and (self._chord is None or not any(self.app.hotkey_manager.audio_keys_down(self._chord))):
                self._armed = True
                self._released = True
                self._chord = None
            else:
                return
        if self.busy or self.dialog is not None or (self._thread is not None and self._thread.is_alive()):
            self.app.toast.show_warning('Audio translation is already running.')
            return
        if self.files is not None:
            # A held shortcut is up by the time the save question is answered, so only the panel records after it.
            if trigger == 'panel':
                continuation = lambda: self.start_recording('panel')
            else:
                continuation = self.start_recording if self.config.get('audio_save_policy', 'ask') == 'never' else None
            self.request_close('replace', continuation)
            return
        if self.config.get('audio_enabled', True) is not True:
            self.app.toast.show_warning('Audio translation is disabled. Enable it in Settings > API keys.')
            return
        # The target first: the recognizer translates into it in one-step mode.
        self.target = self._audio_target()
        self.recognizer = self._recognizer()
        if not self._has_keys(self.recognizer):
            return
        self._ensure_pump()
        self.app._translation_tasks().cancel()
        self.transcript = ''
        self.result_mode = 'text'
        self._translation_cache = {}
        self.main_owner = None
        self.trigger = trigger
        if trigger == 'panel':
            self._chord, self._released, self._armed = None, True, True
        else:
            self._chord = self.config.get('audio_hotkey', 'win+alt+a')
            self._released = False
            self._armed = False
        self.capture = CaptureSession(self.config.get('audio_source', 'pc'),
                                      self.config.get('audio_microphone'), self.config.get('audio_playback'))
        session = self._open_session('recording', self.recognizer, source=self.capture.source,
                                     denoise=bool(self.config.get('audio_denoise', True)), trigger=trigger)
        self.capture.sid = session['sid']
        if trigger == 'hold':
            self.app.quick_translate_manager.capture_mouse_position()
            self.app.quick_translate_manager.show_audio_status('Preparing recording…', self.target)
        self.state = 'preparing'
        self._notify('preparing', source=self.capture.source, target=self.target)
        capture, recognizer, denoise = self.capture, self.recognizer, self.config.get('audio_denoise', True)
        timings = session['timings']
        self._begin(lambda cancelled, progress: self._capture_work(capture, recognizer, cancelled, progress, denoise,
                                                                   timings),
                    self._quick_done)

    def _audio_target(self):
        target = self.config.get('audio_target_language', 'Auto')
        return self.app.selected_language if target == 'Auto' else target

    def _has_keys(self, recognizer):
        """Say why nothing can be recognized, when no key can do it."""
        if recognizer.rows:
            return True
        if getattr(recognizer, 'manual_mode', False):
            logging.warning('[Audio] Manual audio key unavailable or failed verification')
            self.app.toast.show_warning('Your manual audio key failed or is unsupported. Replace it or switch Audio to Auto in Settings > API keys.')
        else:
            logging.warning('[Audio] No eligible audio key available in Auto mode')
            self.app.toast.show_warning('Add a working API key in Settings > API keys to translate audio.')
        return False

    def _capture_work(self, capture, recognizer, cancelled, progress, denoise, timings=None):
        timings = {} if timings is None else timings
        files = None
        try:
            files = capture.run(progress)
            timings['record_s'] = round(getattr(capture, 'elapsed', 0.0), 2)
            check_cancel(cancelled)
            progress('captured', files, capture.limit_reached)
            started = time.monotonic()
            transcript = recognizer.transcribe(files.tracks, cancelled, progress, denoise)
            timings['stt_ms'] = round((time.monotonic() - started) * 1000)
            progress('transcript', transcript, 0)
            if getattr(recognizer, 'mode', '') == 'one_step' and getattr(recognizer, 'one_step_translation', ''):
                translated = recognizer.one_step_translation
                attribution = recognizer.last_model
            else:
                started = time.monotonic()
                translated, attribution = self._translate(transcript, self.target, cancelled, progress)
                timings['translate_ms'] = round((time.monotonic() - started) * 1000)
            return transcript, translated, attribution
        finally:
            if is_cancelled(cancelled) and files is not None and files is not self.files:
                files.cleanup()

    def translate_file(self, path, target, owner, custom_prompt='', source_language=None):
        if self.busy or self.dialog is not None or (self._thread is not None and self._thread.is_alive()):
            self.app.toast.show_warning('Audio translation is already running.')
            return
        if self.files is not None:
            self.request_close('replace', lambda: self.translate_file(path, target, owner, custom_prompt, source_language))
            return
        try:
            stat = Path(path).stat()
        except OSError:
            self.app.toast.show_warning('File no longer exists.')
            return
        self.main_owner, self.target = owner, target
        self._upload_path = path
        self._upload_draft = self.app.original_text.get('1.0', 'end-1c')
        self.result_mode = 'custom' if custom_prompt else 'text'
        self.transcript = ''
        self._chord = None
        identity = (str(Path(path).resolve()), stat.st_size, stat.st_mtime_ns,
                    self.config.get('audio_model', 'whisper-large-v3'))
        previous = self._file_recognizers.get(identity)
        if previous is None:
            self._translation_cache = {}
        recognizer = self._recognizer()
        if previous is not None:
            recognizer.cache = previous.cache
        if source_language:
            from src.constants import LANGUAGES
            recognizer.language = next((code for name, code, native in LANGUAGES if name == source_language), '')
        self._file_recognizers = {identity: recognizer}
        self.app._translation_tasks().cancel()
        self.app._main_pending_source = ''
        self.app._start_translate_animation(label='Recognizing speech')
        self.state = 'decoding'
        # The file by its extension and size only: its name is the user's (Decision 86)
        kind_of_file, megabytes = Path(path).suffix.lower(), round(stat.st_size / 1048576, 2)
        session = self._open_session('file', recognizer, ext=kind_of_file, mb=megabytes,
                                     reused_transcripts=previous is not None, custom_prompt=bool(custom_prompt))
        self._begin(lambda cancelled, progress: self._file_work(
            path, recognizer, target, session, cancelled, progress, custom_prompt, source_language), self._main_done)

    def translate_file_from_panel(self, path):
        """An audio or video file chosen on the panel: recognized and translated like a recording, with the result in
        the quick popup under the panel (Decision 88). The file is read, never changed; its decoded audio is not
        kept, so there is no Play and no save question."""
        if self.busy or self.dialog is not None or (self._thread is not None and self._thread.is_alive()):
            self.app.toast.show_warning('Audio translation is already running.')
            return
        if self.files is not None:
            self.request_close('replace', lambda: self.translate_file_from_panel(path))
            return
        if self.config.get('audio_enabled', True) is not True:
            self.app.toast.show_warning('Audio translation is disabled. Enable it in Settings > API keys.')
            return
        try:
            stat = Path(path).stat()
        except OSError:
            self.app.toast.show_warning('File no longer exists.')
            return
        self.target = self._audio_target()
        recognizer = self._recognizer()
        if not self._has_keys(recognizer):
            return
        self._ensure_pump()
        self.app._translation_tasks().cancel()
        self.recognizer, self.transcript, self.result_mode = recognizer, '', 'text'
        self._translation_cache, self._file_recognizers = {}, {}
        self.main_owner, self.trigger = None, 'panel'
        self._chord, self._released, self._armed = None, True, True
        self.state = 'decoding'
        session = self._open_session('file', recognizer, ext=Path(path).suffix.lower(),
                                     mb=round(stat.st_size / 1048576, 2), reused_transcripts=False,
                                     custom_prompt=False, trigger='panel')
        self._notify('processing', stage='decoding', done=0, total=0)
        target = self.target
        self._begin(lambda cancelled, progress: self._file_work(path, recognizer, target, session, cancelled,
                                                                progress), self._quick_done)

    def _file_work(self, path, recognizer, target, session, cancelled, progress, custom_prompt='',
                   source_language=None):
        """Decode the file's first audio track locally (up to 30 minutes), recognize it and translate it."""
        sid, timings = session['sid'], session['timings']
        # The file by its extension and size only: its name is the user's (Decision 86)
        kind_of_file = Path(path).suffix.lower()
        megabytes = round(Path(path).stat().st_size / 1048576, 2)
        files = AudioFiles()
        try:
            output = files.directory / 'upload.wav'
            progress('decoding', 0, FILE_LIMIT)
            started = time.monotonic()
            seconds = decode_media(path, output, cancelled, FILE_LIMIT, progress)
            timings['decode_ms'] = round((time.monotonic() - started) * 1000)
            log_event('Audio', 'file.decode', sid=sid, ext=kind_of_file, mb=megabytes,
                      seconds=round(seconds or 0, 1), ms=timings['decode_ms'])
            started = time.monotonic()
            transcript = recognizer.transcribe([AudioTrack(str(output), 'File')], cancelled, progress)
            timings['stt_ms'] = round((time.monotonic() - started) * 1000)
            progress('transcript', transcript, 0)
            if getattr(recognizer, 'mode', '') == 'one_step' and getattr(recognizer, 'one_step_translation', ''):
                translated = recognizer.one_step_translation
                attribution = recognizer.last_model
            else:
                started = time.monotonic()
                translated, attribution = self._translate(transcript, target, cancelled, progress,
                                                           custom_prompt, source_language)
                timings['translate_ms'] = round((time.monotonic() - started) * 1000)
            return transcript, translated, attribution
        finally:
            files.cleanup()

    def _translate(self, transcript, target, cancelled, progress, custom_prompt='', source_language=None, raw_prompt=None):
        results, credit = [], []
        chunks = [raw_prompt] if raw_prompt is not None else list(text_chunks(transcript))
        for index, part in enumerate(chunks, 1):
            check_cancel(cancelled)
            progress('translating', index, len(chunks))
            cache_key = hashlib.sha256(repr((part, target, custom_prompt, source_language, raw_prompt)).encode()).digest()
            if cache_key in self._translation_cache:
                result, who = self._translation_cache[cache_key]
                results.append(result)
                if who and who not in credit:
                    credit.append(who)
                continue
            self._task = TranslationTasks().begin()
            try:
                with self._task.activate():
                    result = self.text_service._call_model(part) if raw_prompt is not None else self.text_service.translate_text(
                        part, target, custom_prompt or None, source_language=source_language,
                        allow_linked=len(chunks) == 1, save_history=False)
                    self._task.check()
                if str(result).startswith('Error:'):
                    raise AudioError(str(result)[6:].strip())
                results.append(result)
                who = self._task.attribution or self.text_service.last_attribution
                self._translation_cache[cache_key] = result, who
                if who and who not in credit:
                    credit.append(who)
            except TranslationStopped:
                check_cancel(cancelled)
                raise AudioError('Text translation timed out. The recognized text is still available.') from None
            finally:
                self._task = None
        check_cancel(cancelled)
        return (results[0] if len(results) == 1 else '\n\n'.join(map(str, results))), ', '.join(credit)

    def _begin(self, operation, on_done):
        self.generation += 1
        generation = self.generation
        self.cancelled = cancelled = threading.Event()
        self.busy = True
        self._on_done = on_done

        def progress(*args):
            self.events.put((generation, 'progress', args))

        def worker():
            try:
                result = operation(cancelled, progress)
                self.events.put((generation, 'done', (result, None)))
            except Exception as error:
                self.events.put((generation, 'done', (None, error)))
        self._thread = threading.Thread(target=worker, daemon=True, name='AudioWorkflow')
        self._thread.start()
        self._ensure_pump()

    def _pump(self):
        self._pump_scheduled = False
        if self._closed:
            return
        if self.busy and self.main_owner is not None:
            if not self.main_owner.winfo_exists():
                self.cancel()
        if self._chord is not None and not self._released:
            if not self.app.hotkey_manager.user32.GetForegroundWindow():
                self.cancel()
                self._released, self._armed, self._chord = True, True, None
            elif not any(self.app.hotkey_manager.audio_keys_down(self._chord)):
                self._released, self._armed, self._chord = True, True, None
                if self.capture:
                    self.capture.stop()
        for _ in range(150):
            try:
                generation, kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation or self.cancelled.is_set():
                if kind == 'progress' and payload[0] == 'captured':
                    payload[1].cleanup()
                continue
            if kind == 'progress':
                self._progress(*payload)
            else:
                self.busy = False
                self.capture = None
                if self._on_done:
                    self._on_done(*payload)
        is_active = (self.busy
                     or (self._chord is not None and not self._released)
                     or not self.events.empty()
                     or (self._thread is not None and self._thread.is_alive()))
        if is_active and not self._closed:
            self._ensure_pump()

    def _progress(self, stage, value, total):
        self.state = stage
        if stage == 'captured':
            self.files = value
            if total:
                self.app.toast.show_warning('The 3-minute recording limit was reached. Translating the recording.')
            self._notify('processing', stage='captured', done=0, total=0)
            return
        if stage == 'transcript':
            self.transcript = value
            return
        if stage == 'recording':
            capture = self.capture
            self._notify('recording', elapsed=float(value), limit=float(getattr(capture, 'limit', 180) or 180),
                         levels=capture.current_levels() if hasattr(capture, 'current_levels') else {})
        else:
            self._notify('processing', stage=stage, done=value, total=total)
        if self.main_owner is not None:
            label = 'Translating audio: {done}/{total}' if stage == 'translating' else 'Recognizing speech: {done}/{total}'
            self.app._translate_status = tr(label, done=int(value), total=int(total))
            self.app._view.set_status(self.app._translate_status)
            return
        if self.trigger == 'panel':
            return              # the panel shows the recording; no status popup is opened for it
        if stage == 'recording':
            source = dict(SOURCE_OPTIONS).get(self.capture.source if self.capture else 'pc', 'PC audio')
            self.app.quick_translate_manager.update_audio_status(
                tr('Recording · {input_name} · {time}', input_name=tr(source),
                   time=f'{int(value) // 60:02d}:{int(value) % 60:02d}'),
                'Release all shortcut keys to translate', total)
        elif stage in ('transcribing', 'translating'):
            label = 'Translating audio: {done}/{total}' if stage == 'translating' else 'Recognizing speech: {done}/{total}'
            self.app.quick_translate_manager.update_audio_status(tr(label, done=int(value), total=int(total)),
                                                                   'You can cancel while processing.')

    def _history(self, result):
        transcript, translated, attribution = result
        self.app.translation_service.history_manager.add_entry(
            transcript, translated, self.target, model_used=attribution, source_type=self.result_mode,
            **self.app.translation_service._alignment_history_args(translated))

    def _quick_done(self, result, error):
        self.state = 'error' if error else 'result'
        outcome = 'cancelled' if isinstance(error, AudioCancelled) else 'error' if error else 'ok'
        self._close_session(outcome, result, error)
        self._notify('idle', outcome=outcome, message=str(error) if error else '')
        if self.trigger == 'panel':
            self._anchor_result()
        self.rendering = True
        try:
            if error:
                if isinstance(error, AudioCancelled):
                    early_release = (
                        not is_cancelled(self.cancelled)
                        and 'before the device was ready' in str(error).lower()
                    )
                    self.app.quick_translate_manager.close()
                    if early_release and self.trigger == 'hold' and hasattr(self.app, 'toast'):
                        self.app.toast.show_info('Hold the shortcut while speaking, then release all keys to translate.')
                    return
                from src.core.result_status import FailureText
                err_notice = FailureText('Error: ' + tr(str(error)), raw_reason='Error: ' + str(error))
                self.app.show_quick_translate(self.transcript, err_notice, self.target,
                                               model_info='', audio_result=True,
                                               on_audio_play=self.play if self.files else None)
            else:
                from src.core.result_status import mark_translation
                self._history(result)
                self.app.show_quick_translate(result[0], mark_translation(result[1]), self.target, model_info=result[2],
                                               audio_result=True, on_audio_play=self.play if self.files else None)
        finally:
            self.rendering = False

    def _anchor_result(self):
        """A panel recording's result opens directly under the panel (Decision 88); with the panel gone, at the
        cursor."""
        quick = self.app.quick_translate_manager
        rect = self.result_anchor() if self.result_anchor else None
        if rect:
            quick.anchor_below(rect)
        else:
            quick.capture_mouse_position()

    def _main_done(self, result, error):
        outcome = 'cancelled' if isinstance(error, AudioCancelled) else 'error' if error else 'ok'
        self._close_session(outcome, result, error)
        self._notify('idle', outcome=outcome, message=str(error) if error else '')
        owner, self.main_owner = self.main_owner, None
        self.app._main_pending_source = None
        self.app._stop_translate_animation()
        if owner is not self.app.popup or not owner.winfo_exists():
            return
        if error:
            transcript = self.transcript
            self.app._view.show_error(tr(str(error)), action='Use recognized text' if transcript else None,
                                      on_action=(lambda: self._use_transcript(transcript)) if transcript else None)
            return
        self._history(result)
        from src.core.result_status import mark_translation
        if self.app.original_text.get('1.0', 'end-1c') != self._upload_draft:
            self.app.show_quick_translate(result[0], mark_translation(result[1]), self.target,
                                          model_info=result[2], audio_result=True)
            self.app.toast.show_warning('Audio translation finished. The draft changed; the result is in a quick popup.')
            return
        if self.app.attachment_area:
            self.app.attachment_area._remove_item(self._upload_path)
        self.app._update_translation_with_original(mark_translation(result[1]), result[0], source_text=result[0])

    def _use_transcript(self, transcript):
        if self.app.original_text.get('1.0', 'end-1c') == self._upload_draft:
            if self.app.attachment_area:
                self.app.attachment_area._remove_item(self._upload_path)
            self.app._update_translation_with_original('', transcript, source_text=transcript)

    def retry(self, source_language=None, raw_prompt=None):
        if (self.busy or self.files is None or self.dialog is not None
                or (self._thread is not None and self._thread.is_alive())):
            return
        if self.state == 'result':
            self._translation_cache = {}
        previous = self.recognizer
        self.recognizer = self._recognizer()
        if previous is not None:
            self.recognizer.cache = previous.cache
            self.recognizer.language = previous.language
        self.app.quick_translate_manager.show_audio_status('Recognizing speech…', self.target)
        self.trigger = 'retry'          # asked from the result popup: its status popup shows the progress
        files, recognizer, transcript = self.files, self.recognizer, self.transcript
        self.result_mode = 'custom' if raw_prompt is not None else 'text'
        denoise = self.config.get('audio_denoise', True)
        timings = self._open_session('retry', recognizer, reused_transcript=bool(transcript),
                                     custom_prompt=raw_prompt is not None)['timings']

        def work(cancelled, progress):
            started = time.monotonic()
            text = transcript or recognizer.transcribe(files.tracks, cancelled, progress, denoise)
            if not transcript:
                timings['stt_ms'] = round((time.monotonic() - started) * 1000)
            progress('transcript', text, 0)
            started = time.monotonic()
            translated, attribution = self._translate(text, self.target, cancelled, progress,
                                                       source_language=source_language, raw_prompt=raw_prompt)
            timings['translate_ms'] = round((time.monotonic() - started) * 1000)
            return text, translated, attribution
        self._begin(work, self._quick_done)

    def cancel(self):
        was_busy = self.busy
        self._close_session('cancelled')
        self.cancelled.set()
        self.generation += 1
        self.busy = False
        self._released = True
        self._armed = True
        self._chord = None
        if self.capture is not None:
            self.capture.cancel()
        if self._task is not None:
            self._task.cancel()
        self.capture = None
        if self.main_owner is not None:
            self.app._main_pending_source = None
            self.app._stop_translate_animation()
            self.main_owner = None
        if was_busy:
            self._notify('idle', outcome='cancelled', message='')

    def play(self):
        if self.files is None or self.busy:
            return
        files = self.files

        def playback():
            import winsound
            path = files.directory / 'playback.wav'
            try:
                files.save(path)
                if files is not self.files:
                    return
                self._playing = True
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            except (OSError, AudioError, RuntimeError):
                pass
        threading.Thread(target=playback, daemon=True, name='AudioPlayback').start()

    def request_close(self, reason='close', continuation=None):
        if self.dialog is not None:
            return True
        if self.main_owner is not None:
            return False
        self.cancel()
        if self.files is None:
            self.app.quick_translate_manager.close()
            if continuation:
                continuation()
            return True
        policy = self.config.get('audio_save_policy', 'ask')
        if policy == 'never':
            self._discard(continuation)
            return True
        if policy == 'always':
            self._save_then_close(continuation)
            return True
        remember = tk.BooleanVar(master=self.root, value=False)

        def choose(save):
            if remember.get():
                self.config.set('audio_save_policy', 'always' if save else 'never')
            if self.dialog:
                self.dialog.close()
                self.dialog = None
            if save:
                self._save_then_close(continuation)
            else:
                self._discard(continuation)

        self.dialog = ui.Dialog(self.root, 'Save this recording?',
                                'Keep a WAV copy of this recording? No discards the temporary audio. Translation history keeps text only.',
                                on_cancel=lambda: choose(False))
        ui.Toggle(self.dialog.body, "Don't ask again", variable=remember).pack(anchor='w', pady=(ui.get().px(12), 0))
        self.dialog.add_button('No', lambda: choose(False), default=True, close=False)
        self.dialog.add_button('Yes', lambda: choose(True), close=False)
        rect = self.result_anchor() if self.result_anchor else None
        if rect:
            self.dialog.stay_in_front(rect)        # the panel is always on top; the question must be above it
        self.dialog.show()
        return True

    def _save_then_close(self, continuation):
        panel = self.front_window() if self.front_window else None
        destination = filedialog.asksaveasfilename(parent=panel or self.root, title=tr('Save recording'),
                        defaultextension='.wav', filetypes=[(tr('WAV audio'), '*.wav')],
                        initialfile=time.strftime('CrossTrans2-%Y%m%d-%H%M%S.wav'))
        if not destination:
            self._discard(continuation)
            return
        files = self.files
        try:
            files.save(destination)
        except (OSError, AudioError):
            self.app.toast.show_warning('Could not save the recording. Try another location.')
            return
        self._discard(continuation)

    def _discard(self, continuation=None):
        if self._playing:
            try:
                import winsound
                winsound.PlaySound(None, 0)
            except (ImportError, RuntimeError):
                pass
            self._playing = False
        if self.files:
            self.files.cleanup()
            self.files = None
        self.transcript = ''
        self.recognizer = None
        self.app.quick_translate_manager.set_audio_result(False)
        self.app.quick_translate_manager.close()
        if continuation:
            continuation()

    def shutdown(self):
        self._closed = True
        self.cancel()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self.dialog:
            self.dialog.close()
            self.dialog = None
        if self.files:
            self.files.cleanup()
            self.files = None
        while True:
            try:
                generation, kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress' and payload[0] == 'captured':
                payload[1].cleanup()
