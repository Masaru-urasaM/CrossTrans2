"""
Auto-Update System for CrossTrans.
"""
import os
import sys
import json
import logging
import tempfile
import subprocess
import urllib.request
import time
import re
from pathlib import Path
from uuid import uuid4
from typing import Optional, Callable

from packaging import version

from src.constants import VERSION, GITHUB_REPO
from src.core.ssl_pinning import get_ssl_context_for_url
from src.utils.github_updates import open_request, validate_token, TOKEN_REQUIRED, ACCESS_DENIED, NO_RELEASE


class UpdateError(Exception):
    """Base exception for update-related errors."""
    pass


class DownloadCancelledException(UpdateError):
    """Exception raised when user cancels download."""
    pass


# Update System Constants
UPDATE_CHECK_TIMEOUT = 30  # seconds - GitHub API request timeout
UPDATE_DOWNLOAD_TIMEOUT = 180  # seconds - Download timeout
UPDATE_CHECK_MAX_RETRIES = 3  # Max retry attempts for update check
UPDATE_THREAD_TIMEOUT = 60  # seconds - Thread join timeout
RELEASE_NOTES_MAX_LENGTH = 300  # characters - Truncate release notes
PROGRESS_WINDOW_SIZE = "350x200"  # Progress dialog dimensions
UPDATE_TOAST_DURATION = 5000  # milliseconds - Toast notification duration
STARTUP_UPDATE_DELAY = 3  # seconds - Delay before auto-check on startup
THREAD_NAMES = {
    'check': 'UpdateCheckThread',
    'download': 'DownloadThread',
    'monitor': 'UpdateMonitor',
    'startup': 'StartupUpdateCheck'
}


# Valid error types for telemetry
VALID_ERROR_TYPES = {'network', 'rate_limit', 'ssl', 'parse_error', 'timeout', 'other'}

# Error detection patterns (for classifying error messages)
ERROR_PATTERNS = {
    'network': ['connect', 'connection', 'internet', 'network', 'unreachable', 'dns'],
    'rate_limit': ['rate limit', '403', 'too many requests'],
    'ssl': ['ssl', 'certificate', 'tls', 'secure connection'],
    'parse_error': ['parse', 'invalid json', 'malformed', 'invalid response'],
    'timeout': ['timeout', 'timed out', 'time out'],
}


def classify_error_type(error_message: str) -> str:
    """Classify error type based on error message content.

    Args:
        error_message: The error message to classify

    Returns:
        Error type string from VALID_ERROR_TYPES ('network', 'rate_limit', etc.)
        Returns 'other' if no pattern matches.
    """
    if not error_message:
        return 'other'

    error_lower = error_message.lower()

    for error_type, patterns in ERROR_PATTERNS.items():
        if any(pattern in error_lower for pattern in patterns):
            return error_type

    return 'other'


# User-friendly error messages
ERROR_MESSAGES = {
    'network': (
        "Cannot connect to GitHub.\n\n"
        "Possible solutions:\n"
        "• Check your internet connection\n"
        "• Disable VPN/proxy temporarily\n"
        "• Check firewall settings\n"
        "• Try again in a few moments"
    ),
    'rate_limit': (
        "GitHub rate limit reached.\n\n"
        "Please try again in a few minutes."
    ),
    'ssl': (
        "SSL/Security error.\n\n"
        "Possible solutions:\n"
        "• Update Windows to latest version\n"
        "• Check system date/time is correct\n"
        "• Disable antivirus temporarily\n"
        "• Contact support if issue persists"
    ),
    'parse_error': (
        "Invalid response from GitHub.\n\n"
        "GitHub may be experiencing issues.\n"
        "Please try again later."
    ),
    'timeout': (
        "Update check timed out.\n\n"
        "Your connection may be slow.\n"
        "Please try again."
    )
}


def launch_installer(path, parameters):
    """Use the installer bootstrap's own elevation so it retains the original user token."""
    import ctypes
    from ctypes import wintypes
    shell = ctypes.windll.shell32.ShellExecuteW
    shell.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                      wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    shell.restype = ctypes.c_void_p
    result = shell(None, 'open', os.path.abspath(path), parameters, os.path.dirname(path), 1)
    if not result or result <= 32:
        raise OSError(f'Windows could not start the installer (code {result})')


class AutoUpdater:
    """Handles checking, downloading and installing updates from GitHub releases.

    Provides comprehensive update functionality with:
    - GitHub API integration for checking latest releases
    - Automatic download with progress tracking
    - SHA256 integrity verification
    - Installer-based installation with an elevation readiness handshake
    - Comprehensive error handling and logging
    """

    def __init__(self, test_mode: bool = False, mock_response: Optional[dict] = None,
                 *, access_token: str = "", require_auth: bool = False):
        """Initialize AutoUpdater.

        Args:
            test_mode: If True, uses mock_response instead of real GitHub API calls.
                       Useful for testing without network access.
            mock_response: Mock response dict to return in test mode.
                           Should have same structure as check_update() return value.
        """
        self.latest_version: Optional[str] = None
        self.exe_url: Optional[str] = None
        self.release_notes: str = ""
        self.download_path: Optional[str] = None
        self.expected_sha256: Optional[str] = None
        self.test_mode = test_mode
        self.mock_response = mock_response
        self._access_token = validate_token(access_token)
        self.require_auth = require_auth

    def check_update(self, max_retries: int = 3) -> dict:
        """Check GitHub for newer version with retry logic and comprehensive logging.

        Args:
            max_retries: Maximum number of retry attempts (default: 3)

        Returns:
            dict with keys:
            - has_update: bool
            - version: str (new version if available)
            - notes: str (release notes)
            - error: str (error message if failed)
        """
        # Test mode support
        if self.test_mode and self.mock_response:
            logging.info("TEST MODE: Using mock response")
            return self.mock_response

        if self.require_auth and not self._access_token:
            return {"has_update": False, "error": TOKEN_REQUIRED, "requires_auth": True}
        self.exe_url = None
        self.expected_sha256 = None
        logging.info("=" * 60)
        logging.info("UPDATE CHECK STARTED")
        logging.info(f"Current version: {VERSION}")
        logging.info(f"GitHub repo: {GITHUB_REPO}")

        for attempt in range(max_retries):
            try:
                logging.info(f"Update check attempt {attempt + 1}/{max_retries}")

                url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
                logging.info(f"Fetching: {url}")

                # Create SSL context with error handling
                try:
                    ctx = get_ssl_context_for_url(url)
                    logging.info("SSL context created successfully")
                except Exception as ssl_err:
                    logging.error(f"SSL context creation failed: {ssl_err}", exc_info=True)
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        logging.warning(f"Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    return {
                        'has_update': False,
                        'error': ERROR_MESSAGES['ssl']
                    }

                # Make API request
                logging.info("Sending GitHub API request...")
                with open_request(url, token=self._access_token, timeout=30, context=ctx) as resp:
                    logging.info(f"Response status: {resp.status}")
                    logging.debug(f"Response headers: {dict(resp.headers)}")

                    data = json.loads(resp.read().decode())
                    logging.debug(f"Response data keys: {list(data.keys())}")

                # Validate and parse response
                latest = data.get('tag_name', '').lstrip('v')
                logging.info(f"Latest version from GitHub: {latest}")

                if not latest:
                    logging.error("No tag_name in GitHub response")
                    return {
                        'has_update': False,
                        'error': ERROR_MESSAGES['parse_error']
                    }

                # Version comparison
                try:
                    current_ver = version.parse(VERSION)
                    latest_ver = version.parse(latest)
                    logging.info(f"Version comparison: {VERSION} ({current_ver}) vs {latest} ({latest_ver})")

                    if latest_ver > current_ver:
                        logging.info(f"New version available: {latest}")

                        # Find exe asset
                        asset_sha256 = None
                        for asset in data.get('assets', []):
                            if asset['name'].lower() == f'CrossTrans2Setup_v{latest}.exe'.lower():
                                self.exe_url = asset.get('url') if self._access_token else asset.get('browser_download_url')
                                digest = re.fullmatch(r'sha256:([0-9a-fA-F]{64})',
                                                      asset.get('digest') or '')
                                asset_sha256 = digest.group(1) if digest else None
                                logging.info(f"Found EXE asset: {self.exe_url}")
                                break

                        if not self.exe_url:
                            logging.warning("No .exe asset found in release")
                            return {
                                'has_update': False,
                                'error': 'No CrossTrans 2 installer found in the release'
                            }

                        self.latest_version = latest
                        self.release_notes = data.get('body', '')

                        # Try to extract SHA256 from release notes
                        sha256_match = re.search(r'SHA256:?\s*([0-9a-fA-F]{64})', self.release_notes, re.IGNORECASE)
                        if asset_sha256:
                            self.expected_sha256 = asset_sha256
                            logging.info('Using GitHub EXE asset SHA256: %s', asset_sha256)
                        elif sha256_match:
                            self.expected_sha256 = sha256_match.group(1)
                            logging.info(f"Found SHA256 checksum in release notes: {self.expected_sha256}")
                        else:
                            logging.warning("No SHA256 checksum found in release notes")
                            self.expected_sha256 = None

                        logging.info("Update check completed successfully - update available")
                        return {
                            'has_update': True,
                            'version': latest,
                            'notes': self.release_notes
                        }
                    else:
                        logging.info(f"No update available. Current version ({VERSION}) is up to date.")
                        return {'has_update': False, 'version': VERSION}

                except version.InvalidVersion as e:
                    logging.error(f"Invalid version format: {e}")
                    return {
                        'has_update': False,
                        'error': f'Version parsing error: {e}'
                    }

            except urllib.error.HTTPError as e:
                error_body = self._read_error_response(e) if not self._access_token else '(private response omitted)'
                logging.error('GitHub update HTTP %s', e.code)
                if self._access_token and e.code in (401, 403, 404):
                    if e.code == 403 and e.headers.get('X-RateLimit-Remaining') == '0':
                        return {'has_update': False, 'error': ERROR_MESSAGES['rate_limit']}
                    return {'has_update': False, 'error': NO_RELEASE if e.code == 404 else ACCESS_DENIED,
                            'requires_auth': e.code != 404}

                if e.code == 403:
                    # GitHub rate limit
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        logging.warning(f"GitHub rate limit (403), retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    else:
                        logging.error("GitHub rate limit exceeded after all retries")
                        return {
                            'has_update': False,
                            'error': ERROR_MESSAGES['rate_limit']
                        }

                elif e.code == 404:
                    # Repo not found - don't retry
                    logging.error("GitHub repository not found (404)")
                    return {
                        'has_update': False,
                        'error': 'Update service not available (repository not found)'
                    }

                elif e.code >= 500:
                    # Server error - retry
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        logging.warning(f"GitHub server error {e.code}, retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    else:
                        logging.error(f"GitHub server error {e.code} persisted after all retries")
                        return {
                            'has_update': False,
                            'error': f'GitHub server error: HTTP {e.code}'
                        }

                # Other HTTP errors - don't retry
                logging.error(f"HTTP error {e.code}: {error_body}")
                return {
                    'has_update': False,
                    'error': f'GitHub API error: HTTP {e.code}'
                }

            except urllib.error.URLError as e:
                logging.error(f"Network error: {e.reason}")

                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logging.warning(f"Network error, retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    logging.error("Network error persisted after all retries")
                    return {
                        'has_update': False,
                        'error': ERROR_MESSAGES['network']
                    }

            except Exception as e:
                logging.error(f"Unexpected error in update check: {e}", exc_info=True)
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logging.warning(f"Unexpected error, retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    return {
                        'has_update': False,
                        'error': f'Update check failed: {str(e)}'
                    }

        logging.error("Update check failed after all retry attempts")
        return {
            'has_update': False,
            'error': 'Update check failed after all retry attempts'
        }

    def _read_error_response(self, http_error) -> str:
        """Helper to safely read error response body."""
        try:
            return http_error.read().decode('utf-8')
        except Exception:
            return "(could not read error details)"

    def download(self, progress_callback: Optional[Callable[[int], None]] = None) -> dict:
        """Download the new version with SHA256 integrity verification.

        Args:
            progress_callback: Called with progress percentage (0-100)

        Returns:
            dict with keys:
            - success: bool
            - error: str (if failed)
        """
        if not self.exe_url or not self.latest_version:
            logging.error("Download attempted without update URL or version")
            return {'success': False, 'error': 'No update available'}

        if not getattr(sys, 'frozen', False):
            logging.warning("Download attempted from source (not frozen EXE)")
            return {'success': False, 'error': 'Auto-update only works with exe version'}

        temp_dir = None
        try:
            # Clean any previously downloaded temp file/directory from this updater instance
            self._cleanup_download()
            # Create temp directory
            temp_dir = tempfile.mkdtemp(prefix='crosstrans2_update_')
            self.download_path = os.path.join(temp_dir, f'CrossTrans2Setup_v{self.latest_version}.exe')
            logging.info(f"Download path: {self.download_path}")

            ctx = get_ssl_context_for_url(self.exe_url)

            logging.info(f"Starting download from: {self.exe_url}")
            with open_request(self.exe_url, token=self._access_token, binary=True,
                              timeout=180, context=ctx) as response:
                total = int(response.headers.get('Content-Length', 0))
                downloaded = 0
                logging.info(f"File size: {total} bytes ({total / 1024 / 1024:.1f} MB)")

                with open(self.download_path, 'wb') as f:
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)

                        if progress_callback:
                            progress_callback(min(100, int(downloaded * 100 / total)) if total > 0 else 0)

            logging.info(f"Download completed: {downloaded} bytes")

            # Verify SHA256 checksum if available
            if hasattr(self, 'expected_sha256') and self.expected_sha256:
                logging.info("Verifying download integrity with SHA256...")
                actual_sha256 = self._calculate_sha256(self.download_path)
                logging.info(f"Expected SHA256: {self.expected_sha256}")
                logging.info(f"Actual SHA256:   {actual_sha256}")

                if actual_sha256.lower() != self.expected_sha256.lower():
                    logging.error("SHA256 mismatch! Download may be corrupted.")
                    self._cleanup_download(temp_dir)
                    return {
                        'success': False,
                        'error': 'Downloaded file integrity check failed. Please try again.'
                    }

                logging.info("✓ Download integrity verified successfully!")
            else:
                self._cleanup_download(temp_dir)
                return {'success': False, 'error': 'The installer has no verified SHA256 digest'}

            return {'success': True}

        except Exception as e:
            self._cleanup_download(temp_dir)
            if self._access_token:
                logging.error('Private update download failed (%s)', type(e).__name__)
                return {'success': False, 'error': 'Private update download failed. Check token access and try again.'}
            logging.error(f"Download failed: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    @staticmethod
    def _remove_empty_temp_dir(directory: Optional[Path], prefix: str) -> None:
        if directory is None:
            return
        try:
            if directory.name.startswith(prefix):
                directory.rmdir()
        except OSError:
            pass

    def _cleanup_download(self, temp_dir: Optional[str] = None) -> None:
        """Delete a failed/corrupted download file and its empty crosstrans2_update_* folder."""
        path = self.download_path
        self.download_path = None
        parent = Path(temp_dir) if temp_dir else (Path(path).parent if path else None)
        if path:
            try:
                if os.path.exists(path):
                    os.unlink(path)
                    logging.info("Deleted update download")
            except OSError as del_err:
                logging.warning(f"Could not delete update download: {del_err}")
        self._remove_empty_temp_dir(parent, 'crosstrans2_update_')

    def _calculate_sha256(self, file_path: str) -> str:
        """Calculate SHA256 hash of a file.

        Args:
            file_path: Path to the file

        Returns:
            SHA256 hash as hex string
        """
        import hashlib

        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def install_and_restart(self) -> dict:
        """Launch the verified all-users installer; Inno handles elevation and file replacement."""
        if not self.download_path or not os.path.isfile(self.download_path):
            return {'success': False, 'error': 'No downloaded installer found'}
        if not self.expected_sha256:
            return {'success': False, 'error': 'The installer has no verified SHA256 digest'}
        try:
            actual = self._calculate_sha256(self.download_path)
            if actual.lower() != self.expected_sha256.lower():
                return {'success': False, 'error': 'Installer integrity check failed'}
            self._handoff_file = Path(tempfile.mkdtemp(prefix='crosstrans2_handoff_')) / 'ready'
            self._handoff_token = uuid4().hex
            parameters = subprocess.list2cmdline(['/SILENT', '/NORESTART', '/SP-', '/UPDATE=1',
                                                  f'/WAITPID={os.getpid()}',
                                                  f'/READYFILE={self._handoff_file}',
                                                  f'/READYTOKEN={self._handoff_token}'])
            launch_installer(self.download_path, parameters)
            logging.info('[Update] Waiting for the elevated installer to become ready')
            return {'success': True, 'pending': True}
        except Exception as error:
            self.cancel_install_handoff()
            logging.exception('[Update] Could not start the installer')
            return {'success': False, 'error': str(error)}


    def confirm_install_and_restart(self):
        """Exit only after the elevated installer acknowledged readiness, never on UAC cancellation."""
        file = getattr(self, '_handoff_file', None)
        try:
            ready = file is not None and file.read_text(encoding='utf-8') == self._handoff_token
        except OSError:
            ready = False
        if not ready:
            return False
        logging.info('[Update] Elevated installer ready; closing CrossTrans 2')
        logging.shutdown()
        os._exit(0)
        return True

    def cancel_install_handoff(self):
        """Withdraw a pending restart; the waiting installer must abort without replacing files."""
        file = getattr(self, '_handoff_file', None)
        self._handoff_file = None
        if file is not None:
            file.unlink(missing_ok=True)
            self._remove_empty_temp_dir(file.parent, 'crosstrans2_handoff_')
