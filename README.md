<div align="center">

# E.R.I.C.A
### Enttelligence Research & Insights Cinematic Assistant

**Internal cinema intelligence platform that maps theater amenity strings to canonical screen formats and movie projection formats — with AI fallback, batch processing, and a full review workflow.**

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react)](https://react.dev)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=flat-square&logo=postgresql)](https://www.postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=flat-square&logo=redis)](https://redis.io)
[![AWS Bedrock](https://img.shields.io/badge/AWS_Bedrock-Mistral_Large-FF9900?style=flat-square&logo=amazonaws)](https://aws.amazon.com/bedrock)

</div>

---

## What it does

Theater chains represent the same screen format in hundreds of different ways:

```
"IMAX with Laser | Reserved Seating | CC"
"Cinemark XD | Dolby Atmos | D-BOX"
"70mm Film | No Passes | Stadium Seating"
"3 D | Reserved"                           ← malformed but still valid
```

E.R.I.C.A normalizes all of these to a canonical format (`IMAX`, `Standard`, `70MM`, `3D`, etc.) using a three-track cascade engine — with AWS Bedrock AI as the last resort for anything the rule engine can't confidently resolve.

---

## Features

### Amenity Screen Format Detection
- Single-string detection with confidence score and match diagnostics
- Bulk CSV/XLSX batch upload → output XLSX with format column
- VIP circuit override (Cineplex, Caribbean) always wins
- Priority bucket cascade: P1 (premium large format) → P6 (Standard)

### Movie Format Detection *(new)*
- Detects projection format: **70MM · 35MM · 3D · 2D** from the same amenity strings
- Handles malformed inputs: `"3 D"`, `"3-D"`, `"70 mm"`, `"REALD 3D"` all resolve correctly
- 94.91% accuracy against 7,344-row ground-truth dataset (keyword-only, no AI)

### AI Review Queue
- Bedrock fires only on true no-matches — not on every row
- All AI suggestions land in a Review Queue for human approval or rejection
- Approving a suggestion creates a new mapping and reloads the engine instantly

### Dedup Cache
- Redis-backed, 30-day TTL, per-amenity-string key
- MGET bulk prefetch in batch jobs — one Redis round-trip per batch, not per row
- Same string never hits Bedrock twice within the TTL window

### Batch AI Modes
| Mode | Behavior | Use case |
|------|----------|----------|
| Skip AI | No AI calls, instant | First pass / full dataset |
| Sample | AI on first 50 unique unmatched strings | Spot-check |
| Full AI | AI on all unmatched strings | Complete coverage |
| Async Batch | AWS `CreateModelInvocationJob` via S3 | Large jobs (requires config) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend                              │
│   React 18 · TypeScript · Vite · Tailwind · shadcn/ui        │
│                                                              │
│  /detection   /amenities    /review                          │
│  /movie-detection  /movie-formats  /movie-review             │
└──────────────────────┬──────────────────────────────────────┘
                       │ REST + polling
┌──────────────────────▼──────────────────────────────────────┐
│                       Backend (FastAPI)                       │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Detection Engine                        │    │
│  │                                                      │    │
│  │  Input string                                        │    │
│  │      │                                               │    │
│  │      ▼                                               │    │
│  │  Layer 0 ── VIP circuit override                     │    │
│  │      │                                               │    │
│  │      ▼                                               │    │
│  │  Layer 1 ── Priority bucket cascade                  │    │
│  │             Track A: light normalization dict        │    │
│  │             Track B: stopword removal dict           │    │
│  │             Track C: concat-exact + token-set match  │    │
│  │      │                                               │    │
│  │      ▼  (no match)                                   │    │
│  │  Layer 2 ── AWS Bedrock AI ──► Review Queue          │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  PostgreSQL (mappings, jobs, review items)                   │
│  Redis      (Bedrock dedup cache, 30-day TTL)                │
└──────────────────────────────────────────────────────────────┘
```

---

## Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/prad29/entelligence-ai.git
cd entelligence-ai
cp .env.example .env        # fill in BEDROCK_API_KEY, BEDROCK_REGION

# 2. Start the stack
docker-compose up -d        # postgres + redis + backend + frontend

# 3. (Optional) Seed from your amenity spreadsheet
docker-compose exec backend python app/cli.py seed-from-xlsx /path/to/Amenities.xlsx

# 4. (Optional) Seed international amenities (separate table/command)
docker-compose exec backend python app/cli.py seed-intl-from-xlsx /path/to/International-Amenities-Priorities.xlsx
```

| Service | URL |
|---------|-----|
| Frontend | http://localhost:5173 |
| API docs (Swagger) | http://localhost:8000/docs |
| API docs (ReDoc) | http://localhost:8000/redoc |

---

## Project Structure

```
entelligence-ai/
├── backend/
│   ├── app/
│   │   ├── detection/              # Amenity screen format engine
│   │   │   ├── engine.py           # ScreenFormatEngine + MappingIndex
│   │   │   ├── normalizer.py       # Track A / B / C pre-processing
│   │   │   ├── bedrock_client.py   # AWS Bedrock Mistral Large client
│   │   │   ├── types.py            # Frozen dataclasses
│   │   │   └── loader.py           # DB → engine builder
│   │   ├── movie_detection/        # Movie format engine (70MM/35MM/3D/2D)
│   │   │   ├── engine.py           # MovieFormatEngine
│   │   │   ├── types.py
│   │   │   └── loader.py
│   │   ├── routers/                # FastAPI route handlers
│   │   │   ├── detect.py           # Single + batch amenity detection
│   │   │   ├── movie_detect.py     # Single + batch movie format detection
│   │   │   ├── amenities.py        # Master amenity list CRUD
│   │   │   ├── movie_formats.py    # Master movie format list CRUD
│   │   │   ├── review.py           # Amenity review queue
│   │   │   ├── movie_review.py     # Movie format review queue
│   │   │   ├── jobs.py             # Batch job polling + download
│   │   │   └── movie_jobs.py
│   │   ├── workers/
│   │   │   ├── batch_worker.py     # Amenity batch job runner
│   │   │   └── movie_batch_worker.py
│   │   ├── models.py               # SQLModel table definitions
│   │   ├── schemas.py              # Pydantic request/response schemas
│   │   ├── cache.py                # Redis helpers + cache key functions
│   │   ├── config.py               # Settings (pydantic-settings)
│   │   └── main.py                 # App factory + startup
│   ├── alembic/                    # DB migrations
│   └── tests/                      # pytest suite
├── frontend/
│   └── src/
│       ├── components/layout/      # AppSidebar, TopBar
│       ├── hooks/                  # useDetect, useMovieDetect, useBatchJob, ...
│       └── pages/
│           ├── detection/          # AI Amenity Detection
│           ├── amenities/          # Master Amenity List
│           ├── review/             # Amenity Review Queue
│           ├── movie-detection/    # AI Movie Format Detection
│           ├── movie-formats/      # Master Movie Format List
│           └── movie-review/       # Movie Format Review Queue
└── docker-compose.yml
```

---

## API Reference

### Amenity Detection

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/detect/single` | Detect screen format for one amenity string |
| `POST` | `/api/v1/detect/batch` | Upload CSV/XLSX → batch job |
| `GET` | `/api/v1/jobs/{job_id}` | Poll job status |
| `GET` | `/api/v1/jobs/{job_id}/download` | Download result XLSX |

### Movie Format Detection

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/movie-detect/single` | Detect 70MM/35MM/3D/2D for one amenity string |
| `POST` | `/api/v1/movie-detect/batch` | Upload CSV/XLSX → batch job |
| `GET` | `/api/v1/movie-jobs/{job_id}` | Poll job status |
| `GET` | `/api/v1/movie-jobs/{job_id}/download` | Download result XLSX |

### Master Lists & Review Queues

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/amenities` | List all amenity mappings (paginated) |
| `POST` | `/api/v1/amenities` | Add new mapping |
| `GET` | `/api/v1/movie-formats` | List all movie format mappings |
| `POST` | `/api/v1/movie-formats` | Add new mapping |
| `POST` | `/api/v1/review/{id}/approve` | Approve AI suggestion |
| `POST` | `/api/v1/movie-review/{id}/approve` | Approve movie format AI suggestion |

### International Amenity Detection

Sibling module for international amenity strings (`4DX`, `ScreenX`, `Onyx`, `Xplus`,
`KinoEvolution`, etc.) with its own master list and priority tiers (P1–P5). No
Bedrock/AI fallback and no review queue — an unmatched string returns `Standard` /
`No Match`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/intl-detect/single` | Detect screen format for one international amenity string |
| `POST` | `/api/v1/intl-detect/batch` | Upload CSV/XLSX → batch job (requires `amenities` or `amenities_string` column) |
| `GET` | `/api/v1/intl-jobs/{job_id}` | Poll job status |
| `GET` | `/api/v1/intl-jobs/{job_id}/download` | Download result XLSX |
| `GET` | `/api/v1/intl-amenities` | List all international amenity mappings (paginated) |
| `POST` | `/api/v1/intl-amenities` | Add new mapping |
| `POST` | `/api/v1/intl-amenities/{id}/approve` | Approve a pending mapping |
| `POST` | `/api/v1/intl-amenities/import` | Bulk import mappings |
| `GET` | `/api/v1/intl-amenities/export` | Export approved mappings |

**Single detect request:**
```json
POST /api/v1/movie-detect/single
{ "amenity": "70mm Film | No Passes | Stadium Seating" }
```

**Response:**
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

---

## Environment Variables

```env
# Database
DATABASE_URL=postgresql://amenity:amenity@localhost:5432/amenitydb

# Security
SECRET_KEY=change-me-to-a-long-random-string

# AWS Bedrock
BEDROCK_REGION=us-east-1
BEDROCK_MODEL_ID=mistral.mistral-large-2407-v1:0
BEDROCK_API_KEY=

# AI behaviour
AI_TRIGGER_MODE=on          # on | off | residual
BEDROCK_CACHE_TTL_DAYS=30

# Redis
REDIS_URL=redis://redis:6379/0

# Batch processing
MAX_BATCH_ROWS=10000
JOB_TTL_HOURS=24
BEDROCK_MAX_CONCURRENCY=20
BATCH_AI_SAMPLE_LIMIT=50

# Engine tuning
TRACK_C_MIN_LEN=4
```

---

## Running Tests

```bash
cd backend
python -m pytest tests/ -v

# With coverage
python -m pytest tests/ --cov=app --cov-report=term-missing
```

---

## How to Add a New Screen Format

1. Navigate to **Master Amenity List** → **+ Add Mapping**
2. Fill in: keyword, screen format, circuit (or leave blank for global), priority tier
3. Submit → lands in **Review Queue**
4. Approve → engine reloads instantly, keyword is detectable within seconds

Same flow applies to **Master Movie Format List** for 70MM/35MM/3D/2D mappings.

---

## Detection Accuracy

Cross-verified against **7,344 real theater showtimes**:

| Mode | Accuracy |
|------|----------|
| Keyword-only (no AI) | **94.91%** |
| With AI fallback | ~99%+ (AI handles remaining edge cases) |

The ~5% gap is almost entirely source-data labeling inconsistencies (e.g. strings containing `"3D"` explicitly but labeled `2D` in the original dataset) — not engine errors.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui, react-hook-form, Zod |
| Backend | FastAPI, SQLModel, Alembic, Pydantic v2 |
| Database | PostgreSQL 16 |
| Cache | Redis 7 |
| AI | AWS Bedrock — Mistral Large |
| Container | Docker + docker-compose |
| Testing | pytest |
