"""
Semantic retrieval via Vespa hybrid search (BM25 + ANN vector).

Embedding model: AWS Bedrock Titan Text Embeddings v2
Index store: Vespa container (persistent, HNSW, angular distance)

On startup, build_semantic_index() deploys the Vespa app package (if not
already deployed), checks how many documents are indexed, feeds any missing
rows, and returns a VespaSemanticIndex ready for query-time use.

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
_VESPA_APP_NAME = "movie-title-matching"
_BEDROCK_MAX_RETRIES = 3
_BEDROCK_BACKOFF_BASE = 0.5
_FEED_BATCH_SIZE = 50


# ---------------------------------------------------------------------------
# Text composition
# ---------------------------------------------------------------------------

def _compose_embed_text(row: dict) -> str:
    """Build the text to embed for a master row: title + year + director."""
    title = row.get("movie_title") or ""
    release_date = row.get("release_date") or ""
    year = release_date[:4] if len(release_date) >= 4 and release_date[:4].isdigit() else ""
    director = row.get("director") or ""
    parts = [title]
    if year and year != "0000":
        parts.append(year)
    if director:
        parts.append(director)
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


def get_embedding(
    text: str,
    settings,
    client=None,
) -> Optional[list[float]]:
    """
    Embed a single text string via Bedrock Titan Text Embeddings v2.
    Returns None on any failure so the caller can degrade gracefully.
    """
    if client is None:
        client = _get_bedrock_client(settings)
    if client is None:
        return None

    body = json.dumps({
        "inputText": text[:8192],
        "dimensions": settings.EMBEDDING_DIMENSION,
        "normalize": True,
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
            return result["embedding"]
        except Exception as exc:
            exc_name = type(exc).__name__
            if "Throttling" in exc_name and attempt < _BEDROCK_MAX_RETRIES - 1:
                time.sleep(_BEDROCK_BACKOFF_BASE * (2 ** attempt))
            else:
                logger.warning("semantic_index: embedding call failed: %s", exc)
                return None

    return None


def _embed_batch(
    rows: list[dict],
    settings,
    client,
) -> list[Optional[list[float]]]:
    """Embed a list of rows, returning None for any that fail."""
    results: list[Optional[list[float]]] = []
    for i, row in enumerate(rows):
        text = _compose_embed_text(row)
        emb = get_embedding(text, settings, client=client)
        results.append(emb)
        # Pace requests to avoid Bedrock throttling
        if (i + 1) % _FEED_BATCH_SIZE == 0:
            time.sleep(0.2)
    return results


# ---------------------------------------------------------------------------
# Vespa application deployment
# ---------------------------------------------------------------------------

def _deploy_vespa_app(vespa_url: str) -> bool:
    """
    Deploy the Vespa application package from backend/vespa/.
    Returns True if deployment succeeded (or was already deployed).
    """
    try:
        from vespa.application import Vespa
        from vespa.package import ApplicationPackage, Schema, Document, Field, FieldSet, RankProfile
        from vespa.package import HNSW
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


def _count_indexed_docs(vespa_url: str) -> int:
    """Return the number of documents currently in the Vespa index."""
    try:
        import requests
        resp = requests.get(
            f"{vespa_url}/document/v1/movie_master/movie_master/docid?wantedDocumentCount=1",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("documentCount", 0)
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# VespaSemanticIndex
# ---------------------------------------------------------------------------

class VespaSemanticIndex:
    """Wraps a pyvespa Vespa client for hybrid BM25+ANN search."""

    def __init__(self, vespa_url: str, settings) -> None:
        from vespa.application import Vespa
        self._app = Vespa(url=vespa_url)
        self._settings = settings

    def search(
        self,
        query_embedding: list[float],
        query_text: str,
        k: int = 10,
        exclude_ids: Optional[set[int]] = None,
    ) -> list[tuple[int, float]]:
        """
        Hybrid BM25 + ANN search.
        Returns list of (movie_master_id, score) sorted by score descending.
        Scores are scaled to [0, 0.6] for pipeline weight compatibility.
        """
        exclude_ids = exclude_ids or set()

        try:
            import numpy as np
            vec = list(map(float, query_embedding))

            fetch_k = k + len(exclude_ids) + 10
            body = {
                "yql": (
                    f"select movie_master_id from {_VESPA_SCHEMA} "
                    f"where ({{targetHits:{fetch_k}}}nearestNeighbor(embedding,q_embedding)) "
                    f"or userQuery()"
                ),
                "query": query_text,
                "ranking": "hybrid",
                "input.query(q_embedding)": vec,
                "hits": fetch_k,
            }

            result = self._app.query(body=body)
            hits = result.hits if result.hits else []

            output: list[tuple[int, float]] = []
            for hit in hits:
                mid = hit["fields"].get("movie_master_id")
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
) -> int:
    """Feed rows with embeddings into Vespa. Returns count of successful feeds."""
    try:
        from vespa.application import Vespa
        app = Vespa(url=vespa_url)
    except Exception as exc:
        logger.warning("semantic_index: cannot create Vespa client for feeding: %s", exc)
        return 0

    fed = 0
    for row, emb in zip(rows, embeddings):
        if emb is None:
            continue
        doc_id = str(row["id"])
        fields = {
            "movie_master_id": row["id"],
            "title": row.get("movie_title") or "",
            "embed_text": _compose_embed_text(row),
            "embedding": emb,
        }
        try:
            resp = app.feed_data_point(
                schema=_VESPA_SCHEMA,
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
) -> Optional[VespaSemanticIndex]:
    """
    Build (or reuse) the Vespa semantic index.

    1. Deploy Vespa app package if not already deployed.
    2. Count current indexed documents.
    3. If count < len(master_rows), embed and feed the missing rows.
    4. Return a VespaSemanticIndex ready for queries.

    Returns None if semantic search is disabled or infrastructure is unavailable.
    """
    if not settings.SEMANTIC_SEARCH_ENABLED:
        logger.info("semantic_index: disabled via SEMANTIC_SEARCH_ENABLED=False")
        return None

    vespa_url = settings.VESPA_URL

    # Deploy schema if needed
    if not _deploy_vespa_app(vespa_url):
        logger.warning("semantic_index: Vespa deploy failed, skipping semantic index")
        return None

    current_count = _count_indexed_docs(vespa_url)
    logger.info(
        "semantic_index: Vespa has %d/%d rows indexed",
        current_count,
        len(master_rows),
    )

    if current_count < len(master_rows):
        bedrock_client = _get_bedrock_client(settings)
        if bedrock_client is None:
            logger.warning("semantic_index: no Bedrock client, cannot feed embeddings")
            # Still return an index if some docs exist
            if current_count > 0:
                return VespaSemanticIndex(vespa_url, settings)
            return None

        rows_to_feed = master_rows[current_count:]
        logger.info("semantic_index: embedding %d rows...", len(rows_to_feed))
        embeddings = _embed_batch(rows_to_feed, settings, bedrock_client)
        fed = _feed_rows(vespa_url, rows_to_feed, embeddings)
        logger.info("semantic_index: fed %d/%d rows to Vespa", fed, len(rows_to_feed))

    return VespaSemanticIndex(vespa_url, settings)
