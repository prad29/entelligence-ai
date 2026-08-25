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
    AGENTIC_BATCH_MAX_CONCURRENCY: int = 4
    # Bedrock-throttle retry/backoff on the sandbox call path (runner.py's
    # _call_sandbox). AGENTIC_THROTTLE_MAX_RETRIES is IN-PROCESS fast-fail
    # retries within a single _call_sandbox() invocation (never retried if
    # the failed attempt was slow — see runner.py — because a slow failure
    # means the claude CLI already spent its own internal retry budget and
    # an immediate retry from here wouldn't help). AGENTIC_THROTTLE_BACKOFF_
    # BASE_SECONDS is the base for that in-process exponential-backoff sleep.
    # AGENTIC_THROTTLE_CELERY_BACKOFF_SECONDS is a SEPARATE, larger base for
    # the Celery-level self.retry(countdown=...) once AgenticThrottleError
    # is finally raised (limits.throttle_retry_countdown) — that backoff
    # releases the sandbox semaphore slot for the whole wait, so it can
    # afford to be much longer than the in-process one.
    AGENTIC_THROTTLE_MAX_RETRIES: int = 1
    AGENTIC_THROTTLE_BACKOFF_BASE_SECONDS: float = 2.0
    AGENTIC_THROTTLE_CELERY_BACKOFF_SECONDS: int = 30
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
    # Additional key slots for rotation — used when SERPAPI_API_KEY runs out
    # of credits mid-run so the job can fail over to the next configured key
    # instead of stalling until the primary key's quota resets.
    SERPAPI_API_KEY_2: str = ""
    SERPAPI_API_KEY_3: str = ""
    SERPAPI_API_KEY_4: str = ""
    SERPAPI_API_KEY_5: str = ""
    SERPAPI_API_KEY_6: str = ""
    SERPAPI_API_KEY_7: str = ""
    SERPAPI_API_KEY_8: str = ""
    SERPAPI_API_KEY_9: str = ""
    SERPAPI_API_KEY_10: str = ""
    SERPAPI_API_KEY_11: str = ""
    SERPAPI_API_KEY_12: str = ""
    SERPAPI_API_KEY_13: str = ""
    # SerpApi credits reset on each key's own monthly billing date, which we
    # don't track per key — this cooldown is an approximation: a key marked
    # exhausted becomes eligible again this many hours after the exhaustion
    # timestamp, so it can self-heal within about a day of its real reset
    # rather than staying locked out of rotation for weeks.
    SERPAPI_KEY_COOLDOWN_HOURS: int = 24
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

    # ── LLM/API usage observability (see local-docs/2026-08-24-observability-platform-design.md)
    # Single kill switch: every instrumentation site checks this before doing
    # any work, so the feature can be disabled in production without a deploy
    # of changed call-site code.
    USAGE_TRACKING_ENABLED: bool = True
    # Raw LlmCallLog retention (spec §8). Rollups are kept indefinitely;
    # SerpApiCallLog/SerperCallLog/SerpApiCreditSnapshot are never pruned.
    USAGE_RAW_RETENTION_DAYS: int = 30
    # Max raw rows one hourly rollup run folds in. At the stated 100k+
    # calls/day (~4.2k/hour) this leaves ~12x headroom for a backlog after an
    # outage while still bounding a single beat tick's memory/runtime.
    USAGE_ROLLUP_BATCH_SIZE: int = 50000
    # Hard bound on a report's date range (spec §9/§11) so a hand-crafted
    # query can't ask for a decade of CSV/PDF.
    USAGE_REPORT_MAX_DAYS: int = 366
    # SerpApi's documented account endpoint — the only third-party
    # remaining-credit API available to us (Serper has none, see below).
    SERPAPI_ACCOUNT_URL: str = "https://serpapi.com/account"
    # Serper has NO remaining-credits API (spec §7) — only a web dashboard.
    # Credits left is therefore self-tracked as SERPER_QUOTA_TOTAL minus the
    # count of SerperCallLog rows since SERPER_QUOTA_PERIOD_START.
    # OPERATIONAL NOTE: topping up or changing the Serper plan REQUIRES
    # bumping SERPER_QUOTA_TOTAL (and usually SERPER_QUOTA_PERIOD_START) here
    # or the reported balance silently drifts. 0 means "quota unknown" and the
    # API reports used-count only, with quota_total/remaining as null.
    SERPER_QUOTA_TOTAL: int = 0
    # ISO date (YYYY-MM-DD) the current quota period began. Empty means
    # "count all SerperCallLog rows ever".
    SERPER_QUOTA_PERIOD_START: str = ""

    @property
    def SERPAPI_API_KEYS(self) -> list[tuple[int, str]]:
        """Ordered (slot, key) pairs for every configured SerpApi key, slot 1 = SERPAPI_API_KEY."""
        slots = [
            (1, self.SERPAPI_API_KEY),
            (2, self.SERPAPI_API_KEY_2),
            (3, self.SERPAPI_API_KEY_3),
            (4, self.SERPAPI_API_KEY_4),
            (5, self.SERPAPI_API_KEY_5),
            (6, self.SERPAPI_API_KEY_6),
            (7, self.SERPAPI_API_KEY_7),
            (8, self.SERPAPI_API_KEY_8),
            (9, self.SERPAPI_API_KEY_9),
            (10, self.SERPAPI_API_KEY_10),
            (11, self.SERPAPI_API_KEY_11),
            (12, self.SERPAPI_API_KEY_12),
            (13, self.SERPAPI_API_KEY_13),
        ]
        return [(slot, key) for slot, key in slots if key]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
