# International Amenity → Screen Format Mapping — Phased Implementation Plan

Companion to [`international-amenity-screen-format.md`](./international-amenity-screen-format.md).
That document is the **authoritative scope**. This document is purely the *ordered
execution plan* for it: exact files, exact names, exact tests, per-phase definition of
done. It does not re-decide architecture.

Branch: `feature/international-screen-format` (already checked out, at `23331a4`, even
with `origin/stage`). One git commit per phase. PR against `stage` after Phase 5.

---

## 0. Locked naming conventions

**Every subagent implementing any phase must use these names verbatim.** Deviating
breaks other phases.

### Backend — database

| Thing | Name |
| --- | --- |
| Table (SQLModel auto-derived) | `intlamenitymapping` |
| Model class | `IntlAmenityMapping` (in `backend/app/models.py`) |
| Batch-job table | `intldetectionjob` |
| Batch-job model class | `IntlDetectionJob` (in `backend/app/models.py`) |
| Indexes | `ix_intlamenitymapping_amenity_keyword`, `ix_intlamenitymapping_circuit_name` |
| Alembic revision id | `e1f2a3b4c5d6` |
| Alembic `down_revision` | `326d2ebe211d` (current head — verify with `alembic heads` before writing) |

### Backend — package `backend/app/intl_detection/`

| File | Public names |
| --- | --- |
| `__init__.py` | empty |
| `types.py` | `IntlApprovedMapping` (frozen dataclass), `IntlDetectionResult` (dataclass) |
| `engine.py` | `IntlMappingIndex`, `IntlScreenFormatEngine` |
| `loader.py` | `build_intl_engine_from_db(session) -> IntlScreenFormatEngine` |
| `seed_loader.py` | `parse_intl_xlsx(path) -> list[IntlAmenityMapping]`, `seed_intl_db(session, path, reset=True) -> int` |

**No `intl_detection/normalizer.py`.** Reuse `app.detection.normalizer`
(`normalize_string`, `track_a_clean`, `track_b_clean`, `track_c_tokens`) exactly as
`app/movie_detection/{engine,loader}.py` already do. See Risk R5.

### Backend — routers, workers, CLI, schemas

| Thing | Name |
| --- | --- |
| Router modules | `backend/app/routers/intl_detect.py`, `intl_amenities.py`, `intl_jobs.py` |
| Router prefixes | `/api/v1/intl-detect`, `/api/v1/intl-amenities`, `/api/v1/intl-jobs` |
| FastAPI tags | `intl-detect`, `intl-amenities`, `intl-jobs` |
| Worker module | `backend/app/workers/intl_batch_worker.py` |
| Worker entrypoint | `run_intl_batch_job(job_id, upload_path, include_diagnostics, detection_engine, audit_mode=False)` |
| Upload dir | `/tmp/intl_amenity_uploads` |
| Output dir / file | `/tmp/intl_amenity_outputs/{job_id}_output.xlsx` |
| App state engine | `app.state.intl_engine` |
| CLI function / command | `seed_intl_from_xlsx` → `seed-intl-from-xlsx` |
| Pydantic schemas | `IntlAmenityMappingCreate`, `IntlAmenityMappingRead`, `IntlAmenityMappingPatch` (in `backend/app/schemas.py`) |
| Bulk import endpoint | `POST /api/v1/intl-amenities/import` — see Risk R7 |
| Export endpoint | `GET /api/v1/intl-amenities/export` |

### Frontend

| Thing | Name |
| --- | --- |
| Routes | `/intl-detection`, `/intl-amenities` |
| Pages dir 1 | `frontend/src/pages/intl-detection/` — `IntlDetectionPage.tsx`, `IntlSingleDetector.tsx`, `IntlResultCard.tsx`, `IntlBatchUploader.tsx` |
| Pages dir 2 | `frontend/src/pages/intl-amenities/` — `IntlAmenitiesPage.tsx`, `IntlAmenityFormDrawer.tsx` |
| Hooks | `frontend/src/hooks/useIntlDetect.ts`, `useIntlAmenities.ts`, `useIntlBatchJob.ts` |
| Shared component | `frontend/src/components/ui/RegionToggle.tsx` |
| Persisted-job namespace | `'intl-amenity-detect'` (must differ from domestic `'amenity-detect'` — Risk R6) |

### Tests

`backend/tests/test_intl_engine.py`, `test_intl_seed_loader.py`,
`test_intl_api_integration.py`, `test_intl_batch_worker.py`.

---

## 1. Verified facts about the source spreadsheet

Confirmed by reading `~/Downloads/International Amenities Priorities (1).xlsx` with
openpyxl — the seed parser only needs to handle **this** shape:

- One worksheet, `Sheet1`. 107 rows × 2 columns.
- Row 1 is the header `('Amenity', 'Mapped to')`.
- Tier markers in column A at rows 2, 27, 54, 98, 104 →
  `AMENITIES_PRIORITY_1` … `AMENITIES_PRIORITY_5`.
- **101 data rows** (not ~106): tier counts P1=24, P2=26, P3=43, P4=5, P5=3.
- Column C does not exist. No `circuit_name`, no `na_default`, no `Sheet3`.
- 25 distinct `screen_format` values, including `4DX`, `MX4D`, `Epic Vue`,
  `KinoEvolution`, `Dolby Cinema`, `IMAX`, `iSense`, `ScreenX`, `Onyx`,
  `ONYX - Pathe`, `Xplus`, `XD`, `XL`, `Cnmk XD`, `Finity`, `ICE CGR`,
  `Laser Ultra`, `SuperScreen`, `TCX Toho`, `Ultimate`, `VueXtreme`,
  `LED Cinema`, `MAXXOmniplex`, `MacroXE Cinepolis`, `Standard`.
- **Tiers are P1–P5 only.** There is no P6. See Risk R8.
- **Three non-ASCII keywords** exist and must round-trip:
  `4DX Voorpremière` → `4DX`, `Dolby Cinema Voorpremière` → `Dolby Cinema`,
  `ScreenX Voorpremière` → `ScreenX`. The shared normalizer's NFD accent folding
  already handles these — this is a concrete assertion to put in the tests.
- **`KinoEvolution` appears in both P1 and P3** with different keywords. Tier ordering
  therefore genuinely matters; do not assume format→tier is a function.
- **P5 maps to `Standard`** (`70MM`, `Digital`, `Digital 3D`). These are *deliberate*
  `Standard` mappings and must be distinguishable from a no-match `Standard`
  (`match_source` differs: `"Keyword Match"` vs `"No Match"`).

---

## Phase 1 — Data model + Alembic migration

Smallest possible commit that leaves `main` importable and all existing tests green.

### Files to modify

**`backend/app/models.py`** — append two models after `AmenityMapping` /
`DetectionJob` (keep them adjacent to their domestic twins for readability):

- `IntlAmenityMapping` — exact field list from the design doc §Data model, verbatim.
  `circuit_name` and `na_default` stay present-but-dormant.
- `IntlDetectionJob` — clone of `DetectionJob` field-for-field: `id: str` uuid4 pk,
  `status="queued"`, `total=0`, `processed=0`, `file_path`, `output_path`,
  `include_diagnostics=False`, `audit_mode=False`, `created_at`, `ttl`, `stats`.
  Add a one-line docstring citing the one-table-per-feature convention at
  `models.py:153-159`, matching how `MovieTitleIntlBatchJob` documents itself.

### Files to create

**`backend/alembic/versions/e1f2a3b4c5d6_add_intl_amenity_tables.py`**

- `revision = 'e1f2a3b4c5d6'`, `down_revision = '326d2ebe211d'`.
- **Must open with the `inspector.has_table()` no-op guard** copied from
  `326d2ebe211d_add_deletedshowtimejob_table.py:18-23`, checked against *both*
  `intlamenitymapping` and `intldetectionjob`. Rationale: `create_db_and_tables()`
  (SQLModel `create_all`) runs on FastAPI startup, so in a dev container the tables
  may already exist by the time alembic runs, and an unguarded `create_table` would
  crash the migration.
- Use the modern idiom of the newest migration — plain `sa.String()` /
  `sa.Integer()` / `sa.DateTime()` / `sa.Boolean()` with `server_default=`, not
  `sqlmodel.sql.sqltypes.AutoString()`.
- `op.create_index(op.f('ix_intlamenitymapping_amenity_keyword'), 'intlamenitymapping', ['amenity_keyword'])`
  and the same for `circuit_name`, mirroring `2153535df54c_initial_tables.py`.
- `downgrade()` drops both indexes then both tables.

### Tests

No new tests. This phase is validated by migration round-trip:

```
cd backend && alembic upgrade head && alembic downgrade -1 && alembic upgrade head
```

### Definition of done

- [ ] `alembic heads` shows exactly one head, `e1f2a3b4c5d6`.
- [ ] `alembic upgrade head` succeeds on an already-`create_all`-ed database (proves
      the guard works) **and** on a fresh empty database (proves the DDL works).
- [ ] `alembic downgrade -1` then `upgrade head` succeeds.
- [ ] `python -c "from app.main import app"` succeeds.
- [ ] `pytest backend/tests -q` — no regressions (baseline must be recorded before
      the phase starts, so a pre-existing failure isn't mistaken for a new one).
- [ ] Commit: `feat: add IntlAmenityMapping and IntlDetectionJob tables`

---

## Phase 2 — `intl_detection` package + CLI seed + unit tests

The heart of the feature and the phase with the most judgement calls. Everything here
is pure-Python and DB-light, so it is also the most testable.

### Files to create

**`backend/app/intl_detection/__init__.py`** — empty.

**`backend/app/intl_detection/types.py`**

- `IntlApprovedMapping` — `@dataclass(frozen=True)`: `amenity_keyword: str`,
  `screen_format: str`, `priority_tier: int`, `circuit_name: Optional[str]`,
  `na_default: Optional[str]`, `norm_exact: str`, `norm_track_a: str`,
  `norm_track_b: str`, `norm_track_c: frozenset`. Same shape as
  `detection/types.py:ApprovedMapping`; the two dormant columns are carried through so
  the loader is a straight field copy and no migration is needed later.
- `IntlDetectionResult` — `@dataclass`: `screen_format: str`, `match_track: str`,
  `confidence: float`, `matched_keyword: Optional[str] = None`,
  `detected_keyword: Optional[str] = None`, `priority_tier: Optional[int] = None`,
  `match_source: Optional[str] = None`, `fired_ai: bool = False`,
  `diagnostics: Optional[dict] = field(default=None)`.
  **Keep `fired_ai` and hardcode it `False`** — the frontend and worker both read it,
  and keeping the field means the later Bedrock phase is additive. Do **not** add
  `ai_suggested_format` / `ai_reasoning`; AI is out of scope and dead optional fields
  invite dead code paths.
- **No `IntlBedrockSuggestion`.** Out of scope per design doc.

**`backend/app/intl_detection/engine.py`** — ported from
`app/movie_detection/engine.py` (185 lines), which is the codebase's actual
sibling-module precedent, with the domestic circuit machinery deleted.

- Import `track_a_clean, track_b_clean, track_c_tokens, normalize_string` **from
  `app.detection.normalizer`**. Do not fork it.
- Module constants: `_IGNORE_TOKENS`, `_NON_ALNUM` — copy from
  `movie_detection/engine.py`.
- `_concat_form(s)`, `_split_segments(s)` (splits on `|`, drops ignore tokens and
  `•`), `_match_segment(seg)` running Track A → Track B → Track C.
- `IntlMappingIndex(mappings: list[IntlApprovedMapping])`:
  - sort mappings by `priority_tier` ascending once at construction;
  - build `_exact: dict[str, IntlApprovedMapping]`, `_track_a`, `_track_b`,
    `_track_c` (list for token-set scan), and — **adopting `movie_detection`'s
    improvement over domestic** — `_concat_exact: dict[str, IntlApprovedMapping]`
    with first-writer-wins so exact concat matches are O(1) and are not subject to the
    `TRACK_C_MIN_LEN=4` guard. This matters for intl: `XD`, `XL`, `LED`, `70MM` are
    all shorter than 4 characters.
  - **No `aliases` parameter.** Intl has no `CircuitAlias` analogue (design doc,
    out-of-scope §3).
- `IntlScreenFormatEngine(index)`:
  - `detect(self, amenity: str) -> IntlDetectionResult` — **single argument, no
    `circuit` param**, exactly like `MovieFormatEngine.detect`. There is no intl
    circuit data, so no Layer 0.
  - **Deleted relative to domestic:** `_VIP_CIRCUITS` / VIP override,
    `_IMAX_CIRCUIT` override, `na_default` resolution, `_P6_TIER = 6` handling, and
    the circuit-match / circuit-scoped rungs of the tie-break ladder.
  - **Retained tie-break ladder** (in order): lower `priority_tier` wins → earlier
    segment position → longer matched keyword → track order A < B < C.
  - Confidence: `1.0` Track A, `0.9` Track B, `0.75` Track C — identical to domestic
    so the UI's confidence rendering needs no special-casing.
  - No match → `IntlDetectionResult(screen_format="Standard", match_track="none",
    confidence=0.0, match_source="No Match", fired_ai=False)`. Note this differs from
    `MovieFormatEngine`, which sets `fired_ai=True` on no-match to trigger Bedrock —
    intl must **not**, since there is no fallback wired.
  - A keyword match must set `match_source="Keyword Match"` so a P5 `Standard` is
    distinguishable from a no-match `Standard` (see §1).

**`backend/app/intl_detection/loader.py`** — near-verbatim
`movie_detection/loader.py` (29 lines):

```
build_intl_engine_from_db(session) -> IntlScreenFormatEngine
  select(IntlAmenityMapping).where(status == "approved")
  → IntlApprovedMapping(... norm_exact=normalize_string(kw).lower(),
                        norm_track_a=track_a_clean(kw),
                        norm_track_b=track_b_clean(kw),
                        norm_track_c=track_c_tokens(kw))
  → IntlMappingIndex(mappings) → IntlScreenFormatEngine(index)
```

No alias query.

**`backend/app/intl_detection/seed_loader.py`** — ported from
`detection/seed_loader.py` but **deliberately restructured** to fix the domestic
version's testability problem (Risk R3):

- `_clean_cell(value) -> str` — copy verbatim (strips `\xa0`, the literal string
  `xa0`, and smart quotes). Non-negotiable: real cells in this file contain NBSP.
- `parse_intl_xlsx(path: str) -> list[IntlAmenityMapping]` — **pure, no DB, no
  session**. Reads `wb.worksheets[0]` only. Loop: if `col_a.startswith("AMENITIES_PRIORITY_")`
  set `current_tier = int(suffix)` and `continue`; else if `current_tier is not None
  and col_a and col_b` append `IntlAmenityMapping(amenity_keyword=col_a,
  screen_format=col_b, priority_tier=current_tier, status="approved")`. Rows before
  the first marker (the header row) are skipped because `current_tier is None`.
  Dedupe with key `(normalize_string(kw).lower(), screen_format, priority_tier)`,
  first-wins. Never sets `circuit_name` or `na_default`.
- `seed_intl_db(session, path, reset=True) -> int` — thin wrapper: optionally clears
  `intlamenitymapping`, then `session.add()` each parsed row, `commit()`, return the
  count.
  - **Use `session.exec(delete(IntlAmenityMapping))` (SQLAlchemy Core `delete`), not
    `TRUNCATE ... RESTART IDENTITY CASCADE`.** Domestic's `seed_db` uses raw
    Postgres-only `TRUNCATE`, which is exactly why it has no unit test. `delete()`
    works on both Postgres and the SQLite in-memory engine the test suite uses. See
    Risk R3.

### Files to modify

**`backend/app/cli.py`** — add a third `@cli.command()`:

```
seed_intl_from_xlsx(path: str = typer.Argument(..., help="Path to International Amenities Priorities.xlsx"),
                    reset: bool = typer.Option(True, "--reset/--no-reset", ...))
```

Body mirrors `seed_from_xlsx`: `create_db_and_tables()` → `with Session(db_engine) as
session: n = seed_intl_db(session, path, reset=reset)` → `typer.echo(f"Seeded {n}
international mappings from {path}")`. Typer derives the command name
`seed-intl-from-xlsx`. **Do not touch `seed_from_xlsx`** — design doc §Scope is
explicit that the domestic seed path stays untouched.

### Tests to write

**`backend/tests/test_intl_seed_loader.py`**

Build the fixture xlsx **in-process with openpyxl into a `tmp_path`** — never commit a
binary fixture and never depend on `~/Downloads`. A module-scoped fixture writes a
sheet reproducing the real structure: header row, `AMENITIES_PRIORITY_1` + a few rows,
`AMENITIES_PRIORITY_2` + rows, …, `AMENITIES_PRIORITY_5` + `70MM`/`Digital` → `Standard`.

- `test_header_row_is_not_treated_as_a_mapping` — `('Amenity','Mapped to')` produces
  no row.
- `test_tier_markers_assign_priority_tier` — a row after `AMENITIES_PRIORITY_3` gets
  `priority_tier == 3`.
- `test_rows_missing_a_format_are_skipped` — blank column B is dropped.
- `test_blank_rows_are_skipped`.
- `test_all_seeded_rows_are_approved` — every row `status == "approved"`.
- `test_circuit_name_and_na_default_are_never_populated`.
- `test_duplicate_keyword_within_tier_is_deduped`.
- `test_same_keyword_in_two_tiers_is_kept` — dedupe key includes tier, so a genuine
  cross-tier repeat survives.
- `test_nbsp_and_smart_quotes_are_stripped` — `'4DX\xa0'` → `'4DX'`.
- `test_accented_keyword_survives_parsing` — `'4DX Voorpremière'` round-trips.
- `test_seed_intl_db_inserts_parsed_rows` and
  `test_seed_intl_db_reset_clears_existing_rows` — both against an in-memory SQLite
  session, which is only possible because of the `delete()` decision above.

**`backend/tests/test_intl_engine.py`**

Add an intl fixture set to `backend/tests/conftest.py`: a `_make_intl_mapping(keyword,
fmt, tier)` helper filling all four norm fields via the shared normalizer,
`INTL_MAPPINGS` built from ~15 real entries drawn from the actual spreadsheet
(`MX4D`, `4DX`, `4DX 3D`, `4DX Voorpremière`, `ScreenX`, `Onyx`, `ONYX - Pathe`,
`Xplus`, `KinoEvolution` at both P1 and P3, `Dolby Cinema`, `IMAX`, `XD`, `XL`,
`LED`, `70MM`→`Standard`), and
`@pytest.fixture(scope="module") def intl_engine() -> IntlScreenFormatEngine`.
Mirror `conftest.py`'s existing `_make_mapping` / `ALL_MAPPINGS` / `engine` shape.

- Track A exact: `detect("4DX")` → `4DX`, `match_track == "A"`, `confidence == 1.0`.
- Track A case/whitespace insensitivity: `detect("  screenx  ")` → `ScreenX`.
- Track B stopword/hyphen: `detect("ONYX - Pathe")` → `ONYX - Pathe`,
  `match_track == "B"`.
- Track C token-set: `detect("4DX 3D Premium")` → `4DX`, `match_track == "C"`,
  `confidence == 0.75`.
- Concat-exact index: `detect("4DX3D")` → `4DX` — proves `_concat_exact` fires below
  `TRACK_C_MIN_LEN`.
- Short-keyword match: `detect("XD")` → `XD`; `detect("XL")` → `XL`.
- Accent folding: `detect("4DX Voorpremière")` → `4DX` **and**
  `detect("4DX Voorpremiere")` (unaccented) → `4DX`.
- Tier precedence: a pipe string containing both a P1 and a P3 keyword resolves to the
  P1 format.
- Pipe segmentation: `detect("Reserved Seating | ScreenX")` → `ScreenX` (ignore token
  dropped).
- No match: `detect("Comfy Recliners")` → `screen_format == "Standard"`,
  `match_source == "No Match"`, `confidence == 0.0`, **`fired_ai is False`**.
- Deliberate-Standard disambiguation: `detect("70MM")` → `screen_format ==
  "Standard"` but `match_source == "Keyword Match"` and `priority_tier == 5`.
- Empty / `None`-ish input: `detect("")` returns the no-match result without raising.
- `test_detect_takes_no_circuit_argument` — calling `detect("4DX", "AMC")` raises
  `TypeError`, pinning the deliberate signature divergence from domestic.

### Definition of done

- [ ] `pytest backend/tests/test_intl_engine.py backend/tests/test_intl_seed_loader.py -q` all green.
- [ ] `pytest backend/tests -q` — no regressions.
- [ ] `cd backend && python app/cli.py seed-intl-from-xlsx "$HOME/Downloads/International Amenities Priorities (1).xlsx"`
      against the local Docker Postgres prints `Seeded 101 international mappings`.
- [ ] `SELECT priority_tier, count(*) FROM intlamenitymapping GROUP BY 1 ORDER BY 1;`
      returns exactly `1→24, 2→26, 3→43, 4→5, 5→3`.
- [ ] Re-running the seed command is idempotent (still 101 rows, not 202).
- [ ] `grep -rn "TRUNCATE" backend/app/intl_detection/` returns nothing.
- [ ] `grep -rn "bedrock\|Bedrock" backend/app/intl_detection/` returns nothing.
- [ ] Commit: `feat: add intl_detection engine, loader, seed parser and CLI command`

---

## Phase 3 — Routers + batch worker + integration tests

### Architecture note read before starting

The design doc says *"New Celery task `intl_batch_worker.py`, mirroring
`movie_batch_worker.py`."* **`movie_batch_worker.py` is not a Celery task.** It is a
plain function launched with `threading.Thread(..., daemon=True)` from
`routers/movie_detect.py:129-135`, and neither it nor `batch_worker.py` appears in
`celery_app.py`'s `include` list. See Risk R1.

**Decision for this plan: mirror the actual precedent — `threading.Thread`.** This is
the faithful reading of "mirroring `movie_batch_worker.py`", requires no new Celery
queue, no `celery_app.py` change, and no new `docker-compose.yml` worker service. The
intl workload is a rule-engine-only pass with no Bedrock calls, so it is strictly
lighter than the domestic job that already runs this way. If the reviewer wants real
Celery, that is a scoped follow-up, not a Phase 3 expansion.

### Files to create

**`backend/app/workers/intl_batch_worker.py`** — port of `movie_batch_worker.py` with
both Bedrock passes deleted. Expect roughly 200 lines versus the original's 460,
because the entire second pass disappears.

- `_peek_headers(contents, ext)` and `_read_rows(upload_path)` — copy verbatim from
  `movie_batch_worker.py:23-51`. (Duplicated rather than shared, consistent with how
  `batch_worker.py` and `movie_batch_worker.py` each carry their own copy. Do not
  refactor the domestic workers to share it in this phase — that would put domestic
  regression risk inside an intl commit.)
- `run_intl_batch_job(job_id, upload_path, include_diagnostics, detection_engine,
  audit_mode=False)`:
  - `with Session(db_engine) as session:` fetch `IntlDetectionJob`, set
    `status="processing"`, commit.
  - `try:` single pass over rows calling `detection_engine.detect(amenity_string)`;
    append output columns `screen_format`, `match_track`, `confidence`,
    `matched_keyword`, `priority_tier`, `match_source` (plus a `diagnostics` JSON
    column only when `include_diagnostics`).
  - Update `job.processed` periodically (every N rows) so the frontend progress bar
    moves, matching the domestic cadence.
  - **No `ThreadPoolExecutor`, no `threading.Semaphore`, no `PatternFill` AI
    highlighting** — nothing is AI-classified, so the yellow-fill code has no
    trigger and must not be copied over as dead code.
  - `audit_mode`: when the upload has both an amenity column and an existing
    `screen_format` column, append a `match_status` column (`MATCH` /
    `MISMATCH`) comparing engine output to the provided value, same semantics as
    domestic audit mode.
  - Write `/tmp/intl_amenity_outputs/{job_id}_output.xlsx` (always xlsx regardless of
    input extension, same as domestic), `os.makedirs(..., exist_ok=True)` first.
  - Set `job.output_path`, `job.stats = json.dumps({"matched": n, "no_match": m})`,
    `job.status = "completed"`, `job.ttl = datetime.utcnow() +
    timedelta(hours=settings.JOB_TTL_HOURS)`, commit.
  - `except Exception:` set `status="failed"`, log with `logger.exception`, commit —
    never let a thread die silently.
  - `finally:` `os.remove(upload_path)` guarded by `os.path.exists`.

**`backend/app/routers/intl_detect.py`** — port of `movie_detect.py` minus the entire
Bedrock/cache/`MovieFormatReviewItem` block.

- `router = APIRouter(prefix="/api/v1/intl-detect", tags=["intl-detect"])`,
  `_UPLOAD_DIR = "/tmp/intl_amenity_uploads"`.
- `class IntlDetectSingleRequest(BaseModel): amenity: str`.
- `POST /single` — `engine = request.app.state.intl_engine`;
  `result = engine.detect(payload.amenity)`; return `result.__dict__`. **No `session`
  dependency needed** (nothing is written), and no `AI_TRIGGER_MODE` branch.
- `POST /batch` — copy the domestic validation ladder exactly:
  extension in `(.xlsx, .csv)` else 400; `_estimate_rows(contents, ext)` >
  `settings.MAX_BATCH_ROWS` → 400; `_peek_headers` must contain `amenities` or
  `amenities_string` else 400; `audit_mode` additionally requires `screen_format`
  else 400. Then write the upload, insert an `IntlDetectionJob`, launch
  `threading.Thread(target=run_intl_batch_job, args=(job_id, upload_path, diag_bool,
  request.app.state.intl_engine), kwargs={"audit_mode": audit_mode}, daemon=True)`,
  return `{"job_id": job_id}`.
- `_estimate_rows(contents, ext)` — copy verbatim from `movie_detect.py:139-152`.
- **`circuit_name` is not a required upload column** — this is the one place the intl
  contract deliberately differs from `detect.py`, whose required cols are
  `["amenities", "circuit_name"]`. Call it out in a comment so a future reader does
  not "fix" it.

**`backend/app/routers/intl_jobs.py`** — near-verbatim `movie_jobs.py` (55 lines)
against `IntlDetectionJob`.

- `GET /{job_id}` → `{job_id, status, total, processed, progress, matched, no_match,
  output_url}`. `progress = min(1.0, processed/total) if total else 0.0`,
  `round(..., 3)`. `output_url` is
  `/api/v1/intl-jobs/{id}/download` only when `status == "completed" and output_path`.
  Drop `ai_suggestions` from the payload — it would always be `0`.
- `GET /{job_id}/download` → `FileResponse`; 404 missing job, 400 not completed,
  410 TTL expired, 404 output file gone. `filename=f"intl_amenity_results_{job_id[:8]}.xlsx"`.

**`backend/app/routers/intl_amenities.py`** — port of `amenities.py` (272 lines).

- `router = APIRouter(prefix="/api/v1/intl-amenities", tags=["intl-amenities"])`.
- Reuse the existing `write_audit()` pattern from `amenities.py` for `AuditLog` rows;
  entity string `"IntlAmenityMapping"`.
- `GET ""` — filters `search`, `status`, `tier`, `page`, `page_size` →
  `PaginatedResponse[IntlAmenityMappingRead]`. **Drop the `circuit` filter** — the
  column is dormant and an always-empty filter is a UI liability.
- `POST ""`, `PATCH /{id}`, `DELETE /{id}` — the design doc's API surface lists
  exactly these. Also port `POST /{id}/approve` and `POST /{id}/reject`, because
  `POST ""` inserts with `status="pending"` and without an approve path a newly added
  mapping can never become detectable (the loader filters on
  `status == "approved"`). Skip `PUT /{id}` — `PATCH` covers it and domestic's `PUT`
  is redundant.
- **Every mutation that can change the approved set must end with
  `request.app.state.intl_engine = build_intl_engine_from_db(session)`** — same
  rebuild-on-write contract as domestic. Forgetting this is the single most likely
  silent bug in this phase.
- `POST /import` and `GET /export` — ports of `amenities.py:211-241` and `244-272`.
  Import requires headers `amenity_keyword`, `screen_format`, `priority_tier`;
  inserts `status="pending"`; returns `{"imported": n}`. Export writes the approved
  set with columns `amenity_keyword, screen_format, priority_tier, status`
  (dropping `circuit_name` / `na_default`, which are always blank).

### Files to modify

**`backend/app/schemas.py`** — add `IntlAmenityMappingCreate`,
`IntlAmenityMappingRead` (`class Config: from_attributes = True`),
`IntlAmenityMappingPatch`. Same field lists as their domestic twins **minus
`circuit_name` and `na_default`** — the columns exist in the table for future-proofing
but must not be part of the public write contract yet, or the frontend will grow a
field it can never populate meaningfully.

**`backend/app/main.py`**

- Line 8-ish: `from app.routers import intl_detect, intl_amenities, intl_jobs`.
- After line 53: three `app.include_router(...)` calls.
- In `startup()` (line 217+), inside the existing `with Session(db_engine)` block,
  after `app.state.movie_engine = ...`:
  `from app.intl_detection.loader import build_intl_engine_from_db` and
  `app.state.intl_engine = build_intl_engine_from_db(session)`.

### Tests to write

**`backend/tests/test_intl_api_integration.py`**

Follow the pattern documented in `test_deleted_showtimes_api.py`'s header, **not**
`test_api_integration.py`'s: set `os.environ["DATABASE_URL"] = "sqlite:///:memory:"`
before importing app modules, build a `StaticPool` sqlite engine, monkeypatch
`_db_module.engine`, `SQLModel.metadata.create_all`, and put the
`app.dependency_overrides[get_session]` assignment in an **autouse fixture** with
teardown — because pytest imports every test module before running any test, so a
module-level override would clobber other already-collected modules' overrides.
Inject `app.state.intl_engine` explicitly (startup does not run under `TestClient`
construction in this pattern). `TestClient(app, raise_server_exceptions=False)`.

- `POST /api/v1/intl-detect/single` with `{"amenity": "4DX"}` → 200,
  `screen_format == "4DX"`, `fired_ai is False`.
- Single with an unmatched string → `"Standard"` / `"No Match"`.
- `GET /api/v1/intl-amenities` on an empty table → `items == []`, `total == 0`.
- `POST /api/v1/intl-amenities` → 200, row created with `status == "pending"`.
- `test_pending_mapping_is_not_detectable_until_approved` → then
  `POST /{id}/approve` → the same single-detect call now matches. This is the test
  that pins the `app.state.intl_engine` rebuild contract.
- `PATCH /{id}` updates a field; `DELETE /{id}` removes it and the mapping stops
  matching.
- `GET /api/v1/intl-amenities?search=...&status=approved&tier=1` filters correctly and
  `total_pages` is right.
- `POST /api/v1/intl-detect/batch` rejects `.txt` (400), rejects a file missing the
  amenity column (400), rejects `audit_mode=true` without `screen_format` (400).
- `POST /batch` with a valid 3-row xlsx returns a `job_id`, and
  `GET /api/v1/intl-jobs/{job_id}` returns 200 with a `status` field.
- `GET /api/v1/intl-jobs/does-not-exist` → 404.
- `GET /api/v1/intl-jobs/{id}/download` on a `queued` job → 400; on a job with an
  expired `ttl` → 410.
- `POST /api/v1/intl-amenities/import` with a valid xlsx → `{"imported": n}`;
  missing a required header → 400.
- `GET /api/v1/intl-amenities/export` → 200 with the xlsx content type.
- `test_domestic_endpoints_are_unaffected` — `GET /api/v1/amenities` still 200s, and
  `POST /api/v1/detect/single` still accepts its `circuit_name` payload. Cheap
  insurance that the intl routers did not shadow a domestic prefix.

**`backend/tests/test_intl_batch_worker.py`**

Call `run_intl_batch_job` **directly and synchronously** (no thread), exactly the way
a worker function should be tested. Note: there is no S3 round-trip to test — the
design doc's "S3 round-trip" line describes the deleted-showtimes worker, not the
amenity workers, which are purely local-filesystem (`/tmp/...`). See Risk R2.

- `test_job_lifecycle_queued_to_completed` — `status` ends `"completed"`,
  `processed == total`, `output_path` exists on disk.
- `test_output_workbook_has_appended_columns` — reopen with openpyxl and assert the
  original columns are preserved in order followed by the appended ones.
- `test_csv_input_produces_xlsx_output`.
- `test_matched_and_no_match_counts_land_in_stats`.
- `test_unmatched_row_gets_standard_and_no_match`.
- `test_row_with_empty_amenity_cell_does_not_crash_the_job`.
- `test_malformed_upload_marks_job_failed_not_hung` — point at a nonexistent /
  corrupt path, assert `status == "failed"` and `error`/`stats` recorded.
- `test_upload_file_is_deleted_after_completion`.
- `test_ttl_is_set_on_completion`.
- `test_audit_mode_appends_match_status_column`.

### Definition of done

- [ ] `pytest backend/tests/test_intl_api_integration.py backend/tests/test_intl_batch_worker.py -q` all green.
- [ ] `pytest backend/tests -q` — no regressions (especially
      `test_api_integration.py`, which is the file most at risk from the
      `dependency_overrides` interaction).
- [ ] `grep -rn "celery" backend/app/workers/intl_batch_worker.py backend/app/routers/intl_*.py`
      returns nothing (confirms the threading decision was implemented, not half-migrated).
- [ ] `GET /docs` lists all three intl routers with their correct prefixes.
- [ ] Manual: seed → `POST /api/v1/intl-detect/single {"amenity":"ScreenX Voorpremière"}`
      returns `ScreenX`.
- [ ] Manual: upload a 20-row xlsx to `/api/v1/intl-detect/batch`, poll
      `/api/v1/intl-jobs/{id}` to `completed`, download the output and eyeball it.
- [ ] Commit: `feat: add intl detect/amenities/jobs routers and batch worker`

---

## Phase 4 — Frontend pages, routes, RegionToggle

No frontend test framework exists in this repo (`frontend/package.json` has no
vitest/jest/playwright — scripts are only `dev`, `build: "tsc -b && vite build"`,
`lint: "oxlint"`, `preview`). Phase 4's gate is therefore typecheck + lint + a
manual click-through, an accepted and explicitly-recorded deviation from the 80%
coverage rule. See Risk R9.

### Files to create

**`frontend/src/components/ui/RegionToggle.tsx`**

- Props: `interface RegionToggleProps { domesticPath: string; intlPath: string }`.
- Derives the active side from `useLocation().pathname` and navigates with
  `useNavigate()`. Two buttons, `Domestic` / `International`, styled as a segmented
  control matching `Tabs.tsx`'s visual language (same `h-9`, `rounded-lg`,
  `bg-zinc-100 dark:bg-zinc-800/60 p-1` shell; active pill
  `bg-white dark:bg-zinc-900 shadow-sm`) so it reads as a sibling control rather
  than a bolted-on widget.
- `role="group"` + `aria-label="Region"`, `aria-pressed` on each button, and a
  `focus-visible:ring-[#4A9FD4]/50` ring consistent with the rest of the UI.
- **Sub-tab preservation**: append the current `?tab=` search param when navigating.
  See the `Tabs.tsx` decision below.

**`frontend/src/pages/intl-detection/IntlDetectionPage.tsx`**

Structurally `DetectionPage.tsx` plus the toggle row. Because `Tabs.tsx` renders its
own `TabsPrimitive.List` and exposes no slot for right-aligned content, wrap:

```
<div className="flex flex-col gap-4">
  <div className="flex items-center justify-end">
    <RegionToggle domesticPath="/detection" intlPath="/intl-detection" />
  </div>
  <Tabs defaultValue={...} tabs={[single, batch]}> ... </Tabs>
</div>
```

**Do not modify `Tabs.tsx`.** It is consumed by ~8 pages; adding a `value` /
`onValueChange` / toolbar-slot API to satisfy one requirement is a shared-component
change with domestic regression risk inside a frontend-only commit. See Risk R4.

For sub-tab preservation, read `useSearchParams()` and pass
`defaultValue={searchParams.get('tab') === 'batch' ? 'batch' : 'single'}`, and have
`RegionToggle` carry `?tab=` across. `Tabs` is uncontrolled, so `defaultValue` is
honoured on mount — which is exactly the moment that matters, since crossing regions
is a route change and therefore a remount. This gets the design doc's behaviour with
zero shared-component churn. The tab does not currently write itself back to the URL
on click, so preservation works when the toggle is used after a route-level tab
choice; if the reviewer wants full round-tripping, promoting `Tabs` to controlled is a
scoped follow-up.

**`frontend/src/pages/intl-detection/IntlSingleDetector.tsx`**

Port of `SingleDetector.tsx` (131 lines). Changes:

- `EXAMPLE_CHIPS` replaced with real intl strings from the spreadsheet:
  `'4DX'`, `'4DX3D'`, `'ScreenX Voorpremière'`, `'ONYX - Pathe'`, `'Dolby Cinema'`,
  `'MX4D'`, `'Xplus'`.
- **Remove the `Circuit Name (optional)` `<Input>` entirely** — the intl API takes no
  circuit. Keeping a dead field is worse than removing it.
- Renders `<IntlResultCard result={result} />`.

**`frontend/src/pages/intl-detection/IntlResultCard.tsx`**

Port of `ResultCard.tsx` (125 lines). Changes:

- **Delete the AI-suggestion callout block** — it is gated on `result.fired_ai`,
  which is always `false` for intl, so it is unreachable code.
- Extend `getFormatVariant()` for the intl vocabulary: `4dx`/`mx4d` (accent),
  `imax`, `dolby cinema`, `screenx`, `onyx`, `xplus`, `kinoevolution`,
  `epic vue`, `led cinema`, `standard`. Anything unrecognised falls through to the
  neutral variant — do not throw on an unknown format, since the master list is
  user-editable.
- Surface `match_source` so a P5 `Standard` is visually distinct from a no-match
  `Standard`. This is the single most valuable UI difference from domestic.

**`frontend/src/pages/intl-detection/IntlBatchUploader.tsx`**

Port of `BatchUploader.tsx` (278 lines). Changes:

- `useDropzone` accepts `.csv` / `.xlsx` (unchanged).
- Keep the `includeDiagnostics` and `auditMode` toggles.
- **Stat tiles: `Total` / `Keyword Match` / `No Match → Standard` only.** Drop
  `AI Classified` and `Anomalies` — no AI, and no anomaly detection in the intl
  worker.
- Uses `useIntlBatchJob()`; download via
  `window.open(\`${import.meta.env.VITE_API_URL ?? ''}${job.output_url}\`)`, same as
  domestic.
- Copy text should say which column is required (`amenities` or `amenities_string`)
  and must **not** mention `circuit_name`.

**`frontend/src/pages/intl-amenities/IntlAmenitiesPage.tsx`**

Port of `AmenitiesPage.tsx` (298 lines). Changes:

- Toolbar: search input, status `<Select>`, tier `<Select>` **P1–P5 only** (no P6 in
  the intl data). **Drop the circuit filter input.**
- `<RegionToggle domesticPath="/amenities" intlPath="/intl-amenities" />` in the
  toolbar row.
- The existing `ml-auto` button cluster holds `Import` / `Export` / `Add Mapping`.
  **Wire `Import` and `Export` to the real endpoints** (`POST
  /api/v1/intl-amenities/import` via a hidden file input, `GET
  /api/v1/intl-amenities/export` via `window.open`). Domestic's buttons have no
  `onClick` and are dead; shipping the intl page with the same dead buttons would be
  copying a known defect. Fixing domestic's is out of scope.
- `tierVariantMap` limited to P1–P5. `statusVariantMap` unchanged.
- DataTable columns: keyword, format, tier, status, updated_at, actions. **No circuit
  column.**

**`frontend/src/pages/intl-amenities/IntlAmenityFormDrawer.tsx`**

Port of `AmenityFormDrawer.tsx` (128 lines). Changes:

- zod schema `intlAmenitySchema`: `tier: z.enum(['P1','P2','P3','P4','P5'])` — **P6
  removed**; `status: z.enum(['approved','pending','rejected'])`.
- No `circuit` field, no `na_default` field.

**`frontend/src/hooks/useIntlDetect.ts`**

Port of `useDetect.ts` (46 lines). `POST /api/v1/intl-detect/single` with
`{ amenity }` only. `IntlDetectResult` interface: `screen_format`, `match_track`,
`confidence`, `matched_keyword`, `priority_tier`, `match_source`, `fired_ai`,
`diagnostics`. No `circuit_name`, no `ai_*`.

**`frontend/src/hooks/useIntlAmenities.ts`**

Port of `useAmenities.ts` (130 lines) against `/api/v1/intl-amenities`. Keep the
UI-model / API-model split: `IntlAmenity { id, keyword, screen_format, tier, status,
updated_at }` ← `fromApi()` mapping `tier: \`P${a.priority_tier}\``, and the inverse
on write. Expose `create`, `update` (PATCH), `remove`, `approve`, `reject`.

**`frontend/src/hooks/useIntlBatchJob.ts`**

Port of `useBatchJob.ts` (135 lines). Changes:

- **`const STORAGE_NAMESPACE = 'intl-amenity-detect'`** — must not be
  `'amenity-detect'`, or an in-flight intl job and an in-flight domestic job overwrite
  each other in localStorage via `persistedJob.ts`'s `'batch-job:'` prefix. Risk R6.
- Polls `/api/v1/intl-jobs/${jobId}` every 2000 ms; resumes a persisted job on mount.
- `uploadIntlBatch(file, includeDiagnostics, auditMode)` → `POST
  /api/v1/intl-detect/batch` (`?audit_mode=true` when set).

### Files to modify

**`frontend/src/routes.tsx`** — two imports and two `<Route>` entries, placed
immediately after their domestic siblings:

```
<Route path="/intl-detection" element={<IntlDetectionPage />} />
<Route path="/intl-amenities" element={<IntlAmenitiesPage />} />
```

The `<Route path="*" element={<Navigate to="/detection" replace />} />` catch-all
stays last.

**`frontend/src/components/layout/TopBar.tsx`** — two entries in `pageTitles`
(lines 8-19): `'/intl-detection': 'AI Amenity Detection (International)'`,
`'/intl-amenities': 'Master Amenity List (International)'`.

**Ordering matters.** The title is resolved with
`Object.entries(pageTitles).find(([path]) => location.pathname.startsWith(path))`,
i.e. first-match-wins over insertion order. `'/intl-detection'.startsWith('/detection')`
is `false`, so there is no collision with the existing keys, but insert the intl keys
**before** any shorter prefix that could ever shadow them and add a comment noting the
`startsWith` fragility.

**`frontend/src/pages/detection/DetectionPage.tsx`** and
**`frontend/src/pages/amenities/AmenitiesPage.tsx`** — add `<RegionToggle>` to the
domestic side too, per design doc §Frontend ("on both ... pages"). These are the only
two domestic files touched in the whole build; keep the diffs to inserting the toggle
and nothing else.

### Files explicitly NOT modified

- **`frontend/src/components/layout/AppSidebar.tsx`** — design doc §Frontend is
  explicit: unchanged, no new nav items, no toggle. Verified safe: `SidebarNavItem`
  marks itself active via `location.pathname.startsWith(to)`, and
  `'/intl-detection'.startsWith('/detection')` is `false`, so adding the intl routes
  does **not** produce a false-active highlight on the Detection nav item. No sidebar
  entry means the intl pages are reachable only via `RegionToggle` (or a direct URL) —
  that is the intended behaviour, not an oversight.
- **`frontend/src/components/ui/Tabs.tsx`** — see Risk R4.

### Definition of done

- [ ] `cd frontend && npm run build` (`tsc -b && vite build`) passes with zero errors.
- [ ] `cd frontend && npm run lint` (oxlint) passes clean.
- [ ] Manual click-through against the running stack:
  - [ ] `/intl-detection` renders, TopBar reads *AI Amenity Detection (International)*.
  - [ ] Single tab: each example chip returns the right format; `ScreenX Voorpremière`
        → `ScreenX`; a nonsense string → `Standard` labelled *No Match*; `70MM` →
        `Standard` labelled *Keyword Match / P5*.
  - [ ] Batch tab: upload → progress advances → completes → download opens a
        populated xlsx.
  - [ ] `/intl-amenities` lists the 101 seeded rows, tier filter P1–P5 works, search
        works, pagination works.
  - [ ] Add a mapping → appears as `pending` → approve → immediately detectable on
        `/intl-detection` without a restart.
  - [ ] Import and Export buttons both work.
  - [ ] `RegionToggle` round-trips `/detection` ↔ `/intl-detection` and
        `/amenities` ↔ `/intl-amenities`; the sub-tab is preserved.
  - [ ] Sidebar highlighting is unchanged while on an intl route.
  - [ ] Start an intl batch job and a domestic batch job in two tabs; **both** resume
        correctly on reload (proves the localStorage namespace split).
  - [ ] Keyboard: `RegionToggle` is tab-reachable with a visible focus ring.
  - [ ] Light and dark themes both look intentional.
- [ ] Commit: `feat: add international detection and master-list frontend pages`

---

## Phase 5 — Final wiring, docs, verification

### Files to modify

**`docs/CLAUDE.md`** — new `## International Amenity Detection` section: the three
endpoint prefixes, the required upload column, the `seed-intl-from-xlsx` command, and
one sentence stating that intl has **no Bedrock fallback and no review queue** so the
next reader does not go looking for one.

**`docs/international-amenity-screen-format.md`** — do not rewrite the design, but
append a short *Implementation notes* section recording the four resolved
discrepancies: (a) the worker is threaded, not Celery; (b) bulk import is `/import`,
not `/bulk-import`; (c) the real row count is 101 with tiers P1–P5; (d) no frontend
tests exist so Phase 4's gate is build + lint + manual.

**`README.md`** (if it lists routes or CLI commands) — add the two routes and the
seed command.

### Checks, not code

- `alembic heads` → single head.
- Full `pytest backend/tests -q`.
- `cd frontend && npm run build && npm run lint`.
- `docker-compose down && docker-compose up -d --build`, then confirm from a cold
  start: startup logs show no exception, `app.state.intl_engine` is populated (a
  single-detect call succeeds without a 500), and the domestic engine still works.
- `git diff stage...HEAD --stat` reviewed: confirm the only domestic files touched are
  `models.py`, `schemas.py`, `main.py`, `cli.py`, `routes.tsx`, `TopBar.tsx`,
  `DetectionPage.tsx`, `AmenitiesPage.tsx`, `conftest.py`, plus docs — and that
  `AppSidebar.tsx`, `Tabs.tsx`, `detection/`, `movie_detection/`, `batch_worker.py`,
  `amenities.py`, `detect.py`, `jobs.py` are **not** in the diff.
- `code-reviewer` + `security-reviewer` agents over the full branch diff (file-upload
  handling and unbounded queries are the relevant risk areas).

### Definition of done

- [ ] All of the above pass.
- [ ] Commit: `docs: document international amenity screen format feature`
- [ ] PR opened against `stage` with a summary covering all five phases and a test
      plan derived from the per-phase manual checks.

---

## Risks and ambiguities

Found while reading the actual code; the design doc did not anticipate these.

### R1 — "New Celery task `intl_batch_worker.py`" is based on a false premise (HIGH)

Design doc §Scope says the worker should be a Celery task "mirroring
`movie_batch_worker.py`". But `movie_batch_worker.py` is **not** a Celery task:
`run_movie_batch_job` is a plain function launched via
`threading.Thread(..., daemon=True)` from `routers/movie_detect.py:129-135`, and
neither it nor `batch_worker.py` appears in `celery_app.py`'s `include` list (Celery
is used only for semantic index, agentic match, prod-db sync, external match, and
deleted showtimes). "Mirror the precedent" and "use Celery" are therefore mutually
exclusive instructions.

*Plan's resolution:* mirror the precedent — threads. Rationale in Phase 3. Cost of
the alternative: a new queue in `celery_app.py`, a new `task_routes` entry, a new
`celery-*-worker` service in `docker-compose.yml`, and prod deploy config — none of
which are in the design doc's scope list. **This should be confirmed with the
requester before Phase 3 starts**, since reversing it later means rewriting the
worker, the router dispatch, and the worker test.

### R2 — There is nothing to mirror for the batch-worker tests (HIGH)

Design doc §Testing says "Batch worker test mirroring `movie_batch_worker` tests (job
lifecycle, S3 round-trip)". Two problems: (a)
`grep -rln "movie_detection\|MovieFormatEngine\|movie-detect" backend/tests/` returns
**nothing** — there are no `movie_detection` or `movie_batch_worker` tests at all;
(b) the amenity/movie workers never touch S3 (they write `/tmp/movie_outputs/...`);
S3 belongs to the deleted-showtimes worker. So the intl worker tests must be authored
fresh from `test_engine.py` + `test_api_integration.py` +
`test_deleted_showtimes_api.py` patterns, and there is no S3 round-trip to cover.
Phase 3 enumerates the tests explicitly for this reason.

### R3 — Domestic `seed_db` is Postgres-only, which is why it is untested (MEDIUM)

`detection/seed_loader.py:seed_db` runs
`session.exec(sa_text("TRUNCATE amenitymapping, circuitalias RESTART IDENTITY CASCADE"))`.
That raises under the SQLite in-memory engine the whole test suite uses, so a faithful
port would make `test_intl_seed_loader.py` untestable. *Resolution:* the intl
`seed_loader` splits a pure `parse_intl_xlsx` from a DB-touching `seed_intl_db`, and
the latter uses SQLAlchemy Core `delete()` instead of raw `TRUNCATE`. Slight divergence
from domestic, deliberate, and it is what makes the phase's test list achievable.

### R4 — `Tabs.tsx` cannot host the RegionToggle or preserve the sub-tab as-is (MEDIUM)

`frontend/src/components/ui/Tabs.tsx` is **uncontrolled** — its props are exactly
`{ defaultValue, tabs, children, className }`, with no `value` / `onValueChange` — and
it renders its own `TabsPrimitive.List` with **no slot for right-aligned toolbar
content**. So the design doc's "same row as the existing `Single | Batch Upload` tabs,
right-aligned" and "preserving the active sub-tab" cannot both be satisfied without
touching a component shared by ~8 pages. *Resolution:* put the toggle in its own
right-aligned row directly above the tabs (visually adjacent, no shared-component
change), and use a `?tab=` search param for sub-tab preservation via `defaultValue`.
This is a small, visible deviation from the design doc's literal wording and **should
be shown to the requester during Phase 4 review**; promoting `Tabs` to controlled with
a `toolbar` slot is the clean fix if they want the toggle strictly inline, but it
carries domestic regression risk and belongs in its own commit.

### R5 — The shared normalizer's stopword list is English-only (MEDIUM, accepted)

`detection/normalizer.py`'s `track_b_clean` strips
`STOPWORDS = {"the","a","an","and","or","with","in","at","by"}` — English only. Intl
strings are multilingual (Dutch `Voorpremière`, French, Japanese-derived `TCX Toho`).
Track B will therefore be weaker for non-English strings than it is domestically.
Accepted for this build: `movie_detection` reuses the same normalizer wholesale, the
NFD accent folding already handles the accented cases correctly, and the actual
spreadsheet's 101 rows contain no non-English stopwords that would matter. Recorded so
that if intl match quality disappoints in review, the stopword list is the first place
to look — not the engine.

### R6 — localStorage collision between domestic and intl batch jobs (MEDIUM)

`useBatchJob.ts` hardcodes `STORAGE_NAMESPACE = 'amenity-detect'`, and
`lib/persistedJob.ts` keys off `'batch-job:' + namespace`. If `useIntlBatchJob.ts` is
copied without changing that constant, an in-flight intl job silently overwrites an
in-flight domestic job (and vice versa) and one of them becomes unresumable. Pinned in
the naming table and in the Phase 4 manual checklist.

### R7 — Design-doc endpoint citation and name are both slightly off (LOW)

§API surface cites "domestic's bulk import endpoint at `amenities.py:223-260`" and
names the intl one `POST /api/v1/intl-amenities/bulk-import`. The real domestic
endpoint is `POST /import` at `amenities.py:211-241` (with `GET /export` at 244-272).
*Resolution:* use `/import` and `/export` for consistency with domestic, since the
frontend hook is being written fresh either way and matching the sibling module beats
matching a typo. Flagged as an intentional, trivial deviation.

### R8 — Intl has no P6, so domestic's "deliberate Standard" mechanism has no analogue (LOW)

Domestic's engine has `_P6_TIER = 6` to represent an explicit "this maps to Standard on
purpose" tier. The intl spreadsheet's tiers are P1–P5 only, and its
deliberate-`Standard` rows (`70MM`, `Digital`, `Digital 3D`) live in **P5**.
Consequences: (a) do not port `_P6_TIER`; (b) the drawer's zod enum and the page's tier
filter are P1–P5; (c) the *only* way to distinguish a P5 `Standard` from a no-match
`Standard` is `match_source`, so that field must be populated by the engine and
surfaced by `IntlResultCard`. Baked into Phase 2 and Phase 4.

### R9 — No frontend test tooling exists (LOW, accepted)

`frontend/package.json` has no vitest, jest, or playwright; scripts are only `dev`,
`build`, `lint`, `preview`. Phase 4 therefore cannot meet the global 80%-coverage rule
and is gated on `tsc -b && vite build` + `oxlint` + the manual checklist instead.
Standing up a frontend test harness is a much larger piece of work than this feature
and is out of scope. Recorded as a knowing, documented deviation rather than an
oversight.

### R10 — Test-suite `dependency_overrides` leakage (LOW but easy to trip)

`test_api_integration.py` assigns `app.dependency_overrides[get_session]` at **module
level**. As `test_deleted_showtimes_api.py`'s header docstring explains, pytest imports
every test module before running any test, so a second module-level assignment would
silently clobber the first for every already-collected file. `test_intl_api_integration.py`
must use the **autouse-fixture** pattern with teardown. If `test_api_integration.py`
starts failing after Phase 3, this is the cause.

### R11 — `create_db_and_tables()` at startup races the migration (LOW, mitigated)

`main.py:232` calls `create_db_and_tables()` (SQLModel `create_all`) on every startup,
so in a dev container the intl tables may exist before `alembic upgrade head` runs.
Mitigated by the `inspector.has_table()` guard in Phase 1, copied from
`326d2ebe211d`. Do not omit it.

### R12 — Approve path is required for the master list to be useful (LOW)

`build_intl_engine_from_db` filters `status == "approved"`, and `POST
/api/v1/intl-amenities` inserts `status="pending"` (mirroring domestic). Without
`POST /{id}/approve`, a user-added mapping can never become detectable and the "Add
Mapping" button is a dead end — the design doc's API surface lists only
`GET/POST/PATCH/DELETE`. Phase 3 therefore also ports `approve` / `reject`. This is an
additive clarification of the design doc's intent, not a scope change.
