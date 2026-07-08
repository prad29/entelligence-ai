import re
from typing import Optional
from app.title_matching.types import NormalizedTitle

# Promo prefixes to strip (regex, case-insensitive)
_PROMO_PATTERNS = [
    r'MegaReelDeal\s*',
    r'KIDSHOW\s*[:\-]?\s*',
    r'\$\d+\s*',
    r'Summer Kids(?: Movie Series)?\s*[:\-]\s*',
    r'RBO Cinema Season \d{4}[-–]\d{2,4}\s*[:\-]\s*',
    r'Marathon\s*[:\-]\s*',
    r'FLASHBACK\s*[:\-]?\s*',
    r'\(Open Captioning\)\s*',
    r'\(OV[^)]*\)\s*',
]

_COUNTRY_MAP = {
    'germany': 'DE', 'france': 'FR', 'australia': 'AU', 'uk': 'UK',
    'usa': 'US', 'us': 'US', 'canada': 'CA',
}

_EDITION_KEYWORDS = [
    'Live Action', '25th Anniversary', '30th Anniversary', '4K', 'IMAX',
    '3D', 'OV', 'ENCORE', 'Re-issue', 'Reissue', 'Flashback', 'Special Edition',
]

_FRANCHISE_PATTERNS = {
    r'\bhp\b|harry potter': 'harry_potter',
    r'\btoy story\b': 'toy_story',
    r'\bmoana\b': 'moana',
    r'\bhttyd\b|how to train your dragon': 'httyd',
    r'\bbatman\b': 'batman',
}

_ORDINAL_PATTERNS = [
    (r'part\s*(\d+)', lambda m: int(m.group(1))),
    (r'(\d+)/(\d+)', lambda m: int(m.group(1))),   # "7/1" → 7 (part 1 of 7)
    (r'\b(\d+)\b(?!\s*(?:mm|min|fps))', lambda m: int(m.group(1))),
    (r'\b(I{1,3}|IV|VI{0,3}|IX|XI{0,2}|XIV|XV)\b', lambda m: _roman(m.group(1))),
]

_ROMAN = {
    'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5,
    'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10,
    'XI': 11, 'XII': 12, 'XIII': 13, 'XIV': 14, 'XV': 15,
}


def _roman(s: str) -> int:
    return _ROMAN.get(s.upper(), 0)


def normalize_title(raw: str) -> NormalizedTitle:
    try:
        import ftfy
        text = ftfy.fix_text(raw)
    except ImportError:
        text = raw

    # Strip promo prefixes
    for pat in _PROMO_PATTERNS:
        text = re.sub(pat, '', text, flags=re.IGNORECASE).strip()

    # Extract edition markers (before stripping)
    edition_markers = [kw for kw in _EDITION_KEYWORDS if kw.lower() in text.lower()]

    # Extract country code from trailing word
    country_code: Optional[str] = None
    for word, code in _COUNTRY_MAP.items():
        if re.search(r'\b' + word + r'\b', text, re.IGNORECASE):
            country_code = code
            text = re.sub(r'\b' + word + r'\b', '', text, flags=re.IGNORECASE).strip()
            break

    # Event type classification
    event_type = _classify_event(text)

    # Franchise hint
    franchise_hint: Optional[str] = None
    for pat, hint in _FRANCHISE_PATTERNS.items():
        if re.search(pat, text, re.IGNORECASE):
            franchise_hint = hint
            break

    # Ordinal extraction
    ordinal: Optional[int] = None
    for pat, extractor in _ORDINAL_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = extractor(m)
            if 1 <= val <= 20:          # guard against false positives like "2026"
                ordinal = val
                break

    return NormalizedTitle(
        cleaned=text.strip(' .,:-'),
        edition_markers=edition_markers,
        country_code=country_code,
        event_type=event_type,
        franchise_hint=franchise_hint,
        ordinal=ordinal,
    )


def _classify_event(text: str) -> str:
    t = text.lower()
    if any(kw in t for kw in (
        'double feature', 'saga', 'marathon', ' 1-4', ' 1–4', ' 5-7', ' 5–7',
        ' v vi vii', ' ov 1', 'parts 1',
    )):
        return 'MULTI_FILM'
    if any(kw in t for kw in (
        'season finale', 'opera', 'ballet', 'met live', 'roh', 'rbo',
        'concert', 'ufc', 'wwe', 'theatrical event',
    )):
        return 'NON_MOVIE'
    if any(kw in t for kw in (
        'anniversary', 'flashback', 're-issue', 'reissue', '4k remaster',
        'encore', 'special edition',
    )):
        return 'RERELEASE'
    return 'MOVIE'
