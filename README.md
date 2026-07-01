# MovieQ — Amenity Screen Format Detector

Internal tool that maps theater showtime amenity strings to canonical screen formats
(e.g. `IMAX`, `4DX`, `Cnmk XD`, `Standard`). See [CLAUDE.md](CLAUDE.md) for the
full project guide and [CLAUDE_CODE_BUILD_BRIEF.md](CLAUDE_CODE_BUILD_BRIEF.md) for
the authoritative build spec.

## Quick Start

```bash
cp .env.example .env          # fill in DATABASE_URL, SECRET_KEY, Bedrock vars
docker-compose up -d          # starts postgres, backend, frontend
# Seed the master list (optional — skip if no xlsx available):
cd backend && python app/cli.py seed-from-xlsx /path/to/Amenities\ Priority.xlsx
```

Then open `http://localhost:5173` (frontend) and `http://localhost:8000/docs`
(interactive API docs).

## Architecture

```
frontend/          React + TypeScript + Vite + Tailwind + shadcn/ui
backend/
  app/
    detection/     Pure detection engine (no I/O, unit-testable)
      engine.py    ScreenFormatEngine + MappingIndex
      normalizer.py  Pre-processing, Track A/B/C helpers
      types.py     Frozen dataclasses: ApprovedMapping, DetectionResult
      loader.py    Builds engine from DB (approved rows only)
    routers/       FastAPI route handlers
    workers/       Background batch processing
    models.py      SQLModel table definitions
    main.py        App factory + startup hook
  tests/           pytest suite (no DB required for unit tests)
```

## Detection pipeline (summary)

| Layer | What fires | When it stops |
|-------|-----------|--------------|
| 0 | VIP override (Cineplex / Caribbean) | Always beats everything |
| 1 | Priority bucket P1→P6 (exact → Track A → Track B → Track C) | First confident match |
| 2 | AWS Bedrock Mistral Large (AI) | True no-match only; P6 Standard does NOT call AI |

## Running tests

```bash
cd backend
python -m pytest tests/ -v
```

## Folder map

| Path | What it is |
|---|---|
| `CLAUDE.md` | Project guide — quick start, structure, how-to |
| `CLAUDE_CODE_BUILD_BRIEF.md` | Authoritative, self-contained build spec |
| `data/` | Seed spreadsheet `Amenities Priority.xlsx`, compiled `screen_format_mapping.json` |
| `docs/` | `BUSINESS_LOGIC.md`, `BusinessLogic_Comparison.md` |
| `reference-engine/` | Validation-only Python reference implementation |
| `analysis/` | Ambiguity mining workbook |
| `ui-preview/` | shadcn-style UI mockup |
