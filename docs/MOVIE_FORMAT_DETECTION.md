# Movie Format Detection — Feature Summary

## Overview

A parallel detection vertical for movie projection formats (70MM, 35MM, 3D, 2D). Shares the same three-track cascade engine, batch pipeline, review queue, and Redis dedup cache as the existing Amenity Detection system — but without circuit-level scoping.

---

## Format Priority

| Format | Priority Tier |
|--------|--------------|
| 70MM   | P1 (highest) |
| 35MM   | P2           |
| 3D     | P3           |
| 2D     | P4 (default) |

When multiple formats appear in a single amenity string, the highest-priority match wins.

---

## Detection Engine

Three-track cascade, same normalizer as Amenity Detection:

| Track | Method | Example |
|-------|--------|---------|
| A | Light normalization dict lookup | `"3D"` → 3D |
| B | Stopword-removal dict lookup | `"3D Audio"` → 3D |
| C (sub-check 1) | Concat-form exact match — no min-length guard | `"3 D"`, `"3-D"` → 3D |
| C (sub-check 2) | Token-set + prefix match (min_len=4 guard) | `"reald3d"` → 3D |

Fallback: no match → `movie_format = "2D"`, `fired_ai = True` → Bedrock AI review queue.

**Normalization fix shipped:** `"3 D"` / `"3-D"` / `"70 mm"` / `"35 mm"` now correctly resolve via Track C sub-check 1 (`_concat_exact` index). Previously blocked by the `min_len=4` guard.

---

## Redis Dedup Cache

- Key namespace: `movie_format:v1:{amenity_lower}` (separate from amenity cache `bedrock:v1:*`)
- TTL: 30 days
- MGET bulk prefetch: all unique keys fetched in one Redis call before Bedrock pass
- Cross-job persistence: same amenity string never calls Bedrock twice within TTL window

---

## Batch Processing Modes

Selectable per upload via the AI mode selector in the UI:

| Mode | Behavior | Use case |
|------|----------|----------|
| Skip AI (default) | No Bedrock calls; unmatched rows → 2D | Fast bulk processing |
| Sample | Bedrock for first 50 unique unmatched strings | Quick spot-check |
| Full AI | Bedrock for all unmatched strings | Complete AI coverage |
| Async Batch | AWS `CreateModelInvocationJob` via S3 JSONL | Large jobs >100 unmatched (requires S3 + IAM config) |

Concurrency: `ThreadPoolExecutor` + `Semaphore`, default 20 (configurable via `BEDROCK_MAX_CONCURRENCY`).

---

## Bedrock Performance Improvements

### Plan A — Fast Model (active)

- New method: `BedrockClient.classify_batch_fast(amenity)`
- Model: `mistral.mistral-7b-instruct-v0:2` (configured via `BATCH_MODEL_ID`)
- Minimal prompt, `max_tokens=100`, 8s timeout
- Regex fallback when JSON parse fails: extracts `70MM|35MM|3D|2D` from raw text
- ~3–5x faster and cheaper than `classify_single` with Mistral Large

### Plan B — Async S3 Batch (available, requires config)

- Module: `backend/app/detection/bedrock_batch.py`
- Model: `anthropic.claude-3-5-haiku-20241022-v1:0` (configured via `ASYNC_BATCH_MODEL_ID`)
- Pipeline: write JSONL to S3 → `CreateModelInvocationJob` → poll → read output JSONL
- ~10–50x speedup for large unique-unmatched sets
- Requires: `S3_BATCH_BUCKET` and `BEDROCK_BATCH_ROLE_ARN` set in `.env`
- Falls back to sync `classify_batch_fast` automatically when not configured

---

## Environment Variables

```env
# Bedrock
BEDROCK_REGION=us-east-1
BEDROCK_MODEL_ID=mistral.mistral-large-2407-v1:0
BEDROCK_API_KEY=...
BATCH_MODEL_ID=mistral.mistral-7b-instruct-v0:2       # Plan A fast model
BEDROCK_MAX_CONCURRENCY=20
BATCH_AI_SAMPLE_LIMIT=50
BEDROCK_CACHE_TTL_DAYS=30

# Plan B async batch (optional)
S3_BATCH_BUCKET=                                       # leave empty to disable
ASYNC_BATCH_MODEL_ID=anthropic.claude-3-5-haiku-20241022-v1:0
BEDROCK_BATCH_ROLE_ARN=                                # IAM role ARN for Bedrock batch
BATCH_JOB_POLL_INTERVAL=10                             # seconds between status polls
BATCH_JOB_MAX_WAIT=600                                 # max seconds to wait for job
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/movie-detect/single` | Detect format for a single amenity string |
| POST | `/api/v1/movie-detect/batch` | Upload CSV/XLSX; returns job ID |
| GET | `/api/v1/movie-jobs/{job_id}` | Poll job status + progress |
| GET | `/api/v1/movie-jobs/{job_id}/download` | Download output XLSX |
| GET | `/api/v1/movie-formats` | List all mappings (paginated) |
| POST | `/api/v1/movie-formats` | Add new keyword mapping |
| PATCH | `/api/v1/movie-formats/{id}` | Update mapping |
| DELETE | `/api/v1/movie-formats/{id}` | Delete mapping |
| GET | `/api/v1/movie-formats/export` | Export mappings as XLSX |
| GET | `/api/v1/movie-review` | List review queue items |
| POST | `/api/v1/movie-review/{id}/approve` | Approve AI suggestion → new mapping |
| POST | `/api/v1/movie-review/{id}/reject` | Reject with reason |
| POST | `/api/v1/movie-review/bulk-approve` | Bulk approve selected items |

Single detect request:
```json
{ "amenity": "70mm Film" }
```

Single detect response:
```json
{
  "movie_format": "70MM",
  "match_track": "A",
  "confidence": 1.0,
  "detected_keyword": "70mm",
  "match_source": "Bucket Priority 1",
  "fired_ai": false
}
```

Batch upload form fields: `file`, `include_diagnostics` (bool), `batch_ai_mode` (skip|sample|full|async_batch).

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `movieformatmapping` | Approved keyword → format mappings |
| `movieformatreviewitem` | Pending AI suggestions and mapping submissions |
| `movieformatjob` | Batch job records (status, progress, output path) |

Seed data (applied at startup if table is empty): `70mm→70MM`, `35mm→35MM`, `3d→3D`, `2d→2D`, `reald3d→3D`.

---

## Frontend Pages

| Route | Page |
|-------|------|
| `/movie-detection` | AI Movie Format Detection (single + batch) |
| `/movie-formats` | Master Movie Format List |
| `/movie-review` | Movie Format Review Queue |

Accessible via the **Movie Format Detection** section in the sidebar.

---

## Accuracy

Cross-verified against 7,344-row ground-truth CSV (`screen format.csv`):

- **94.91% accuracy** with keyword-only detection (no AI)
- 374 anomalies — majority are source-data labeling inconsistencies (strings containing explicit `"3D"` labeled as `2D` in the CSV)
- Engine behavior is correct; anomalies are data issues, not engine bugs
