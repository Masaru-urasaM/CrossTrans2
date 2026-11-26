"""
Expanded translation window for CrossTrans (TODO.md U7, V26).

A larger view for one translation, to read or to show someone. It used to open at 80 % of the screen
with lines as wide as the window - about 190 characters a paragraph, where 50-80 read comfortably
(WCAG 1.4.8, Fluent) - and its Copy and Close buttons looked disabled (1.63:1). Now the text sits in
a centred column one comfortable line wide (`popup_layout.reading_width`), in a larger type size, the
window opens as wide as that column needs, and full screen keeps the column rather than stretching
it. Copy and Close are real buttons; Copy says "Copied" once, on itself.
"""
import tkinter as tk
from tkinter import font as tkfont
from typing import Optional

import pyperclip

from src.core.lang_id import output_hint
from src.ui import components as ui
from src.ui import popup_layout
from src.ui.components import context as C
from src.ui.components.dialog import work_area
from src.ui.ruby_text import RubyText, insert_output
from src.ui.theme import engine
from src.ui.theme.compat import NO_AUTOSTYLE
from src.ui.theme import fonts as type_ramp

MARGIN = 48                  # at 100 %: clear space each side of the column
MIN_WIDTH = 640              # at 100 %
WORK_WIDTH_SHARE = 0.9
WORK_HEIGHT_SHARE = 0.8
MIDDLE_DOT = chr(0x00B7)
COPY_FEEDBACK_MS = 1500


def plural(count: int, noun: str) -> str:
    """'1 line', '12 lines'."""
    return f"{count:,} {noun}" + ('' if count == 1 else 's')


def counts_text(text: str) -> str:
    """'32 characters . 1 word . 2 lines' - counted from the real text (`get_plain`), so the
    numbers describe it rather than the gaps ruby leaves in `get()`."""
    return f" {MIDDLE_DOT} ".join((plural(len(text), 'character'), plural(len(text.split()), 'word'),
                                   plural(text.count('\n') + 1, 'line')))


class ExpandedTranslationWindow:
    """Expanded view for translation.

    - A centred reading column, a larger type size, a scroll bar only when it is needed
    - Full screen toggle (F11) that keeps the column
    - Copy with feedback on the button; Ctrl+C copies the selection, or everything when nothing
      is selected
    - Counts of characters, words and lines; Esc closes
    - Furigana for Japanese results (read-only, so I3 holds by construction)
    """

    def __init__(self, root: tk.Tk, toast_manager, config=None):
        """Initialize the expanded window manager.

        Args:
            root: Root Tk window
            toast_manager: ToastManager instance for notifications
            config: Config object, read for the furigana toggle
        """
        self.root = root
        self.toast = toast_manager
        self.config = config

    def show(self, translated: str, target_language: str,
             source_language: Optional[str] = None) -> None:
        """Show the expanded translation window.

        Args:
            translated: The translated text to display
            target_language: The target language name for the title
            source_language: The source text's settled language, if any - with the
                target it decides the readings (`lang_id.output_hint`)
        """
        if not translated:
            self.toast.show_warning("No translation to expand")
            return

        ctx = C.get()
        px, p = ctx.px, ctx.palette
        expanded = tk.Toplevel(self.root)
        expanded.withdraw()
        expanded.title(f"Translation - {target_language}")
        expanded.sizes_from_work_area = True
        engine.attach(expanded)

        script = type_ramp.script_for_language(target_language)
        text_font = ctx.font('reading_large', script)
        # One comfortable line of this text in this font, and the window it needs around it
        column = popup_layout.reading_width(translated, tkfont.Font(root=expanded, font=text_font).measure)
        left, top, right, bottom = work_area()
        width = min(max(column + 2 * px(MARGIN), px(MIN_WIDTH)), int((right - left) * WORK_WIDTH_SHARE))
        height = int((bottom - top) * WORK_HEIGHT_SHARE)
        x = left + (right - left - width) // 2
        y = top + (bottom - top - height) // 2
        expanded.geometry(f"{width}x{height}+{x}+{y}")
        expanded.minsize(min(px(MIN_WIDTH), width), min(px(360), height))

        frame = ui.Frame(expanded, on='bg', padding=px(24))
        frame.pack(fill='both', expand=True)

        # Header: the title, and the window's own controls
        header = ui.Frame(frame)
        header.pack(fill='x', pady=(0, px(16)))
        ui.Label(header, f"Translation to {target_language}", kind='subtitle').pack(side='left')
        close_btn = ui.Button(header, "Close", command=expanded.destroy, kind='subtle', icon='x-lg')
        close_btn.pack(side='right')
        copy_btn = ui.Button(header, "Copy", kind='soft', icon='copy')
        copy_btn.pack(side='right', padx=(0, px(8)))
        screen_btn = ui.IconButton(header, 'arrows-fullscreen', label="Full screen (F11)")
        screen_btn.pack(side='right', padx=(0, px(8)))

        # Footer: counts, and the keys (packed before the body so a short window never loses it)
        footer = ui.Frame(frame)
        footer.pack(side='bottom', fill='x', pady=(px(12), 0))
        status_label = ui.Label(footer, "", kind='muted')
        status_label.pack(side='left')
        ui.Label(footer, f"F11 Full screen {MIDDLE_DOT} Esc Close {MIDDLE_DOT} Ctrl+C Copy",
                 kind='muted').pack(side='right')

        # The reading column. Its width is fixed in pixels (never wider than the window leaves)
        # and the holder fills the height only, so it stays centred whatever the window does.
        body = ui.Frame(frame)
        body.pack(fill='both', expand=True)
        # A plain tk.Frame holds the width (the size a ttk.Frame asks for is its children's)
        holder = tk.Frame(body, width=column, height=1, bg=ctx.plane_colour('bg'), **NO_AUTOSTYLE)
        holder.grid_propagate(False)        # its children are gridded: that is the manager to switch off
        holder.pack(side='top', fill='y', expand=True)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        # Read-only. A disabled tk.Text still supports mouse selection and Ctrl+C (verified), so
        # nothing is lost, and read-only is what lets the box hold furigana: typing beside a reading
        # would leave it describing text the user has already changed, and edit_undo() cannot
        # restore a destroyed embedded window.
        expanded_text = RubyText(holder, wrap=tk.WORD, width=1, height=1, bg=ctx.plane_colour('bg'),
                                 base_fg=p.text, kanji_fg=p.text, ruby_fg=p.ruby, base_font=text_font,
                                 relief='flat', spacing1=0, spacing3=ctx.px(4), cursor='xterm',
                                 padx=0, pady=0, insertbackground=p.text, selectbackground=p.selection,
                                 selectforeground=p.selection_text, inactiveselectbackground=p.selection)
        insert_output(expanded_text, '1.0', translated,
                      lang_hint=output_hint(source_language, target_language),
                      enabled=bool(self.config and self.config.get_furigana_enabled()))
        expanded_text.config(state='disabled')
        expanded_text.grid(row=0, column=0, sticky='nsew')
        ui.AutoScrollbar(holder, expanded_text)

        def fit_column(event=None):
            if event is not None and event.widget is not expanded:
                return
            room = expanded.winfo_width() - 2 * px(MARGIN)
            holder.configure(width=max(px(240), min(column, room)))

        def toggle_fullscreen(event=None):
            on = not bool(expanded.attributes('-fullscreen'))
            expanded.attributes('-fullscreen', on)
            screen_btn.icon_name = 'fullscreen-exit' if on else 'arrows-fullscreen'
            screen_btn.set_on(screen_btn.is_on)
            screen_btn.tooltip.text = "Exit full screen (F11)" if on else "Full screen (F11)"

        def reset_copy_button():
            try:
                copy_btn.configure(text="Copy")
                copy_btn.set_icon('copy')
            except tk.TclError:
                pass        # the window closed meanwhile

        def copy_expanded(event=None):
            # get_plain(), never get(): an embedded ruby frame contributes no characters to get(),
            # so the clipboard would lose every annotated word.
            text = expanded_text.get_plain().strip()
            if text:
                pyperclip.copy(text)
                copy_btn.configure(text="Copied")
                copy_btn.set_icon('check-lg')
                expanded.after(COPY_FEEDBACK_MS, reset_copy_button)

        def copy_key(event=None):
            if expanded_text.tag_ranges('sel'):
                return None             # a selection: RubyText's own <<Copy>> puts it on the clipboard
            copy_expanded()
            return 'break'

        def retheme(manager):
            palette = manager.palette
            try:
                holder.configure(bg=ctx.plane_colour('bg'))
                expanded_text.recolour(bg=ctx.plane_colour('bg'), base_fg=palette.text,
                                       kanji_fg=palette.text, ruby_fg=palette.ruby,
                                       selection=palette.selection)
            except tk.TclError:
                pass

        copy_btn.configure(command=copy_expanded)
        screen_btn.configure(command=toggle_fullscreen)
        unsubscribe = ctx.manager.subscribe(retheme)
        expanded.bind('<Destroy>', lambda e: unsubscribe() if e.widget is expanded else None, add='+')
        expanded.bind('<Configure>', fit_column, add='+')
        expanded.bind('<Escape>', lambda e: expanded.destroy())
        expanded.bind('<F11>', toggle_fullscreen)
        expanded.bind('<Control-c>', copy_key)

        # The counts are computed once: the box is read-only, so they cannot go stale. Counted
        # from get_plain(), so the numbers describe the real text rather than ruby's gaps.
        status_label.configure(text=counts_text(expanded_text.get_plain()))

        # Show it, in front, with the text focused (Ctrl+C and the wheel work at once)
        expanded.deiconify()
        expanded.lift()
        expanded.attributes('-topmost', True)
        expanded.update()
        expanded.attributes('-topmost', False)
        expanded.focus_force()
        expanded_text.focus_set()
        fit_column()
