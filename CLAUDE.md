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

## Environment Variables

See .env.example for all variables.
