"""Phase 2 tests for app.lobby_check.images: the SSRF guard and the fetch/
framing logic, all offline (httpx.stream and socket.getaddrinfo are
monkeypatched — no real network calls).
"""

from __future__ import annotations

import io
import socket

import httpx
import pytest

from app.lobby_check import images
from app.lobby_check.errors import LobbyCheckImageError

ALLOWED_URL = "https://mm-intelligence.s3.amazonaws.com/lobby/x.jpg"


# --- _assert_safe_url --------------------------------------------------------

def test_rejects_non_https(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("54.1.2.3", 443))])
    with pytest.raises(LobbyCheckImageError, match="https URL"):
        images._assert_safe_url("http://mm-intelligence.s3.amazonaws.com/lobby/x.jpg")


def test_rejects_disallowed_host(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("54.1.2.3", 443))])
    with pytest.raises(LobbyCheckImageError, match="not allow-listed"):
        images._assert_safe_url("https://evil.example.com/lobby/x.jpg")


def test_rejects_private_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("10.0.0.5", 443))])
    with pytest.raises(LobbyCheckImageError, match="non-public address"):
        images._assert_safe_url(ALLOWED_URL)


def test_rejects_link_local_ip(monkeypatch):
    # e.g. the EC2/ECS metadata endpoint, 169.254.169.254 — the canonical
    # SSRF target this guard exists to block.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("169.254.169.254", 443))])
    with pytest.raises(LobbyCheckImageError, match="non-public address"):
        images._assert_safe_url(ALLOWED_URL)


def test_rejects_unresolvable_host(monkeypatch):
    def _raise(*a, **k):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", _raise)
    with pytest.raises(LobbyCheckImageError, match="could not resolve"):
        images._assert_safe_url(ALLOWED_URL)


def test_accepts_public_ip_on_allowed_host(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("54.1.2.3", 443))])
    assert images._assert_safe_url(ALLOWED_URL) == ALLOWED_URL


# --- fetch_image --------------------------------------------------------------

class _FakeStreamResponse:
    def __init__(self, status_code=200, headers=None, body=b"jpegbytes"):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self):
        # Chunk it to exercise the streaming size-check path.
        chunk_size = 4
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


@pytest.fixture(autouse=True)
def _resolve_to_public_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("54.1.2.3", 443))])


def test_fetch_image_success(monkeypatch):
    resp = _FakeStreamResponse(headers={"content-type": "image/jpeg", "content-length": "9"})
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: resp)
    assert images.fetch_image(ALLOWED_URL) == b"jpegbytes"


def test_fetch_image_accepts_octet_stream(monkeypatch):
    # S3's default Content-Type for an object uploaded without one set --
    # must NOT be rejected just because it isn't an image/* MIME type.
    resp = _FakeStreamResponse(headers={"content-type": "application/octet-stream", "content-length": "9"})
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: resp)
    assert images.fetch_image(ALLOWED_URL) == b"jpegbytes"


def test_fetch_image_rejects_non_200(monkeypatch):
    resp = _FakeStreamResponse(status_code=404)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: resp)
    with pytest.raises(LobbyCheckImageError, match="HTTP 404"):
        images.fetch_image(ALLOWED_URL)


def test_fetch_image_rejects_bad_content_type(monkeypatch):
    resp = _FakeStreamResponse(headers={"content-type": "text/html"})
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: resp)
    with pytest.raises(LobbyCheckImageError, match="unsupported content-type"):
        images.fetch_image(ALLOWED_URL)


def test_fetch_image_rejects_oversized_via_content_length(monkeypatch):
    resp = _FakeStreamResponse(headers={"content-length": str(images.MAX_IMAGE_BYTES + 1)})
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: resp)
    with pytest.raises(LobbyCheckImageError, match="Bedrock cap"):
        images.fetch_image(ALLOWED_URL)


def test_fetch_image_rejects_oversized_while_streaming(monkeypatch):
    # Content-Length lies (absent) — the streamed body itself exceeds the cap.
    oversized_body = b"x" * (images.MAX_IMAGE_BYTES + 100)
    resp = _FakeStreamResponse(headers={"content-type": "image/jpeg"}, body=oversized_body)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: resp)
    with pytest.raises(LobbyCheckImageError, match="Bedrock cap"):
        images.fetch_image(ALLOWED_URL)


def test_fetch_image_rejects_empty_body(monkeypatch):
    resp = _FakeStreamResponse(headers={"content-type": "image/jpeg"}, body=b"")
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: resp)
    with pytest.raises(LobbyCheckImageError, match="empty body"):
        images.fetch_image(ALLOWED_URL)


def test_fetch_image_wraps_transport_error(monkeypatch):
    def _raise(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "stream", _raise)
    with pytest.raises(LobbyCheckImageError, match="image fetch failed"):
        images.fetch_image(ALLOWED_URL)


# --- image_framing ------------------------------------------------------------

def _make_jpeg_bytes(width: int, height: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color="white").save(buf, format="JPEG")
    return buf.getvalue()


def test_image_framing_close():
    framing, w, h = images.image_framing(_make_jpeg_bytes(629, 900))
    assert (framing, w, h) == ("close", 629, 900)


def test_image_framing_wide():
    framing, w, h = images.image_framing(_make_jpeg_bytes(768, 512))
    assert (framing, w, h) == ("wide", 768, 512)


def test_image_framing_unknown():
    framing, _, _ = images.image_framing(_make_jpeg_bytes(500, 500))
    assert framing == "unknown"
