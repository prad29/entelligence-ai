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

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
