"""
Attachment area of the Translator (TODO.md U7): the files and images that go to the model with
the text, and the place to drop them.

It used to be a 95 px strip of 90 x 85 px thumbnails with emoji, a red "Clear All", a plain-grey
"+" bitmap and hand-set colours. Now it is a quiet card in the source card: a line that says what
it takes ("Drop files here"), an Attach button, and one tile per file - a thumbnail or a file icon,
its name and kind, a remove button. A file dragged over the card (or the source card around it, see
`accept_drops_on`) rings it in the accent.

The public surface is what the app and the drop handler have always used: `add_file()`,
`get_attachments()`, `clear()`, `attachments`, `on_change`.
"""
import logging
import os
import tkinter as tk
from tkinter import filedialog
from typing import Callable, List, Optional

from PIL import Image, ImageTk

from src.ui import components as ui
from src.ui.i18n import tr
from src.ui.components import context as C
from src.ui.components.feedback import Tooltip
from src.ui.components.styles import name
from src.core.text_files import TEXT_EXTENSIONS, EXTRACTOR_EXTENSIONS, validate_document
from src.core.audio_media import MEDIA_EXTENSIONS
from src.utils.log_events import log_event

try:
    from tkinterdnd2 import DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')
DOCUMENT_EXTENSIONS = TEXT_EXTENSIONS + EXTRACTOR_EXTENSIONS
FILE_ICONS = {'.txt': 'file-earmark-text', '.docx': 'file-earmark-word', '.pdf': 'file-earmark-pdf',
              '.srt': 'badge-cc'}
UNKNOWN_FILE_ICON = 'file-earmark'
THUMBNAIL_PX = 32
NAME_CHARS = 26
ELLIPSIS = chr(0x2026)
KIND_SEPARATOR = chr(0x00B7)


def parse_dropped_paths(raw: str) -> List[str]:
    """The paths in a tkinterdnd2 drop: a string of paths separated by spaces, those with a space in
    them wrapped in braces (`{C:/My Files/a b.txt} C:/c.png`)."""
    if not raw:
        return []
    if '{' not in raw:
        return raw.split()
    paths, current, in_brace = [], '', False
    for char in raw:
        if char == '{':
            in_brace = True
        elif char == '}':
            in_brace = False
            if current:
                paths.append(current)
                current = ''
        elif char == ' ' and not in_brace:
            if current:
                paths.append(current)
                current = ''
        else:
            current += char
    if current:
        paths.append(current)
    return paths


def shorten(filename: str, limit: int = NAME_CHARS) -> str:
    """`filename` cut to `limit` characters: its stem ends in an ellipsis, its extension stays."""
    if len(filename) <= limit:
        return filename
    stem, extension = os.path.splitext(filename)
    keep = max(4, limit - len(extension) - 1)
    return stem[:keep] + ELLIPSIS + extension


def kind_text(path: str, is_image: bool) -> str:
    """'Image . 1.2 MB' - what the file is and how big, for the tile's second line."""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    label = 'Image' if is_image else os.path.splitext(path)[1].lstrip('.').upper() or 'File'
    if size is None:
        return label
    if size >= 1024 * 1024:
        amount = f'{size / (1024 * 1024):.1f} MB'
    elif size >= 1024:
        amount = f'{size / 1024:.0f} KB'
    else:
        amount = f'{size} B'
    return f'{tr(label)} {KIND_SEPARATOR} {amount}'


class AttachmentArea(ui.Frame):
    """Widget to display and manage attachments."""

    def __init__(self, parent, config, on_change: Optional[Callable] = None,
                 on_open_settings_tab: Optional[Callable] = None):
        super().__init__(parent)
        self.config = config
        self.on_change = on_change
        self.on_open_settings_tab = on_open_settings_tab
        self.attachments: List[dict] = []      # {'type': 'image'|'file', 'path': str}
        self.thumbnails = []                   # PhotoImages must outlive their labels
        self.tiles = {}                        # path -> tile widget
        self._dragging = False
        self._layout_signature = None
        self._compact = False
        self._compact_open = False
        self._create_ui()
        self._setup_dnd()

    # ------------------------------------------------------------------ #
    # Building
    # ------------------------------------------------------------------ #
    def _create_ui(self) -> None:
        ctx = C.get()
        px = ctx.px
        self.zone = ui.Card(self, padding=(12, 8, 12, 8))
        self.zone.pack(fill='x')
        self.header = ui.Frame(self.zone)
        self.header.pack(fill='x')
        self.hint_icon = ui.Label(self.header, '', kind='muted')
        self.hint_icon.configure(image=ctx.icon('paperclip', 16))
        self.hint_icon.pack(side='left', padx=(0, px(8)))
        self.hint = ui.Label(self.header, 'Drop files here', kind='muted')
        self.hint.pack(side='left')
        self.add_btn = ui.Button(self.header, 'Attach file', command=self._show_add_menu, kind='subtle',
                                 icon='image')
        self.add_btn.pack(side='left', padx=(px(8), 0))
        self.clear_btn = ui.Button(self.header, 'Clear all', command=self.clear, kind='subtle')
        self.clear_btn.set_icon('x-lg')
        Tooltip(self.clear_btn, 'Clear all')
        self.expand_btn = ui.IconButton(self.header, 'chevron-right', command=self._toggle_compact,
                                         label='Show or hide attached files')
        # shown beside the Attach button's end only while there is something to clear
        self.tile_row = ui.Frame(self.zone)
        self.tile_row.bind('<Configure>', lambda e: self._reflow(), add='+')
        self.header.bind('<Configure>', lambda event: self._fit_header(), add='+')
        Tooltip(self.add_btn, 'Drop files here')

    def _fit_header(self):
        """Keep attachment actions reachable beside the source-language picker."""
        try:
            ctx = C.get()
            room = self.header.winfo_width()
            if room <= 1:
                return
            extra = self.expand_btn.winfo_reqwidth() if self.expand_btn.winfo_manager() else 0
            if self.clear_btn.winfo_manager():
                from tkinter import font
                full = self.clear_btn.winfo_reqwidth()
                if not self.clear_btn.cget('text'):
                    full += font.Font(root=self, font=ctx.font('body')).measure(tr('Clear all')) + ctx.px(4)
                label = '' if room < self.add_btn.winfo_reqwidth() + full + extra + ctx.px(12) else 'Clear all'
                if self.clear_btn.cget('text') != tr(label):
                    self.clear_btn.configure(text=label)
            actions = self.add_btn.winfo_reqwidth() + extra
            if self.clear_btn.winfo_manager():
                actions += self.clear_btn.winfo_reqwidth()
            hint = self.hint_icon.winfo_reqwidth() + self.hint.winfo_reqwidth() + ctx.px(16)
            if room >= actions + hint:
                if not self.hint.winfo_manager():
                    self.hint_icon.pack(side='left', padx=(0, ctx.px(8)), before=self.add_btn)
                    self.hint.pack(side='left', before=self.add_btn)
            else:
                self.hint_icon.pack_forget()
                self.hint.pack_forget()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ #
    # Drag and drop (tkinterdnd2 when it is there; the app's drop handler covers the rest)
    # ------------------------------------------------------------------ #
    def _setup_dnd(self) -> None:
        if not HAS_DND:
            logging.debug("tkinterdnd2 not available, skipping DnD setup for AttachmentArea")
            return
        for widget in (self, self.zone, self.header, self.tile_row):
            self.accept_drops_on(widget)
        logging.info("tkinterdnd2 drag-and-drop enabled for AttachmentArea")

    def accept_drops_on(self, widget) -> None:
        """Let files be dropped on `widget` too (the source card around this area): the whole
        card is the drop zone, the strip is where it says so."""
        if not HAS_DND or not hasattr(widget, 'drop_target_register'):
            return
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<DropEnter>>', self._on_drag_enter)
            widget.dnd_bind('<<DropLeave>>', self._on_drag_leave)
            widget.dnd_bind('<<Drop>>', self._on_drop)
        except Exception as error:
            logging.warning(f"Could not accept drops on {widget}: {error}")

    def _on_drag_enter(self, event):
        self._set_dragging(True)
        return event.action

    def _on_drag_leave(self, event):
        self._set_dragging(False)
        return event.action

    def _set_dragging(self, flag: bool) -> None:
        """Ring the zone in the accent while a file is over the card."""
        if flag == self._dragging:
            return
        self._dragging = flag
        try:
            self.zone.configure(style=name('drop' if flag else 'card', self.zone_outer_plane, 'TFrame'))
        except tk.TclError:
            pass

    @property
    def zone_outer_plane(self) -> str:
        """The plane the zone card sits on (a card's own `.plane` is what its children sit on)."""
        return getattr(self, 'plane', 'bg')

    # ------------------------------------------------------------------ #
    # Adding
    # ------------------------------------------------------------------ #
    def _features(self) -> tuple:
        return (bool(self.config.get('vision_enabled', False)),
                bool(self.config.get('file_processing_enabled', False)))

    def _audio_available(self):
        if self.config.get('audio_enabled', True) is not True:
            return False
        if hasattr(self.config, 'has_usable_audio_keys'):
            return self.config.has_usable_audio_keys()
        if hasattr(self.config, 'has_any_audio_capable'):
            return self.config.has_any_audio_capable()
        getter = getattr(self.config, 'get_audio_keys', None) or getattr(self.config, 'get_api_keys', None)
        if getter:
            return any(entry.get('audio_capable') is True for entry in getter())
        return False

    def _show_add_menu(self) -> None:
        """The Attach menu: what can be attached depends on what the user's models can read."""
        ctx = C.get()
        p = ctx.palette
        menu = ui.Menu(self, tearoff=0, bg=p.surface, fg=p.text, activebackground=p.soft,
                       activeforeground=p.soft_text, font=ctx.font('body'), relief='flat', bd=1)
        vision, files = self._features()
        audio = self._audio_available()
        if not vision and not files and not audio:
            menu.add_command(label="No upload features are on yet", state='disabled')
            menu.add_separator()
            menu.add_command(label="Open Settings", command=self._open_settings_hint,
                             image=ctx.icon('gear', 16, p.text), compound='left')
        else:
            if vision:
                menu.add_command(label="Images...", command=self._browse_images)
            if files:
                menu.add_command(label="Text files and documents...",
                                 command=self._browse_documents)
            if audio:
                menu.add_command(label='Audio and video...', command=self._browse_media)
        try:
            menu.tk_popup(self.add_btn.winfo_rootx(),
                          self.add_btn.winfo_rooty() + self.add_btn.winfo_height())
        finally:
            menu.grab_release()

    def _open_settings_hint(self) -> None:
        """Open API keys, where connection tests establish attachment support."""
        if self.on_open_settings_tab:
            self.on_open_settings_tab("API Key")
        else:
            from tkinter import messagebox
            messagebox.showinfo(tr("Set up attachments"),
                                tr("To attach images or files:\n\n"
                                   "1. Open Settings > API keys.\n"
                                   "2. Add a key and choose a compatible model.\n"
                                   "3. Test the key, then try attaching again."),
                                parent=self)

    def add_file(self, file_path, show_warning=False):
        """Add a file attachment. Returns whether it was added."""
        self._last_rejection = ''
        if not os.path.exists(file_path):
            log_event('Attachments', 'rejected', ext=os.path.splitext(file_path)[1].lower(), reason='missing')
            return False
        added = self._add_file(file_path, show_warning)
        # By kind and size only: the name is the user's (Decision 86)
        ext = os.path.splitext(file_path)[1].lower()
        try:
            kb = round(os.path.getsize(file_path) / 1024, 1)
        except OSError:
            kb = None
        if added:
            kind = next((item['type'] for item in self.attachments if item['path'] == file_path), '')
            log_event('Attachments', 'added', ext=ext, kind=kind, kb=kb, count=len(self.attachments))
        else:
            log_event('Attachments', 'rejected', ext=ext, kb=kb, reason=self._rejection_reason or 'unknown')
        return added

    def _add_file(self, file_path, show_warning):
        self._rejection_reason = ''
        ext = os.path.splitext(file_path)[1].lower()
        is_image = ext in IMAGE_EXTENSIONS
        is_audio = ext in MEDIA_EXTENSIONS
        vision, files = self._features()

        if is_audio:
            if self.config.get('audio_enabled', True) is not True:
                self._rejection_reason = 'audio_off'
                self._last_rejection = tr('Audio translation is disabled. Enable it in Settings > API keys.')
                self._warn(show_warning, 'Audio is off', self._last_rejection)
                return False
            if not self._audio_available():
                self._rejection_reason = 'no_audio_key'
                self._last_rejection = tr('Add and test an Audio key in Settings > API keys first.')
                self._warn(show_warning, 'Audio is not ready', self._last_rejection)
                return False
            if any(item['type'] == 'audio' and item['path'] != file_path for item in self.attachments):
                self._rejection_reason = 'second_media_file'
                self._last_rejection = tr('Translate one audio or video file at a time.')
                self._warn(show_warning, 'Audio is not ready', self._last_rejection)
                return False

        if is_image and not vision:
            self._rejection_reason = 'no_vision_key'
            self._warn(show_warning, "Images are off",
                       tr("Image attachments need a key that can read images.\n\nTest a compatible key in Settings > API keys, then try again."))
            return False
        if not is_image and not is_audio and not files:
            self._rejection_reason = 'no_file_key'
            self._warn(show_warning, "Files are off",
                       tr("File attachments need a working key.\n\nTest a key in Settings > API keys, then try again."))
            return False
        if not is_image and not is_audio:
            try:
                validate_document(file_path)
            except (OSError, ValueError) as error:
                self._rejection_reason = 'unreadable_document'
                self._last_rejection = tr(str(error))
                self._warn(show_warning, "Could not read file", self._last_rejection)
                return False
        if any(a['path'] == file_path for a in self.attachments):
            return True                          # already there: nothing to add twice

        self.attachments.append({'type': 'audio' if is_audio else 'image' if is_image else 'file', 'path': file_path})
        self._render_item(file_path, is_image)
        self._update_visibility()
        if self.on_change:
            self.on_change()
        return True

    def _warn(self, show: bool, title: str, message: str) -> None:
        if show:
            from tkinter import messagebox
            messagebox.showwarning(tr(title), message, parent=self)

    # ------------------------------------------------------------------ #
    # Tiles
    # ------------------------------------------------------------------ #
    def _render_item(self, file_path: str, is_image: bool) -> None:
        """One tile: thumbnail or file icon, name over kind, a remove button."""
        ctx = C.get()
        px = ctx.px
        filename = os.path.basename(file_path)
        tile = ui.Card(self.tile_row, on=self.zone.plane, padding=(8, 6, 4, 6))
        tile.columnconfigure(1, weight=1)
        picture = ui.Label(tile, '', kind='body')
        image = self._thumbnail(file_path) if is_image else None
        if image is None:
            image = ctx.icon(FILE_ICONS.get(os.path.splitext(file_path)[1].lower(), UNKNOWN_FILE_ICON), 24)
        picture.configure(image=image)
        picture.grid(row=0, column=0, rowspan=2, padx=(0, px(8)))
        title = ui.Label(tile, shorten(filename), kind='body', localize=False)
        title.grid(row=0, column=1, sticky='w')
        caption = ui.Label(tile, kind_text(file_path, is_image), kind='muted')
        caption.grid(row=1, column=1, sticky='w')
        tile.title_label, tile.caption_label = title, caption
        remove = ui.IconButton(tile, 'x-lg', command=lambda p=file_path: self._remove_item(p), size=12,
                               label=f'Remove {filename}')
        remove.grid(row=0, column=2, rowspan=2, padx=(px(8), 0))
        for widget in (tile, picture, title, caption):
            widget.bind('<Double-Button-1>', lambda e, p=file_path: self._open_file(p))
        for widget in (tile, picture, title, caption):
            Tooltip(widget, f"{filename}\nDouble-click to open")
        self.tiles[file_path] = tile
        self._layout_signature = None
        self._reflow()

    def _thumbnail(self, file_path: str):
        """A THUMBNAIL_PX square picture of an image, or None when it cannot be read."""
        side = C.get().px(THUMBNAIL_PX)
        try:
            with Image.open(file_path) as source:
                picture = source.convert('RGBA')
            picture.thumbnail((side, side))
            photo = ImageTk.PhotoImage(picture)
        except Exception as error:
            # The tile falls back to a file icon; the log says why (the exception's text could hold the path)
            log_event('Attachments', 'thumbnail.fail', level=logging.WARNING,
                      ext=os.path.splitext(file_path)[1].lower(), error=type(error).__name__)
            return None
        self.thumbnails.append(photo)
        return photo

    def _reflow(self) -> None:
        """Wrap the tiles into rows of the width there is. Only when the arrangement would change:
        re-gridding resizes the row, which fires <Configure> again."""
        try:
            available = self.tile_row.winfo_width()
            tiles = [self.tiles[a['path']] for a in self.attachments if a['path'] in self.tiles]
            if not tiles:
                return
            gap = C.get().px(8)
            if available > 1:
                text_width = max(C.get().px(40), available - C.get().px(108))
                for tile in tiles:
                    if getattr(tile, '_text_width', None) != text_width:
                        tile._text_width = text_width
                        tile.title_label.configure(wraplength=text_width)
                        tile.caption_label.configure(wraplength=text_width)
            widths = [t.winfo_reqwidth() for t in tiles]
            rows, row, used = [], [], 0
            for tile, width in zip(tiles, widths):
                if row and available > 1 and used + width + gap > available:
                    rows.append(row)
                    row, used = [], 0
                row.append(tile)
                used += width + gap
            rows.append(row)
            signature = tuple(tuple(str(t) for t in r) for r in rows)
            if signature == self._layout_signature:
                return
            self._layout_signature = signature
            for tile in tiles:
                tile.grid_forget()
            for r, members in enumerate(rows):
                for c, tile in enumerate(members):
                    tile.grid(row=r, column=c, sticky='w', padx=(0, gap), pady=(0, gap if r < len(rows) - 1 else 0))
        except tk.TclError:
            pass

    def _open_file(self, file_path: str) -> None:
        """Open a file with the system's default application."""
        from tkinter import messagebox
        if not os.path.exists(file_path):
            messagebox.showwarning("File not found",
                                   f"The file no longer exists:\n{os.path.basename(file_path)}",
                                   parent=self)
            return
        try:
            os.startfile(file_path)
            logging.debug(f"Opened a {os.path.splitext(file_path)[1].lower()} file")
        except Exception as error:
            logging.error(f"Failed to open a {os.path.splitext(file_path)[1].lower()} file: {type(error).__name__}")
            messagebox.showerror("Cannot open file",
                                 f"Could not open:\n{os.path.basename(file_path)}\n\n{error}", parent=self)

    def _remove_item(self, path: str) -> None:
        """Remove one attachment."""
        self.attachments = [a for a in self.attachments if a['path'] != path]
        tile = self.tiles.pop(path, None)
        if tile is not None:
            tile.destroy()
        self._layout_signature = None
        self._update_visibility()
        self._reflow()
        if self.on_change:
            self.on_change()

    def clear(self) -> None:
        """Clear all attachments."""
        self.attachments = []
        self.thumbnails = []
        for tile in self.tiles.values():
            tile.destroy()
        self.tiles = {}
        self._layout_signature = None
        self._update_visibility()
        if self.on_change:
            self.on_change()

    def get_attachments(self) -> List[dict]:
        """The attachments, in the order they were added."""
        return self.attachments

    def _update_visibility(self) -> None:
        """Clear all and the tile rows exist only while there is something attached."""
        px = C.get().px
        collapsed = self._compact and not self._compact_open
        self.hint.configure(text=f'{len(self.attachments)} attached' if self._compact else 'Drop files here')
        if self._compact and self.attachments:
            self.expand_btn.pack(side='right')
            self.expand_btn.icon_name = 'chevron-right' if collapsed else 'chevron-down'
            self.expand_btn.set_on(False)
        else:
            self.expand_btn.pack_forget()
        if collapsed:
            self.clear_btn.pack_forget()
            self.tile_row.pack_forget()
            self._fit_header()
            return
        if self.attachments:
            if not self.clear_btn.winfo_manager():
                self.clear_btn.pack(side='right')
            if not self.tile_row.winfo_manager():
                self.tile_row.pack(fill='x', pady=(px(8), 0))
        else:
            self.clear_btn.pack_forget()
            self.tile_row.pack_forget()
        self._fit_header()

    def set_compact(self, compact):
        if bool(compact) != self._compact:
            self._compact = bool(compact)
            self._compact_open = False
            self._update_visibility()

    def _toggle_compact(self):
        self._compact_open = not self._compact_open
        self._update_visibility()

    # ------------------------------------------------------------------ #
    # Browsing
    # ------------------------------------------------------------------ #
    def _browse_images(self) -> None:
        if not self.config.get('vision_enabled', False):
            self._warn(True, "Images are off",
                       tr("Image attachments need a key that can read images.\n\nTest a compatible key in Settings > API keys, then try again."))
            return
        files = filedialog.askopenfilenames(
            filetypes=[("Images", " ".join('*' + e for e in IMAGE_EXTENSIONS))], parent=self)
        for path in files:
            self.add_file(path, show_warning=True)

    def _browse_media(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[
            (tr('Audio and video'), ' '.join('*' + suffix for suffix in MEDIA_EXTENSIONS))])
        if path:
            self.add_file(path, show_warning=True)

    def _browse_documents(self) -> None:
        if not self.config.get('file_processing_enabled', False):
            self._warn(True, "Files are off",
                       tr("File attachments need a working key.\n\nTest a key in Settings > API keys, then try again."))
            return
        files = filedialog.askopenfilenames(
            filetypes=[("Text files and documents", " ".join('*' + e for e in DOCUMENT_EXTENSIONS)),
                       ("All files", "*")], parent=self)
        for path in files:
            self.add_file(path, show_warning=True)

    # ------------------------------------------------------------------ #
    # Dropping
    # ------------------------------------------------------------------ #
    def _on_drop(self, event):
        """Files dropped on the card: add what the user's models can read, say once what was left."""
        self._set_dragging(False)
        paths = parse_dropped_paths(event.data)
        if not paths:
            logging.warning("Drop event has no data")
            return
        vision, files = self._features()
        rejected_images, rejected_files, rejected_unsupported = [], [], []
        for path in paths:
            if not os.path.exists(path):
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext in MEDIA_EXTENSIONS:
                if not self.add_file(path):
                    rejected_unsupported.append(os.path.basename(path) + ': ' + self._last_rejection)
            elif ext in IMAGE_EXTENSIONS:
                if vision:
                    if not self.add_file(path):
                        rejected_unsupported.append(os.path.basename(path))
                else:
                    rejected_images.append(os.path.basename(path))
            else:
                if files:
                    if not self.add_file(path):
                        rejected_unsupported.append(os.path.basename(path) + ': ' + self._last_rejection)
                else:
                    rejected_files.append(os.path.basename(path))
        message = self._rejection_message(rejected_images, rejected_files, rejected_unsupported)
        if message:
            from tkinter import messagebox
            messagebox.showwarning(tr("Some files were left out"), tr(message), parent=self)

    @staticmethod
    def _rejection_message(images: List[str], files: List[str], unsupported: List[str]) -> str:
        """One note for everything a drop left out ('' when nothing was)."""
        parts = []
        for names, heading in ((images, "Images left out (Upload Image Mode is off):"),
                               (files, "Files left out (Upload File Mode is off):"),
                               (unsupported, "Not supported:")):
            if names:
                text = heading + "\n" + "\n".join(names[:3])
                if len(names) > 3:
                    text += f"\n... and {len(names) - 3} more"
                parts.append(text)
        return "\n\n".join(parts)
