import re
import unicodedata
from typing import FrozenSet


_NOISE_PATTERN = re.compile(r"[^a-z0-9\s]")
_MULTI_SPACE = re.compile(r"\s+")

# Collapse "70 M M" → "70MM" (spaced-out unit letters after a digit)
_DIGIT_SPACED_LETTERS = re.compile(r"(\d+)\s+([a-zA-Z])\s+([a-zA-Z])(?=\s|$)")

# Collapse "70 MM" → "70MM", "35 mm" → "35mm" etc. before any other normalization
_DIGIT_SPACE_UNIT = re.compile(r"(\d+)\s+([a-zA-Z]+)")

# Matches the literal ASCII sequence x-a-0 (OCR artifact for \xa0)
_LITERAL_XA0 = re.compile(r"xa0", re.IGNORECASE)

# Track A: remove ® ™ © $, map / @ . to space, keep - + & '
_TRACK_A_CLEAN = re.compile(r"[®™©$]")
_TRACK_A_MAP = re.compile(r"[/@.]")

# Track B extends Track A: also map hyphens to space
_TRACK_B_MAP = re.compile(r"[-]")


def _fold_accents(text: str) -> str:
    """Decompose Unicode, strip combining marks, then recompose."""
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _pre_normalize(text: str) -> str:
    """
    Pre-processing applied before all tracks:
    - Replace real \xa0 (non-breaking space) with regular space
    - Replace literal 'xa0' sequence with empty string
    - Fold accents (CINÉ → CINE)
    - Normalize smart quotes / curly apostrophes
    - Collapse digit+space+unit (e.g. "70 MM" -> "70MM", "35 mm" -> "35mm")
    """
    # Real non-breaking space
    text = text.replace("\xa0", " ")
    # Literal xa0 sequence
    text = _LITERAL_XA0.sub("", text)
    # Accent folding
    text = _fold_accents(text)
    # Smart quotes → straight
    text = text.replace("'", "'").replace("'", "'")
    text = text.replace(""", '"').replace(""", '"')
    # Collapse "70 M M" -> "70MM" (unit split across spaces)
    text = _DIGIT_SPACED_LETTERS.sub(r"\1\2\3", text)
    # Collapse "70 MM" -> "70MM", "35 mm" -> "35mm"
    text = _DIGIT_SPACE_UNIT.sub(r"\1\2", text)
    return text


def normalize_string(text: str) -> str:
    """Lower-case and strip non-alphanumeric characters."""
    text = _pre_normalize(text)
    text = text.lower()
    text = _NOISE_PATTERN.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    return text


def track_a_clean(text: str) -> str:
    """
    Track A: light clean — remove ® ™ © $, map / @ . to space.
    Case-insensitive; keeps hyphens, +, &, '.
    """
    text = _pre_normalize(text)
    text = _TRACK_A_CLEAN.sub("", text)
    text = _TRACK_A_MAP.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text).strip().lower()
    return text


def track_b_clean(text: str) -> str:
    """Track B: Track A + hyphen→space, then stopwords removed."""
    STOPWORDS = {"the", "a", "an", "and", "or", "with", "in", "at", "by"}
    text = track_a_clean(text)
    text = _TRACK_B_MAP.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    tokens = text.split()
    filtered = [t for t in tokens if t not in STOPWORDS]
    return " ".join(filtered)


def track_c_tokens(text: str) -> FrozenSet[str]:
    """
    Track C: all alnum-only tokens from the normalized text.

    No length filter here — short tokens like "xd", "x", "gtx" are the
    discriminating suffix of multi-word keywords ("Luxury Lounger XD",
    "Screen X", "GTX DUBBED") and must be retained so the engine can
    require ALL keyword tokens to be present in the query.
    The concat path in the engine applies its own min_len guard separately.
    """
    tokens = normalize_string(text).split()
    return frozenset(t for t in tokens if t)
