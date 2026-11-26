"""Prepare the same complete, on-screen Tk fixture before human/private review."""
import sys
import time
from types import SimpleNamespace


def inside(rect, work):
    left, top, right, bottom = rect
    return work[0] <= left < right <= work[2] and work[1] <= top < bottom <= work[3]


def scenario_factory(original_factory, private_check=False):
    def factory(base):
        cls = original_factory(base)
        class PreparedManualScenario(cls):
            def __init__(self, *args):
                super().__init__(*args)
                self.preparation_stage = 0
                self.preparation_started = time.perf_counter()
                self.app.popup.withdraw()
                manager = self.app.quick_translate_manager
                self.original_reveal = manager._reveal
                manager._reveal = lambda *_args, **_kwargs: None
                self.desired_quick = None
                self.preparation_settlement = None

            def _prepare_windows(self):
                from src.ui.components.dialog import work_area
                from tools.ui_gallery import window_rect
                from tools.tk_resize.settlement import LayoutSettlement, snapshot_layout
                work = tuple(work_area())
                left, top, right, bottom = work
                manager = self.app.quick_translate_manager
                popup, layout = manager.popup, manager._layout
                if time.perf_counter() - self.preparation_started > 30:
                    raise TimeoutError('Fixture preparation did not converge')
                if not (popup is not None and layout and layout.get('done')
                        and layout.get('resize_metrics') and popup.winfo_width() > 1):
                    return False
                if self.preparation_stage == 0:
                    main = self.app.popup
                    main.title('CrossTrans2 - TK RESIZE TEST')
                    main.geometry(f'{min(880, right - left - 60)}x{min(740, bottom - top - 90)}+{left + 20}+{top + 30}')
                    popup.title('Quick Translate - TK RESIZE TEST')
                    manager._grip_press(SimpleNamespace(x_root=0, y_root=0))
                    if manager._grip_start is None:
                        return False
                    width = max(640, manager._grip_start['min_width'])
                    height = max(660, manager._grip_start['min_height'])
                    if width > right - left - 40 or height > bottom - top - 60:
                        raise ValueError('Work area cannot contain the prepared fixture minimum')
                    # Keep room to grow rightward as well as leftward.
                    x = left + max(20, min(960, right-left-width-80))
                    y = top + 40
                    self.desired_quick = (x, y, width, height)
                    manager._user_rect = self.desired_quick
                    manager._user_size = (width, height)
                    manager._grip_release()
                    self.preparation_stage = 1
                    return False
                if self.preparation_stage == 1:
                    actual = (popup.winfo_x(), popup.winfo_y(), popup.winfo_width(), popup.winfo_height())
                    if (actual != self.desired_quick or manager._resize_settle_job is not None
                            or manager._resize_save_job is not None):
                        return False
                    manager._reveal = self.original_reveal
                    self.original_reveal()
                    self.app.show_main_window()
                    self.preparation_settlement = LayoutSettlement(stable_ms=200, timeout_ms=10000)
                    self.settlement_started = time.perf_counter()
                    self.preparation_stage = 2
                    return False
                view = self.app._view
                if view._arrange_job is not None or view.body_area._layout_job is not None:
                    return False
                status = self.preparation_settlement.observe(snapshot_layout(self.app),
                    (time.perf_counter() - self.settlement_started) * 1000)
                if status == 'timeout':
                    raise TimeoutError('Mapped main/quick descendants did not settle')
                if status != 'settled':
                    return False
                rectangles = {name: tuple(window_rect(int(window.wm_frame(), 16)))
                              for name, window in (('main', self.app.popup), ('quick', popup))}
                if not all(inside(rect, work) for rect in rectangles.values()):
                    return False
                native = manager._native_sizing
                self.recorder.write_json('prepared-windows.json', dict(work_area=work,
                    rectangles=rectangles, desired_quick=self.desired_quick,
                    layout_complete=True, within_work_area=True,
                    native_installed=bool(getattr(native, 'hwnd', None)),
                    native_enabled=bool(getattr(native, 'enabled', False)),
                    native_child_hooks=len(getattr(native, 'child_hwnds', ())),
                    preparation_seconds=time.perf_counter() - self.preparation_started,
                    private_check=private_check))
                return True

            def _tick(self):
                if self.phase == 'visible' and time.perf_counter() >= self.deadline:
                    try:
                        if not self._prepare_windows():
                            self.timer = self.root.after(40, self._tick)
                            return
                        if private_check:
                            self.recorder.scenario('prepared-private-fixture', **self._describe())
                            self._capture('prepared')
                            self._finish()
                            return
                    except Exception as error:
                        self.recorder.exception('fixture.preparation.error', error)
                        self.findings.append(dict(kind='fixture-preparation-failed', error_type=type(error).__name__))
                        self._finish()
                        return
                super()._tick()

            def _finish(self):
                self.app.quick_translate_manager._reveal = self.original_reveal
                super()._finish()
        return PreparedManualScenario
    return factory


def main(argv=None):
    from tools.tk_resize import worker
    arguments = list(sys.argv[1:] if argv is None else argv)
    private_check = '--private-check' in arguments
    if private_check:
        arguments.remove('--private-check')
        if '--manual-visible-desktop' in arguments:
            raise ValueError('Private check cannot request the public desktop')
    elif '--manual-visible-desktop' not in arguments:
        raise ValueError('Human fixture requires an explicit visible-desktop request')
    worker.scenario_class = scenario_factory(worker.scenario_class, private_check)
    return worker.main(arguments)


if __name__ == '__main__':
    raise SystemExit(main())
