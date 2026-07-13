from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Optional
from urllib.error import URLError

from app.config import settings
from app.title_matching.types import TitleMatchResult
from app.title_matching.agentic import (
    AgenticConfigError,
    AgenticSubprocessError,
    AgenticTimeoutError,
)
from app.title_matching.agentic.prompt_builder import build_prompt
from app.title_matching.agentic.result_parser import parse_agent_output

logger = logging.getLogger(__name__)


def run_agentic_match(
    title: str,
    show_date: Optional[str] = None,
    theater: Optional[str] = None,
    ticketing_url: Optional[str] = None,
    use_poster_vision: bool = False,
) -> TitleMatchResult:
    _check_sandbox_reachable()

    # Pre-fetch DB candidates before calling the sandbox so the agent
    # never needs to call localhost (the sidecar can't reach compose services).
    db_candidates = _fetch_db_candidates(title)
    vespa_candidates = _fetch_vespa_candidates(title)

    if use_poster_vision:
        _annotate_poster_availability(db_candidates)

    prompt = build_prompt(
        title, show_date, theater, ticketing_url,
        db_candidates=db_candidates,
        vespa_candidates=vespa_candidates,
        use_poster_vision=use_poster_vision,
    )

    tools = "WebFetch,WebSearch" if use_poster_vision else "WebSearch"

    logger.info(
        "agentic_match_start title=%r model=%s bedrock=%s db_hits=%d vespa_hits=%d poster_vision=%s",
        title, settings.AGENTIC_CLAUDE_MODEL, settings.AGENTIC_USE_BEDROCK,
        len(db_candidates), len(vespa_candidates), use_poster_vision,
    )

    stdout = _call_sandbox(prompt, tools)

    logger.debug("agentic_match_raw_output length=%d", len(stdout))
    result = parse_agent_output(stdout)

    # Retry once if parse produced a fallback (model stopped before outputting JSON)
    if result.suggested_movie_id == 0 and result.evidence.get("parse_error"):
        logger.warning(
            "agentic_parse_failed_retrying title=%r parse_error=%r",
            title, result.evidence["parse_error"][:100],
        )
        retry_prompt = (
            f"{prompt}\n\n"
            "IMPORTANT: Your previous response did not contain valid JSON output. "
            "You MUST respond with ONLY the JSON object and nothing else. "
            "No explanations, no tool calls — just the raw JSON."
        )
        try:
            stdout2 = _call_sandbox(retry_prompt, tools)
            result = parse_agent_output(stdout2)
            logger.info("agentic_retry_success title=%r id=%d", title, result.suggested_movie_id)
        except Exception as retry_exc:
            logger.warning("agentic_retry_failed title=%r error=%s", title, retry_exc)

    # If Claude identified the movie but couldn't match a DB id (id=0),
    # do a second DB lookup using Claude's identified movie_title.
    # This handles cases like "Graveyard Shift: CANNIBAL HOLOCAUST (New Restoration)"
    # where the pre-fetch found nothing but Claude correctly identified the film.
    if result.suggested_movie_id == 0 and result.suggested_movie_title and result.suggested_movie_title != "Unknown":
        logger.info(
            "agentic_post_lookup title=%r claude_identified=%r",
            title, result.suggested_movie_title,
        )
        # Strip parentheticals from Claude's identified title (e.g. "The Odyssey (L'Odyssée)" → "The Odyssey")
        post_query = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", result.suggested_movie_title).strip(" -:")
        post_hits = _db_search(post_query or result.suggested_movie_title)
        if post_hits:
            db_candidates = post_hits  # refresh for cover_image lookup below
            best = post_hits[0]
            result.suggested_movie_id = best["id"]
            result.canonical_movie_id = best["id"]
            if not result.suggested_movie_title or result.suggested_movie_title == "Unknown":
                result.suggested_movie_title = best["movie_title"]
            logger.info(
                "agentic_post_lookup_hit id=%d title=%r",
                best["id"], best["movie_title"],
            )

    # Attach cover_image from DB candidates (original pre-fetch or post-lookup)
    if result.suggested_movie_id:
        cover_lookup = {
            c["id"]: c.get("cover_image", "")
            for c in db_candidates
            if c.get("id")
        }
        img = cover_lookup.get(result.suggested_movie_id, "")
        if img and "noimage" not in img.lower():
            result.cover_image = img

    return result


def _call_sandbox(prompt: str, tools: str) -> str:
    """POST to the claude-sandbox sidecar and return raw stdout."""
    payload = json.dumps({
        "prompt": prompt,
        "model": settings.AGENTIC_CLAUDE_MODEL,
        "tools": tools,
        "timeout_seconds": settings.AGENTIC_TIMEOUT_SECONDS,
    }).encode()

    url = f"{settings.CLAUDE_SANDBOX_URL.rstrip('/')}/run"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=settings.AGENTIC_TIMEOUT_SECONDS + 10) as resp:
            body = json.loads(resp.read())
    except URLError as exc:
        raise AgenticConfigError(
            f"Claude sandbox unreachable at {url}. "
            "Ensure the claude-sandbox service is running and healthy. "
            f"Error: {exc}"
        )

    exit_code = body.get("exit_code", -1)
    timed_out = body.get("timed_out", False)
    stderr = body.get("stderr", "")
    stdout = body.get("stdout", "")

    if timed_out:
        raise AgenticTimeoutError(
            f"Agent timed out after {settings.AGENTIC_TIMEOUT_SECONDS}s for title. "
            "Increase AGENTIC_TIMEOUT_SECONDS or check claude-sandbox logs."
        )

    if exit_code != 0:
        excerpt = stderr[:500] if stderr else "(no stderr)"
        raise AgenticSubprocessError(
            f"Claude exited with code {exit_code}. "
            f"Check CLAUDE_CODE_USE_BEDROCK and AWS credentials. stderr: {excerpt}"
        )

    return stdout


def _check_sandbox_reachable() -> None:
    """Fail fast with a clear message if the sandbox sidecar isn't up."""
    url = f"{settings.CLAUDE_SANDBOX_URL.rstrip('/')}/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            if not data.get("claude_available"):
                raise AgenticConfigError(
                    "claude-sandbox is running but `claude` binary is not available inside it. "
                    "Rebuild the claude-sandbox image: docker compose build claude-sandbox"
                )
    except URLError as exc:
        raise AgenticConfigError(
            f"claude-sandbox not reachable at {settings.CLAUDE_SANDBOX_URL}. "
            "Start it with: docker compose up claude-sandbox. "
            f"Error: {exc}"
        )


def _fetch_db_candidates(title: str) -> list[dict]:
    """Best-effort keyword pre-fetch via direct DB query (avoids HTTP self-call deadlock).
    Claude does the real identification — this just gives it a head start."""
    try:
        bare = title
        if ":" in bare:
            after = bare.split(":")[-1].strip()
            if after:
                bare = after
        bare = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", bare).strip(" -:")
        return _db_search(bare or title)
    except Exception as exc:
        logger.warning("db_candidate_fetch_failed title=%r error=%s", title, exc)
        return []


def _db_search(query: str) -> list[dict]:
    """ILIKE search against Movie Master via direct DB query."""
    try:
        from sqlmodel import Session, select
        from app.models import MovieMaster
        from app.database import engine as db_engine

        with Session(db_engine) as session:
            stmt = (
                select(MovieMaster)
                .where(MovieMaster.movie_title.ilike(f"%{query}%"))
                .limit(20)
            )
            rows = session.exec(stmt).all()
            return [
                {
                    "id": r.id,
                    "movie_title": r.movie_title,
                    "release_date": str(r.release_date) if r.release_date else None,
                    "cover_image": r.cover_image or "",
                }
                for r in rows
            ]
    except Exception as exc:
        logger.warning("db_search_failed query=%r error=%s", query, exc)
        return []


def _fetch_vespa_candidates(title: str) -> list[dict]:
    """Hybrid semantic+BM25 search against Vespa."""
    try:
        body = json.dumps({
            "yql": "select * from sources * where userQuery()",
            "query": title,
            "ranking": "hybrid",
            "hits": 10,
        }).encode()
        req = urllib.request.Request(
            "http://vespa:8080/search/",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            hits = data.get("root", {}).get("children", [])
            return [
                {
                    "id": h.get("fields", {}).get("id"),
                    "movie_title": h.get("fields", {}).get("movie_title"),
                    "release_date": h.get("fields", {}).get("release_date"),
                    "relevance": h.get("relevance"),
                }
                for h in hits
            ]
    except Exception as exc:
        logger.warning("vespa_candidate_fetch_failed title=%r error=%s", title, exc)
        return []


def _annotate_poster_availability(candidates: list[dict]) -> None:
    """Add has_poster=True/False to each candidate in-place."""
    for c in candidates:
        img = (c.get("cover_image") or "").strip()
        c["has_poster"] = bool(img) and "noimage" not in img.lower()


async def run_agentic_match_async(
    title: str,
    show_date: Optional[str] = None,
    theater: Optional[str] = None,
    ticketing_url: Optional[str] = None,
    use_poster_vision: bool = False,
) -> TitleMatchResult:
    return await asyncio.to_thread(
        run_agentic_match, title, show_date, theater, ticketing_url, use_poster_vision,
    )
