from __future__ import annotations

import logging
import re
from urllib.parse import ParseResult, urlparse, urlunparse

from app.title_matching.evidence_types import ExtractionPlatform, ExtractionTier

logger = logging.getLogger(__name__)

# Mapping from domain suffix/exact to (platform, tier).
# Ordered: more-specific entries should appear before catch-all GENERIC ones
# (though lookup is dict-based by exact netloc match).
_DOMAIN_TABLE: dict[str, tuple[str, str]] = {
    "gqtmovies.com": (ExtractionPlatform.CINEMAPLUS, ExtractionTier.T1_HTTP),
    "entertainmentcinemas.com": (ExtractionPlatform.CINEMAPLUS, ExtractionTier.T1_HTTP),
    "tickets.ifccenter.com": (ExtractionPlatform.AGILE_TICKETING, ExtractionTier.T1_HTTP),
    "pccmovies.com": (ExtractionPlatform.GENERIC, ExtractionTier.T1_HTTP),
    "viff.org": (ExtractionPlatform.VIFF, ExtractionTier.T1_HTTP),
    "silver.afi.com": (ExtractionPlatform.INDY_SYSTEMS, ExtractionTier.T2_HEADLESS),
    "wyomovies.com": (ExtractionPlatform.INDY_SYSTEMS, ExtractionTier.T2_HEADLESS),
    "cinepolisusa.com": (ExtractionPlatform.INDY_SYSTEMS, ExtractionTier.T2_HEADLESS),
    "penncinema.com": (ExtractionPlatform.INDY_SYSTEMS, ExtractionTier.T2_HEADLESS),
    "tickets.cineplex.de": (ExtractionPlatform.CINEPLEX_DE, ExtractionTier.T2_HEADLESS),
    "eventcinemas.com.au": (ExtractionPlatform.EVENT_CINEMAS, ExtractionTier.T2_HEADLESS),
    "kinepolis.fr": (ExtractionPlatform.GENERIC, ExtractionTier.T3_GEO_PROXY),
    "experience.cineworld.co.uk": (ExtractionPlatform.GENERIC, ExtractionTier.T3_GEO_PROXY),
    "apiv2.megaplextheatres.com": (ExtractionPlatform.GENERIC, ExtractionTier.T3_GEO_PROXY),
}

_INDY_SYSTEMS_DOMAINS: frozenset[str] = frozenset(
    {
        "silver.afi.com",
        "wyomovies.com",
        "cinepolisusa.com",
        "penncinema.com",
    }
)

# Regex for extracting the show slug from a VIFF path (matches both /checkout/ and /cart/)
_VIFF_SLUG_RE = re.compile(r"/(?:checkout|cart)/(?:event/)?([^/?#]+)")


def _repair_viff(parsed: ParseResult, url: str) -> str:
    """Redirect VIFF checkout/cart URLs to the whats-on page."""
    path = parsed.path
    if "/checkout/" not in path and "/cart/" not in path:
        return url

    match = _VIFF_SLUG_RE.search(path)
    if not match:
        logger.debug("VIFF URL repair: no slug found in path %r, returning unchanged", path)
        return url

    slug = match.group(1)
    repaired = urlunparse(
        (parsed.scheme, parsed.netloc, f"/whats-on/{slug}/", "", "", "")
    )
    logger.debug("VIFF URL repair: %r → %r", url, repaired)
    return repaired


def _repair_indy_systems(parsed: ParseResult, url: str) -> str:
    """Strip /checkout or /cart suffix from Indy Systems URLs."""
    path = parsed.path
    if "/checkout" not in path and "/cart" not in path:
        return url

    # Strip the checkout/cart segment and everything after it
    clean_path = re.sub(r"/(checkout|cart).*$", "", path)
    repaired = urlunparse(
        (parsed.scheme, parsed.netloc, clean_path, "", "", "")
    )
    logger.debug("Indy Systems URL repair: %r → %r", url, repaired)
    return repaired


def _repair_event_cinemas(parsed: ParseResult, url: str) -> str:
    """Strip URL fragment from Event Cinemas URLs."""
    if not parsed.fragment:
        return url

    repaired = urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, "")
    )
    logger.debug("Event Cinemas URL repair: stripped fragment from %r → %r", url, repaired)
    return repaired


def route(url: str) -> tuple[str, str, str]:
    """
    Route a ticketing URL to its platform, extraction tier, and repaired URL.

    Returns:
        (platform, tier, repaired_url)  where platform and tier are string
        constants from ExtractionPlatform / ExtractionTier.
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    # Strip www. prefix for matching
    if domain.startswith("www."):
        domain = domain[4:]

    entry = _DOMAIN_TABLE.get(domain)
    if entry:
        platform, tier = entry
        logger.debug("Matched domain %r → platform=%s tier=%s", domain, platform, tier)
    else:
        platform, tier = ExtractionPlatform.GENERIC, ExtractionTier.T1_HTTP
        logger.debug("No domain match for %r, defaulting to GENERIC/T1_HTTP", domain)

    # Apply URL repair
    if domain == "viff.org":
        repaired_url = _repair_viff(parsed, url)
    elif domain in _INDY_SYSTEMS_DOMAINS:
        repaired_url = _repair_indy_systems(parsed, url)
    elif domain == "eventcinemas.com.au":
        repaired_url = _repair_event_cinemas(parsed, url)
    else:
        repaired_url = url

    return platform, tier, repaired_url
