"""Matched production Tk fixture worker; never launches on the user's desktop."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time


class TkScenario:
    def __init__(self, app, services, recorder, workload, arguments):
        self.app, self.services, self.recorder, self.workload, self.arguments = app, services, recorder, workload, arguments
        self.root = app.root
        self.started = time.perf_counter()
        self.phase, self.deadline, self.index, self.cycle = 'prepare', 0, 0, 0
        self.answers = queue.SimpleQueue()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='BaselineCore')
        self.captures, self.findings, self.cycle_samples = [], [], []
        self.outcome = self.page = None
        self.timer = None
        self._last_tick = None
        from src.ui import components
        self.scale = components.get().scale
        self.callback_errors = []

    def start(self):
        self.root.bind_all('<Expose>', lambda event: self.recorder.opened('quick', 'tk-Expose-notification')
                           if event.widget is self.app.quick_translate_manager.popup else None, add='+')
        self.timer = self.root.after(1, self._tick)

    def _later(self, phase, seconds):
        self.phase, self.deadline = phase, time.perf_counter() + seconds

    def _prepare(self):
        from src.core.translation_task import TranslationTasks
        from tools.tk_resize.services import Attachment, TranslationIntent
        app, workload = self.app, self.workload
        from src.ui.components.dialog import work_area
        left, top, right, bottom = work_area()
        self.app.popup.geometry(f'{min(round(1000 * self.scale), right - left - round(40 * self.scale))}x'
                                f'{min(round(760 * self.scale), bottom - top - round(60 * self.scale))}')
        self.task = TranslationTasks().begin()
        attachments = ()
        if workload.attachment:
            path = self.recorder.directory / 'fixtures/notes.md'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'# Meeting\r\nHello world\n')
            attachment = Attachment.admit(path)
            attachments = (attachment,)
            app.attachment_area.add_file(str(path))
        intent = TranslationIntent(workload.source, workload.target, workload.source_language,
            attachments=attachments, linked=workload.linked, fixture_delay=0)
        if workload.source or attachments:
            def operation():
                try:
                    self.answers.put(self.services.execute(self.task, intent))
                except Exception as error:
                    self.answers.put(error)
            self.worker.submit(operation)
            self._later('answer', 0)
        else:
            self._later('loaded', .2)

    def _show(self):
        app, workload = self.app, self.workload
        if workload.surface in ('quick', 'both'):
            self._open_quick()
            if workload.surface == 'quick':
                app.popup.withdraw()
        elif workload.surface == 'hidden':
            app.popup.withdraw()
        elif workload.surface == 'history':
            self.page = app._open_history()
        elif workload.surface == 'dictionary':
            self.page = app._open_home_dictionary()
            boxes = self.page.custom_boxes
            if boxes is None:
                raise RuntimeError('The configured Dictionary word selector was not created')
            for _ in range(max(0, workload.phrase_count - len(boxes._boxes))):
                previous = len(boxes._boxes)
                boxes._add_box_at(len(boxes._boxes) - 1)
                if len(boxes._boxes) != previous + 1:
                    raise RuntimeError('Dictionary did not admit the requested phrase count')
            for index, box in enumerate(boxes._boxes):
                box.text_widget.insert('1.0', 'Fixture phrase ' + str(index + 1))
        if workload.surface != 'hidden':
            app.popup.bind('<Configure>', lambda event: self.recorder.geometry('main', event.width, event.height)
                           if event.widget is app.popup else None, add='+')

    def _open_quick(self):
        manager = self.app.quick_translate_manager
        manager._last_mouse_x, manager._last_mouse_y = 500, 300
        result = self.outcome.text if self.outcome else ''
        started = time.perf_counter()
        self.recorder.opening('quick')
        manager.show(result, self.workload.target, original=self.workload.source,
                     source_lang=self.workload.source_language)
        self.recorder.open_call('quick', time.perf_counter() - started)
        manager.popup.bind('<Configure>', lambda event: self.recorder.geometry('quick', event.width, event.height)
                           if event.widget is manager.popup else None, add='+')

    def _describe(self):
        from tkinter.font import Font
        from tools.tk_resize.scenarios import content_identity
        inline = getattr(self.app, '_inline_reading', None)
        source_segments = getattr(inline, 'segments', ())
        result_segments = getattr(self.app.trans_text, '_segments', ())
        links = getattr(getattr(self.app, '_main_linked', None), 'controller', None)
        metadata = getattr(self.outcome.text, 'alignment', None) if self.outcome else None
        main = self.app.popup
        quick = self.app.quick_translate_manager.popup
        editor_geometry = {}
        for side, widget in [('source', self.app.original_text), ('result', self.app.trans_text)]:
            font = Font(root=widget, font=widget.cget('font'))
            size = font.actual('size')
            editor_geometry[side] = dict(width=widget.winfo_width(), height=widget.winfo_height(),
                font_family=font.actual('family'), font_pixels=abs(size) if size < 0 else size * widget.winfo_fpixels('1p'))
        source, result = self.app.original_text.get('1.0', 'end-1c'), self.app.trans_text.get_plain()
        dictionary = self.page if self.workload.surface == 'dictionary' else None
        boxes = getattr(dictionary, 'custom_boxes', None)
        selector = getattr(dictionary, 'word_frame', None)
        identity = content_identity(source, result,
            dictionary_context=getattr(dictionary, 'selection_context', '') or '',
            words=[item.word for item in selector.word_labels] if selector else (),
            phrases=boxes.get_all_phrases() if boxes else (), dictionary_rows=len(boxes._boxes) if boxes else 0,
            basic_modes=self.app.config.get_nlp_basic_mode())
        return dict(self.workload.descriptor(), **identity, result_chars=len(result),
            source_ruby_segments=sum(bool(segment.ruby) for segment in source_segments),
            result_ruby_segments=sum(bool(segment.ruby) for segment in result_segments),
            source_annotation_reason='annotation-limit' if len(self.workload.source) > 3000 and self.workload.furigana else
                'disabled' if not self.workload.furigana else 'annotated' if any(segment.ruby for segment in source_segments) else 'no-reading',
            actual_link_groups=len(metadata['groups']) if metadata else 0,
            actual_ui_link_groups=len(links.groups) if links else 0,
            editor_geometry=editor_geometry,
            history_count=len(self.services.history_snapshot()),
            main_geometry=[main.winfo_x(), main.winfo_y(), main.winfo_width(), main.winfo_height()],
            quick_geometry=[quick.winfo_x(), quick.winfo_y(), quick.winfo_width(), quick.winfo_height()] if quick else None,
            tcl_patchlevel=str(self.root.tk.call('info', 'patchlevel')), tk_scaling=float(self.root.tk.call('tk', 'scaling')))

    def _capture(self, suffix):
        from tools.ui_gallery import capture, is_blank
        windows = [('main', self.app.popup), ('quick', self.app.quick_translate_manager.popup)]
        for name, window in windows:
            if window is None or not window.winfo_exists() or not window.winfo_ismapped():
                continue
            path = self.recorder.directory / f'{name}-{suffix}.png'
            if not capture(int(window.wm_frame(), 16), str(path)):
                self.findings.append(dict(kind='capture-unavailable', window=name, state=suffix))
            else:
                self.captures.append(dict(window=name, state=suffix, file=path.name))
                if is_blank(str(path)):
                    self.findings.append(dict(kind='capture-blank', window=name, state=suffix))

    def _resize(self):
        windows = [('main', self.app.popup), ('quick', self.app.quick_translate_manager.popup)]
        from src.ui.components.dialog import work_area
        left, top, right, bottom = work_area()
        scale = self.scale
        for name, window in windows:
            if window is None or not window.winfo_exists() or not window.winfo_ismapped():
                continue
            if self.index == 0:
                self.recorder.resize(name, True, 'scripted-geometry')
            fraction = self.index / 79
            width = round((1050 - 370 * abs(2 * fraction - 1)) * scale)
            height = round((790 - 110 * abs(2 * fraction - 1)) * scale)
            width = min(width, right - left - round(40 * scale))
            height = min(height, bottom - top - round(60 * scale))
            self.recorder.size_request(name, width, height, 'scripted-geometry')
            window.geometry(f'{width}x{height}')
        self.index += 1
        if self.index >= 80:
            for name, window in windows:
                if window is not None and window.winfo_exists() and window.winfo_ismapped():
                    self.recorder.resize(name, False, 'scripted-geometry')
            self._later('resized', .4)

    def _finish(self):
        import quiet_session
        self.recorder.write_json('scenario.json', dict(workload=self._describe(), captures=self.captures,
            findings=self.findings, cycles=self.cycle_samples, blocked_effects=list(quiet_session.BLOCKED),
            callback_errors=self.callback_errors,
            duration_seconds=time.perf_counter() - self.started,
            method='Private-desktop scripted geometry; Tk event-loop/geometry callbacks are not compositor frames'))
        self.worker.shutdown(wait=False, cancel_futures=True)
        self.recorder.event('scenario.complete', findings=len(self.findings), blocked_effects=len(quiet_session.BLOCKED))
        self.root.quit()

    def _tick(self):
        self.timer = None
        try:
            now = time.perf_counter()
            if now - self.started > max(90, self.arguments.idle_seconds + 60):
                raise TimeoutError('Tk scenario did not finish before its overall deadline')
            if self.phase == 'prepare':
                self._prepare()
            elif now < self.deadline:
                pass
            elif self.phase == 'answer':
                try:
                    answer = self.answers.get_nowait()
                except queue.Empty:
                    answer = None
                if isinstance(answer, Exception):
                    raise answer
                if answer is not None:
                    if str(answer.text).startswith('Error:'):
                        raise RuntimeError('Fixture translation failed before comparison')
                    self.outcome = answer
                    self.services.translation.flush_key_usage()
                    self.app.selected_language = self.workload.target
                    self.app._view.source_chip.set(self.workload.source_language)
                    self.app.original_text.delete('1.0', 'end')
                    self.app.original_text.insert('1.0', self.workload.source)
                    self.app._update_translation_with_original(answer.text, answer.extracted_source,
                                                               source_text=self.workload.source)
                    self.app._refresh_reading_pane()
                    self._later('loaded', .3)
            elif self.phase == 'loaded':
                self._show()
                self._later('visible', .5)
            elif self.phase == 'visible':
                self.recorder.scenario(self.workload.name, **self._describe())
                self._capture('initial')
                print(json.dumps(dict(status='scenario-ready', workload=self.workload.name)), flush=True)
                if self.workload.surface == 'hidden':
                    self.recorder.scenario('resident-hidden-idle', **self._describe())
                    self._later('finish', self.arguments.idle_seconds + 1)
                elif self.workload.surface == 'cycles':
                    self._later('cycle-open', 0)
                else:
                    self.recorder.scenario(self.workload.name + '-steady', **self._describe())
                    self._later('resize-start', 2)
            elif self.phase == 'resize-start':
                self.phase = 'resize'
            elif self.phase == 'resize':
                self._resize()
            elif self.phase == 'resized':
                self._capture('resized')
                self._finish()
                return
            elif self.phase == 'cycle-open':
                self._open_quick()
                self.app.theme_manager.set_mode(('pink', 'blue', 'yellow', 'green', 'light', 'dark')[self.cycle % 6])
                self.app.quick_translate_manager.popup.geometry(f'{680 + self.cycle % 4 * 80}x{650 + self.cycle % 3 * 40}')
                self._later('cycle-close', .15)
            elif self.phase == 'cycle-close':
                layout = self.app.quick_translate_manager._layout
                if not layout or not layout.get('revealed'):
                    self.timer = self.root.after(16, self._tick)
                    return
                self.app.quick_translate_manager.close()
                self.cycle += 1
                with self.recorder.lock:
                    sample = dict(self.recorder.resources[-1]) if self.recorder.resources else {}
                self.cycle_samples.append(dict(cycle=self.cycle, **sample))
                self.recorder.event('cycle.complete', cycle=self.cycle)
                self._later('cycle-open' if self.cycle < self.arguments.cycles else 'finish', .1)
            elif self.phase == 'finish':
                self._finish()
                return
        except Exception as error:
            self.recorder.exception('scenario.error', error)
            self.findings.append(dict(kind='scenario-error', error_type=type(error).__name__))
            self._finish()
            return
        self.timer = self.root.after(16, self._tick)


def main(argv=None):
    from tools.tk_resize.scenarios import WORKLOADS, workload
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=tuple(WORKLOADS), required=True)
    parser.add_argument('--final-validation', action='store_true', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--theme', default='light', choices=('system', 'pink', 'blue', 'yellow', 'green', 'light', 'dark'))
    parser.add_argument('--ui-language', default='en', choices=('en', 'ja'))
    parser.add_argument('--text-scale', type=float, default=1.0, choices=(1.0, 1.5))
    parser.add_argument('--dpi-factor', type=float, default=1.0, choices=(1.0, 1.5))
    parser.add_argument('--idle-seconds', type=float, default=30)
    parser.add_argument('--cycles', type=int, default=50)
    arguments = parser.parse_args(argv)
    if arguments.idle_seconds < 30 or arguments.cycles < 50:
        parser.error('At least 30 idle seconds and 50 lifecycle cycles are required')
    if any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for char in arguments.run_id):
        parser.error('--run-id must be a simple label')
    root = Path(__file__).resolve().parents[2]
    directory = root / 'logs/qt_quick_trial' / arguments.run_id
    directory.mkdir(parents=True, exist_ok=False)
    os.environ['APPDATA'] = str(directory / 'appdata')
    sys.path[:0] = [str(root), str(root / 'tests')]
    import quiet_session
    quiet_session.install()
    if os.name == 'nt' and not quiet_session.DESKTOP_NAME:
        raise RuntimeError('Refusing a Tk baseline without a private desktop')
    import src.core  # noqa: F401
    from tools.tk_resize.telemetry import RunRecorder, ProcessProbe
    from tools.tk_resize.profile import TrialConfig
    from tools.tk_resize.services import TrialServices
    from tools.ui_gallery import install_stubs
    from src import app as app_module
    from src.constants import VERSION
    import tkinter
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    install_stubs()
    config = TrialConfig(directory / 'profile')
    current = workload(arguments.scenario)
    for key, value in dict(last_run_version=VERSION, welcome_seen=True, default_target_language=current.target,
        theme_mode=arguments.theme, ui_language=arguments.ui_language, text_scale=arguments.text_scale,
        furigana_enabled=current.furigana, linked_reading_enabled=current.linked).items():
        config.set(key, value)
    recorder = RunRecorder(directory, root, scenario='tk-' + current.name)
    recorder.update_manifest(renderer='production-tk', desktop='private', graphics_api='Tk/GDI',
        measurement_limits=['Tk exposes no frame-present notification here',
            'Private scripted geometry is not physical dragging or pointer-to-photon latency',
            'The idle case measures hidden residency; a user-desktop tray icon is intentionally absent'])
    services = TrialServices(config, recorder)
    services.seed_history(current.history_count)
    original_config, original_service = app_module.Config, app_module.TranslationService
    from src.core.nlp_manager import nlp_manager
    original_nlp_config = nlp_manager.config
    nlp_manager.set_config(config)
    original_load = tkinter.Tk._loadtk
    def load_tk(widget):
        original_load(widget)
        value = float(widget.tk.call('tk', 'scaling'))
        widget.tk.call('tk', 'scaling', value * arguments.dpi_factor)
    app_module.Config, app_module.TranslationService = lambda: config, lambda _config: services.translation
    tkinter.Tk._loadtk = load_tk
    app = None
    stop = threading.Event()
    sampler = None
    try:
        app = app_module.TranslatorApp()
        app.theme_manager.stop_watching()
        app.show_main_window()
        app.root.report_callback_exception = lambda _type, error, tb: recorder.exception('tk.callback', error.with_traceback(tb))
        def sample():
            probe = ProcessProbe()
            try:
                while not stop.wait(.5):
                    recorder.sample(probe.sample())
            except Exception as error:
                recorder.exception('resource-probe.error', error)
            finally:
                probe.close()
        sampler = threading.Thread(target=sample, daemon=True, name='BaselineResourceProbe')
        sampler.start()
        controller = TkScenario(app, services, recorder, current, arguments)
        from tools.ui_gallery import capture_tk_callback_errors
        capture_tk_callback_errors(app.root, controller.callback_errors)
        controller.start()
        print(json.dumps(dict(status='ready', log_directory=str(directory), offline_fixture=True)), flush=True)
        app.root.mainloop()
        return 1 if controller.findings or controller.callback_errors or quiet_session.BLOCKED else 0
    except Exception as error:
        recorder.exception('baseline.run.error', error)
        return 1
    finally:
        stop.set()
        if sampler:
            sampler.join(2)
        if app:
            app.theme_manager.stop_watching()
            app.quick_translate_manager.close()
            app.root.destroy()
        tkinter.Tk._loadtk = original_load
        app_module.Config, app_module.TranslationService = original_config, original_service
        nlp_manager.set_config(original_nlp_config)
        recorder.update_manifest(fixture_save_retries=config.save_retry_stats)
        recorder.close(dict(consolidated_validation=dict(status='pending'), resource_comparison=dict(status='pending')))


if __name__ == '__main__':
    raise SystemExit(main())
