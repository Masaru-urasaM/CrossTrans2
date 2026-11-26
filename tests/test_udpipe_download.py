"""
Tests for NLPManager._download_udpipe_model (security audit S1/S2): a UDPipe model is only ever fetched over
a verified TLS connection, checked against a pinned SHA-256 and moved into place atomically. Nothing here
touches the network or the user's profile: urlopen is replaced and the model directory is a tmp_path.
"""
import hashlib
import inspect
import io
import re
import ssl
import urllib.error
import urllib.request

import pytest

from src.core import nlp_manager as nm

NAME = "english-ewt-ud-2.5"
PAYLOAD = b"udpipe-model-bytes" * 4096
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()



class FakeResponse(io.BytesIO):
    """A urlopen() result: readable in chunks and usable as a context manager."""


class DyingResponse:
    """Delivers one chunk, then the connection drops."""

    def __init__(self, first):
        self._first = first

    def read(self, size=-1):
        if self._first is not None:
            chunk, self._first = self._first, None
            return chunk
        raise ConnectionResetError("connection reset by peer")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def net(monkeypatch, tmp_path):
    """Route model storage into tmp_path, pin PAYLOAD for NAME and record every urlopen call."""
    monkeypatch.setattr(nm, "get_udpipe_model_dir", lambda: str(tmp_path))
    monkeypatch.setitem(nm.UDPIPE_MODEL_SHA256, NAME, DIGEST)
    state = type("Net", (), {})()
    state.calls = []
    state.responder = lambda: FakeResponse(PAYLOAD)

    def fake_urlopen(url, *args, **kwargs):
        state.calls.append((url, args, kwargs))
        result = state.responder()
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return state


def download(name=NAME):
    return nm.NLPManager()._download_udpipe_model(name)


def leftovers(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir())


class TestVerifiedDownload:
    def test_a_matching_download_is_installed_without_leftovers(self, net, tmp_path):
        ok, error = download()
        assert (ok, error) == (True, "")
        assert (tmp_path / f"{NAME}.udpipe").read_bytes() == PAYLOAD
        assert leftovers(tmp_path) == [f"{NAME}.udpipe"]

    def test_the_request_uses_the_default_verifying_context(self, net):
        download()
        assert len(net.calls) == 1
        url, _args, kwargs = net.calls[0]
        assert url == nm.UDPIPE_MODEL_URLS[NAME]
        assert "context" not in kwargs  # urllib then builds its own certificate-verifying context
        assert kwargs.get("timeout")

    def test_a_checksum_mismatch_installs_nothing(self, net, tmp_path):
        net.responder = lambda: FakeResponse(PAYLOAD + b"tampered")
        ok, error = download()
        assert ok is False
        assert "checksum" in error.lower()
        assert leftovers(tmp_path) == []

    def test_a_dropped_connection_leaves_no_partial_model(self, net, tmp_path):
        net.responder = lambda: DyingResponse(PAYLOAD[:1000])
        ok, error = download()
        assert ok is False
        assert error
        assert leftovers(tmp_path) == []

    def test_an_oversized_download_is_stopped(self, net, tmp_path, monkeypatch):
        monkeypatch.setattr(nm, "UDPIPE_MAX_MODEL_BYTES", 1024)
        ok, error = download()
        assert ok is False
        assert "too large" in error.lower()
        assert leftovers(tmp_path) == []

    def test_a_model_without_a_pinned_checksum_is_refused_before_any_request(self, net, monkeypatch):
        monkeypatch.delitem(nm.UDPIPE_MODEL_SHA256, NAME)
        ok, error = download()
        assert ok is False
        assert "checksum" in error.lower()
        assert net.calls == []

    def test_an_unknown_model_is_refused(self, net):
        ok, error = download("klingon-xyz-ud-9.9")
        assert ok is False
        assert net.calls == []


class TestCertificateFailures:
    @pytest.mark.parametrize("failure", [
        urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")),
        ssl.SSLError("handshake failure"),
    ])
    def test_a_tls_failure_is_final_and_never_retried_unverified(self, net, tmp_path, failure):
        net.responder = lambda: failure
        ok, error = download()
        assert ok is False
        assert len(net.calls) == 1
        assert "certificate" in error.lower()
        assert leftovers(tmp_path) == []

    def test_a_plain_network_error_reports_the_cause(self, net, tmp_path):
        net.responder = lambda: urllib.error.URLError("no route to host")
        ok, error = download()
        assert ok is False
        assert "no route to host" in error
        assert len(net.calls) == 1


class TestExistingFiles:
    def test_a_valid_existing_model_is_not_downloaded_again(self, net, tmp_path):
        (tmp_path / f"{NAME}.udpipe").write_bytes(PAYLOAD)
        assert download() == (True, "")
        assert net.calls == []

    def test_a_corrupt_existing_model_is_replaced(self, net, tmp_path):
        (tmp_path / f"{NAME}.udpipe").write_bytes(b"truncated")
        assert download() == (True, "")
        assert (tmp_path / f"{NAME}.udpipe").read_bytes() == PAYLOAD
        assert len(net.calls) == 1
        assert leftovers(tmp_path) == [f"{NAME}.udpipe"]


class TestPinnedCatalog:
    def test_every_model_url_has_a_pinned_digest(self):
        assert set(nm.UDPIPE_MODEL_URLS) == set(nm.UDPIPE_MODEL_SHA256)

    def test_every_offered_pack_can_be_installed(self):
        # A pack whose model has no pinned digest can never finish installing (Decision 90, Macedonian).
        for language, pack in nm.LANGUAGE_PACKS.items():
            if pack.udpipe_model:
                assert pack.udpipe_model in nm.UDPIPE_MODEL_SHA256, language

    def test_macedonian_is_still_a_translation_language_without_a_pack(self):
        from src.constants import LANGUAGES
        assert "Macedonian" in [entry[0] for entry in LANGUAGES]
        assert "Macedonian" not in nm.LANGUAGE_PACKS
        assert not any(name.startswith("macedonian") for name in nm.UDPIPE_MODEL_URLS)

    def test_pins_are_well_formed_and_belong_to_a_known_model(self):
        assert set(nm.UDPIPE_MODEL_SHA256) <= set(nm.UDPIPE_MODEL_URLS)
        for name, digest in nm.UDPIPE_MODEL_SHA256.items():
            assert re.fullmatch(r"[0-9a-f]{64}", digest), name

    def test_every_pack_model_has_a_url(self):
        for language, pack in nm.LANGUAGE_PACKS.items():
            if pack.udpipe_model:
                assert pack.udpipe_model in nm.UDPIPE_MODEL_URLS, language


class TestNoUnsafePatterns:
    def test_certificate_verification_is_never_switched_off(self):
        source = inspect.getsource(nm)
        assert "CERT_NONE" not in source
        assert "check_hostname = False" not in source
        assert "_create_unverified_context" not in source

    def test_a_language_pack_carries_no_shell_command(self):
        # S2: the unused post_install hook ran its text with shell=True.
        assert "post_install" not in {f for f in nm.LanguagePack.__dataclass_fields__}
        assert "shell=True" not in inspect.getsource(nm)
