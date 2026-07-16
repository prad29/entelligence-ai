from __future__ import annotations

import dataclasses
import json
import logging
from hashlib import sha256
from typing import Optional

import redis as _redis

from app.config import settings
from app.title_matching.evidence_types import EvidenceResult

logger = logging.getLogger(__name__)

_client: Optional[_redis.Redis] = None
_memory_cache: dict[str, str] = {}


def _get_redis() -> Optional[_redis.Redis]:
    global _client
    if _client is not None:
        return _client
    try:
        client = _redis.from_url(settings.REDIS_URL, decode_responses=True)
        client.ping()
        _client = client
        return _client
    except Exception:
        logger.warning("Redis unavailable; falling back to in-memory cache")
        return None


def _cache_key(url: str) -> str:
    digest = sha256(url.encode()).hexdigest()[:16]
    return f"evidence:v1:{digest}"


def get(url: str) -> Optional[EvidenceResult]:
    key = _cache_key(url)
    try:
        client = _get_redis()
    except Exception:
        logger.warning("Redis unavailable; falling back to in-memory cache")
        client = None
    if client is not None:
        try:
            raw = client.get(key)
            if raw is not None:
                logger.debug("Redis cache hit for key %s", key)
                return EvidenceResult(**json.loads(raw))
            return None
        except Exception:
            logger.warning("Redis get failed for key %s; trying memory cache", key)
    raw = _memory_cache.get(key)
    if raw is not None:
        logger.debug("Memory cache hit for key %s", key)
        return EvidenceResult(**json.loads(raw))
    return None


def set(url: str, result: EvidenceResult) -> None:
    key = _cache_key(url)
    serialized = json.dumps(dataclasses.asdict(result))
    ttl = settings.BEDROCK_CACHE_TTL_DAYS * 86400
    try:
        client = _get_redis()
    except Exception:
        logger.warning("Redis unavailable; falling back to in-memory cache")
        client = None
    if client is not None:
        try:
            client.set(key, serialized, ex=ttl)
            return
        except Exception:
            logger.warning("Redis set failed for key %s; falling back to memory cache", key)
    _memory_cache[key] = serialized
