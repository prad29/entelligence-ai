# International Amenity → Screen Format Mapping — Design

## Context

Domestic amenity strings are normalized to canonical screen formats (`MX4D`, `IMAX`,
`Dolby Cinema`, etc.) via a rule-based priority-bucket cascade engine
(`backend/app/detection/`), backed by the `AmenityMapping` table and an AI (Bedrock)
fallback + human review queue for anything the rule engine can't resolve.

We need the same capability for international amenity strings, which use a different,
messier vocabulary (mixed languages, chain-specific formats like `4DX`, `ScreenX`,
`Onyx`, `Xplus`, `KinoEvolution`) and a different priority-tier list, curated in a
separate spreadsheet (`International Amenities Priorities.xlsx`).

## Architecture decision: new sibling module

Mirrors the existing `movie_detection` module, which was built as a full parallel
feature set alongside `detection` rather than folding into it. This keeps zero risk to
the domestic `AmenityMapping` table/engine (which was just re-synced from prod) and
allows the intl priority-tier scheme to evolve independently.

**Rejected alternative**: adding a `region` column to the existing `AmenityMapping`
table. Rejected because it would require modifying the domestic engine/loader/migration
and mixes two independently curated bucket lists into one table — higher regression
risk for no real benefit, and inconsistent with this codebase's documented
one-table-per-feature convention (`backend/app/models.py:153-159`).

## Scope for this build

**In scope:**
- New `IntlAmenityMapping` table (mirrors `AmenityMapping` columns, including a
  `circuit_name` column that stays nullable/dormant — no circuit data exists in the
  current xlsx, but the column avoids a future migration when it does).
- New `intl_detection/` package: `engine.py` (`IntlScreenFormatEngine`,
  `IntlMappingIndex` — same 3-track cascade as domestic: exact → stopword-cleaned →
  token/concat fuzzy), `loader.py`, `seed_loader.py`, `types.py`.
- Single-sheet xlsx parser (`intl_detection/seed_loader.py`) matching the actual file
  structure: `Sheet1`, two columns (`Amenity`, `Mapped to`), tier markers
  `AMENITIES_PRIORITY_1`..`_5`. No Sheet3, no `na_default`, no circuit parsing.
- New CLI command `seed-intl-from-xlsx <path>` (dedicated, not a flag on the existing
  `seed-from-xlsx`, to avoid touching the domestic seed path).
- New routers: `intl_detect.py` (single + batch detect), `intl_amenities.py` (master
  list CRUD), `intl_jobs.py` (batch job polling).
- New Celery task `intl_batch_worker.py`, mirroring `movie_batch_worker.py`.
- New frontend routes `/intl-detection`, `/intl-amenities` with page components
  (`IntlDetectionPage`, `IntlSingleDetector`, `IntlResultCard`, `IntlBatchUploader`,
  `IntlAmenitiesPage`, `IntlAmenityFormDrawer`) ported from their domestic counterparts,
  pointed at `/api/v1/intl-*` endpoints.
- A `RegionToggle` component added to the toolbar row (same row as the existing
  `Single | Batch Upload` tabs, right-aligned) on both `DetectionPage`/
  `IntlDetectionPage` and `AmenitiesPage`/`IntlAmenitiesPage`. Clicking it navigates to
  the sibling route. The global sidebar (`AppSidebar.tsx`) is **not** modified — no
  toggle there, per explicit direction.

**Explicitly out of scope for this build:**
- AI/Bedrock fallback for unmatched intl strings (to be added later). Unmatched
  strings simply return a no-match `DetectionResult` (e.g. `screen_format: "Standard"`,
  `match_source: "No Match"`, `fired_ai: false`).
- `IntlReviewItem` table / review queue / `/intl-review` route — dropped along with
  Bedrock, since there's nothing to review without AI suggestions.
- `IntlCircuitAlias` table — domestic's `CircuitAlias` was confirmed to be dead weight
  (every entry is an identity mapping; only effect of an empty table is a harmless log
  warning). Not worth replicating. If real intl circuit data materializes, circuit
  resolution can be built directly against `circuit_name` without an alias layer.

## Data model

```python
class IntlAmenityMapping(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    amenity_keyword: str = Field(index=True)
    screen_format: str
    priority_tier: int
    circuit_name: Optional[str] = Field(default=None, index=True)  # dormant, nullable
    na_default: Optional[str] = None
    status: str = Field(default="pending")  # draft|pending|approved|rejected
    notes: Optional[str] = None
    created_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = Field(default=1)
```

New Alembic migration creates `intlamenitymapping` with indexes on `amenity_keyword`
and `circuit_name`, matching the domestic migration's shape.

## Seed data

Source file: `International Amenities Priorities.xlsx` — `Sheet1`, columns
`Amenity` | `Mapped to`, tier markers `AMENITIES_PRIORITY_1` through `_5`. Parser
dedupes within tier (same approach as domestic's Sheet1 parsing), sets
`status="approved"` on all seeded rows, and does not populate `circuit_name` or
`na_default` for any row (no such data exists in this file).

## API surface

- `POST /api/v1/intl-detect/single` — single amenity string detection
- `POST /api/v1/intl-detect/batch` — batch upload, returns job id
- `GET /api/v1/intl-jobs/{id}` — job status polling
- `GET /api/v1/intl-amenities` / `POST` / `PATCH /{id}` / `DELETE /{id}` — master list CRUD
- `POST /api/v1/intl-amenities/bulk-import` — bulk import (mirrors domestic's bulk import
  endpoint at `amenities.py:223-260`)

## Frontend

- Routes: `/intl-detection`, `/intl-amenities` (added to `routes.tsx`)
- `RegionToggle` — small segmented control, e.g. `[Domestic] [International]`, placed in
  the toolbar row next to `Single | Batch Upload` tabs on both detection pages and both
  master-list pages. On click, navigates to the sibling route
  (`/detection` ↔ `/intl-detection`, `/amenities` ↔ `/intl-amenities`), preserving the
  active sub-tab (single/batch) where applicable.
- `TopBar.tsx`'s `pageTitles` map gets two new entries: `/intl-detection` → `"AI Amenity
  Detection (International)"`, `/intl-amenities` → `"Master Amenity List
  (International)"`.
- Sidebar (`AppSidebar.tsx`) is unchanged — no new nav items, no toggle there.

## Testing

Mirrors the existing domestic test suite shape:
- Unit tests for `intl_detection/seed_loader.py` parsing (tier markers, dedup, malformed
  rows) against a small fixture xlsx built from the real file's structure.
- Unit tests for `intl_detection/engine.py` cascade matching (Track A/B/C) using a
  fixture `IntlMappingIndex` built from a handful of real intl entries (e.g. `MX4D`,
  `4DX`, `ScreenX`, `Onyx`).
- API integration tests for `intl_detect`, `intl_amenities`, `intl_jobs` routers.
- Batch worker test mirroring `movie_batch_worker` tests (job lifecycle, S3 round-trip).

## Rollout

1. Implement on `feature/international-screen-format` (branched from `stage`, which is
   113 commits ahead of `main` and is the actual active development line / PR target).
2. Work broken into phases, each ending in a commit.
3. PR opened against `stage` once all phases are complete and tests pass.
4. Local Docker Compose stack rebuilt for manual verification before requesting review.

## Implementation notes

Recorded once the build was actually done, for anyone comparing this doc's original
assumptions against what shipped:

- **Batch worker uses `threading.Thread`, not Celery.** §Scope above says "New Celery
  task `intl_batch_worker.py`, mirroring `movie_batch_worker.py`" — but
  `movie_batch_worker.py` is not itself a Celery task; it's a plain function launched
  via `threading.Thread(..., daemon=True)` from `routers/movie_detect.py`, and neither
  it nor `batch_worker.py` appears in `celery_app.py`'s `include` list. The confirmed
  decision was to mirror the actual precedent (threads), not the doc's literal wording,
  since the intl workload is a rule-engine-only pass with no Bedrock calls and is
  strictly lighter than the domestic job that already runs this way.
- **Bulk import/export are at `/import` and `/export`**, not `/bulk-import` as cited
  above — matching the real domestic endpoint (`amenities.py`'s `POST /import` /
  `GET /export`), not the doc's mis-cited name.
- **The real xlsx produced 97 approved rows after dedup**, not the ~101 raw data rows
  before dedup. The dedupe key is `(normalized keyword, screen_format, priority_tier)`,
  and several duplicate/near-duplicate keywords collapsed under normalization — e.g.
  `IMAX-3D` and `IMAX 3D` normalize to the same Track A/B form and collapse to a single
  row. Final approved distribution by tier: P1→22, P2→25, P3→42, P4→5, P5→3.
- **No frontend automated test framework exists in this repo** (`frontend/package.json`
  has no vitest/jest/playwright). Phase 4's verification gate was therefore
  `tsc -b && vite build` + `oxlint` + a manual/Playwright click-through against the
  running stack, not automated tests — an accepted, explicitly-recorded deviation from
  the general 80%-coverage expectation.
