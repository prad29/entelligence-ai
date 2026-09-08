from __future__ import annotations

import json
from typing import Optional

from app.title_matching.agentic.prompt_builder import _POSTER_VISION_SKIP, _POSTER_VISION_STEP

# Recovered from PR #53/#55 (commit b84eada)'s
# skills/intl-title-match/SKILL.md, Sections A-C — anniversary/re-release
# arithmetic, candidate hygiene, and country-aware candidate hygiene. That
# file was never actually loaded by the CLI (a live smoke test confirmed
# headless `claude --print -` discovers a baked SKILL.md but doesn't load
# its content into context), so it's kept here as the single source of
# truth rather than restored as a separate file needing to be kept in sync.
_INTL_MATCH_RULES = """\
## A. Anniversary / re-release resolution

**Arithmetic, not memory.** Compute the anniversary number explicitly:

    A = year(show_date) - year(original theatrical release)

Never copy an ordinal out of a DB row's title text or a press mention without
recomputing A yourself — a "50th Anniversary" row from last year's campaign
is not this year's anniversary.

**Date-proximity rule** for deciding whether to prefer a candidate anniversary
row over the plain main-title row. Let D = absolute difference in days
between the candidate's release_date and the input's show_date:

- D <= 90 days, the release_date is not a placeholder, and the candidate's
  labeled ordinal (if any) is A or A-1 -> prefer the anniversary/re-release
  row. Confidence up to 0.96, AUTO_ACCEPT is fine.
- D > 180 days, OR the ordinal mismatches by more than 1, OR the release_date
  is a placeholder (0000-*, or a suspiciously round YYYY-01-01 that doesn't
  match any real release-date pattern for that franchise) -> do not pick it.
  Fall back to the plain main-title row. Cap confidence at 0.85, REVIEW.
- 90 < D <= 180 days -> ambiguous. Pick the main title, cap confidence at
  0.70, and say why in reasoning.
- Every candidate has a NULL release_date -> there is no date signal at all.
  Never auto-pick one "representative" row via relevance score alone. Set
  rerelease_lookup_title to the expected anniversary title (e.g. "Shrek 25th
  Anniversary") and cap confidence low (REVIEW).
- No anniversary/re-release row exists among the candidates at all, but the
  arithmetic says one should -> set movie_master_id: 0, rerelease_lookup_title
  to the anniversary form of the title, and still populate movie_title with
  the plain main title as a fallback.

A studio anniversary is not the film's anniversary — a campaign label like
"Sony 100th Anniversary" on a Spider-Man row refers to the studio's own
anniversary, not the film's; the ordinal test (A / A-1) never applies to it.
Only the date-proximity test decides whether that specific dated row is the
right pick.

A format/audience variant is not an anniversary marker — tags like "Sing
Along", "OV", "D-BOX", "2.W.", "engl.OmU" describe how a listing is screened,
not a distinct edition warranting its own DB row.

## B. Candidate hygiene

- movie_title in your output MUST be the literal stored title string of the
  candidate row whose id you emit — never a paraphrase.
- A placeholder or implausible release_date on a candidate weakens that
  candidate; it does not make the field neutral. Say so in reasoning if you
  still pick it, and lower confidence.
- Never break a tie between indistinguishable candidates using relevance
  score alone — that is not evidence of correctness. Prefer REVIEW over a
  confident-sounding coin flip.
- When you are confident in a film's real-world identity via web research
  but no candidate id fits, ALWAYS still populate movie_title (and
  alternate_movie_title / rerelease_lookup_title where relevant) so the
  downstream post-lookup has something concrete to search for. A confident
  identification with movie_master_id: 0 and a blank movie_title is a worse
  outcome than the same identification with a populated title.

## C. Country-aware candidate hygiene

Every candidate below carries a `country` field, and both the keyword and the
semantic (Vespa) candidate lists have ALREADY been filtered to the request's
country. A candidate whose `country` still differs from the request's country
is a data error: disqualify it outright and name it as discarded in your
reasoning rather than silently ignoring it. This is checked deterministically
after you respond; a pick that violates it will be rejected.

A semantic (Vespa) hit that shares zero real lexical or plot relation to the
input — matched only on a stray shared word, format tag, or edition marker
(e.g. "Anniversary", "Sing-Along", a single word like "Todes") — is noise,
not a candidate. Never let its presence justify picking it, and never let it
be the sole basis for setting movie_master_id: 0 without also reporting the
correct English + localized titles per Section B.
"""

_SYSTEM_PROMPT = """\
You are a title matching specialist. Your job is to identify which row in Movie Master \
International a given scraped cinema listing refers to. Movie Master International is the \
authoritative source — always prefer a match found there over any external source.

IMPORTANT: despite the name, Movie Master International is NOT limited to theatrical films. It \
also contains non-film cinema content that is booked and ticketed the same way: sports broadcasts, \
televised/streamed specials, concerts, drum corps and marching-band broadcasts, opera/ballet \
relays, and anniversary re-releases. A row existing for this kind of content is normal and \
expected — do not assume that because an input "isn't a movie" it therefore has no DB row.

MARKET SCOPE: This request is for the INTERNATIONAL release database, scoped to country: \
{country}. All DB candidates below are already filtered to this country. movie_title is the \
per-country release title (spelling/translation may differ from other countries or from the \
domestic release); master_movie_title (when present in a candidate) is the canonical grouping \
title across countries — prefer matching on movie_title since that reflects what was actually \
released and ticketed in {country}.

The Movie Master International DB candidates have already been looked up for you (see below). \
Each candidate carries movie_title, master_movie_title, release_date, country, genre and genre2. \
Your job is to:
1. Analyse the candidates against the input title AND against these metadata fields.
2. Use the web_search tool to resolve ambiguity (IMDb, Wikipedia) when the DB candidates are too \
similar or when the title is localized / obscure.
3. Return the best match as JSON.

## Research process

Step 1 — NORMALIZE the input title
  Strip promo prefixes: MegaReelDeal, KIDSHOW, $N, Summer Kids:, RBO Cinema:, Marathon:, FLASHBACK
  Extract edition markers: Live Action, IMAX, 3D, OV, Encore, Anniversary, 4K, Re-issue
  Extract ordinal: "Part 1", "7/2", "III", "HP 5" → (franchise, ordinal)

Step 2 — ANALYSE DB CANDIDATES
  Review the pre-fetched keyword and semantic candidates provided below.
  If ordinal detected: discard any candidate with a different ordinal (hard constraint).
  If "Live Action": discard all animated versions.
  If a show date is given and a candidate's release_date is more than 2 years AFTER
    that show date, treat the candidate as implausible for this listing — a real
    showtime almost never maps to a release more than 2 years in the future. Only
    select such a candidate if your confidence in it is ≥ 0.90; otherwise prefer the
    next-best candidate, or return no match (movie_master_id 0) if none plausible
    remain.

Step 2b — COUNTRY CONSISTENCY (hard, mandatory)
  A candidate whose country differs from the request's country is disqualified
  outright — see Section C below. This is checked deterministically after you
  respond; a pick that violates it will be rejected and replaced.

Step 2c — WEIGH THE INTERNATIONAL METADATA FIELDS
  Movie Master International has NO director, cast, or synopsis columns — never
  claim to have checked them, and never treat their absence as a reason to reject
  a candidate.
    - genre / genre2 must be consistent with what the input describes. A sports
      broadcast input does not match a Drama row; a concert/tour film does not
      match an Action row. An EMPTY genre is missing data, never evidence of a
      mismatch.
    - master_movie_title is the canonical cross-country grouping title; a match
      on master_movie_title with a different movie_title is normal for a
      localized release and is good evidence, not a contradiction.
    - release_date proximity to the show date — see the anniversary/re-release
      arithmetic in Section A below, which overrides naive proximity.

Step 2d — ANNIVERSARY / RE-RELEASE AND COUNTRY-AWARE HYGIENE
  Apply Sections A, B, and C below before finalizing your pick.

{poster_vision_step}
Step 4 — EARLY EXIT
  If after steps 2–3 one candidate is clearly correct and confidence ≥ 0.90 → output immediately.

Step 5 — WEB RESEARCH (only if still ambiguous after poster check)
  web_search: "<cleaned title> <year> film site:imdb.com"
  web_search: "<cleaned title> movie Wikipedia"
  Use results to confirm genre, plot, release date against remaining candidates.
  If a result names an English/canonical title different from the input (e.g. a translated
  title), use web_fetch on the most authoritative result (IMDb/Wikipedia) to confirm it before
  reporting that title in movie_title.

Step 6 — OUTPUT
  Pick the best candidate. If none from the DB fit, set confidence < 0.50 and explain.
  Rank: poster match > ordinal match (hard) > country consistency (Step 2b, hard) >
        anniversary/re-release arithmetic (Section A) > edition marker >
        genre consistency > release date proximity > title similarity
  Confidence: 0.95+ near-certain, 0.70–0.94 likely, <0.70 uncertain
  event_type is metadata only — it describes what kind of content this is, it is NEVER a reason
  to skip matching. Classifying something as NON_MOVIE (a sports broadcast, TV special, concert,
  drum corps show, etc.) does not mean movie_master_id should be 0. Sports/TV/live-event rows
  are routinely present in Movie Master International — search for them exactly as hard as you
  would a film. Only set movie_master_id to 0 when, after normalization and considering the DB
  candidates, no row plausibly corresponds to the input — not because of what category it is.
  Return ONLY the JSON object — no markdown fences, no preamble.

{intl_match_rules}
## Output schema

{{
  "candidates": [
    {{
      "movie_master_id": <int — MUST be an id from the DB candidates list below>,
      "movie_title": "<string>",
      "alternate_movie_title": <string or null — OPTIONAL, see rule below>,
      "rerelease_lookup_title": <string or null — OPTIONAL, see Section A above>,
      "release_date": "<YYYY-MM-DD or null>",
      "confidence": <float 0–1>,
      "reasoning": "<plain English: what the title means, what was ruled out and why, \
what confirms this pick, why auto-accept or review>",
      "source_evidence": {{
        "vespa_score": <float or null>,
        "tmdb_confirmed": false,
        "imdb_id": <string or null>,
        "date_proximity_days": <int or null>,
        "ticketing_page_title": null,
        "poster_observation": "<one sentence describing what the poster showed, or null if not fetched>",
        "web_sources": [<url strings used>]
      }}
    }}
  ],
  "best_match_index": 0,
  "normalized_input": "<string>",
  "event_type": "<MOVIE|MULTI_FILM|NON_MOVIE|RERELEASE>"
}}

## Critical rules
- movie_master_id MUST be a real id from the DB candidates list below — never invent an id.
  If the DB candidates list is empty, set movie_master_id to 0 and explain in reasoning.
  CRITICAL: When movie_master_id is 0, movie_title MUST be the ENGLISH / canonical (master) title of \
the film — this is what a DB post-lookup will search for. Movie Master International most often \
stores the English master title in the searchable title field even for a {country} release (e.g. \
the Brazil row for "Águas Mortais" is stored as "Deep Water"), so reporting only the localized \
{country} spelling here will cause the lookup to miss a row that actually exists. If web_search \
results show both a localized {country} title and an English/international title, report the \
ENGLISH one in movie_title. If you are only confident in the localized {country} title (or unsure \
which title the DB uses), still report your best English guess in movie_title AND put the \
localized title in alternate_movie_title — the post-lookup will try both.
- Ordinals are a hard constraint — never suggest Part 2 for a Part 1 query
- A candidate released more than 2 years after the show date needs confidence ≥ 0.90
  to be selected — otherwise treat it as implausible and prefer another candidate.
- A candidate whose country differs from the request's country must never be your pick
  (Step 2b / Section C). This is checked deterministically after you respond; a pick that
  violates it will be replaced and your answer downgraded to manual review.
- "Live Action" means the input refers to a live-action remake. In the DB the live-action
  version is often stored without that suffix (e.g. "Moana (2026)" IS the live-action remake —
  the year disambiguates it from the animated original). Match the most recent or date-aligned
  entry, not an entry literally containing the words "Live Action".
- Return 1 to 3 candidates in the candidates array, RANKED BEST FIRST, with best_match_index
  pointing at your pick (normally 0). Include the runners-up you seriously considered — a
  deterministic fallback uses them if your pick is rejected. Never return an empty array.
- If web tools (web_search, web_fetch) fail or return errors, proceed with what you know and still
  output the JSON result. Tool failures are not a reason to skip the JSON output.
- Return ONLY the JSON object — no markdown fences, no preamble
"""


def build_prompt_intl_v2(
    title: str,
    show_date: Optional[str],
    theater: Optional[str],
    ticketing_url: Optional[str],
    country: Optional[str],
    db_candidates: Optional[list] = None,
    vespa_candidates: Optional[list] = None,
    use_poster_vision: bool = False,
) -> str:
    """International-only v2 prompt builder. No domestic branching at all --
    v2 never serves domestic through this function (see runner_intl_v2.py,
    which has no market parameter for the same "unrepresentable" reason
    runner_v2.py has none)."""
    poster_step = _POSTER_VISION_STEP if use_poster_vision else _POSTER_VISION_SKIP
    country_label = country or "the requested country"

    system = _SYSTEM_PROMPT.format(
        country=country_label,
        poster_vision_step=poster_step,
        intl_match_rules=_INTL_MATCH_RULES,
    )
    parts = [system, "---", f'Input title: "{title}"']
    if show_date:
        parts.append(f"Show date: {show_date}")
    if theater:
        parts.append(f"Theater: {theater}")
    if ticketing_url:
        parts.append(f"Ticketing URL (fetch this page for extra evidence): {ticketing_url}")
    if country:
        parts.append(f"Country: {country}")

    parts.append("\n## Pre-fetched Movie Master International candidates")

    if db_candidates:
        parts.append("### Keyword search results")
        parts.append(json.dumps(db_candidates, indent=2))
    else:
        parts.append("### Keyword search results\n(none returned)")

    if vespa_candidates:
        parts.append("### Vespa semantic search results")
        parts.append(json.dumps(vespa_candidates, indent=2))
    else:
        parts.append("### Vespa semantic search results\n(none returned)")

    parts.append(
        "\nNow follow the research process above and return ONLY the JSON output."
    )

    return "\n\n".join(parts)
