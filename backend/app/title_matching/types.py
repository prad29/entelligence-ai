from dataclasses import dataclass
from typing import Optional


@dataclass
class NormalizedTitle:
    cleaned: str                    # promo-stripped, mojibake-fixed
    edition_markers: list[str]      # ["Live Action", "4K", etc.]
    country_code: Optional[str]     # "DE", "FR", "AU", "UK" or None
    event_type: str                 # "MOVIE" | "MULTI_FILM" | "NON_MOVIE" | "RERELEASE"
    franchise_hint: Optional[str]   # "harry_potter", "toy_story", etc.
    ordinal: Optional[int]          # 1–8 parsed from title


@dataclass
class CandidateResult:
    movie_master_id: int
    movie_title: str
    release_date: Optional[str]
    cover_image: Optional[str]
    score: float                    # 0–1
    source: str                     # "alias" | "franchise_map" | "fuzzy"
    raw_fuzzy_score: Optional[float] = None


@dataclass
class TitleMatchResult:
    suggested_movie_id: int
    suggested_movie_title: str
    canonical_movie_id: int         # after parent_id resolution (= suggested_movie_id if no valid parent)
    confidence: float
    decision: str                   # "AUTO_ACCEPT" | "REVIEW" | "REVIEW_NON_MOVIE" | "REVIEW_MULTI_FILM"
    reasoning: str
    evidence: dict
    cover_image: Optional[str] = None
    ticketing_poster_url: Optional[str] = None
    fired_ai: bool = False
