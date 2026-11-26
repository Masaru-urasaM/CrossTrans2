"""
Toast Notification System for CrossTrans.
Provides non-intrusive visual feedback for user actions.

A toast is a small floating card - a hairline ring on the surface colour - that holds an info bar
of its kind (TODO.md U6): success, warning, error and information are told apart by tint and by
icon, in both themes, with text that is at least 4.5:1 on its tint. (Until 2.0 the colours were
asked for and never shown - ttkbootstrap 1.x repainted them, TODO.md V3.)
"""
import tkinter as tk
from src.ui.theme.compat import NO_AUTOSTYLE
from typing import Optional, List, Callable
from dataclasses import dataclass
from enum import Enum

from src.ui import components as ui
from src.ui.theme import windows


class ToastType(Enum):
    """Types of toast notifications with associated styling."""
    SUCCESS = "success"
    ERROR = "error"
    INFO = "info"
    WARNING = "warning"


@dataclass
class ToastConfig:
    """Configuration for a toast type."""
    kind: str                # the info bar's kind: info, success, warning or danger
    default_duration: int


class ToastManager:
    """
    Manages toast notifications with stacking and animations.

    Features:
    - Multiple toast types (success, error, info, warning)
    - Smooth fade in/out animations
    - Automatic stacking when multiple toasts
    - Click to dismiss
    - Auto-dismiss after duration

    Usage:
        toast = ToastManager(root)
        toast.show("Copied to clipboard!", ToastType.SUCCESS)
        toast.show("Translation failed", ToastType.ERROR, duration=5000)
    """

    CONFIGS = {
        ToastType.SUCCESS: ToastConfig('success', 2000),
        ToastType.ERROR: ToastConfig('danger', 4000),
        ToastType.INFO: ToastConfig('info', 2500),
        ToastType.WARNING: ToastConfig('warning', 3000),
    }

    # Layout constants
    MARGIN_BOTTOM = 60   # Above taskbar
    MARGIN_RIGHT = 20    # From right edge
    TOAST_GAP = 10       # Gap between stacked toasts
    MAX_TOASTS = 5       # Maximum visible toasts

    # Animation settings
    FADE_IN_STEP = 0.1   # Alpha increment per frame
    FADE_OUT_STEP = 0.1  # Alpha decrement per frame
    FADE_IN_DELAY = 20   # ms between fade-in frames
    FADE_OUT_DELAY = 25  # ms between fade-out frames

    def __init__(self, root: tk.Tk) -> None:
        """
        Initialize ToastManager.

        Args:
            root: The root Tkinter window
        """
        self.root = root
        self.active_toasts: List[tk.Toplevel] = []
        self._dismiss_callbacks: dict = {}  # toast -> after_id for auto-dismiss
        self._timers: dict = {}            # toast -> every root-owned animation/dismiss job

    def _after(self, toast, delay, callback):
        """A destroyed Toplevel deletes commands, not timers; keep both on the root."""
        def run():
            jobs = self._timers.get(toast)
            if jobs is not None:
                jobs.discard(job)
                if not jobs:
                    self._timers.pop(toast, None)
            if toast.winfo_exists():
                callback()
        job = self.root.after(delay, run)
        self._timers.setdefault(toast, set()).add(job)
        return job

    def _cancel_timers(self, toast):
        for job in self._timers.pop(toast, ()):
            self.root.after_cancel(job)

    def _on_destroy(self, event, toast):
        if event.widget is not toast:
            return
        self._cancel_dismiss(toast)
        self._cancel_timers(toast)
        if toast in self.active_toasts:
            self.active_toasts.remove(toast)
            self._reposition_all()

    def show(self,
             message: str,
             toast_type: ToastType = ToastType.INFO,
             duration: Optional[int] = None) -> tk.Toplevel:
        """
        Show a toast notification.

        Args:
            message: Text to display
            toast_type: Type of toast (affects color/icon)
            duration: Display duration in ms (None = use default for type)

        Returns:
            The toast Toplevel window (for testing/advanced use)
        """
        # Remove oldest toast if at max
        if len(self.active_toasts) >= self.MAX_TOASTS:
            oldest = self.active_toasts[0]
            self._dismiss(oldest, animate=False)

        config = self.CONFIGS[toast_type]
        duration = max(duration or config.default_duration, ui.get().manager.notification_duration_ms)

        toast = self._build(config, message, wrap=300)

        # Calculate position
        toast.update_idletasks()
        self._position_toast(toast)

        # Add to active list
        self.active_toasts.append(toast)

        # Animate fade in, then schedule auto-dismiss
        self._fade_in(toast, callback=lambda: self._schedule_dismiss(toast, duration))

        return toast

    def show_success(self, message: str, duration: Optional[int] = None) -> tk.Toplevel:
        """Convenience method for success toast."""
        return self.show(message, ToastType.SUCCESS, duration)

    def show_error(self, message: str, duration: Optional[int] = None) -> tk.Toplevel:
        """Convenience method for error toast."""
        return self.show(message, ToastType.ERROR, duration)

    def show_info(self, message: str, duration: Optional[int] = None) -> tk.Toplevel:
        """Convenience method for info toast."""
        return self.show(message, ToastType.INFO, duration)

    def show_warning(self, message: str, duration: Optional[int] = None) -> tk.Toplevel:
        """Convenience method for warning toast."""
        return self.show(message, ToastType.WARNING, duration)

    def show_warning_with_shake(self, message: str, duration: Optional[int] = 3000) -> tk.Toplevel:
        """Show warning toast with shake animation to grab attention.

        Args:
            message: Text to display
            duration: Display duration in ms (default 3000)

        Returns:
            The toast Toplevel window
        """
        toast = self.show(message, ToastType.WARNING, duration)
        # Start shake animation after fade-in completes
        if ui.get().manager.animations_enabled:
            self._after(toast, 250, lambda: self._shake_toast(toast, 0))
        return toast

    def _shake_toast(self, toast: tk.Toplevel, step: int) -> None:
        """Animate toast with horizontal shake effect.

        Args:
            toast: The toast window to shake
            step: Current animation step (0-7)
        """
        if not ui.get().manager.animations_enabled or not toast.winfo_exists() or step >= 8:
            return

        try:
            # Get current position
            geometry = toast.geometry()
            # Parse geometry string: "WxH+X+Y" or "+X+Y"
            if '+' in geometry:
                parts = geometry.split('+')
                x = int(parts[-2])
                y = int(parts[-1])
                size = '+'.join(parts[:-2]) if len(parts) > 2 else ''

                # Shake pattern: right, left, right, left (decreasing amplitude)
                offsets = [8, -8, 6, -6, 4, -4, 2, -2]
                offset = offsets[step] if step < len(offsets) else 0

                new_x = x + offset
                if size:
                    toast.geometry(f"{size}+{new_x}+{y}")
                else:
                    toast.geometry(f"+{new_x}+{y}")

                # Schedule next shake frame (40ms = 25fps)
                self._after(toast, 40, lambda: self._shake_toast(toast, step + 1))
        except (tk.TclError, ValueError):
            pass  # Toast was destroyed or invalid geometry

    def show_warning_with_action(self,
                                 message: str,
                                 action_text: str,
                                 action_callback: Callable,
                                 duration: Optional[int] = 6000) -> tk.Toplevel:
        """Show warning toast with a clickable action link.

        Args:
            message: Main warning message
            action_text: Text for the clickable action link (e.g., "Open API Key Settings")
            action_callback: Function to call when action is clicked
            duration: Display duration in ms (default 6000 - longer for user to read and click)

        Returns:
            The toast Toplevel window

        Example:
            toast.show_warning_with_action(
                "No vision-capable API found.",
                "Open API Key Settings",
                lambda: app.show_settings_api_tab()
            )
        """
        # Remove oldest toast if at max
        if len(self.active_toasts) >= self.MAX_TOASTS:
            oldest = self.active_toasts[0]
            self._dismiss(oldest, animate=False)

        config = self.CONFIGS[ToastType.WARNING]

        def on_action():
            # dismiss the toast and trigger the callback
            self._dismiss(toast, animate=False)
            action_callback()

        toast = self._build(config, message, wrap=320, action=action_text, on_action=on_action)

        # Position and show
        toast.update_idletasks()
        self._position_toast(toast)
        self.active_toasts.append(toast)
        self._fade_in(toast, callback=lambda: self._schedule_dismiss(toast, duration))

        return toast

    def _build(self, config: ToastConfig, message: str, wrap: int, action: str = '',
               on_action: Optional[Callable] = None) -> tk.Toplevel:
        """The toast window: invisible (alpha 0) until it is placed and faded in. A click anywhere
        on it but the action dismisses it. `toast.bar` is its info bar."""
        ctx = ui.get()
        p = ctx.palette
        toast = tk.Toplevel(self.root, bg=p.surface, highlightthickness=1,
                            highlightbackground=p.border, highlightcolor=p.border, **NO_AUTOSTYLE)
        toast.overrideredirect(True)
        toast.attributes('-topmost', True)
        toast.attributes('-alpha', 0.0)  # Start invisible for fade-in
        toast.bind('<Map>', lambda event, w=toast, pal=p: (
            windows.style_floating(w, pal) if event.widget is w else None), add='+')

        card = ui.Frame(toast, on='surface', padding=ctx.px(6))
        card.pack(fill=tk.BOTH, expand=True)
        bar = ui.InfoBar(card, message, kind=config.kind, on='surface', action=action,
                         on_action=on_action)
        bar.text.configure(wraplength=ctx.px(wrap))     # a toast is as wide as its text, to a limit
        bar.unbind('<Configure>')                       # ...so the bar need not fit it to a width
        bar.pack(fill=tk.BOTH, expand=True)
        toast.bar = bar

        # A click anywhere on it dismisses it: the toplevel is in every child's bindtags, so one
        # binding covers them all. Not the action, which dismisses the toast itself once it ran.
        toast.bind('<Button-1>', lambda e, t=toast: None if e.widget is bar.action
                   else self._dismiss(t))
        toast.bind('<Destroy>', lambda e, t=toast: self._on_destroy(e, t), add='+')
        return toast

    def dismiss_all(self) -> None:
        """Dismiss all active toasts immediately."""
        for toast in self.active_toasts[:]:  # Copy list to avoid modification during iteration
            self._dismiss(toast, animate=False)

    def _position_toast(self, toast: tk.Toplevel) -> None:
        """Position toast at bottom-right, accounting for stack."""
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        toast_w = toast.winfo_reqwidth()
        toast_h = toast.winfo_reqheight()

        # Calculate Y based on existing toasts (stack from bottom)
        y_offset = self.MARGIN_BOTTOM
        for existing_toast in self.active_toasts:
            if existing_toast != toast and existing_toast.winfo_exists():
                try:
                    y_offset += existing_toast.winfo_reqheight() + self.TOAST_GAP
                except tk.TclError:
                    pass  # Toast was destroyed

        x = screen_w - toast_w - self.MARGIN_RIGHT
        y = screen_h - toast_h - y_offset

        toast.geometry(f"+{x}+{y}")

    def _schedule_dismiss(self, toast: tk.Toplevel, duration: int) -> None:
        """Schedule auto-dismiss after duration."""
        if toast.winfo_exists():
            after_id = self._after(toast, duration, lambda: self._dismiss(toast))
            self._dismiss_callbacks[toast] = after_id

    def _cancel_dismiss(self, toast: tk.Toplevel) -> None:
        """Cancel scheduled auto-dismiss."""
        if toast in self._dismiss_callbacks:
            try:
                self.root.after_cancel(self._dismiss_callbacks[toast])
            except (tk.TclError, ValueError):
                pass  # Already cancelled or toast destroyed
            del self._dismiss_callbacks[toast]

    def _fade_in(self,
                 toast: tk.Toplevel,
                 callback: Optional[Callable] = None,
                 alpha: float = 0.0) -> None:
        """Animate fade in."""
        if not toast.winfo_exists():
            return
        if not ui.get().manager.animations_enabled:
            toast.attributes('-alpha', 1.0)
            if callback:
                callback()
            return

        alpha += self.FADE_IN_STEP
        if alpha <= 0.95:
            try:
                toast.attributes('-alpha', alpha)
                self._after(toast, self.FADE_IN_DELAY,
                            lambda: self._fade_in(toast, callback, alpha))
            except tk.TclError:
                pass  # Toast was destroyed
        else:
            try:
                toast.attributes('-alpha', 0.95)
            except tk.TclError:
                pass
            if callback:
                callback()

    def _fade_out(self,
                  toast: tk.Toplevel,
                  callback: Optional[Callable] = None,
                  alpha: float = 0.95) -> None:
        """Animate fade out."""
        if not ui.get().manager.animations_enabled or not toast.winfo_exists():
            if callback:
                callback()
            return

        alpha -= self.FADE_OUT_STEP
        if alpha > 0:
            try:
                toast.attributes('-alpha', alpha)
                self._after(toast, self.FADE_OUT_DELAY,
                            lambda: self._fade_out(toast, callback, alpha))
            except tk.TclError:
                if callback:
                    callback()
        else:
            if callback:
                callback()

    def _dismiss(self, toast: tk.Toplevel, animate: bool = True) -> None:
        """
        Dismiss a toast.

        Args:
            toast: The toast to dismiss
            animate: Whether to animate the dismissal
        """
        if toast not in self.active_toasts:
            return

        # Cancel any scheduled auto-dismiss
        self._cancel_dismiss(toast)
        self._cancel_timers(toast)

        # Remove from active list first to prevent re-dismiss
        self.active_toasts.remove(toast)

        def on_complete():
            try:
                if toast.winfo_exists():
                    toast.destroy()
            except tk.TclError:
                pass  # Already destroyed
            # Reposition remaining toasts
            self._reposition_all()

        if animate:
            self._fade_out(toast, callback=on_complete)
        else:
            on_complete()

    def _reposition_all(self) -> None:
        """Reposition all active toasts after one is dismissed."""
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        y_offset = self.MARGIN_BOTTOM
        for toast in self.active_toasts:
            if toast.winfo_exists():
                try:
                    toast_w = toast.winfo_reqwidth()
                    toast_h = toast.winfo_reqheight()

                    x = screen_w - toast_w - self.MARGIN_RIGHT
                    y = screen_h - toast_h - y_offset

                    toast.geometry(f"+{x}+{y}")
                    y_offset += toast_h + self.TOAST_GAP
                except tk.TclError:
                    pass  # Toast was destroyed during iteration
