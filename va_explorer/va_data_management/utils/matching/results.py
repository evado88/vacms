"""Structured result types for VA -> Death matching.

Kept dependency-free (pure dataclasses) so they are trivially serialisable into
the ``VADeathMatch`` JSON columns and the retrospective command's CSV report.
"""

from dataclasses import dataclass, field
from typing import List, Optional

# Per-field comparison classifications.
EXACT = "exact"
NORMALIZED = "normalized"
FUZZY = "fuzzy"
COMPATIBLE = "compatible"
CONTRADICTORY = "contradictory"
MISSING = "missing"


@dataclass
class FieldComparison:
    field: str
    va_value: Optional[str]
    death_value: Optional[str]
    classification: str
    weight: float
    awarded: float
    similarity: Optional[float] = None

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "va_value": self.va_value,
            "death_value": self.death_value,
            "classification": self.classification,
            "weight": self.weight,
            "awarded": round(self.awarded, 2),
            "similarity": self.similarity,
        }


@dataclass
class CandidateScore:
    """A single Death candidate scored against one VA."""

    death_pk: int
    death_id: Optional[str]
    method: str
    score: float
    comparisons: List[FieldComparison] = field(default_factory=list)
    hard_disqualified: bool = False

    def _fields(self, classification: str) -> List[str]:
        return [c.field for c in self.comparisons if c.classification == classification]

    @property
    def supporting_fields(self) -> List[str]:
        return [
            c.field
            for c in self.comparisons
            if c.classification in {EXACT, NORMALIZED, FUZZY, COMPATIBLE}
            and c.awarded > 0
        ]

    @property
    def contradictory_fields(self) -> List[str]:
        return self._fields(CONTRADICTORY)

    @property
    def missing_fields(self) -> List[str]:
        return self._fields(MISSING)

    def as_alternative(self) -> dict:
        return {
            "death_pk": self.death_pk,
            "death_id": self.death_id,
            "score": round(self.score, 2),
            "method": self.method,
        }


@dataclass
class MatchResult:
    """The full, explainable outcome of evaluating one VA."""

    va_pk: int
    va_model: str  # "verbalautopsy" | "vasa"
    va_identifier: Optional[str]
    status: str
    method: Optional[str]
    death_pk: Optional[int]
    death_id: Optional[str]
    score: float
    score_separation: float
    supporting_fields: List[str] = field(default_factory=list)
    contradictory_fields: List[str] = field(default_factory=list)
    missing_fields: List[str] = field(default_factory=list)
    alternatives: List[dict] = field(default_factory=list)
    comparisons: List[dict] = field(default_factory=list)
    algorithm_version: str = ""
    source: str = ""
    changed: bool = False
    previously_linked: bool = False
    previous_was_manual: bool = False
    error: Optional[str] = None

    def to_report_row(self) -> dict:
        return {
            "va_model": self.va_model,
            "va_pk": self.va_pk,
            "va_identifier": self.va_identifier,
            "death_pk": self.death_pk,
            "death_id": self.death_id,
            "score": round(self.score, 2),
            "score_separation": round(self.score_separation, 2),
            "method": self.method or "",
            "status": self.status,
            "supporting_fields": ";".join(self.supporting_fields),
            "contradictory_fields": ";".join(self.contradictory_fields),
            "missing_fields": ";".join(self.missing_fields),
            "alternatives": ";".join(
                f"{a['death_id'] or a['death_pk']}={a['score']}"
                for a in self.alternatives
            ),
            "changed": self.changed,
            "previously_linked": self.previously_linked,
            "previous_was_manual": self.previous_was_manual,
            "source": self.source,
            "algorithm_version": self.algorithm_version,
            "error": self.error or "",
        }
