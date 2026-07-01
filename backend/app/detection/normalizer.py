import re
from typing import FrozenSet


_NOISE_PATTERN = re.compile(r"[^a-z0-9\s]")
_MULTI_SPACE = re.compile(r"\s+")


def normalize_string(text: str) -> str:
    """Lower-case and strip non-alphanumeric characters."""
    text = text.lower()
    text = _NOISE_PATTERN.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    return text


def track_a_clean(text: str) -> str:
    """Track A: exact normalized match."""
    return normalize_string(text)


def track_b_clean(text: str) -> str:
    """Track B: normalized with common noise words removed."""
    STOPWORDS = {"the", "a", "an", "and", "or", "with", "in", "at", "by"}
    tokens = normalize_string(text).split()
    filtered = [t for t in tokens if t not in STOPWORDS]
    return " ".join(filtered)


def track_c_tokens(text: str) -> FrozenSet[str]:
    """Track C: token set for fuzzy intersection matching."""
    tokens = normalize_string(text).split()
    return frozenset(t for t in tokens if len(t) >= 3)
