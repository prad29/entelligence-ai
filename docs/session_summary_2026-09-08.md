# Session Summary — 2026-09-08

## Goal

Domestic movie-title mapping was matching almost entirely on title text, release-date
proximity, and ordinal/edition markers. Two problems drove this session:

1. Genre, cast, director, and synopsis exist in `fq_movie_master` but were barely/never
   used to power the match decision — even though a close title match with contradicting
   metadata (wrong genre, wrong director, empty everything) is a strong signal of a bad
   match.
2. A pure rule-based fallback path existed that could pick a match with zero web
   research — unable to disambiguate two DB rows sharing a title from different eras
   (e.g. a 90s original vs. a 2025 remake), which only Claude's web-search step can
   resolve.

## Decisions taken (chronological)

- **Scope**: agentic domestic single-match + batch pipeline only. International and the
  external-API match path are explicitly out of scope.
- **New fields**: add `genre` + `synopsis` end-to-end. `director`/`cast_list` already
  existed on `MovieMaster` but were never wired into the agentic candidate payload —
  fixed that too. Declined `genre2`/`running_time` (out of scope).
- **Incomplete-metadata rule**: if a candidate has BOTH `director` AND `synopsis` empty,
  disregard it and fall through to the next-closest candidate — **unless** genre is
  exactly `"Sports"` or `"Concert/Special Events"` (verified against a 1,000-row CSV
  sample of `fq_movie_master`: those two genres are 100%/96% empty on both fields;
  Short Film/Documentary/Specialty Spot are 13–58% empty — not clean enough to exempt).
- **Enforcement**: both the prompt (Claude is told the rule and the exemption) *and* a
  deterministic code guardrail after Claude responds — never trust the model alone on a
  business rule like this.
- **v2, not a v1 edit**: build this as a new, additive pipeline (`/api/v2/movie-title-match`)
  rather than editing v1's prompt/runner in place, so v1 stays provably byte-identical.
  Batch got a v2 sibling too (`/api/v2/movie-title-match/batch`), reusing the *same*
  `MovieTitleBatchJob` table via a new nullable `pipeline_variant` discriminator instead
  of a second table — a second table would have required editing the cross-pipeline
  fairness scheduler (`agentic_scheduler_task.py`) in six places, or silently broken v1's
  queue-window fairness math.
- **Guardrail fall-through order**: tier 1 = Claude's own runner-up candidates (now
  allowed to return 1–3, ranked); tier 2 = the raw pre-fetched DB order — each candidate
  must also pass the ordinal hard-constraint and a title-similarity floor
  (`AGENTIC_V2_FALLTHROUGH_MIN_TITLE_SIMILARITY`, default 80) before being accepted as a
  replacement. Rejected re-invoking Claude on a fall-through (doubles latency/cost per
  rejection, doubles sandbox-semaphore occupancy, and Claude already had every candidate
  and every metadata field in its one shot).
- **Confidence policy**: a guardrail-swapped pick is always capped below 0.90 and forced
  to `REVIEW` (never `AUTO_ACCEPT`) — Claude never actually endorsed that specific row.
- **Guardrail default mode**: `enforce` (not staged through `log_only` first) — shipped
  as one unit rather than a phased rollout. `off`/`log_only` are kept as an operational
  kill switch, not a rollout step.
- **Bigger, explicitly-authorized change to v1**: retire the non-agentic rule-based
  fallback matcher **everywhere**, including v1. Rationale: a pure fuzzy/date scorer with
  no web research can't disambiguate the same-title-different-era case; "no match" is
  always better than a confident-looking wrong guess. Chosen depth: **full removal**, not
  a bypass — delete the dead modules and their tests rather than leaving them inert.
- **Error handling on infra failure** (sandbox down/throttled/unparseable output):
  **always return HTTP 200 with an honest no-match** (`id=0, confidence=0.0,
  decision=REVIEW, fired_ai=False`), never a 500/503 and never a substituted guess. The
  failure type/message stays visible in `reasoning`/`evidence` for diagnosis; server-side
  logging is unaffected.
- **`evidence_fetcher.py` (ticketing-page poster/metadata scraper)**: kept, not deleted —
  verified the frontend (`MovieTitleMatchCard.tsx`) actively renders
  `ticketing_poster_url`/`page_metadata`, so its only caller (the deleted `engine.py`) was
  replaced with a call from the new route handlers, not left orphaned.
- **Observability**: added `PATH_AGENTIC_CLI_V2` so v1 vs. v2 cost/latency/decision rates
  are splittable in the usage dashboard without a backfill.

## What was built

**Schema / data plumbing (additive, shared by v1 and v2):**
- `alembic/versions/f7a8b9c0d1e2_...py` — nullable `moviemaster.genre`,
  `moviemaster.synopsis`, `movietitlebatchjob.pipeline_variant`. No backfill, no
  `server_default`, guarded by column-existence checks.
- `app/models.py`, `app/title_matching/prod_db.py` (`_DOMESTIC_COLUMNS`),
  `app/title_matching/seed_loader.py` (alias tuples + both upsert branches).
- `app/config.py` — `AGENTIC_V2_METADATA_GUARDRAIL_MODE` (default `enforce`),
  `AGENTIC_V2_SYNOPSIS_MAX_CHARS`, `AGENTIC_V2_CAST_MAX_NAMES`,
  `AGENTIC_V2_FALLTHROUGH_MIN_TITLE_SIMILARITY`.

**v2 pipeline (new files):**
- `app/title_matching/agentic/prompt_builder_v2.py` — full sibling system prompt (weighs
  genre/cast/director/synopsis, the Step 2b incomplete-metadata rule, 1–3 ranked
  candidates).
- `app/title_matching/agentic/metadata_guardrail.py` — `candidate_passes`,
  `is_genre_exempt`, `apply_metadata_guardrail` (the deterministic fall-through).
- `app/title_matching/agentic/runner_v2.py` — thin domestic-only entry point.
- `app/routers/movie_title_match_v2.py` — `POST /single`, `POST /batch`,
  `GET /batch/{job_id}`, `GET /batch/{job_id}/download` under `/api/v2/movie-title-match`.
- Seams in `app/title_matching/agentic/runner.py` (`variant`, `include_metadata`,
  keyword-only, default-off) and `app/tasks/agentic_match_task.py` (`agentic_batch_row`'s
  `variant` kwarg, `enqueue_next_window`'s conditional publish kwarg via a new
  `_variant_of()` helper) — v1's call shapes are unchanged when the new kwargs are
  omitted.

**v1 fallback removal (real behavior change, explicitly authorized):**
- Deleted `app/title_matching/engine.py`, `loader.py`, `decision_engine.py`,
  `candidate_generator.py`.
- `app/main.py` — removed the three dead startup/watcher blocks that only existed to
  build/refresh the fuzzy engine.
- `app/routers/movie_title_match.py` — `/single` now calls `run_agentic_match` directly;
  catches `AgenticError` and returns the honest-no-match 200 described above; the
  `/master/seed` route's dead engine-reload block removed.
- `app/title_matching/evidence_fetcher.py` — added `attach_to_result()`, called from both
  the v1 and v2 route handlers.

**Deploy runbook consequence (documented in `.env.example`/`docs/CLAUDE.md`):**
`AGENTIC_TITLE_MATCH_ENABLED` flipped from `false` to `true` in `.env.example`. In any
deployment where this flag is off, `/single` and `/batch` now return 400 instead of
silently returning a rule-based guess. `MovieTitleAlias`-based matching is gone
system-wide (the table/model stays, since it costs nothing and a destructive migration
would be for zero benefit; no code reads it anymore).

## Verification

- Full backend test suite run against the local dev Postgres/Redis: **657 passed, 3
  skipped** (Python 3.9 venv in the repo was stale/broken for this codebase — created a
  fresh Python 3.12 venv, `.venv312`, gitignored).
- Found and fixed one real regression during testing: `_post_lookup_search`/
  `_fetch_db_candidates` were unconditionally passing `include_metadata=...` to
  `_db_search`, which broke existing tests that mock `_db_search` with a narrower
  signature — fixed by only including the kwarg when `True`, matching the pattern already
  used for the batch task's `variant` kwarg.
- Consolidated three new test files down to one (`tests/test_agentic_v2.py`) per
  feedback to keep test-file count down.
- Two pre-existing, environment-caused failures identified and confirmed unrelated by
  reproducing them against the fully-reverted (stashed) codebase:
  - `test_semantic_e2e.py::test_love_island_surfaced_by_semantic` — the shared dev Vespa
    instance now holds real production-scale synced data, drowning out the test's
    fixture embedding.
  - `test_agentic_runner_candidates.py::TestDbSearchTrigramFallback` — pre-existing
    test-ordering leak of `app.database.engine` from an unrelated fixture elsewhere in
    the suite; also, `pg_trgm` isn't installed on this local dev Postgres, so these tests
    skip cleanly in isolation anyway.
- Migration applied directly to the local dev Postgres (`alembic upgrade head`) —
  confirmed safe first: the running `backend`/`celery-*` containers build from `./backend`
  with no source volume mount, so they were unaffected until explicitly rebuilt.
- Rebuilt all 8 backend/celery Docker images and recreated the containers
  (`docker compose build` + `docker compose up -d`); confirmed clean startup and the four
  new `/api/v2/movie-title-match/*` routes present in `/openapi.json`.
- Ran a real domestic sync against production MySQL and confirmed via direct Postgres
  query: 47,285 rows total, 45,776 with genre, 39,133 with director, 37,406 with cast,
  37,463 with synopsis. Spot-checked real narrative rows (populated) and Sports-genre
  rows (correctly empty director/synopsis, matching the exemption design).

## Open items / follow-ups (not done this session)

- `SELECT DISTINCT genre` against the full production table to confirm no multi-value
  genre strings exist beyond the 1,000-row CSV sample checked.
- No v2 sibling for `tasks/external_match_task.py` (external API) or international batch
  — explicitly deferred, not forgotten.
- Frontend is untouched — v2 is API-only for now.
- `evidence_fetcher.py`'s underlying scraper subsystem was reprieved from deletion but
  not otherwise revisited.
