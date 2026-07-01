import unicodedata
import re


def normalize_string(s: str) -> str:
    # Handle real non-breaking space and literal "xa0" sequence
    s = s.replace('\xa0', ' ').replace('xa0', ' ')
    # Smart quotes
    s = s.replace('‘', "'").replace('’', "'")
    s = s.replace('“', '"').replace('”', '"')
    # Accent-fold via NFD decomposition
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return s.strip()


def track_a_clean(s: str) -> str:
    s = normalize_string(s)
    s = re.sub(r'[®™©$]', '', s)
    s = re.sub(r'[/@.]', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip().lower()


def track_b_clean(s: str) -> str:
    s = track_a_clean(s)
    s = s.replace('-', ' ')
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def track_c_tokens(s: str, min_len: int = 4) -> list:
    s = normalize_string(s)
    s = re.sub(r'[^a-zA-Z0-9\s]', '', s)
    tokens = s.lower().split()
    return [t for t in tokens if len(t) >= min_len]
