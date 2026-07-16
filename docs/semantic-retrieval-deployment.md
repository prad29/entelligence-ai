# Semantic Retrieval — Production Deployment Guide

This document covers everything needed to deploy the Vespa-backed semantic retrieval feature to production. Semantic retrieval adds embedding-based candidate search to the title-matching pipeline, enabling titles like "Love Island Season Finale" to correctly surface `id=147057 "Peacock's Love Island USA Theatrical Event"` — a case that fuzzy matching alone can never solve.

---

## Architecture Overview

```
Title input
    │
    ├─ Alias/Franchise map (score 0.95) ──────┐
    ├─ Fuzzy / BM25 rapidfuzz (score 0–0.5) ──┼──► top-K candidates → Decision Engine
    └─ Vespa hybrid BM25+ANN  (score 0–0.6) ──┘
```

**New components:**
- `vespaengine/vespa:8` container — stores all master-row embeddings, serves hybrid search
- AWS Bedrock Titan Text Embeddings v2 (`amazon.titan-embed-text-v2:0`) — generates 1024-dim vectors at index-build time and query time
- `backend/app/title_matching/semantic_index.py` — orchestrates deploy, feed, and search
- `backend/vespa/` — Vespa application package (schema + services.xml)

**On every backend startup:**
1. Deploy Vespa app package (no-op if already deployed at current generation)
2. Count indexed documents; feed any missing rows (incremental)
3. `CandidateGenerator` holds a `VespaSemanticIndex` and queries it after fuzzy matching

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Docker / Docker Compose v2 | Vespa runs as a container |
| AWS Bedrock access | IAM role with `bedrock:InvokeModel` on `amazon.titan-embed-text-v2:0` in `us-east-1` (or your region) |
| ~2 GB RAM for Vespa | Single-node, no redundancy needed for this workload |
| ~500 MB disk for embeddings | 45k rows × 1024 dim × 4 bytes ≈ 175 MB + Vespa overhead |

---

## Step 1 — IAM Role Setup (production only, skip for local dev)

Semantic retrieval uses the **ambient IAM role** (no credentials in `.env`). On ECS/EC2:

1. Attach an IAM policy to your task/instance role:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["bedrock:InvokeModel"],
    "Resource": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
  }]
}
```

2. Verify boto3 can pick up the role:
```bash
aws bedrock invoke-model \
  --model-id amazon.titan-embed-text-v2:0 \
  --content-type application/json \
  --accept application/json \
  --body '{"inputText":"test","dimensions":1024,"normalize":true}' \
  /tmp/out.json && cat /tmp/out.json
```

For local dev, set `AWS_PROFILE` or `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` in your shell before `docker compose up`.

---

## Step 2 — Environment Variables

Add to `.env` (or your production secrets manager):

```dotenv
# Vespa URL — use the service name inside docker-compose network
VESPA_URL=http://vespa:8080

# Bedrock region (must match the IAM policy above)
BEDROCK_REGION=us-east-1

# Enable semantic retrieval (default: True)
SEMANTIC_SEARCH_ENABLED=true

# Embedding model — do not change unless you rebuild the index
EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0

# Embedding dimension — must match the Vespa schema (movie_master.sd)
EMBEDDING_DIMENSION=1024
```

**Warning:** If you change `EMBEDDING_MODEL_ID` or `EMBEDDING_DIMENSION` in production, you must wipe the Vespa index and re-feed all rows. See Step 6.

---

## Step 3 — Deploy the Stack

### First deployment

```bash
# Pull and start all services (Vespa takes ~2 min to initialize)
docker compose up -d

# Watch Vespa come up — wait for both config server and query API
docker compose logs -f vespa
# Look for: "Vespa app deployed successfully" in backend logs once it starts
```

### Health checks to verify

```bash
# Vespa config server (available ~30s after start)
curl http://localhost:19071/state/v1/health

# Vespa query API (available ~2 min after start, after app package is deployed)
curl http://localhost:8080/state/v1/health

# Backend — check semantic index was built
docker compose logs backend | grep "semantic_index:"
```

Expected backend log sequence:
```
semantic_index: Vespa app already deployed          # or "deployed successfully" on first run
semantic_index: Vespa has 0/45347 rows indexed      # on first run
semantic_index: embedding 45347 rows...             # Bedrock calls in progress
semantic_index: fed 45347/45347 rows to Vespa       # all rows fed
```

> **First-run time estimate:** ~45k rows × ~0.8s per Bedrock Titan embed = ~10 hours at the default rate limit. Bedrock's provisioned throughput can be increased — see Step 5 for how to speed this up.

---

## Step 4 — Verify Semantic Retrieval is Working

Run the e2e smoke test against the live stack:

```bash
# From the project root (requires .venv with dependencies)
DATABASE_URL=postgresql://amenity:amenity@localhost:5432/amenitydb \
VESPA_URL=http://localhost:8080 \
SEMANTIC_SEARCH_ENABLED=false \
backend/.venv/bin/pytest backend/tests/test_semantic_e2e.py -v -s
```

All 7 tests should pass. The key assertion:
```
[PASS] id=147057 found with source='semantic'  ← Love Island now correctly resolved
[PASS] Confirmed: fuzzy does NOT find id=147057 — semantic is needed  ← baseline confirmed
```

You can also test via the API endpoint directly:
```bash
curl -s http://localhost:8000/api/v1/movie-title-match/single \
  -X POST -H "Content-Type: application/json" \
  -d '{"title": "Love Island Season Finale"}' | python3 -m json.tool
```

**Before semantic retrieval:** returns `id=130064 "Love"`, confidence=0.45  
**After semantic retrieval + real Bedrock embeddings:** should return `id=147057`, confidence ≥ 0.70

---

## Step 5 — Speeding Up the Initial Index Build

The default Bedrock provisioned throughput for Titan Text Embeddings v2 is ~1 req/s. For 45k rows this takes ~12 hours. Options to speed up:

### Option A — Increase Bedrock concurrency (recommended)
Request a provisioned throughput increase in the AWS Console:
- Navigate to **Amazon Bedrock → Provisioned Throughput**
- Request increased TPS for `amazon.titan-embed-text-v2:0`
- Then increase `BEDROCK_MAX_CONCURRENCY` in config and modify `_embed_batch` to run concurrent calls

### Option B — Run the feed as a one-off CLI job before starting the backend

```bash
# From within the backend container or venv, with Vespa already running:
python3 -c "
from app.title_matching.semantic_index import build_semantic_index
from app.config import settings
from app.models import MovieMaster
from sqlmodel import Session, create_engine, select

engine = create_engine(settings.DATABASE_URL)
with Session(engine) as session:
    rows = session.exec(select(MovieMaster)).all()
    master_rows = [{'id': r.id, 'movie_title': r.movie_title,
                    'release_date': r.release_date, 'director': getattr(r, 'director', None)} for r in rows]

build_semantic_index(master_rows, settings)
print('Done')
"
```

Then set `SEMANTIC_SEARCH_ENABLED=true` and restart the backend — it will detect the existing docs and skip re-feeding.

### Option C — Pre-build on dev, copy to production
Run the feed on a dev machine with higher Bedrock quotas, then use `vespa export` to snapshot the index and load it in production.

---

## Step 6 — Rebuilding the Index

Required when:
- You change `EMBEDDING_MODEL_ID` or `EMBEDDING_DIMENSION`
- The Movie Master table has a large batch of new rows
- You want to re-embed all rows with a better model

```bash
# 1. Stop the backend
docker compose stop backend

# 2. Wipe the Vespa index
curl -X DELETE \
  "http://localhost:8080/document/v1/movie_master/movie_master/docid?selection=true&cluster=movie_content"

# 3. Restart — the backend will re-detect 0 docs and re-feed
docker compose start backend

# Watch progress
docker compose logs -f backend | grep "semantic_index:"
```

---

## Step 7 — Production Data Volume Sizing

| Component | Storage |
|---|---|
| Vespa embeddings (45k rows × 1024 dim × float32) | ~175 MB |
| Vespa HNSW graph overhead (~2x) | ~350 MB |
| Vespa base + logs | ~500 MB |
| **Total Vespa volume** | **~1 GB** |

Recommended `vespa_data` volume: **5 GB** (room for growth as Movie Master expands).

For Docker on a VM, ensure `/var/lib/docker/volumes/` has enough space.

---

## Step 8 — Monitoring

### Key log lines to watch
```bash
docker compose logs backend | grep "semantic_index:"
```

| Log message | Meaning |
|---|---|
| `Vespa app deployed successfully` | First-time or schema upgrade deploy |
| `Vespa has N/45347 rows indexed` | Current feed progress |
| `fed N/45347 rows to Vespa` | Feed completed |
| `no Bedrock client, cannot feed embeddings` | IAM role not attached / boto3 issue |
| `Vespa deploy failed, skipping semantic index` | Vespa container not healthy |
| `semantic_search failed: ...` | Query-time error (logged at DEBUG, not ERROR — pipeline continues) |

### Query-time latency
Semantic search adds one Bedrock Titan call (~100–200 ms) + one Vespa query (~5–20 ms) per title match request. Monitor the title-match API latency in CloudWatch or your APM tool.

If latency becomes a concern, cache query embeddings in Redis by `hash(normalized_title)` — each title recurs frequently in production and the embedding is deterministic.

---

## Step 9 — Rollback

If semantic retrieval causes issues in production:

**Instant disable (no restart needed):** Set `SEMANTIC_SEARCH_ENABLED=false` in your environment and restart the backend. The pipeline falls back to fuzzy-only — no Vespa or Bedrock calls are made.

**Remove Vespa entirely:** Remove the `vespa` service and `vespa_data` volume from `docker-compose.yml`, set `SEMANTIC_SEARCH_ENABLED=false`, and redeploy. The codebase is fully backward-compatible.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Backend starts but `SEMANTIC_SEARCH_ENABLED` has no effect | `VESPA_URL` in `.env` points to wrong host | Check `VESPA_URL=http://vespa:8080` (inside docker network) vs `http://localhost:8080` (from host) |
| `no Bedrock client` in logs | boto3 not installed or IAM role not attached | `pip install boto3` in the container; verify IAM role |
| `Vespa deploy returned 400` | Schema syntax error or version mismatch | Check `backend/vespa/schemas/movie_master.sd` against Vespa 8 docs |
| `fed 0/N rows` | Vespa not fully started yet | Wait for `http://localhost:8080/state/v1/health` to return 200, then restart backend |
| Semantic search returns empty results | YQL syntax issue or index empty | Run `curl http://localhost:8080/document/v1/movie_master/movie_master/docid?wantedDocumentCount=5` to check doc count |
| High latency on first request | Cold Bedrock call | Pre-warm by running a test query at startup, or cache embeddings in Redis |
