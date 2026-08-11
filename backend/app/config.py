from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://amenity:amenity@localhost:5432/amenitydb"
    SECRET_KEY: str = "change-me"
    BEDROCK_REGION: str = "us-east-1"
    BEDROCK_MODEL_ID: str = "mistral.mistral-large-3-675b-instruct"
    BEDROCK_API_KEY: str = ""
    AI_TRIGGER_MODE: str = "on"
    AI_AUTOAPPLY_CONFIDENCE: Optional[float] = None
    MAX_BATCH_ROWS: int = 10000
    JOB_TTL_HOURS: int = 24
    TRACK_C_MIN_LEN: int = 4
    CIRCUIT_MATCH_MIN_JACCARD: float = 0.5
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    REDIS_URL: str = "redis://redis:6379/0"
    BEDROCK_CACHE_TTL_DAYS: int = 30
    BEDROCK_MAX_CONCURRENCY: int = 20
    BATCH_AI_SAMPLE_LIMIT: int = 50
    VESPA_URL: str = "http://localhost:8080"
    EMBEDDING_MODEL_ID: str = "cohere.embed-multilingual-v3"
    EMBEDDING_DIMENSION: int = 1024
    COHERE_EMBED_BATCH_SIZE: int = 96
    SEMANTIC_SEARCH_ENABLED: bool = True

    # Mode B — Agentic title matching
    AGENTIC_TITLE_MATCH_ENABLED: bool = False
    AGENTIC_CLAUDE_MODEL: str = "us.anthropic.claude-sonnet-5"
    AGENTIC_TIMEOUT_SECONDS: int = 90
    AGENTIC_MAX_CANDIDATES: int = 5
    AGENTIC_TMDB_READ_TOKEN: str = ""
    AGENTIC_USE_BEDROCK: bool = True
    # Claude sandbox sidecar URL (set via CLAUDE_SANDBOX_URL env var)
    CLAUDE_SANDBOX_URL: str = "http://claude-sandbox:3100"
    AGENTIC_BATCH_MAX_CONCURRENCY: int = 2
    # S3 bucket backing batch upload/output storage — required because backend,
    # celery-worker, and celery-agentic-worker are separate containers with no
    # shared filesystem; a local /tmp path written by one is invisible to another.
    AGENTIC_BATCH_S3_BUCKET: str = ""
    AGENTIC_BATCH_S3_REGION: str = "us-east-1"

    # Production MySQL DB — source of truth for Movie Master sync (fq_movie_master /
    # fq_movie_master_intl). Empty defaults let the app boot cleanly where prod DB
    # access isn't configured (e.g. CI, local dev without the sync feature).
    PROD_DB_HOST: str = ""
    PROD_DB_PORT: int = 3306
    PROD_DB_DATABASE: str = ""
    PROD_DB_USERNAME: str = ""
    PROD_DB_PASSWORD: str = ""

    # External API (singletitle/batchtitle) — feature-flagged like Mode B above.
    EXTERNAL_API_ENABLED: bool = False
    # Plaintext API key read from env/secrets manager and hashed into an
    # ApiKey row at startup (see main.py's _seed_env_api_key) — lets a client
    # key live in .env locally and in Secrets Manager in production without
    # ever touching a raw INSERT. Empty default: no key is seeded if unset.
    X_API_KEY: str = ""
    EXTERNAL_API_ROW_MAX_ATTEMPTS: int = 3
    # Safety-net ceiling while polling the Vespa reindex ready-key during a
    # db_update=true job; matching proceeds anyway past this point rather
    # than failing the job outright over indexing lag.
    EXTERNAL_API_SYNC_WAIT_CEILING_SECONDS: int = 1800
    # Longer than JOB_TTL_HOURS (24h, used for xlsx output) — external API
    # rows must survive long enough for a client to retry across a business day.
    EXTERNAL_API_JOB_TTL_HOURS: int = 72

    # Deleted Showtimes Check — SerpApi-backed detector (ported from the
    # standalone showtime_serp_check.py script). Key lives in env/Secrets
    # Manager, never in source.
    SERPAPI_API_KEY: str = ""
    # Own bucket/region setting rather than reusing AGENTIC_BATCH_S3_BUCKET —
    # this feature has its own dedicated input/output folders provisioned in
    # S3, independent of the Mode B agentic batch pipeline's bucket.
    DELETED_SHOWTIME_S3_BUCKET: str = ""
    DELETED_SHOWTIME_S3_REGION: str = "us-east-1"
    DELETED_SHOWTIME_MAX_ROWS: int = 1000
    # Result workbook + audit.json stay downloadable for 30 days — long
    # enough for the job-history list to remain useful across a month.
    DELETED_SHOWTIME_JOB_TTL_HOURS: int = 24 * 30
    # Kill the run after N consecutive failed theater batches (mirrors the
    # script's --abort-after guardrail) rather than burning SerpApi credits
    # on a run that's clearly not getting usable listings back.
    DELETED_SHOWTIME_ABORT_AFTER: int = 5

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
