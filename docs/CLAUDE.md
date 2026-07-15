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

Mode B (agentic) batch pipeline: upload a CSV/XLSX of movie titles, run one
Claude-sandbox match per row, download an XLSX of results. Async Celery job +
polling — never synchronous. Requires `AGENTIC_TITLE_MATCH_ENABLED=true`.

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

## Environment Variables

See .env.example for all variables.
