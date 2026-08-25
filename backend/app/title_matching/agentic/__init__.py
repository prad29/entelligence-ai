class AgenticError(Exception):
    """Base for all Mode B errors."""


class AgenticConfigError(AgenticError):
    """claude CLI not found or ANTHROPIC_API_KEY not set."""


class AgenticTimeoutError(AgenticError):
    """Subprocess exceeded AGENTIC_TIMEOUT_SECONDS."""


class AgenticSubprocessError(AgenticError):
    """Claude CLI exited with a non-zero return code."""


class AgenticThrottleError(AgenticError):
    """Bedrock throttled the request (429 / ThrottlingException) and the
    in-process retry budget (AGENTIC_THROTTLE_MAX_RETRIES fast-fail retries
    inside runner._call_sandbox) was exhausted.

    Deliberately a SUBCLASS of AgenticError (not a sibling) rather than its
    own hierarchy: existing `except AgenticError` call sites must keep
    working unchanged (fail the row rather than crash the whole batch) even
    before any throttle-specific handling is added. Callers that DO want to
    treat throttling differently (back off and re-queue the row via Celery
    countdown, releasing the sandbox semaphore slot for the whole backoff
    window, instead of immediately failing the row outright) must catch
    AgenticThrottleError explicitly BEFORE a generic `except AgenticError`
    branch -- Python matches the first exception clause whose type it is an
    instance of, so ordering it after the generic branch would silently let
    the generic branch swallow every throttle too.
    """


class AgenticParseError(AgenticError):
    """Agent output could not be parsed into a valid TitleMatchResult."""
