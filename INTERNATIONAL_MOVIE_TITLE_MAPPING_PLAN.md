# International Movie Title Mapping

## Context

The product has a working "Domestic Movie Title Mapping" feature: a single-match form and a batch-upload flow that both run an agentic (LLM-driven) matching pipeline against a `MovieMaster` table and a Vespa semantic index, resolving loosely-specified theater listings (`title`, `theater`, `show_date`, `ticketing_url`) to a canonical movie record.

The business now has a second data source — `Movie Master International Dump.csv` — covering international theatrical releases, and wants the same single-match/batch-upload UX for it, gated behind a Domestic/International toggle (default Domestic). International adds one new field end-to-end: `country`, identifying which country's release a given listing refers to.

This isn't a drop-in reuse of the domestic pipeline because the international dump has a fundamentally different grain: domestic's `MovieMaster` is one row per canonical film (a global integer PK), but the international dump is one row per **(movie_id, country, release_date)** — the same film repeats across up to ~10 countries, occasionally with per-country spelling variants, and occasionally re-releases in the same country on a different date. Analysis of the actual CSV (157,218 rows, 21,817 distinct `movie_id`s, 71 countries) confirmed this and surfaced concrete edge cases (53 movie_ids with spelling variants across countries, 145 `movie_title`/`master_movie_title` mismatches, 1 row with `country="undefined"`, 26 rows with `country="USA"` even though this is the "international" dump, and confirmed same-country re-releases on different dates). Feeding this dump through the existing domestic seed loader unmodified would silently collapse per-country rows and destroy the data — that loader keys upserts by `movie_id` alone.

Goal of this plan: ship International Movie Title Mapping as a first-class parallel feature that reuses the *same* agentic engine (parameterized by market/country) rather than a forked pipeline, reuses the same batch/job infrastructure patterns already proven for domestic (S3 storage, async Celery chord dispatch, localStorage job persistence across navigation), and keeps the existing domestic feature completely unaffected — additive tables, additive endpoints, additive UI branches, with the domestic code path defaulting to its exact current behavior.

## Decisions Already Made (not open for reconsideration in this plan)

1. **USA rows in the international dump are kept as-is** — no country-name filtering anywhere in ingestion or querying. `country` is authoritative regardless of its value.
2. **Vespa uses a second document type in the same content cluster** (see §6) — a new `movie_master_intl` schema alongside the existing `movie_master` schema, both in the existing `movie_content` cluster. International indexing/query runs are routed to this second document type; domestic is untouched.
3. **One shared agentic engine**, parameterized by `market`/`country` — not a forked pipeline. `TitleMatchEngine`, `run_agentic_match`, candidate generation, and `prompt_builder` all become market-aware rather than duplicated.
4. **New dedicated table** for international master data at grain `(movie_id, country, release_date)` — cannot reuse `MovieMaster` unmodified since its PK is a global, country-agnostic integer. `movie_id` is carried as a **soft/unenforced reference** (not a hard FK) to the shared film identity, since it's unconfirmed that international `movie_id` always equals domestic `MovieMaster.id` for the same film.
5. **New dedicated batch job model** (`MovieTitleIntlBatchJob`), following this codebase's established one-table-per-feature convention (`MovieFormatJob`, `DetectionJob`, `MovieTitleBatchJob` are all separate tables — no shared table with a type discriminator).
6. **Data cleaning**: the CSV encodes missing values as the literal string `"null"` (not real null) — ingestion must coerce this. The single `country="undefined"` row is skipped and logged. Uniqueness is enforced at `(movie_id, country, release_date)`, not `(movie_id)` or `(movie_id, country)`, because of confirmed same-country re-releases.

## Non-Regression Guarantee for Domestic

Every layer of this plan is additive to the existing domestic feature, never a modification of its existing behavior:

- **DB**: `MovieMaster`, `MovieTitleAlias`, and `MovieTitleBatchJob` are untouched — no column additions, no migration alters them. `MovieMasterIntl` and `MovieTitleIntlBatchJob` are new tables. The existing `_upsert_rows`/`seed_movie_master` domestic loader function is not modified; the international loader is a new, separate function.
- **API**: `TitleMatchRequest` gains new *optional* fields (`market` defaults to `"domestic"`, `country` defaults to `None`) — any existing caller that omits them gets byte-for-byte the same request shape and behavior as today. `POST /batch` gains a new optional `market` form field defaulting to `"domestic"`, preserving the existing code path when omitted. All new endpoints (`/master/intl`, `/master/intl/search`, `/batch/intl/{job_id}`) are additive routes, not replacements.
- **Agentic engine**: `market`/`country` params on `engine.match()`/`run_agentic_match()` default to `"domestic"`/`None`; when `market == "domestic"` (the default), every branch resolves to the exact existing code path — same DB table (`MovieMaster`), same Vespa document type (`movie_master`), same prompt text (the domestic instruction block is preserved verbatim, only the international branch is new).
- **Vespa**: domestic's `movie_master` schema, its content-cluster registration, and its existing doc-id space are unmodified. `movie_master_intl` is a wholly new, separate document type — no shared fields, no shared doc-id space, no query ever unions the two.
- **Batch infra**: the existing Celery task module (`agentic_match_task.py`), its queue, and `MovieTitleBatchJob` handling are unmodified. International gets its own task module and job table so a bug or load spike in international batch processing cannot affect domestic job state, counters, or storage.
- **Frontend**: the Domestic/International toggle defaults to Domestic, and the domestic-path UI (fields shown, request payload, localStorage namespace `'movie-title-match'`) is unchanged when the toggle is left at its default — the international branch only adds new conditional UI (the `country` field, updated batch help text) that renders when `market === 'international'`, never altering the domestic render path.

Practical implication for execution: every PR in the step breakdown below should include a check (in its own testing, not a separate step) that a plain domestic single-match and domestic batch-upload request — sent exactly as they are today, with no new fields — still produce identical results after that PR lands.

## Edge Cases — How They're Handled

- **Per-country title spelling variants** (53 movie_ids, e.g. "Napoleón"/"Napóleon"/"Napoleon"): store and search the per-row `movie_title` (country-specific spelling) directly; retain `master_movie_title` as the canonical grouping/display key. ILIKE + pg_trgm/unaccent (already used domestically) tolerates accent/case/typo variance, scoped by `country`.
- **Shared `movie_id` between domestic and international dumps**: treated as the shared film-level identity, but as a soft/unenforced integer reference — no FK to `moviemaster.id`, no cross-table join at match time. Domestic and international are queried independently based on the request's `market`.
- **USA rows in the international dump**: no special-casing (Decision 1) — an international request with `country="USA"` matches those rows like any other country.
- **Same-country re-releases on different dates**: covered by the `(movie_id, country, release_date)` unique grain — both rows persist as distinct records; the agent disambiguates using show-date proximity, same as it already does for ambiguous candidates today.

## Implementation

### 1. DB layer
- New model `MovieMasterIntl` in `backend/app/models.py` (alongside `MovieMaster`, `MovieTitleBatchJob`): surrogate autoincrement `id` (do **not** reuse the dump's own `id` or key on `movie_id`), plus `source_row_id`, `movie_id` (indexed, soft reference), `movie_title`, `master_movie_title`, `country` (indexed), `country_id`, `release_date`, `studio`, `rating`, `genre`, `genre2`, `running_time`, `updated_on`.
  - `UniqueConstraint("movie_id", "country", "release_date")`.
  - pg_trgm GIN index on `movie_title` (extension already enabled via `backend/alembic/versions/f6a1b2c3d4e5_add_trgm_search_support.py`; just add the new index).
- New Alembic migration creating this table, mirroring `backend/alembic/versions/d4e5f6a1b2c3_add_movie_master_tables.py`.
- New seed-loader path in `backend/app/title_matching/seed_loader.py` (e.g. `seed_intl_from_rows` / `seed_movie_master_intl`) — **separate from** `_upsert_rows`, which must keep collapsing for domestic:
  - Upsert keyed on `(movie_id, country, release_date)`, never on `movie_id` alone.
  - Coerce the literal strings `"null"`/`""`/`"undefined"` (case-insensitive) to `None` before numeric/int conversion.
  - Skip rows with blank/`"undefined"` `country`, counted in a `skipped_undefined_country` stat.
  - No country-name filtering (USA rows load normally).
- New CLI command in `backend/app/cli.py` mirroring `seed_movie_master` (line 34), pointed at the new loader, for seeding `Movie Master International Dump.csv`.
- **Explicit gotcha to guard against in review**: loading the international dump through the *existing* unmodified domestic loader would silently overwrite `MovieMaster.id=<movie_id>` per country row seen last, destroying per-country data. Any PR touching seeding must route international through the new function only.

### 2. Backend API — one router, one engine, parameterized
Reuse `backend/app/routers/movie_title_match.py` rather than adding parallel `/intl/*` routes — the codebase already centers on a single router and a single `TitleMatchEngine`; adding a `market` field is more consistent than duplicating routes.
- `TitleMatchRequest` (movie_title_match.py:19) gains `market: Literal["domestic","international"] = "domestic"` and `country: Optional[str] = None`, with validation requiring `country` when `market == "international"` (422 otherwise).
- `POST /single` passes `market`/`country` into `engine.match(...)`.
- `POST /batch` gains `market: str = Form("domestic")` and routes to `MovieTitleIntlBatchJob` + a new async-dispatched intl Celery task when international; the per-row `country` column (not the form field) is authoritative for matching.
- New `GET /master/intl`, `GET /master/intl/search` mirroring the existing `/master`, `/master/search` (movie_title_match.py:184-237) against `MovieMasterIntl`.
- New `GET /batch/intl/{job_id}` status + download routes (distinct from the domestic ones), since job ids live in a separate table.

### 3. Agentic engine — made market-aware, not duplicated
- `TitleMatchEngine.match()` (`backend/app/title_matching/engine.py:21`) and `run_agentic_match()` (`backend/app/title_matching/agentic/runner.py:26`) gain `market`/`country` params.
- DB candidate generation (`_db_search`, ILIKE/trigram helpers around runner.py:226-298) branches to query `MovieMasterIntl` filtered by `country` when `market == "international"`.
- Vespa candidate fetch (`_fetch_vespa_candidates`, runner.py:301) branches on `market`: domestic queries the existing `movie_master` document type; international queries the new `movie_master_intl` document type (see §6). Both go through the same hybrid BM25+ANN YQL shape, just against different `sources`/schema names.
- **Required change, not optional**: `agentic/prompt_builder.py` currently hardcodes an instruction to resolve the "US/domestic theatrical release" title (lines ~94-99) — this must be conditionalized: domestic keeps the current instruction verbatim; international gets a new instruction to resolve the title as released in the given `country`, using the country-scoped candidate set, with `master_movie_title` explained as the canonical grouping key vs. `movie_title` as the per-country display spelling.
- `types.py:9` already has an unused `country_code` field on the result/candidate type — populate it for international results.

### 4. Batch job infrastructure — reuse the proven async pattern
- New model `MovieTitleIntlBatchJob` in `backend/app/models.py`, copying `MovieTitleBatchJob` (lines 47-63) with a new table name, plus its own migration.
- `backend/app/title_matching/batch_io.py`: `parse_upload` gains a `market` param; required columns become `("movie_title","show_date","ticketing_url","country")` for international (keeping the existing `title`→`movie_title` alias). Output xlsx already preserves input columns after result columns, so `country` flows through automatically.
- New Celery task module (e.g. `backend/app/tasks/agentic_intl_match_task.py`) mirroring `agentic_match_task.py`: per-row task passes `market="international"` and the row's `country`; finalize/dispatch reuse the same `batch_storage` (S3), Redis results-hash, atomic counter-bump, and — critically — the same **async chord dispatch via `.delay()`** fix from commit 646efab (dispatch must not block the request/ALB).

### 5. Frontend
- `MovieTitleMatchingPage.tsx`: add a page-level `market` state (`'domestic' | 'international'`, default `'domestic'`), rendered above the existing Single/Batch `Tabs`, using the same hand-rolled toggle-switch visual pattern already used for "Poster Vision" in `MovieTitleSingleMatcher.tsx`/`MovieTitleBatchMatcher.tsx` — no new toggle component needed. Pass `market` down to both children.
- `MovieTitleSingleMatcher.tsx`: add a `country` field (Input), shown only when `market === 'international'`, required in that mode; threaded into the match request.
- `MovieTitleBatchMatcher.tsx`: update the required-columns help text to add `country` when international is selected; forward `market` in the upload call.
- `useMovieTitleBatchJob.ts`: parameterize by `market` so the localStorage namespace (currently hardcoded `'movie-title-match'`) becomes `'movie-title-match-intl'` for international — this is the fix needed so a domestic and an international batch job in flight at the same time don't collide in persisted state (the persistence mechanism itself, `persistedJob.ts`, needs no change since it already keys by namespace).
- `useMovieTitleMatch.ts`: extend the request type with `market`/`country`.
- No new sidebar entry — the toggle lives inside the existing `/movie-title-matching` page.

### 6. Vespa — second document type in the same content cluster

Decision: add a **second Vespa document type**, `movie_master_intl`, alongside the existing `movie_master` schema, both served by the existing `movie_content` content cluster (`backend/vespa/services.xml`) — no new cluster, no new Vespa instance. This keeps domestic and international as cleanly separated, independently-idspaced corpora while sharing infra (one Vespa deployment, one container/content cluster definition, one embedding pipeline).

- **Schema**: new `backend/vespa/schemas/movie_master_intl.sd`, structurally mirroring `movie_master.sd` (`movie_master_intl_id` int attribute+summary, `title` string with BM25 index, `embed_text` summary-only, `embedding` tensor<float>(x[1024]) with the same HNSW/angular config, same `hybrid` rank-profile shape). Register it in `services.xml` alongside the existing schema, same content cluster.
- **Doc id**: `movie_master_intl_id` = the new `MovieMasterIntl.id` surrogate PK (never the dump's own `id`, never `movie_id` — same reasoning as domestic: an arbitrary, non-colliding integer per row).
- **Ingestion**: extend `backend/app/title_matching/semantic_index.py` with an intl path (e.g. `build_semantic_index_intl` alongside `build_semantic_index`) that loads from `MovieMasterIntl` instead of `MovieMaster`, feeds into the `movie_master_intl` document type, and reuses the same additive-diff idempotency (`_get_indexed_ids` scoped to the new schema) and Cohere batch-embedding pipeline. Triggered by its own Celery task (mirroring `build_semantic_index_task` in `backend/app/tasks/semantic_tasks.py`) — scheduled/run separately from the domestic index build, so an international-titled batch/single run never touches or waits on the domestic index job.
- **Query**: `_fetch_vespa_candidates` (runner.py:301) branches on `market`: domestic queries `sources movie_master` (unchanged); international queries `sources movie_master_intl`. Both use the same hybrid BM25+ANN YQL shape and `hybrid` rank-profile. No cross-schema union needed since a request is always scoped to one market.
- **Routing**: whenever a matching run (single or batch) is scheduled for `market="international"`, both DB candidate generation (§3) and Vespa candidate generation are routed to the international document type/table — never mixed with domestic in the same query.

This is no longer blocking — it's a concrete, sequenced step (see breakdown below) rather than a deferred decision.

## Step Breakdown (dependency order, sized for individual PRs)

1. `MovieMasterIntl` model + migration (unique constraint, trigram index). No dependency on Vespa.
2. Seed loader + CLI command (null-string coercion, undefined-country skip, per-triple upsert) — depends on 1.
3. Vespa `movie_master_intl` schema + services.xml registration + intl semantic-index build task — depends on 1, 2 (needs `MovieMasterIntl` rows to feed). Can run in parallel with 4.
4. Agentic market-awareness: `engine.match`, `run_agentic_match`, country-scoped DB candidate generation, market-branched Vespa candidate fetch, conditional `prompt_builder` instructions — depends on 1; Vespa branch depends on 3 landing (can stub/no-op until then).
5. Single-match API: `TitleMatchRequest` fields + validation, intl master search endpoints — depends on 1, 4.
6. Batch infra: `MovieTitleIntlBatchJob` + migration, `batch_io` market-aware required columns, new Celery task module, intl batch/status/download routes — depends on 1, 4, 5.
7. Frontend: toggle, country field, batch column messaging, namespaced job-persistence hook — depends on 5, 6 (API contracts).

## Verification

- **Seed loader unit tests**: `"null"`-string coercion, undefined-country skip, duplicate `(movie_id, country)` with different `release_date` inserts two rows, duplicate `(movie_id, country, release_date)` upserts in place, same `movie_id` across countries retains multiple rows, USA rows retained unfiltered.
- **API contract tests**: international with blank `country` → 422; international with `country` → routes to intl engine; domestic default unchanged (regression); batch upload missing `country` column → 400 naming it; domestic batch unaffected.
- **Prompt tests**: `build_prompt(market="international", country=...)` includes the country-specific instruction and excludes the hardcoded domestic-release rule; domestic prompt output unchanged.
- **Manual end-to-end**: seed the international dump via the new CLI; toggle International in the UI; single-match a known localized title (e.g. "Napoleón" with country=France) and confirm a country-scoped match; upload a small international batch CSV with a `country` column, confirm the job completes and the output xlsx retains `country`; run a domestic and an international batch job simultaneously and confirm their persisted localStorage state doesn't collide.
