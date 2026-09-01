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
    # Phase 5 (windowed dispatch + round-robin top-up — the fairness fix, see
    # local-docs/2026-08-25-agentic-batch-concurrency-design.md §4.4). 0 means
    # "auto-derive as 2 * AGENTIC_BATCH_MAX_CONCURRENCY" (dispatch_window.
    # target_queue_depth) so the shared queue stays proportionally short as
    # concurrency is raised in later phases without a manual bump here.
    AGENTIC_QUEUE_TARGET_DEPTH: int = 0
    # Floor on a single job's standing dispatch window (dispatch_window.
    # compute_job_window) so a job is never starved to zero just because many
    # other jobs are active at once.
    AGENTIC_JOB_WINDOW_MIN: int = 2
    # Rows published per job per rotation pass in topup_agentic_queue's
    # round-robin top-up, and the chunk self-refill (_after_row_terminal)
    # requests after each terminal row. Deliberately small (not "give one job
    # its whole deficit"): the broker is strict FIFO, so publish order is
    # execution order, and a large per-job chunk before rotating to the next
    # job would reconstruct the exact head-of-line blocking this phase exists
    # to remove.
    AGENTIC_ROUNDROBIN_CHUNK: int = 1
    # Beat tick interval for topup_agentic_queue. Sub-minute cadence needs a
    # plain seconds-based schedule (see celery_app.py's beat_schedule).
    AGENTIC_SCHED_TICK_SECONDS: float = 10.0
    # Log-only stall detection (no alerting -- explicitly out of scope for
    # this phase): a job fully dispatched but not yet fully processed for
    # longer than this is logged as a warning by topup_agentic_queue.
    AGENTIC_STALL_WARN_SECONDS: int = 900
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

    # ── Lobby Check API (see docs/plans/2026-09-01-lobby-check-design.md) —
    # productionizes the mmvision.py prototype's Qwen-on-Bedrock cinema-lobby
    # marketing-material extraction as /api/v1/lobby-check. Only the setting
    # schemas.py needs at import time (the SSRF host allow-list) is added in
    # phase 1; the rest land in phase 3 alongside the job/row tables.
    #
    # Comma-separated exact hostnames a submitted image_url is allowed to
    # target. Confirmed production host: mm-intelligence.s3.amazonaws.com.
    # This server performs the fetch itself, so an unbounded host list here
    # is a live SSRF vector — see images.py for the rest of the guard
    # (https-only, no redirects, private-IP rejection).
    LOBBY_CHECK_ALLOWED_URL_HOSTS: str = "mm-intelligence.s3.amazonaws.com"
    # Phase 2 — Bedrock/Qwen extraction settings. Job/batch/concurrency/
    # secrets settings are added in phase 3 alongside the job/row tables.
    LOBBY_CHECK_MODEL_ID: str = "qwen.qwen3-vl-235b-a22b"
    # Per-converse-call budget. lobby_check/limits.py derives the Celery row
    # task's soft/hard time limits from this (fetch + up to two attempts:
    # the primary call plus the one in-process repair retry).
    LOBBY_CHECK_TIMEOUT_SECONDS: int = 90
    LOBBY_CHECK_IMAGE_FETCH_TIMEOUT_SECONDS: int = 30
    # Below this, at least one of the four confidences is low enough that
    # the record should route to a human — drives both LlmCallLog.decision
    # (AUTO_ACCEPT vs REVIEW) and the API's needs_review flag.
    LOBBY_CHECK_REVIEW_CONFIDENCE_THRESHOLD: float = 0.7
    # If the model's material_condition still disagrees with its own
    # defects list after the repair retry, the persisted condition is the
    # defects-derived value and confidence_material_condition is clamped to
    # at most this — a conflict is a low-confidence signal, not a failure
    # (see docs/plans/2026-09-01-lobby-check-design.md §3.4).
    LOBBY_CHECK_CONDITION_CONFLICT_CONFIDENCE_CAP: float = 0.5

    # Phase 3 — job/batch/concurrency/auth settings.
    #
    # Feature-flagged like EXTERNAL_API_ENABLED — the router is only
    # mounted (main.py) when this is true, so a deploy can land the
    # migration/worker/secret plumbing and flip the surface on separately.
    LOBBY_CHECK_ENABLED: bool = False
    # Plaintext API key read from env/Secrets Manager and hashed into an
    # ApiKey row at startup (see main.py's _seed_api_key), same mechanism as
    # X_API_KEY but a DEDICATED key/row — not shared with the external
    # title-match surface. Rationale: independent blast radius on rotation,
    # and an independent max_rows_per_batch (500 here vs. 10000 there) via
    # the existing per-key override with zero branching.
    LOBBY_CHECK_API_KEY: str = ""
    # ~600 images/day expected (confirmed 2026-09-01) — a 500-row batch at
    # $0.0013/row is ~$0.65 and, at LOBBY_CHECK_MAX_CONCURRENCY=4, finishes
    # within a few poll cycles. Sized far below MAX_BATCH_ROWS=10000, which
    # is priced for a cheap CSV row, not a multi-MB vision call.
    LOBBY_CHECK_MAX_BATCH_ROWS: int = 500
    # Confirmed comfortable against the ~600/day volume — Bedrock on-demand
    # quotas are per-model-per-region, so Qwen has its own TPM/RPM bucket
    # independent of the Claude/Cohere calls already running on this host.
    # Raise only after a soak period if real usage materially exceeds the
    # confirmed estimate (see design doc §4.5/phase 7).
    LOBBY_CHECK_MAX_CONCURRENCY: int = 4
    # Phase 5 fair-dispatch primitives, mirroring the AGENTIC_* settings'
    # naming/values/reasoning (see dispatch_window.py) — lobby-check gets
    # its OWN queue/pool/Redis keys, deliberately not joined to the shared
    # agentic pool (design doc §4.1). 0 means "auto-derive as
    # 2 * LOBBY_CHECK_MAX_CONCURRENCY".
    LOBBY_CHECK_QUEUE_TARGET_DEPTH: int = 0
    LOBBY_CHECK_JOB_WINDOW_MIN: int = 2
    LOBBY_CHECK_ROUNDROBIN_CHUNK: int = 1
    LOBBY_CHECK_SCHED_TICK_SECONDS: float = 10.0
    # Celery self.retry budget for transient/throttle failures (layer 2 of
    # the three-layer retry policy — see extractor.py/errors.py). Also
    # governs the /retry endpoint's attempt-cap predicate (phase 7).
    LOBBY_CHECK_ROW_MAX_ATTEMPTS: int = 3
    LOBBY_CHECK_THROTTLE_BACKOFF_SECONDS: int = 30
    # Longer than JOB_TTL_HOURS (24h) — mirrors EXTERNAL_API_JOB_TTL_HOURS's
    # rationale: lobby-check rows must survive long enough for a client to
    # retry across a business day.
    LOBBY_CHECK_JOB_TTL_HOURS: int = 72

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
