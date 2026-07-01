# Stubs only — full implementation in Phase 2.
# Phase 2 will add SQLModel tables for:
#   AmenityMapping, CircuitOverride, CircuitAlias,
#   DetectionJob, ReviewItem, AuditLog
#
# Importing this module in Phase 1 is safe — no DB tables are created until
# Phase 2 migrations run.

from sqlmodel import SQLModel  # noqa: F401 — re-exported for Alembic


class _StubBase(SQLModel):
    """Placeholder — Phase 2 converts these to full table models."""
    pass


# Phase 2 table stubs (not yet active)
class AmenityMapping(_StubBase, table=False):
    pass


class CircuitOverride(_StubBase, table=False):
    pass


class CircuitAlias(_StubBase, table=False):
    pass


class DetectionJob(_StubBase, table=False):
    pass


class ReviewItem(_StubBase, table=False):
    pass


class AuditLog(_StubBase, table=False):
    pass
