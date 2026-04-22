"""Base engine interface for all validation engines."""

from __future__ import annotations

from abc import ABC, abstractmethod

from validator.models.claim import ContractConfig, Encounter
from validator.models.results import ValidationReport
from validator.reference.loader import ReferenceData


class BaseEngine(ABC):
    """Abstract base for validation engines.

    Each engine receives an encounter, a contract config, reference data,
    and the encounter index within the claim. It appends RuleResults to
    the shared ValidationReport.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short engine identifier used in RuleResult.engine."""

    @abstractmethod
    def validate(
        self,
        encounter: Encounter,
        config: ContractConfig,
        ref: ReferenceData,
        report: ValidationReport,
        encounter_index: int,
    ) -> None:
        """Run validation rules and append results to report."""
