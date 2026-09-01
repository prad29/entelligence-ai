"""Image fetch + framing heuristic for lobby-check.

Ported from mmvision.py's fetch_image/image_framing (repo root prototype),
with two changes: httpx instead of requests (already a backend dependency),
and an SSRF guard. Unlike every other Bedrock-calling code path in this
backend, THIS server performs an HTTP GET against a caller-supplied URL, and
the worker process's environment carries real static AWS credentials (see
docs/plans/2026-09-01-lobby-check-design.md §3.3) — so a bad image_url is a
live SSRF vector, not just a bad input.
"""

from __future__ import annotations

import io
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.lobby_check.errors import LobbyCheckImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Bedrock cap is 5 MB base64
FRAMING_BY_WIDTH = {629: "close", 768: "wide"}

# application/octet-stream is included deliberately: S3 defaults to it for
# any object uploaded without an explicit Content-Type (a common case for
# this bucket in practice), so it is not itself a signal that the object
# isn't a real image -- image_framing's actual PIL decode is what catches a
# genuinely non-image body; this check only exists to reject obvious
# non-image responses (an HTML error page, a JSON error body, ...).
_ALLOWED_CONTENT_TYPES = (
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "application/octet-stream",
)


def _allowed_hosts() -> set[str]:
    return {
        h.strip().lower()
        for h in (settings.LOBBY_CHECK_ALLOWED_URL_HOSTS or "").split(",")
        if h.strip()
    }


def _assert_safe_url(url: str) -> str:
    """Raises LobbyCheckImageError unless `url` is https, on an allow-listed
    host, and does not resolve to a private/loopback/link-local/reserved
    address. schemas.py's pydantic validator already rejects a disallowed
    host at submission time; this re-checks at the point the fetch actually
    happens rather than trusting that nothing in between could let a bad
    URL through — the resolved-IP check in particular has no submission-time
    equivalent at all.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise LobbyCheckImageError(f"image_url must be an https URL: {url!r}")

    host = parsed.hostname or ""
    allowed = _allowed_hosts()
    if allowed and host.lower() not in allowed:
        raise LobbyCheckImageError(f"image_url host {host!r} is not allow-listed")

    try:
        infos = socket.getaddrinfo(host, 443)
    except socket.gaierror as exc:
        raise LobbyCheckImageError(f"could not resolve image_url host {host!r}: {exc}") from exc

    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise LobbyCheckImageError(
                f"image_url host {host!r} resolves to a non-public address ({addr})"
            )

    return url


def fetch_image(url: str) -> bytes:
    """Fetch one image, enforcing the SSRF guard and the size/content-type
    caps. Raises LobbyCheckImageError on any failure — deterministic, so
    the caller (lobby_check_row) must not Celery-retry it.
    """
    safe_url = _assert_safe_url(url)

    try:
        with httpx.stream(
            "GET",
            safe_url,
            timeout=settings.LOBBY_CHECK_IMAGE_FETCH_TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"User-Agent": "amenity-lobby-check/1.0"},
        ) as resp:
            if resp.status_code != 200:
                raise LobbyCheckImageError(
                    f"image fetch returned HTTP {resp.status_code} for {url!r}"
                )

            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type and content_type not in _ALLOWED_CONTENT_TYPES:
                raise LobbyCheckImageError(
                    f"image fetch returned unsupported content-type {content_type!r} for {url!r}"
                )

            declared_length = resp.headers.get("content-length")
            if declared_length and int(declared_length) > MAX_IMAGE_BYTES:
                raise LobbyCheckImageError(
                    f"image is {int(declared_length) / 1e6:.1f} MB per Content-Length, "
                    f"over the {MAX_IMAGE_BYTES / 1e6:.0f} MB Bedrock cap"
                )

            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    # A lying/missing Content-Length must not let an
                    # oversized body get buffered in full before this check.
                    raise LobbyCheckImageError(
                        f"image exceeded the {MAX_IMAGE_BYTES / 1e6:.0f} MB Bedrock cap "
                        f"while streaming for {url!r}"
                    )
                chunks.append(chunk)
    except httpx.TransportError as exc:
        raise LobbyCheckImageError(f"image fetch failed for {url!r}: {exc}") from exc

    body = b"".join(chunks)
    if not body:
        raise LobbyCheckImageError(f"empty body for {url!r}")
    return body


def image_framing(data: bytes) -> tuple[str, int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
    return FRAMING_BY_WIDTH.get(w, "unknown"), w, h
