# Implementation Plan: Persist Batch Upload State Across Navigation

## Requirements Restatement

Today, batch upload progress lives only in React component state (`useState` inside `useMovieTitleBatchJob`, and its siblings `useMovieBatchJob` / `useBatchJob`). React Router **unmounts** the page component when you navigate to a different section, which destroys that state — including the `job_id`, the polling interval, and the last-known progress. The Celery job on the backend keeps running untouched (this is purely a frontend state-loss bug), but when you navigate back, the hook re-mounts fresh with `job: null`, so the UI shows the empty upload form as if nothing was ever started.

**Goal:** when an upload is in progress (or even completed-but-unviewed), switching sections and coming back should show the *same* progress card, still polling, not the empty form. Ideally this should also survive a full page reload, not just in-app navigation.

**Scope decision needed:** there are three near-identical hook+page pairs with this exact bug:

| Page | Hook | Route |
|---|---|---|
| Movie Title Matching → Batch Upload | `useMovieTitleBatchJob` | `/movie-title-matching` |
| Movie Format Detection → Batch | `useMovieBatchJob` | `/movie-detection` (via `MovieBatchUploader`) |
| Amenity Detection → Batch | `useBatchJob` | `/detection` (via `BatchUploader`) |

Build the persistence mechanism generically and apply it to all three, since they share the identical bug and near-identical code — fixing one and leaving the other two broken would just relocate the same complaint. Flag if only the Movie Title Matching one should be touched.

## Root Cause (confirmed by reading the code)

- `useMovieTitleBatchJob` (`frontend/src/hooks/useMovieTitleBatchJob.ts`) stores `job`, `isActive`, and the poll interval ref purely in local component state/refs.
- `MovieTitleMatchingPage` renders `MovieTitleBatchMatcher` inside a `Tabs`/`TabsContent`, and the page itself is only mounted while the route `/movie-title-matching` is active (`routes.tsx`). Navigate away → React Router unmounts `MovieTitleBatchMatcher` → the hook instance is destroyed → `pollRef` interval is cleared via the `useEffect` cleanup, and all state is gone.
- Navigate back → a brand-new hook instance mounts with `job: null`. There is currently zero mechanism to know "there's a job `abc-123` still running out there" — the `job_id` was never persisted anywhere outside memory.
- Backend confirms polling is cheap and safe to resume: `GET /api/v1/movie-title-match/batch/{job_id}` (`movie_title_match.py:124`) is a stateless, idempotent read of `MovieTitleBatchJob` — resuming polling after an arbitrary gap works with no backend changes.

## Implementation Phases

### Phase 1: Persist job identity in `localStorage`
- Add a small helper (e.g. `frontend/src/lib/persistedJob.ts`) with `saveActiveJob(key, jobId)` / `loadActiveJob(key)` / `clearActiveJob(key)`, using a namespaced `localStorage` key per hook (e.g. `batch-job:movie-title-match`).
- On `uploadBatch()` success, persist `job_id` immediately (before the first poll resolves).
- On terminal status (`completed`/`failed`) reached, or on explicit `reset()`, clear the persisted key — so finished/dismissed jobs don't come back from the dead on next visit.
- Chose `localStorage` over `sessionStorage` so the fix also survives a full page reload/browser restart, not just in-app tab switches — matches "session is persisted" in the request. (If it should *not* survive a reload/new tab, `sessionStorage` is a one-line swap.)

### Phase 2: Resume-on-mount logic in the hook
- On hook init, read `loadActiveJob(key)`. If a `job_id` exists:
  - Immediately fire one `GET /batch/{job_id}` to fetch current status (handles the case where it already finished while away).
  - If still `queued`/`processing`, restart the polling interval exactly as `uploadBatch` does today.
  - If terminal, set `job` to the final state (so "Download Results" / error is immediately visible) and clear the persisted key.
  - If the fetch 404s (job expired/TTL'd server-side — `JOB_TTL_HOURS`), clear the persisted key and fall back to the empty form.
- Apply the same pattern to `useMovieBatchJob` and `useBatchJob` (their poll endpoints are `/api/v1/movie-jobs/{id}` and `/api/v1/jobs/{id}` respectively — same shape, different base path).

### Phase 3: Page-level UI consistency
- No structural change needed in `MovieTitleBatchMatcher.tsx` / `MovieBatchUploader.tsx` / `BatchUploader.tsx` — they already render based on `job` being non-null. Once the hook resumes `job` on mount, the existing "job progress" branch renders automatically.
- Double check the brief flash of the empty-form state before the resume fetch resolves — add a lightweight `resuming` boolean (true from mount until the first resumed poll/fetch resolves) so the card doesn't flicker to the upload form for a frame. Small addition to each hook's return shape.

## Dependencies
- None — no new packages. `localStorage` is a browser built-in; no backend changes required (existing GET job-status endpoints are already stateless/idempotent).

## Risks
- **LOW:** Stale `job_id` in localStorage pointing to a job that's since passed its `JOB_TTL_HOURS` and been cleaned up server-side → must handle the 404/410 case gracefully (Phase 2 covers this).
- **LOW:** Multiple browser tabs open to the same page could each try to resume/poll the same job — harmless (GET is idempotent, no double-submission risk since resume never re-uploads), just slightly redundant polling.
- **LOW:** If a user starts a second batch upload before the first's terminal state was ever observed (e.g. closed the tab mid-upload before first poll), only one `job_id` is tracked per hook/key — acceptable, matches current single-active-job UI model.
- **MEDIUM (design choice, not a bug):** `localStorage` persists across full browser restarts and even after the tab is closed. If that's more persistence than wanted (e.g. should clear when the browser fully closes), use `sessionStorage` instead — cleared on tab close but still survives in-app navigation and same-tab reload.

## Estimated Complexity: LOW–MEDIUM
- Phase 1 (persistence helper): ~30 min
- Phase 2 (resume logic, ×1–3 hooks depending on scope): ~1–1.5 hr
- Phase 3 (resuming-flicker guard): ~20 min
- **Total: ~2–2.5 hours** for all three hooks; **~1 hour** if scoped to Movie Title Matching only.
