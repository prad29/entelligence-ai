import logging
import json


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
        }
        for key in [
            "amenity",
            "circuit",
            "unknown_circuit",
            "ai_invocation",
            "job_id",
            "screen_format",
        ]:
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    logging.root.setLevel(logging.INFO)
    # Avoid duplicate handlers if called more than once (e.g. during reload)
    if not any(isinstance(h, logging.StreamHandler) for h in logging.root.handlers):
        logging.root.addHandler(handler)
    else:
        # Replace the first StreamHandler formatter
        for h in logging.root.handlers:
            if isinstance(h, logging.StreamHandler):
                h.setFormatter(StructuredFormatter())
                break
