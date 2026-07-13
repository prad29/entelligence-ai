class AgenticError(Exception):
    """Base for all Mode B errors."""


class AgenticConfigError(AgenticError):
    """claude CLI not found or ANTHROPIC_API_KEY not set."""


class AgenticTimeoutError(AgenticError):
    """Subprocess exceeded AGENTIC_TIMEOUT_SECONDS."""


class AgenticSubprocessError(AgenticError):
    """Claude CLI exited with a non-zero return code."""


class AgenticParseError(AgenticError):
    """Agent output could not be parsed into a valid TitleMatchResult."""
