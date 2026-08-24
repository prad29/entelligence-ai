"""LLM/API usage observability: cost calculation, usage extraction, and
fire-and-forget log writers.

Every public writer in this package swallows its own exceptions and logs
instead (spec §7): observability must never become a reliability risk for the
movie-mapping/detection pipeline it observes.
"""
