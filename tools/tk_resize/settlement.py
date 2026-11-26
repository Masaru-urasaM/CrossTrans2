"""Observe layout convergence without pumping Tk or claiming a presented frame."""
import json


class LayoutSettlement:
    """Require stable descendants and drained layout work within a fixed deadline."""

    def __init__(self, *, stable_ms=120, timeout_ms=10000):
        self.stable_ms, self.timeout_ms = stable_ms, timeout_ms
        self.fingerprint = None
        self.clean_since_ms = self.verified_ms = None
        self.samples = []

    def observe(self, snapshot, elapsed_ms):
        if not snapshot.get('geometry'):
            raise ValueError('Settlement requires actual mapped geometry')
        fingerprint = json.dumps(snapshot['geometry'], sort_keys=True)
        clean = not snapshot['pending'] and not snapshot['inconsistent']
        if not clean or fingerprint != self.fingerprint or self.clean_since_ms is None:
            self.clean_since_ms = elapsed_ms if clean else None
            self.verified_ms = None
        self.fingerprint = fingerprint
        if elapsed_ms >= self.timeout_ms:
            status = 'timeout'
        elif self.clean_since_ms is not None and elapsed_ms - self.clean_since_ms >= self.stable_ms:
            if self.verified_ms is None:
                self.verified_ms = elapsed_ms
            status = 'settled'
        else:
            status = 'pending'
        self.samples.append(dict(elapsed_ms=elapsed_ms, status=status, **snapshot))
        return status

    def report(self):
        return dict(status=self.samples[-1]['status'] if self.samples else 'not-observed',
                    stable_ms=self.stable_ms, timeout_ms=self.timeout_ms,
                    release_to_first_clean_ms=self.clean_since_ms,
                    release_to_verified_stable_ms=self.verified_ms, samples=self.samples,
                    measurement='Observed geometry/layout convergence, not paint or compositor presentation')


def snapshot_layout(app):
    """Read mapped descendants and pending work on the owning UI thread only."""
    geometry, pending, inconsistent = [], [], []
    manager = getattr(app, 'quick_translate_manager', None)
    windows = [('main', app.popup), ('quick', getattr(manager, 'popup', None))]
    for surface, window in windows:
        if window is None or not window.winfo_exists() or not window.winfo_ismapped():
            continue
        stack = [window]
        while stack:
            widget = stack.pop()
            if not widget.winfo_ismapped():
                continue
            path = str(widget)
            geometry.append((surface, path, widget.winfo_geometry()))
            if widget.winfo_class() == 'Text' and widget.tk.getboolean(widget.tk.call(widget._w, 'pendingsync')):
                pending.append(path + ':text-metrics')
            if all(hasattr(widget, name) for name in ('canvas', 'content', '_window', '_layout_job')):
                if widget._layout_job is not None or getattr(widget, '_bar_job', None) is not None:
                    pending.append(path + ':scroll-layout')
                widths = [widget.canvas.winfo_width(), widget.content.winfo_width(),
                          int(float(widget.canvas.itemcget(widget._window, 'width')))]
                if len(set(widths)) != 1:
                    inconsistent.append(dict(widget=path, canvas_content_requested_widths=widths))
                if widget is getattr(app._view, 'body_area', None) and widget.canvas.winfo_height() != widget.winfo_height():
                    inconsistent.append(dict(widget=path, viewport_canvas_heights=
                        (widget.winfo_height(),widget.canvas.winfo_height())))
            stack.extend(reversed(widget.winfo_children()))
    if getattr(app._view, '_arrange_job', None) is not None:
        pending.append('main:arrange')
    for name in ('_grip_preview_after', '_resize_settle_job', '_resize_save_job'):
        if getattr(manager, name, None) is not None:
            pending.append('quick:' + name)
    return dict(geometry=geometry, pending=pending, inconsistent=inconsistent)
