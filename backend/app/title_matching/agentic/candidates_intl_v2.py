"""International v2's own candidate fetch -- forked from runner.py's
_db_search/_fetch_vespa_candidates/_post_lookup_search (international
branches), NOT a shared call. runner.py's versions stay frozen for v1;
these carry the extra fields intl v2's prompt/guardrail actually need
(master_movie_title, genre, genre2) and the restored country-aware Vespa
query (see semantic_index.py's b84eada-recovered YQL precedence fix).

MovieMasterIntl has no cover_image column, so candidate dicts here never
carry one -- unlike runner.py's international dicts, which pad it with "".
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Optional

from app.config import settings
from app.title_matching.agentic.runner import _trigram_search
from app.title_matching.normalizer import has_conflicting_ordinal, normalize_title
from app.title_matching.semantic_index import get_embedding

logger = logging.getLogger(__name__)

_VESPA_SCHEMA_INTL = "movie_master_intl"
_VESPA_ID_FIELD_INTL = "movie_master_intl_id"


def _row_to_dict(r) -> dict:
    return {
        "id": r.id,
        "movie_title": r.movie_title,
        "master_movie_title": r.master_movie_title or "",
        "release_date": str(r.release_date) if r.release_date else None,
        "country": r.country,
        "genre": r.genre or "",
        "genre2": r.genre2 or "",
    }


def _db_search_intl(query: str, country: Optional[str] = None) -> list[dict]:
    """Search MovieMasterIntl, scoped by country when given. Mirrors
    runner._db_search's international branch (ILIKE -> master_movie_title
    ILIKE -> trigram fallback, exact-match-then-shortest-title ranking) --
    kept in sync deliberately, forked rather than imported."""
    try:
        from sqlmodel import Session, select
        from app.database import engine as db_engine
        from app.models import MovieMasterIntl as Model

        with Session(db_engine) as session:
            stmt = select(Model).where(Model.movie_title.ilike(f"%{query}%"))
            if country:
                stmt = stmt.where(Model.country == country)
            stmt = stmt.limit(20)
            rows = session.exec(stmt).all()

            if not rows:
                master_stmt = select(Model).where(Model.master_movie_title.ilike(f"%{query}%"))
                if country:
                    master_stmt = master_stmt.where(Model.country == country)
                rows = session.exec(master_stmt.limit(20)).all()

            if rows:
                rows = sorted(
                    rows,
                    key=lambda r: (r.movie_title.lower() != query.lower(), len(r.movie_title)),
                )
            else:
                rows = _trigram_search(session, Model, query, country=country)

            return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        logger.warning("db_search_intl_failed query=%r error=%s", query, exc)
        return []


def fetch_db_candidates_intl(title: str, country: Optional[str] = None) -> list[dict]:
    """Best-effort keyword pre-fetch. Mirrors runner._fetch_db_candidates's
    title-cleaning (strip a leading colon-prefixed promo segment, strip
    parentheticals) before searching."""
    import re

    try:
        bare = normalize_title(title).cleaned
        if ":" in bare:
            after = bare.split(":")[-1].strip()
            if after:
                bare = after
        bare = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", bare).strip(" -:")
        return _db_search_intl(bare or title, country=country)
    except Exception as exc:
        logger.warning("db_candidate_fetch_intl_failed title=%r error=%s", title, exc)
        return []


def post_lookup_search_intl(
    claude_title: str, country: Optional[str], query_ordinal: Optional[int],
) -> list[dict]:
    """Re-search after an id=0 pick, using a title Claude identified.
    Mirrors runner._post_lookup_search: strip parentheticals, then filter
    out any hit whose ordinal conflicts with the query's (the permissive
    trigram fallback knows nothing about ordinals)."""
    import re

    post_query = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", claude_title).strip(" -:")
    hits = _db_search_intl(post_query or claude_title, country=country)

    if query_ordinal:
        hits = [h for h in hits if not has_conflicting_ordinal(h["movie_title"], query_ordinal)]

    return hits


def fetch_vespa_candidates_intl(title: str, country: Optional[str] = None) -> list[dict]:
    """Hybrid semantic (ANN) + BM25 search against the intl Vespa schema,
    scoped to `country` when given. Restores b84eada's country-aware query
    (see semantic_index.py's matching restoration) directly against the
    international schema, rather than through runner._fetch_vespa_candidates
    (which stays frozen, country-blind, for v1).

    The recall clause (ANN or BM25) is parenthesized as a group BEFORE the
    country filter -- YQL's `and` binds tighter than `or`, so an
    unparenthesized `nn or userQuery() and country contains "X"` would parse
    as `nn or (userQuery() and country contains "X")`, leaving the ANN
    branch country-blind.
    """
    hits = 10
    country_filter = ""
    if country:
        escaped_country = country.replace('"', '\\"')
        country_filter = f' and country contains "{escaped_country}"'
    try:
        embedding = get_embedding(title, settings)

        if embedding is not None:
            recall_clause = (
                f"(({{targetHits:{hits}}}nearestNeighbor(embedding,q_embedding)) "
                f"or userQuery())"
            )
            yql = f"select * from sources {_VESPA_SCHEMA_INTL} where {recall_clause}{country_filter}"
            body_dict = {
                "yql": yql,
                "query": title,
                "ranking": "hybrid",
                "input.query(q_embedding)": embedding,
                "hits": hits,
            }
        else:
            yql = f"select * from sources {_VESPA_SCHEMA_INTL} where userQuery(){country_filter}"
            body_dict = {
                "yql": yql,
                "query": title,
                "ranking": "hybrid",
                "hits": hits,
            }

        body = json.dumps(body_dict).encode()
        req = urllib.request.Request(
            "http://vespa:8080/search/",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            raw_hits = data.get("root", {}).get("children", [])
            return [
                {
                    "id": h.get("fields", {}).get(_VESPA_ID_FIELD_INTL),
                    "movie_title": h.get("fields", {}).get("title"),
                    "release_date": h.get("fields", {}).get("release_date"),
                    "relevance": h.get("relevance"),
                    "country": h.get("fields", {}).get("country"),
                }
                for h in raw_hits
            ]
    except Exception as exc:
        logger.warning("vespa_candidate_fetch_intl_failed title=%r error=%s", title, exc)
        return []
