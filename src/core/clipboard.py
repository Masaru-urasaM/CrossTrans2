"""
Clipboard management for CrossTrans.
Handles saving, restoring, and manipulating clipboard content.
"""
import ctypes
import logging
import time
from typing import Optional, Dict, Any

import pyperclip

# Windows-specific imports
try:
    import win32clipboard
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


class ClipboardManager:
    """Manages clipboard operations with proper preservation of content."""

    @staticmethod
    def get_sequence_number(user32=None) -> int:
        """Return the Windows clipboard sequence number, or 0 if unavailable."""
        try:
            api = user32 if user32 is not None else ctypes.windll.user32
            return int(api.GetClipboardSequenceNumber() or 0)
        except Exception:
            return 0

    @staticmethod
    def save_clipboard() -> Optional[Dict[int, Any]]:
        """Save current clipboard content including files/images."""
        if not HAS_WIN32:
            try:
                return {'text': pyperclip.paste()}
            except Exception:
                return None

        try:
            # Retry opening clipboard (it might be locked by Excel / Clipboard History)
            for _ in range(15):
                try:
                    win32clipboard.OpenClipboard()
                    break
                except Exception:
                    time.sleep(0.015)
            else:
                # Failed after retries
                return None

            formats = []
            fmt = win32clipboard.EnumClipboardFormats(0)
            while fmt:
                formats.append(fmt)
                fmt = win32clipboard.EnumClipboardFormats(fmt)

            saved = {}
            for fmt in formats:
                try:
                    saved[fmt] = win32clipboard.GetClipboardData(fmt)
                except Exception:
                    pass  # Skip unsupported formats
            return saved
        except Exception as e:
            logging.warning(f"Failed to save clipboard: {e}")
            return None
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass  # Clipboard may not be open

    @staticmethod
    def restore_clipboard(saved: Optional[Dict[int, Any]]):
        """Restore saved clipboard content."""
        if not saved:
            return

        if not HAS_WIN32:
            if 'text' in saved:
                try:
                    pyperclip.copy(saved['text'])
                except Exception:
                    pass  # Clipboard unavailable
            return

        try:
            for _ in range(15):
                try:
                    win32clipboard.OpenClipboard()
                    break
                except Exception:
                    time.sleep(0.015)
            else:
                return

            win32clipboard.EmptyClipboard()
            for fmt, data in saved.items():
                try:
                    win32clipboard.SetClipboardData(fmt, data)
                except Exception:
                    pass  # Skip unsupported formats
        except Exception as e:
            logging.warning(f"Failed to restore clipboard: {e}")
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass  # Clipboard may not be open

    @staticmethod
    def set_text(text: str):
        """Set clipboard to text."""
        if HAS_WIN32:
            try:
                for _ in range(15):
                    try:
                        win32clipboard.OpenClipboard()
                        break
                    except Exception:
                        time.sleep(0.015)
                else:
                    # Fallback if open fails
                    pyperclip.copy(text)
                    return

                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
            except Exception:
                pyperclip.copy(text)  # Fallback to pyperclip
            finally:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass  # Clipboard may not be open
        else:
            pyperclip.copy(text)

    @staticmethod
    def get_text(retries: int = 20, delay: float = 0.015) -> str:
        """Get Unicode text from clipboard, retrying transient OpenClipboard locks."""
        if HAS_WIN32:
            opened = False
            try:
                for _ in range(max(1, retries)):
                    try:
                        win32clipboard.OpenClipboard()
                        opened = True
                        break
                    except Exception:
                        time.sleep(delay)
                if not opened:
                    return ""
                if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return ""
                data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                if isinstance(data, str):
                    return data.split('\x00', 1)[0]
                return str(data) if data is not None else ""
            except Exception as exc:
                logging.debug("Clipboard get_text failed: %s", exc)
                return ""
            finally:
                if opened:
                    try:
                        win32clipboard.CloseClipboard()
                    except Exception:
                        pass
        try:
            return pyperclip.paste()
        except Exception:
            return ""  # Return empty on clipboard error
