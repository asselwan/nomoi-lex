"""Validation result models."""

from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Rule result severity levels."""

    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class RuleResult(BaseModel):
    """Single validation finding from an engine."""

    rule_id: str = Field(description="Unique rule identifier, e.g. SS-001")
    engine: str = Field(description="Engine name that produced this result")
    severity: Severity
    message: str
    field: str = Field(
        default="",
        description="Dot-path to the claim field, e.g. encounters[0].activities[2].net",
    )
    expected: str = ""
    actual: str = ""
    encounter_index: int | None = None
    activity_index: int | None = None


class ValidationReport(BaseModel):
    """Aggregated validation output for a claim."""

    claim_id_hash: str = Field(
        description="SHA-256 hash (first 12 chars) of the claim ID for PHI safety",
    )
    encounter_count: int = 0
    results: list[RuleResult] = Field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0

    @classmethod
    def for_claim(cls, claim_id: str) -> ValidationReport:
        digest = hashlib.sha256(claim_id.encode()).hexdigest()[:12]
        return cls(claim_id_hash=digest)

    def add(self, result: RuleResult) -> None:
        self.results.append(result)
        match result.severity:
            case Severity.ERROR:
                self.error_count += 1
            case Severity.WARNING:
                self.warning_count += 1
            case Severity.INFO:
                self.info_count += 1
