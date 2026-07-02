from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://amenity:amenity@localhost:5432/amenitydb"
    SECRET_KEY: str = "change-me"
    BEDROCK_REGION: str = "us-east-1"
    BEDROCK_MODEL_ID: str = "mistral.mistral-large-2407-v1:0"
    BEDROCK_API_KEY: str = ""
    AI_TRIGGER_MODE: str = "on"
    AI_AUTOAPPLY_CONFIDENCE: Optional[float] = None
    MAX_BATCH_ROWS: int = 10000
    JOB_TTL_HOURS: int = 24
    TRACK_C_MIN_LEN: int = 4
    CIRCUIT_MATCH_MIN_JACCARD: float = 0.5
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
