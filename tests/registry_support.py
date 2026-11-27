"""Recorded data and a loopback server for the model-registry tests (Decision 110).

`tests/fixtures/registry_catalog_trimmed.json` is the registry's real catalog (`data/catalog.json` of the model-registry
project, 847 models) cut to at most 8 models per provider: the app's default models first, then one model that reads
images, one deprecated chat model, one that is not a chat model, and active or preview chat models for the rest. The
git snapshot has no `build` block, so the block of a real local build of the same day was added: the served catalog
has one. `tests/fixtures/legacy_config_v2.json` is the live `/v1/config` payload of the crosstrans Worker, captured the
same day, unchanged.

The network is refused in tests except loopback (tests/quiet_session.py), so a `RegistryServer` on 127.0.0.1 plays both
the registry and the Worker. Requests really go through urllib: what it sends, `User-Agent` included, is what is
recorded.
"""
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / 'fixtures'

REGISTRY_PREFIX = '/registry'
WORKER_PATH = '/worker/v1/config'
CATALOG_PATH = REGISTRY_PREFIX + '/v1/catalog'
ETAG = 'W/"20261127T093512Z-local"'


def load_catalog():
    """A fresh copy of the trimmed registry catalog (tests change it)."""
    return copy.deepcopy(json.loads((FIXTURES / 'registry_catalog_trimmed.json').read_text(encoding='utf-8')))


def load_legacy():
    """A fresh copy of the recorded Worker payload."""
    return copy.deepcopy(json.loads((FIXTURES / 'legacy_config_v2.json').read_text(encoding='utf-8')))


class RegistryServer:
    """A loopback HTTP server that answers like the registry (`/registry/v1/catalog`) and the Worker (`/worker/v1/config`).

    `registry` and `worker` are what each answers: a (status, body bytes) pair, or None for "close the connection".
    `hits` records every request as (path, {lowercase header: value}), in order."""

    def __init__(self):
        self.registry = (200, json.dumps(load_catalog()).encode('utf-8'))
        self.worker = (200, json.dumps(load_legacy()).encode('utf-8'))
        self.hits = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                headers = {key.lower(): value for key, value in self.headers.items()}
                outer.hits.append((self.path, headers))
                answer = outer.registry if self.path == CATALOG_PATH else outer.worker if self.path == WORKER_PATH else (404, b'{}')
                if answer is None:
                    self.close_connection = True
                    return
                status, body = answer
                if self.path == CATALOG_PATH and status == 200 and headers.get('if-none-match') == ETAG:
                    status, body = 304, b''
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                if self.path == CATALOG_PATH and status in (200, 304):
                    self.send_header('ETag', ETAG)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name='registry-test-server')

    @property
    def url(self):
        return f'http://127.0.0.1:{self._server.server_address[1]}'

    @property
    def registry_url(self):
        return self.url + REGISTRY_PREFIX

    @property
    def worker_url(self):
        return self.url + WORKER_PATH

    def serve_catalog(self, catalog):
        self.registry = (200, json.dumps(catalog).encode('utf-8'))

    def paths(self):
        return [path for path, _headers in self.hits]

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
