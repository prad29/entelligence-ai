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
    source: str                     # "alias" | "franchise_map" | "fuzzy" | "semantic"
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
    page_metadata: Optional[dict] = None
    # Second title guess for the id=0 DB post-lookup (runner._db_search fallback
    # attempt). Populated only for market="international" when the agent supplies
    # both a localized and an English/master title — e.g. suggested_movie_title
    # ="Deep Water" (English) with alternate_movie_title="Águas Mortais" (the
    # Brazil release title), or vice versa. Lets the post-lookup try both guesses
    # instead of failing outright when the agent picks the "wrong" one of the two
    # for whichever title MovieMasterIntl.movie_title actually stores.
    alternate_movie_title: Optional[str] = None
    # Anniversary/re-release form of the title (e.g. "Shrek 25th Anniversary"),
    # populated only for market="international" when the anniversary rule says
    # a dated re-release row should exist but wasn't found among the pre-fetched
    # candidates. Tried as the FIRST id=0 DB post-lookup attempt (before
    # suggested_movie_title/alternate_movie_title) since it's the most specific
    # guess when it's present.
    rerelease_lookup_title: Optional[str] = None
