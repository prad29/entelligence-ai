from __future__ import annotations
from abc import ABC, abstractmethod
from app.title_matching.evidence_types import EvidenceResult


class AbstractExtractor(ABC):
    @abstractmethod
    def extract(self, url: str, platform: str) -> EvidenceResult: ...
