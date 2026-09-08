# Amenity Screen Format Detector

Internal tool mapping theater showtime amenity strings to canonical screen formats.

## Quick Start

```bash
cp .env.example .env  # fill in values
docker-compose up -d
# Seed from xlsx (optional — skip if no xlsx available):
cd backend && python app/cli.py seed-from-xlsx /path/to/Amenities\ Priority.xlsx
```

## Project Structure

- `backend/` — FastAPI + SQLModel + PostgreSQL
- `frontend/` — React + TypeScript + Vite + Tailwind + shadcn/ui
- `docker-compose.yml` — postgres + backend + frontend

## Detection Engine

Three-layer pipeline:

1. **Layer 0** — VIP override for Cineplex/Caribbean
2. **Layer 1** — Priority bucket matching (P1-P6, hybrid exact+fuzzy: exact → Track A → Track B → Track C)
3. **Layer 2** — AWS Bedrock Mistral Large on true no-match

## How to add a new screen format

1. Go to Master Amenity List
2. Click "+ Add Mapping"
3. Fill in keyword, format, tier
4. Submit → goes to Review Queue
5. Approve in Review Queue → immediately detectable

## Batch Title Matching

Mode B (agentic) is the ONLY domestic title-matching pipeline (single and
batch) — every request runs through a Claude-sandbox subprocess that does web
research + DB search; there is no rule-based fallback. Batch: upload a
CSV/XLSX of movie titles, run one Claude-sandbox match per row, download an
XLSX of results. Async Celery job + polling — never synchronous. Requires
`AGENTIC_TITLE_MATCH_ENABLED=true` (must be true in every deployment; when
false, both `/single` and `/batch` return 400).

A domestic v2 pipeline (prefix `/api/v2/movie-title-match`) additionally
weighs genre/cast/director/synopsis (not just title) and deterministically
rejects a pick whose director AND synopsis are both empty (unless genre is
"Sports" or "Concert/Special Events"), falling through to the next-closest
candidate. v2 batch jobs share the same `movietitlebatchjob` table as v1,
distinguished by the nullable `pipeline_variant` column (`NULL`/`"v1"` vs
`"v2"`) so the cross-pipeline fairness scheduler counts/windows/sweeps both
identically.

An international v2 pipeline (prefix `/api/v2/intl-movie-title-match`,
`country` required on every request) is a **fully standalone module** — it
never calls the shared `run_agentic_match`. It restores country-aware Vespa
matching (a `country` field on the `movie_master_intl` schema, filtered at
query time), the anniversary/re-release date arithmetic, and a deterministic
country-consistency guardrail (MovieMasterIntl has no director/cast/synopsis
columns, so this is intl v2's analogue of domestic v2's metadata guardrail),
plus an independent Bedrock Converse verification pass that re-checks the
first pass's pick. International v1 (`market="international"` on the
existing `/single`/`/batch` endpoints) is untouched by any of this — it keeps
running the original shared pipeline, including a known stale-confidence-
after-post-lookup bug that v2 fixes only for itself (domestic v2 gets its
own, differently-tuned copy of the same fix — the two are deliberately
independent modules with no shared code, since sharing this exact logic
between markets is what caused a prior production regression).

Domestic and international v2 also use **separate `claude-sandbox`
containers** (`claude-sandbox` / `claude-sandbox-intl`), differentiated by
baked-in MCP/model config (env-driven, same image) — NOT for concurrency
isolation. The sandbox semaphore and `celery-agentic-worker` pool stay fully
shared across every market/version by deliberate choice; international v2
competes for the same informal round-robin capacity domestic/intl-v1/the
external API already share today.

**Deploy note**: after a fresh deploy, run
`python app/cli.py rebuild-semantic-index-intl --force-deploy --backfill-country`
once — without it, intl v2's Vespa country filter matches nothing (existing
indexed docs have no `country` attribute until the backfill re-feeds them).

### Endpoints (prefix `/api/v1/movie-title-match`)

- `POST /batch` — multipart upload (`file`, `use_poster_vision`). Validates the
  extension, required columns, and `MAX_BATCH_ROWS`, then dispatches a Celery
  chord. Returns `{ "job_id": "<uuid>" }`.
- `GET /batch/{job_id}` — poll status: `status`, `total`, `processed`,
  `progress` (0–1, `0` when `total == 0`), `matched`, `no_match`, `failed`,
  `output_url`, `error`.
- `GET /batch/{job_id}/download` — XLSX `FileResponse`. `400` if not completed,
  `410` if the TTL expired, `404` if the file is missing.

### Upload columns (required, case-insensitive)

`movie_title`, `show_date`, `ticketing_url`. (No `theater` column — the batch
path always calls the runner with `theater=None`, a deliberate difference from
the single-match UI.)

### Output columns

The output is **always XLSX**, regardless of the upload format. All original
columns are preserved in order, followed by four appended columns in this
order: `mapped_title`, `confidence_score`, `reasoning`, `present_in_db`.
`present_in_db` is `Yes` iff a resolved id (`canonical_movie_id`, else
`suggested_movie_id`) is `> 0` and exists in `MovieMaster`; otherwise `No` with
a blank / `NO MATCH` title. A failed row is `mapped_title=''`,
`confidence_score=0`, `reasoning='error: <message>'`, `present_in_db='No'` — a
single bad row never fails the whole job.

### Concurrency model

Sandbox calls are capped at `AGENTIC_BATCH_MAX_CONCURRENCY` (2) via two layers:

1. A **dedicated `agentic` Celery queue** served by `celery-agentic-worker` at
   `--concurrency=2` (the primary cap; `worker_prefetch_multiplier=1` keeps a
   worker from prefetching extra rows).
2. A **TTL-based Redis semaphore backstop** (`sandbox_semaphore.py`). Each
   acquire writes a unique per-holder key with `SET ... EX <ttl> NX`; live
   concurrency is the count of non-expired holder keys. It is deliberately
   **not** a bare `INCR`/`DECR` counter: a bare counter leaks permanently if a
   holder is `SIGKILL`ed (its `finally`/`DECR` never runs), wedging the cap
   toward 0 with no recovery. The TTL (`AGENTIC_TIMEOUT_SECONDS + 60`s) lets a
   crashed holder's slot self-heal on expiry. If Redis is unreachable the
   semaphore fails open and the queue concurrency still bounds throughput.

Counter updates (`processed`/`matched`/`no_match`/`failed`) use atomic
server-side `col = col + 1` SQL, never a Python read-modify-write, so they
never lose an increment under concurrent workers.

### Job TTL & expiry

On completion the job's `ttl` is set to `now + JOB_TTL_HOURS` (24h). Downloads
after that return `410 Gone`. `finalize_batch` writes the output XLSX and marks
the job completed *before* deleting the source upload and the Redis results
hash, so a retry of the callback after a partial failure still finds its inputs
(it is idempotent — a no-op once the job is already completed).

### Celery version

Built and tested against **Celery 5.6.3** (with a Redis broker/backend). The
real-broker chord + per-member retry interaction is covered by
`backend/tests/test_batch_chord_live.py` (marked `@pytest.mark.integration`),
which runs an actual `agentic`-queue worker subprocess to prove the chord
callback fires exactly once after a retried member succeeds. The eager-mode
data-flow test is `backend/tests/test_batch_e2e.py`.

## International Amenity Detection

Sibling module to the domestic detection engine above, built for international
amenity strings (`4DX`, `ScreenX`, `Onyx`, `Xplus`, `KinoEvolution`, etc.), which use a
different, messier vocabulary and a separate priority-tier list. Lives in
`backend/app/intl_detection/` (`IntlScreenFormatEngine`, `IntlMappingIndex`) and reuses
the domestic `app.detection.normalizer` wholesale rather than forking it. Backed by its
own `IntlAmenityMapping` / `IntlDetectionJob` tables — no `region` column was added to
the domestic `AmenityMapping` table, to keep zero regression risk on the
recently-resynced domestic data.

### Endpoints

- `/api/v1/intl-detect` — `POST /single` (single amenity string), `POST /batch`
  (multipart upload, returns `{ "job_id": ... }`).
- `/api/v1/intl-amenities` — master list CRUD (`GET`, `POST`, `PATCH /{id}`,
  `DELETE /{id}`, `POST /{id}/approve`, `POST /{id}/reject`, `POST /import`,
  `GET /export`).
- `/api/v1/intl-jobs` — `GET /{job_id}` status polling, `GET /{job_id}/download`.

### Batch upload columns

The required upload column is **`amenities` or `amenities_string`** — there is no
`circuit_name` requirement, unlike the domestic `/api/v1/detect/batch` endpoint. Do not
add a circuit column to the intl upload contract; there is no circuit data in the intl
seed source.

### Seeding

```bash
cd backend && python app/cli.py seed-intl-from-xlsx /path/to/International\ Amenities\ Priorities.xlsx
```

Dedicated command, separate from `seed-from-xlsx` — the domestic seed path is
untouched. Parses `Sheet1`, tier markers `AMENITIES_PRIORITY_1`..`_5`, and seeds all
rows with `status="approved"`.

### No AI fallback, no review queue

Unlike the domestic engine's Layer 2 (AWS Bedrock Mistral Large on true no-match) and
its human review queue, **the international engine has no Bedrock/AI fallback and no
review queue in this build** — an unmatched intl amenity string simply returns
`screen_format: "Standard"`, `match_source: "No Match"`, `fired_ai: false`, so do not
go looking for an `/intl-review` route or an `IntlReviewItem` table; neither exists.

## Environment Variables

See .env.example for all variables.
