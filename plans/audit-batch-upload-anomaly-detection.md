# Audit / Anomaly Detection — Batch Upload Enhancement

**Objective:** Add an "audit mode" toggle to the existing Batch Upload components for both Screen Format detection and Movie Format detection. When enabled, the user provides a CSV with an extra column (the format they *expect*), the engine runs detection normally, and the output CSV gains 4 extra columns: `detected_format`, `ai_suggested_format`, `anomaly`, `reasoning`.

**Branch:** `feature/audit-batch-upload`  
**Base:** `stage`

---

## Design Decisions (confirmed with user)

| Question | Answer |
|---|---|
| UI placement | Toggle on existing uploader (not a new tab/page) |
| Anomaly definition | `user_format ≠ detected_format` → anomaly = TRUE |
| Output delivery | Background job + downloadable CSV/XLSX (existing pattern) |
| Reasoning populated | Only when AI (Bedrock) was triggered |

---

## New Input CSV Formats

### Screen Format Audit
**Required columns:** `circuit_name`, `amenities`, `screen_format`  
(same as existing, plus `screen_format`)

### Movie Format Audit
**Required columns:** `amenities_string`, `movie_format`  
(note: column name is `amenities_string`, not `amenities`)

---

## New Output Columns (appended to existing output)

| Column | Type | Notes |
|---|---|---|
| `detected_format` | string | Engine result (Layer 0/1/2) |
| `ai_suggested_format` | string | Bedrock suggestion (empty if AI not triggered) |
| `anomaly` | TRUE / FALSE | `user_format ≠ detected_format` |
| `reasoning` | string | Bedrock reasoning (empty if AI not triggered) |

---

## Architecture Overview

The feature touches 4 layers:

1. **Backend schemas** — new `audit_mode: bool` param on batch endpoints; new `AuditBatchResult` fields
2. **Backend workers** — extend `_process_job()` in both workers to compare user-supplied format vs engine result and write 4 extra columns
3. **Frontend hooks** — pass `audit_mode` flag; handle new job stats
4. **Frontend UI** — add toggle + different column hint on both BatchUploader components

No new routes, tables, or jobs are needed. The existing job infrastructure handles everything.

---

## Step Dependency Graph

```
[Step 1: Backend Schema + Worker - Screen Format]
[Step 2: Backend Schema + Worker - Movie Format]
     ↕ (parallel — no shared files)
[Step 3: Frontend - Screen Format BatchUploader + hook]
[Step 4: Frontend - Movie Format BatchUploader + hook]
     ↕ (parallel — no shared files)
```

Steps 1+2 can run in parallel.  
Steps 3+4 can run in parallel.  
Steps 3+4 depend on the API contract established in Steps 1+2 (but the contract is defined here, so they can also run in parallel with 1+2).

---

## Step 1 — Backend: Screen Format Audit Mode

### Context Brief
The screen format batch endpoint is at `backend/app/routers/detect.py:64-107`. It creates a `DetectionJob`, saves the file, then calls `run_batch_job()` from `backend/app/workers/batch_worker.py`. The job worker's `_process_job()` (lines 115-407) does a 2-pass detection (Layer 1 then AI), then writes an XLSX output file. The detection engine's `DetectionResult` already has `ai_suggested_format` and `ai_reasoning` fields. `screen_format` as the user column name won't conflict — the existing output uses the same column name for the *detected* format, but in audit mode we rename accordingly.

### Task List
- [ ] Add `audit_mode: bool = False` query param to `POST /api/v1/detect/batch` in `detect.py`
- [ ] Pass `audit_mode` through to `DetectionJob` creation (add `audit_mode: bool = False` field to the `DetectionJob` SQLModel in `models.py`)
- [ ] In `run_batch_job()`, forward `audit_mode` to `_process_job()`
- [ ] In `_process_job()`:
  - When `audit_mode=True`, expect column `screen_format` in the input (validate its presence alongside `amenities`)
  - Read the user's `screen_format` value per row as `user_format`
  - After detection, compute: `anomaly = (user_format.strip().lower() != result.screen_format.strip().lower())`
  - Append to output row: `detected_format` (engine result), `ai_suggested_format` (or empty), `anomaly` (TRUE/FALSE), `reasoning` (`ai_reasoning` if `fired_ai` else empty string)
  - Normal mode: existing output unchanged
- [ ] Update `JobStatus` response schema to surface `audit_mode` (so frontend can label columns correctly)

### Verification
```bash
cd backend
python -c "
import asyncio, httpx
# POST to /api/v1/detect/batch with audit_mode=true
# and a CSV containing circuit_name,amenities,screen_format
# Verify output XLSX has 4 extra columns
print('manual test needed — start the server first')
"
```

### Exit Criteria
- `POST /api/v1/detect/batch?audit_mode=true` with a CSV containing `circuit_name,amenities,screen_format` returns a job_id
- Downloaded XLSX has columns: `circuit_name`, `amenities`, `screen_format` (user), `detected_format`, `ai_suggested_format`, `anomaly`, `reasoning`
- `anomaly=TRUE` when user `screen_format` ≠ engine result
- Existing batch mode (no `audit_mode`) still works identically

---

## Step 2 — Backend: Movie Format Audit Mode

### Context Brief
The movie format batch endpoint is at `backend/app/routers/movie_detect.py:77-119`. It creates a `MovieFormatJob` and calls `run_movie_batch_job()` from `backend/app/workers/movie_batch_worker.py`. The worker's `_process_job()` (lines 83-388) does 2-pass detection with AI mode (skip/sample/full). `MovieFormatDetectionResult` in `movie_detection/types.py:16-27` already has `ai_suggested_format` and `ai_reasoning`. The user's input column is `amenities_string` (not `amenities`) and the expected format column is `movie_format`.

### Task List
- [ ] Add `audit_mode: bool = False` query param to `POST /api/v1/movie-detect/batch` in `movie_detect.py`
- [ ] Add `audit_mode: bool = False` field to `MovieFormatJob` SQLModel in `models.py`
- [ ] Pass `audit_mode` through to `run_movie_batch_job()` → `_process_job()`
- [ ] In `_process_job()`:
  - When `audit_mode=True`, expect column `movie_format` in the input (validate presence alongside `amenities_string`)
  - Read user's `movie_format` value per row as `user_format`
  - After detection: `anomaly = (user_format.strip().lower() != result.movie_format.strip().lower())`
  - Append to output row: `detected_format`, `ai_suggested_format` (or empty), `anomaly` (TRUE/FALSE), `reasoning` (`ai_reasoning` if `fired_ai` else empty)
  - Normal mode: unchanged
- [ ] Update `MovieJobStatus` response to surface `audit_mode`

### Verification
```bash
# Start server, POST /api/v1/movie-detect/batch?audit_mode=true
# with CSV: amenities_string,movie_format
# Download output — verify 4 extra columns present and anomaly logic correct
```

### Exit Criteria
- `POST /api/v1/movie-detect/batch?audit_mode=true` with `amenities_string,movie_format` CSV returns job_id
- Downloaded XLSX has: `amenities_string`, `movie_format` (user), `detected_format`, `ai_suggested_format`, `anomaly`, `reasoning`
- Anomaly logic correct
- Normal movie batch still works

---

## Step 3 — Frontend: Screen Format BatchUploader Audit Toggle

### Context Brief
`frontend/src/pages/detection/BatchUploader.tsx` is the screen format batch UI. It uses `useBatchJob` hook from `frontend/src/hooks/useBatchJob.ts`. The uploader shows a drag-drop area, optional "include diagnostics" checkbox, progress bar, and download button. The hook calls `POST /api/v1/detect/batch` with `FormData`. The page is tab-wrapped in `DetectionPage.tsx`.

### UI Design (toggle mode)
Add a labeled toggle below the existing "include diagnostics" checkbox:

```
┌─────────────────────────────────────────────────────────┐
│  [Drag & Drop area]                                     │
│                                                         │
│  ☐  Include diagnostics                                 │
│  ☐  Audit mode — compare against your expected format  │
│     When enabled, CSV must include a `screen_format`   │
│     column with your expected format per row.           │
│     Output will include: detected_format, anomaly,     │
│     ai_suggested_format, reasoning                     │
└─────────────────────────────────────────────────────────┘
```

### Task List
- [ ] Add `auditMode: boolean` state to `BatchUploader.tsx`
- [ ] Render a toggle/checkbox below "include diagnostics" with helper text describing the required extra column and new output columns
- [ ] Pass `auditMode` to `useBatchJob.uploadBatch()` call
- [ ] In `useBatchJob.ts`: add `auditMode?: boolean` param to `uploadBatch()`; append `audit_mode=true` as query param when set
- [ ] When `auditMode` is active, update the column hint text in the uploader to show required columns: `circuit_name, amenities, screen_format`
- [ ] Show anomaly count stat in the progress/completion section (backend will return `anomaly_count` in job stats JSON)

### Exit Criteria
- Toggle appears below "include diagnostics"
- Toggle off (default): existing UX completely unchanged
- Toggle on: helper text explains the `screen_format` column requirement
- Uploaded CSV processed, output downloaded — contains 4 extra columns
- Anomaly count shown in completion stats

---

## Step 4 — Frontend: Movie Format BatchUploader Audit Toggle

### Context Brief
`frontend/src/pages/movie-detection/MovieBatchUploader.tsx` (lines 10-251). Uses `useMovieBatchJob` hook from `frontend/src/hooks/useMovieBatchJob.ts`. Has an AI mode selector (skip/sample/full) unique to this uploader. Same pattern as Step 3 but for movie format.

### UI Design (toggle mode)
Same toggle pattern as Step 3, placed below AI mode selector:

```
┌─────────────────────────────────────────────────────────┐
│  [Drag & Drop area]                                     │
│                                                         │
│  AI Mode: [skip ▼]                                      │
│  ☐  Include diagnostics                                 │
│  ☐  Audit mode — compare against your expected format  │
│     When enabled, CSV must include: amenities_string,  │
│     movie_format columns.                              │
│     Output adds: detected_format, anomaly,             │
│     ai_suggested_format, reasoning                     │
└─────────────────────────────────────────────────────────┘
```

### Task List
- [ ] Add `auditMode: boolean` state to `MovieBatchUploader.tsx`
- [ ] Render toggle below AI mode selector with helper text for `amenities_string, movie_format` columns
- [ ] Pass `auditMode` to `useMovieBatchJob.uploadBatch()`
- [ ] In `useMovieBatchJob.ts`: add `auditMode?: boolean` param; append `audit_mode=true` query param when set
- [ ] Update column hint when audit mode active: required `amenities_string, movie_format`
- [ ] Show anomaly count stat in completion section

### Exit Criteria
- Toggle appears in MovieBatchUploader below AI mode selector
- Toggle off: existing UX unchanged
- Toggle on: helper text correct, required columns updated
- Audit output downloaded with 4 extra columns
- Anomaly count stat displayed

---

## Rollback Strategy

All changes are additive (new query param defaults to `False`, new columns only added when `audit_mode=True`). Rollback = revert the branch. No database migrations required — `audit_mode` on the job model can be added as a nullable column defaulting to False.

---

## Anti-patterns Avoided

- No new routes or job tables — reuses existing infrastructure
- No mutations to detection engine — anomaly computed purely at worker layer
- No breaking changes to existing API contracts — `audit_mode` defaults to `False`
- No frontend duplication — toggle within existing components, not new pages

---

## Files Changed Summary

| File | Change |
|---|---|
| `backend/app/models.py` | Add `audit_mode: bool` to `DetectionJob` and `MovieFormatJob` |
| `backend/app/routers/detect.py` | Add `audit_mode` query param |
| `backend/app/routers/movie_detect.py` | Add `audit_mode` query param |
| `backend/app/workers/batch_worker.py` | Audit mode column comparison + 4 extra output cols |
| `backend/app/workers/movie_batch_worker.py` | Same as above for movie format |
| `frontend/src/hooks/useBatchJob.ts` | `auditMode` param |
| `frontend/src/hooks/useMovieBatchJob.ts` | `auditMode` param |
| `frontend/src/pages/detection/BatchUploader.tsx` | Audit toggle + helper text |
| `frontend/src/pages/movie-detection/MovieBatchUploader.tsx` | Audit toggle + helper text |
