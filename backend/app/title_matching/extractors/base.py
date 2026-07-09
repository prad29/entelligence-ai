from __future__ import annotations

from abc import ABC, abstractmethod

from app.title_matching.evidence_types import EvidenceResult


class AbstractExtractor(ABC):
    @abstractmethod
    def extract(self, url: str, platform: str) -> EvidenceResult:
        """Fetch evidence from *url* for the given *platform*.

        Returns an :class:`EvidenceResult` in all cases — implementations must
        not propagate exceptions to callers.
        """
