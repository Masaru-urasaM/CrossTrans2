"""GitHub update requests with explicit credentials and bounded credential forwarding."""
import re
import urllib.request
from urllib.parse import urlsplit

from src.constants import GITHUB_REPO

API_ROOT = f'https://api.github.com/repos/{GITHUB_REPO}'
TOKEN_REQUIRED = 'Private updates need a GitHub token. Add one in Settings > About > GitHub update token (optional).'
ACCESS_DENIED = 'GitHub denied update access. Check token expiry and Contents: read permission for CrossTrans2.'
NO_RELEASE = 'No published release is available, or the token cannot access this repository.'


def validate_token(value):
    """Reject header injection without ever including the submitted value in an error."""
    value = value.strip()
    if value and not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('The GitHub token must contain only letters, numbers, underscores or hyphens.')
    return value


def api_url(url):
    parts = urlsplit(url)
    return (parts.scheme == 'https' and parts.netloc == 'api.github.com'
            and parts.path.startswith(urlsplit(API_ROOT).path + '/')
            and not parts.fragment)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward a credential to another origin, or follow an HTTP downgrade."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != 'https' or target.username or target.password:
            raise ValueError('GitHub update redirects must use HTTPS without URL credentials.')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and (urlsplit(req.full_url).netloc != target.netloc):
            redirected.remove_header('Authorization')
        return redirected


def open_request(url, *, token='', binary=False, timeout=30, context=None):
    """Only the selected repository's API may receive the explicit update token."""
    headers = {'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json',
               'User-Agent': 'CrossTrans2', 'X-GitHub-Api-Version': '2026-03-10'}
    if token:
        if not api_url(url):
            raise ValueError('Private update assets must use the CrossTrans2 GitHub API.')
        headers['Authorization'] = 'Bearer ' + validate_token(token)
    request = urllib.request.Request(url, headers=headers)
    if not token:
        return urllib.request.urlopen(request, timeout=timeout, context=context)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), SafeRedirect())
    return opener.open(request, timeout=timeout)
