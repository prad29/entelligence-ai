from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://amenity:amenity@localhost:5432/amenitydb"
    SECRET_KEY: str = "change-me"
    BEDROCK_REGION: str = "us-east-1"
    BEDROCK_MODEL_ID: str = "mistral.mistral-large-2407-v1:0"
    BATCH_MODEL_ID: str = "mistral.mistral-7b-instruct-v0:2"
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
    S3_BATCH_BUCKET: str = ""
    ASYNC_BATCH_MODEL_ID: str = "anthropic.claude-3-5-haiku-20241022-v1:0"
    BATCH_JOB_POLL_INTERVAL: int = 10
    BATCH_JOB_MAX_WAIT: int = 600
    BEDROCK_BATCH_ROLE_ARN: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
