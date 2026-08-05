"""
Semantic retrieval via Vespa hybrid search (BM25 + ANN vector).

Embedding model: AWS Bedrock Cohere Embed Multilingual v3
  - 100+ languages in a single embedding space (handles French/German/etc.
    scraped titles against English Movie Master rows)
  - Asymmetric: search_document for index-time, search_query for query-time
  - 96 texts per batch call (vs 1 for Titan), so 45k rows ≈ 469 batch calls

Index store: Vespa container (persistent, HNSW, angular distance, 1024 dims)

On startup, build_semantic_index() deploys the Vespa app package (if not
already deployed), checks which IDs are indexed, feeds any missing rows, and
returns a VespaSemanticIndex ready for query-time use.

If Vespa or Bedrock is unavailable the function returns None and the
CandidateGenerator continues with fuzzy/alias matching only.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_VESPA_SCHEMA = "movie_master"
_VESPA_SCHEMA_INTL = "movie_master_intl"
_VESPA_APP_NAME = "movie-title-matching"
_BEDROCK_MAX_RETRIES = 3
_BEDROCK_BACKOFF_BASE = 0.5
_EMBED_CONCURRENCY = 10   # concurrent batch calls; each batch = 96 texts
_LOG_PROGRESS_EVERY = 500


# ---------------------------------------------------------------------------
# Text composition
# ---------------------------------------------------------------------------

def _compose_embed_text(row: dict) -> str:
    """Build the text to embed for a master row: title + year + director + country.

    country is only ever present on international rows (build_semantic_index_intl_task
    is the only caller that populates it) — appending it here improves country-aware
    ANN recall for the intl schema without affecting the domestic embed text at all.
    """
    title = row.get("movie_title") or ""
    release_date = row.get("release_date") or ""
    year = release_date[:4] if len(release_date) >= 4 and release_date[:4].isdigit() else ""
    director = row.get("director") or ""
    country = row.get("country") or ""
    parts = [title]
    if year and year != "0000":
        parts.append(year)
    if director:
        parts.append(director)
    if country:
        parts.append(country)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Bedrock embedding
# ---------------------------------------------------------------------------

def _get_bedrock_client(settings):
    """Create a boto3 bedrock-runtime client using the ambient IAM role."""
    try:
        import boto3
        return boto3.client("bedrock-runtime", region_name=settings.BEDROCK_REGION)
    except Exception as exc:
        logger.warning("semantic_index: boto3 client creation failed: %s", exc)
        return None


def _embed_texts_cohere(
    texts: list[str],
    input_type: str,
    settings,
    client,
) -> list[Optional[list[float]]]:
    """
    Call Cohere Embed Multilingual v3 for a batch of up to 96 texts.

    input_type must be "search_document" (index time) or "search_query" (query time).
    Returns a list of embeddings in the same order; None for any that fail.
    """
    body = json.dumps({
        "texts": texts,
        "input_type": input_type,
        "embedding_types": ["float"],
    })

    for attempt in range(_BEDROCK_MAX_RETRIES):
        try:
            response = client.invoke_model(
                modelId=settings.EMBEDDING_MODEL_ID,
                contentType="application/json",
                accept="application/json",
                body=body,
            )
            result = json.loads(response["body"].read())
            # Cohere response: {"embeddings": {"float": [[...], ...]}}
            return result["embeddings"]["float"]
        except Exception as exc:
            exc_name = type(exc).__name__
            if "Throttling" in exc_name and attempt < _BEDROCK_MAX_RETRIES - 1:
                time.sleep(_BEDROCK_BACKOFF_BASE * (2 ** attempt))
            else:
                logger.warning("semantic_index: Cohere batch embed failed: %s", exc)
                return [None] * len(texts)

    return [None] * len(texts)


def get_embedding(
    text: str,
    settings,
    client=None,
) -> Optional[list[float]]:
    """
    Embed a single query string via Cohere Embed Multilingual v3.
    Uses input_type="search_query" for asymmetric retrieval.
    Returns None on any failure so the caller can degrade gracefully.
    """
    if client is None:
        client = _get_bedrock_client(settings)
    if client is None:
        return None

    results = _embed_texts_cohere([text], "search_query", settings, client)
    return results[0] if results else None


def _embed_batch(
    rows: list[dict],
    settings,
    client,
) -> list[Optional[list[float]]]:
    """
    Embed all rows for index-time using Cohere Embed Multilingual v3.

    Splits rows into batches of COHERE_EMBED_BATCH_SIZE (96) and runs
    _EMBED_CONCURRENCY batches concurrently. Each concurrent batch gets its
    own boto3 client (clients are not thread-safe).

    With 45k rows: 469 batch calls × ~0.3s each / 10 concurrent = ~14 seconds.
    """
    import concurrent.futures
    import threading

    batch_size = getattr(settings, "COHERE_EMBED_BATCH_SIZE", 96)
    batches = [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)]

    results: list[Optional[list[float]]] = [None] * len(rows)
    completed = [0]
    lock = threading.Lock()

    def _embed_batch_chunk(batch_idx: int, batch: list[dict]) -> None:
        thread_client = _get_bedrock_client(settings)
        texts = [_compose_embed_text(r) for r in batch]
        embeddings = _embed_texts_cohere(texts, "search_document", settings, thread_client)
        start = batch_idx * batch_size
        for j, emb in enumerate(embeddings):
            results[start + j] = emb
        with lock:
            completed[0] += len(batch)
            if completed[0] // _LOG_PROGRESS_EVERY > (completed[0] - len(batch)) // _LOG_PROGRESS_EVERY:
                logger.info(
                    "semantic_index: embedded %d/%d rows", completed[0], len(rows)
                )

    with concurrent.futures.ThreadPoolExecutor(max_workers=_EMBED_CONCURRENCY) as pool:
        futures = [pool.submit(_embed_batch_chunk, i, b) for i, b in enumerate(batches)]
        concurrent.futures.wait(futures)

    return results


# ---------------------------------------------------------------------------
# Vespa application deployment
# ---------------------------------------------------------------------------

def _deploy_vespa_app(vespa_url: str, force: bool = False) -> bool:
    """
    Deploy the Vespa application package from backend/vespa/.
    Returns True if deployment succeeded (or was already deployed).

    force=True skips the "already deployed" short-circuit below and always
    redeploys — required after editing a .sd schema file (e.g. adding the
    intl `country` field), since ApplicationStatus returning 200 only proves
    *some* version of the app is running, not that it matches the schema
    currently on disk.
    """
    if not force:
        try:
            import requests

            # Check if already deployed by probing the schema endpoint
            probe = requests.get(
                f"{vespa_url}/ApplicationStatus",
                timeout=5,
            )
            if probe.status_code == 200:
                logger.info("semantic_index: Vespa app already deployed")
                return True
        except Exception:
            pass

    try:
        # Deploy via config server
        vespa_config_url = vespa_url.replace(":8080", ":19071")
        app_dir = Path(__file__).parent.parent.parent / "vespa"

        import requests
        import zipfile
        import io

        # Build a zip of the app package
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for p in app_dir.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(app_dir))
        buf.seek(0)

        resp = requests.post(
            f"{vespa_config_url}/application/v2/tenant/default/prepareandactivate",
            data=buf.read(),
            headers={"Content-Type": "application/zip"},
            timeout=60,
        )
        if resp.status_code in (200, 201):
            logger.info("semantic_index: Vespa app deployed successfully")
            time.sleep(5)  # Allow activation to complete
            return True
        else:
            logger.warning("semantic_index: Vespa deploy returned %d: %s", resp.status_code, resp.text[:200])
            return False
    except Exception as exc:
        logger.warning("semantic_index: Vespa deploy failed: %s", exc)
        return False


def _get_indexed_ids(vespa_url: str, schema: str = _VESPA_SCHEMA) -> set[int]:
    """
    Return the set of doc ids already in Vespa for the given schema.
    Pages through the document/v1 listing API until exhausted.
    """
    try:
        import requests
        indexed: set[int] = set()
        continuation = None
        while True:
            params = {"wantedDocumentCount": 500}
            if continuation:
                params["continuation"] = continuation
            resp = requests.get(
                f"{vespa_url}/document/v1/{schema}/{schema}/docid",
                params=params,
                timeout=30,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            for doc in data.get("documents", []):
                # doc id is "id:<schema>:<schema>::N"
                raw = doc.get("id", "")
                tail = raw.rsplit("::", 1)[-1]
                if tail.isdigit():
                    indexed.add(int(tail))
            continuation = data.get("continuation")
            if not continuation:
                break
        return indexed
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# VespaSemanticIndex
# ---------------------------------------------------------------------------

class VespaSemanticIndex:
    """Wraps a pyvespa Vespa client for hybrid BM25+ANN search."""

    def __init__(self, vespa_url: str, settings, schema: str = _VESPA_SCHEMA) -> None:
        from vespa.application import Vespa
        self._app = Vespa(url=vespa_url)
        self._settings = settings
        self._schema = schema
        self._id_field = "movie_master_id" if schema == _VESPA_SCHEMA else "movie_master_intl_id"

    def search(
        self,
        query_embedding: list[float],
        query_text: str,
        k: int = 10,
        exclude_ids: Optional[set[int]] = None,
        country: Optional[str] = None,
    ) -> list[tuple[int, float]]:
        """
        Hybrid BM25 + ANN search.
        Returns list of (doc_id, score) sorted by score descending.
        Scores are scaled to [0, 0.6] for pipeline weight compatibility.

        country, when given AND schema is the intl schema, adds an exact-match
        filter clause scoping results to that country — never applied to the
        domestic schema, which has no such field. The recall clause (ANN or
        BM25) is parenthesized as a group BEFORE the filter is appended,
        since YQL's `and` binds tighter than `or`: an unparenthesized
        `nn or userQuery() and country contains "X"` would parse as
        `nn or (userQuery() and country contains "X")`, leaving the ANN branch
        country-blind.
        """
        exclude_ids = exclude_ids or set()

        try:
            import numpy as np
            vec = list(map(float, query_embedding))

            fetch_k = k + len(exclude_ids) + 10
            recall_clause = (
                f"(({{targetHits:{fetch_k}}}nearestNeighbor(embedding,q_embedding)) "
                f"or userQuery())"
            )
            yql = f"select {self._id_field} from {self._schema} where {recall_clause}"
            if country and self._schema == _VESPA_SCHEMA_INTL:
                escaped_country = country.replace('"', '\\"')
                yql += f' and country contains "{escaped_country}"'

            body = {
                "yql": yql,
                "query": query_text,
                "ranking": "hybrid",
                "input.query(q_embedding)": vec,
                "hits": fetch_k,
            }

            result = self._app.query(body=body)
            hits = result.hits if result.hits else []

            output: list[tuple[int, float]] = []
            for hit in hits:
                mid = hit["fields"].get(self._id_field)
                if mid is None or mid in exclude_ids:
                    continue
                raw_score = hit.get("relevance", 0.0)
                # RRF scores are typically in [0, 1]; scale to [0, 0.6]
                scaled = min(float(raw_score), 1.0) * 0.6
                output.append((int(mid), scaled))
                if len(output) >= k:
                    break

            return output

        except Exception as exc:
            logger.debug("semantic_index: search failed: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Index build / feed
# ---------------------------------------------------------------------------

def _feed_rows(
    vespa_url: str,
    rows: list[dict],
    embeddings: list[Optional[list[float]]],
    schema: str = _VESPA_SCHEMA,
) -> int:
    """Feed rows with embeddings into Vespa. Returns count of successful feeds."""
    try:
        from vespa.application import Vespa
        app = Vespa(url=vespa_url)
    except Exception as exc:
        logger.warning("semantic_index: cannot create Vespa client for feeding: %s", exc)
        return 0

    id_field = "movie_master_id" if schema == _VESPA_SCHEMA else "movie_master_intl_id"

    fed = 0
    for row, emb in zip(rows, embeddings):
        if emb is None:
            continue
        doc_id = str(row["id"])
        fields = {
            id_field: row["id"],
            "title": row.get("movie_title") or "",
            "embed_text": _compose_embed_text(row),
            "embedding": emb,
        }
        # country only exists on the intl schema — movie_master has no such
        # field and Vespa rejects unknown fields, so never add it for domestic.
        if schema == _VESPA_SCHEMA_INTL and row.get("country"):
            fields["country"] = row["country"]
        try:
            resp = app.feed_data_point(
                schema=schema,
                data_id=doc_id,
                fields=fields,
            )
            if resp.status_code in (200, 201):
                fed += 1
        except Exception as exc:
            logger.debug("semantic_index: feed failed for id=%s: %s", doc_id, exc)

    return fed


def build_semantic_index(
    master_rows: list[dict],
    settings,
    schema: str = _VESPA_SCHEMA,
    force: bool = False,
    force_deploy: bool = False,
) -> Optional[VespaSemanticIndex]:
    """
    Build (or reuse) the Vespa semantic index for the given schema.

    Uses ID-based diffing so new rows added anywhere in the DB are picked up
    without re-embedding rows that are already indexed.

    1. Deploy Vespa app package if not already deployed.
    2. Fetch the set of already-indexed ids from Vespa for this schema.
    3. Compute the diff: rows in master_rows whose id is not yet in Vespa.
    4. Embed and feed only the missing rows.
    5. Return a VespaSemanticIndex ready for queries.

    force=True skips the ID-diff and re-embeds/re-feeds every row in
    master_rows, overwriting existing docs by id — needed once after adding a
    new field to a schema (e.g. intl `country`) so already-indexed docs pick
    it up; the normal ID-diff path would otherwise never touch them again.
    force_deploy is forwarded to _deploy_vespa_app to force a redeploy of the
    .sd package itself (see that function's docstring).

    Returns None if semantic search is disabled or infrastructure is unavailable.
    """
    if not settings.SEMANTIC_SEARCH_ENABLED:
        logger.info("semantic_index: disabled via SEMANTIC_SEARCH_ENABLED=False")
        return None

    vespa_url = settings.VESPA_URL

    if not _deploy_vespa_app(vespa_url, force=force_deploy):
        logger.warning("semantic_index: Vespa deploy failed, skipping semantic index")
        return None

    if force:
        indexed_ids: set = set()
        rows_to_feed = master_rows
        logger.info(
            "semantic_index[%s]: force=True, re-feeding all %d rows",
            schema, len(master_rows),
        )
    else:
        indexed_ids = _get_indexed_ids(vespa_url, schema=schema)
        rows_to_feed = [r for r in master_rows if r["id"] not in indexed_ids]
        logger.info(
            "semantic_index[%s]: %d/%d rows already indexed; %d to feed",
            schema,
            len(indexed_ids),
            len(master_rows),
            len(rows_to_feed),
        )

    if rows_to_feed:
        bedrock_client = _get_bedrock_client(settings)
        if bedrock_client is None:
            logger.warning("semantic_index: no Bedrock client, cannot feed embeddings")
            if indexed_ids:
                return VespaSemanticIndex(vespa_url, settings, schema=schema)
            return None

        logger.info("semantic_index[%s]: embedding %d rows...", schema, len(rows_to_feed))
        embeddings = _embed_batch(rows_to_feed, settings, bedrock_client)
        fed = _feed_rows(vespa_url, rows_to_feed, embeddings, schema=schema)
        logger.info("semantic_index[%s]: fed %d/%d rows to Vespa", schema, fed, len(rows_to_feed))

    return VespaSemanticIndex(vespa_url, settings, schema=schema)


def build_semantic_index_intl(
    master_rows: list[dict],
    settings,
    force: bool = False,
    force_deploy: bool = False,
) -> Optional[VespaSemanticIndex]:
    """Build (or reuse) the Vespa semantic index for the international schema."""
    return build_semantic_index(
        master_rows, settings, schema=_VESPA_SCHEMA_INTL,
        force=force, force_deploy=force_deploy,
    )
