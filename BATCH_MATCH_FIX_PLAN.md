# Batch Title Matching — Root Cause & Fix Plan

Analysis of `/Users/souveek/Downloads/Result - Sheet1.csv` (22-row batch run).
Every row shows `present_in_db=No` even though a correct mapping already
exists in `moviemaster` for every one of them (verified directly against
Postgres and Vespa). Three independent bugs compound to cause this, plus one
future enhancement stashed for later.

## Confirmed root causes

### Bug 1 — Vespa field-name mismatch (breaks semantic search for 100% of rows)

`backend/app/title_matching/agentic/runner.py::_fetch_vespa_candidates`
reads `fields.id` and `fields.movie_title` from the Vespa response, but the
actual schema (`backend/vespa/schemas/movie_master.sd`) uses field names
`movie_master_id` and `title`. Verified directly against Vespa:

```
curl 'http://localhost:8080/search/?yql=select movie_master_id,title from movie_master where title contains "Sukumari"'
→ {"movie_master_id":147675,"title":"Oh..! Sukumari"} with relevance 0.16
```

Vespa itself is indexed correctly (45,706 docs) and returns the right hit —
the runner just reads the wrong keys, so every candidate silently becomes
`{id: None, movie_title: None}`. This matches the reasoning text in all 22
rows ("vespa results have null id/title fields").

**Fix:** read `fields.movie_master_id` / `fields.title` in
`_fetch_vespa_candidates` (runner.py:257-259).

### Bug 2 — DB keyword search is brittle substring containment

`_db_search` (runner.py) does `MovieMaster.movie_title.ilike(f"%{query}%")`
— pure substring containment. Verified in Postgres that the correct rows
exist but don't survive the round-trip:

- `moviemaster.movie_title = "Oh..! Sukumari"` exists (id 147675), but
  `ILIKE '%Oh Sukumari%'` doesn't match it (punctuation breaks containment).
- `"DCI 2026: Big, Loud & Live"` exists (id 146694), but a query built from
  `"DCI 2026: BIG LOUD AND LIVE"` doesn't match (comma/`&`/"and" mismatch).
- `"Padre no hay más que uno 5"` (accented) vs. ASCII query variants.

With Bug 1 also breaking the semantic fallback, there was no working path
to find these rows at all.

**Fix:** relax the DB keyword search so it isn't defeated by punctuation,
accents, or word-order noise. Plan:
- Enable `pg_trgm` (and `unaccent`) extensions via an Alembic migration —
  both are available in the `postgres:16` image (`pg_available_extensions`
  confirmed present, not yet installed).
- Add a trigram index on `moviemaster.movie_title` and switch `_db_search`
  to a similarity-ranked query (`similarity(movie_title, query) > threshold`,
  ordered by similarity) instead of strict `ILIKE '%...%'`, falling back to
  the existing ILIKE for short/exact queries.
- Normalize both sides (strip punctuation, unaccent) before comparing.

### Bug 3 — prompt short-circuits on NON_MOVIE even when a real DB row exists

`prompt_builder.py`'s system prompt frames the task purely as "movie title
matching" with no statement that `moviemaster` also contains sports
broadcasts, TV specials, and live events (drum corps, FIFA matches, etc).
The agent classifies these as `event_type: NON_MOVIE` and stops trying to
match, even though rows like `"Semifinals - Telemundo presents the FIFA
World Cup"` (id 147261) and `"DCI 2026: Big, Loud & Live"` (id 146694)
genuinely exist and should be matched.

**Decision (confirmed with user):** `event_type` classification stays as
metadata only — it must never short-circuit the matching step. If a real
DB row exists for the content (movie, TV, sports broadcast, live event),
the agent should return it as a match regardless of event_type.

**Fix:** update `_SYSTEM_PROMPT` in `prompt_builder.py`:
- State explicitly that Movie Master contains non-film content (sports
  broadcasts, TV specials, concerts, drum corps, marathons, etc), not just
  theatrical films.
- Remove/rework the framing that lets `NON_MOVIE` classification justify
  `movie_master_id: 0`. Step 6 output rule should read roughly: "event_type
  is metadata only — always attempt to match against DB candidates
  regardless of event_type; only fall back to id=0 if no DB candidate
  plausibly corresponds to the input after normalization."

### Bug 4 — WebSearch is unavailable under Bedrock (confirmed, not fixable in app code)

Live-tested directly against the `claude-sandbox` CLI: the `WebSearch` tool
is **absent from the tool list entirely** whenever `CLAUDE_CODE_USE_BEDROCK=1`
is set, regardless of what's passed via `--tools`/`--allowedTools`. Confirmed
by toggling the env var in the same container:

- With `CLAUDE_CODE_USE_BEDROCK=1` (current prod config): tool list has no
  `WebSearch`; the model instead emits literal text like
  `WebSearch({query: ...})` or explicitly states `"WebSearch has been
  disabled."`, then proceeds without web confirmation.
- With the var unset: `WebSearch` appears in the tool list (session then
  fails for the unrelated reason of no login/API key configured outside
  Bedrock in this container, but the tool itself is present).

This is Bedrock's platform-level constraint (no server-side WebSearch tool
through Bedrock's API, only `WebFetch`), not a bug in `runner.py` or
`prompt_builder.py`. It explains the "web research (DuckDuckGo)" /
"web search tools were unavailable" split seen across the batch's reasoning
text, and it's why cross-lingual identification (e.g. Spanish "Padre no hay
más que uno 5" → DB's English-translated "Father There is Only One 5") had
no path to succeed in that specific run — Claude correctly ruled out the
wrong DB candidate (Part 2) but had no tool to confirm/translate the title
for Part 5, and the DB post-lookup then searched with the Spanish title
against an English-only row.

**No code fix available.** This needs a product decision:
- Run Mode B agentic matching without Bedrock (direct Anthropic API) so
  `WebSearch` is available, if that's operationally acceptable, or
- Accept that cross-lingual/obscure-title resolution relies solely on
  `WebFetch` (ticketing page) + DB/Vespa candidates while on Bedrock, and
  invest more in improving candidate recall (Bugs 1-3) and the
  business-rules file (stashed below) to compensate, or
- Look into whether Bedrock's Claude models support a different
  provider-side search mechanism that could be wired in as a custom
  tool/MCP server instead of the built-in `WebSearch`.

## Verification plan after fixes

1. Unit test `_fetch_vespa_candidates` against a fixture Vespa response
   using the real schema field names (`movie_master_id`, `title`) —
   currently untested, which is how this shipped silently.
2. Unit test `_db_search` / new trigram-based search against known
   punctuation/accent/word-order variants from this batch (Oh Sukumari,
   DCI 2026 BIG LOUD AND LIVE, Padre no hay mas que uno 5A, Francois.e VIP).
3. Re-run the same batch upload (or the underlying 22 titles) end-to-end
   through the real pipeline and confirm `present_in_db=Yes` with the
   correct `mapped_title` for all rows that have a genuine DB match,
   including the FIFA/DCI/Evangelion event rows.
4. Confirm sports/TV/event rows are no longer defaulting to `NON_MOVIE` /
   `movie_master_id: 0` when a real row exists.

## Stashed for later (not in this fix pass)

**Persistent business-rules file for the claude-sandbox sidecar.** Idea:
bake a rules/examples file into the `claude-sandbox` image (e.g.
`/app/business-rules.md`) containing:
- "If input looks like X (sports broadcast / TV special / localized title /
  promo-prefixed title), treat it as Y" heuristics.
- A curated set of verified before/after examples — seeded from this
  batch's 22 rows once the code fixes are verified — showing correct
  classification and DB matching for non-film content, translated titles,
  and punctuation/accent variants.

Wiring options (not yet decided):
- `--append-system-prompt-file` CLI flag (cleanest — explicit, no reliance
  on CLAUDE.md auto-discovery through the per-request ephemeral `$HOME` in
  `claude-sandbox/server.js`).
- Or read the file in `runner.py` and prepend/append its contents to the
  generated prompt directly.

Rationale for deferring: this is a refinement on top of correct
candidate-fetching, not a fix for why candidates are currently empty. Doing
it before the 3 bugs above are fixed would risk the business-rules file
"fixing" symptoms that are actually caused by the field-name bug, muddying
verification. Revisit once bugs 1-3 are fixed and reverified against this
same batch.
