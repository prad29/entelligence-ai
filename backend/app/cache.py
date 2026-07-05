import logging

import redis as _redis

from app.config import settings

logger = logging.getLogger(__name__)

_client: _redis.Redis | None = None


def get_redis() -> _redis.Redis:
    global _client
    if _client is None:
        _client = _redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def bedrock_cache_key(amenity: str, circuit: str) -> str:
    return f"bedrock:v1:{amenity.strip().lower()}:{circuit.strip().lower()}"


def movie_format_cache_key(amenity: str) -> str:
    return f"movie_format:v1:{amenity.strip().lower()}"
