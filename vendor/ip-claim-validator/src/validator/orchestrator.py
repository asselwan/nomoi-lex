"""Orchestrator: runs validation engines in order against a claim.

If a claim arrives with an out-of-scope encounter type, returns a single
INFO result and stops. In-scope: EncounterType 3 (Inpatient Bed, no ER)
and 4 (Inpatient Bed, with ER).
"""

from __future__ import annotations

from validator.engines.base import BaseEngine
from validator.engines.base_payment import BasePaymentEngine
from validator.engines.cahms import CAHMSEngine
from validator.engines.hac import HACEngine
from validator.engines.lama import LAMAEngine
from validator.engines.modifiers import ModifiersEngine
from validator.engines.outlier import OutlierEngine
from validator.engines.principal_procedure import PrincipalProcedureEngine
from validator.engines.split_drg import SplitDRGEngine
from validator.engines.submission_shape import SubmissionShapeEngine
from validator.models.claim import Claim
from validator.models.results import RuleResult, Severity, ValidationReport
from validator.reference.loader import ReferenceData, get_reference_data

_IN_SCOPE_TYPES = frozenset({3, 4})

_ENGINE_ORDER: list[type[BaseEngine]] = [
    SubmissionShapeEngine,   # 1
    PrincipalProcedureEngine,  # 2
    BasePaymentEngine,       # 3
    OutlierEngine,           # 4
    CAHMSEngine,             # 5
    LAMAEngine,              # 6
    HACEngine,               # 7
    SplitDRGEngine,          # 8
    ModifiersEngine,         # 9
]


def validate_claim(
    claim: Claim,
    ref: ReferenceData | None = None,
) -> ValidationReport:
    """Run all validation engines against a claim and return results."""
    if ref is None:
        ref = get_reference_data()

    report = ValidationReport.for_claim(claim.id)
    report.encounter_count = len(claim.encounters)

    if not claim.encounters:
        report.add(
            RuleResult(
                rule_id="ORCH-001",
                engine="orchestrator",
                severity=Severity.ERROR,
                message="Claim has no encounters",
            )
        )
        return report

    engines = [cls() for cls in _ENGINE_ORDER]

    for enc_idx, encounter in enumerate(claim.encounters):
        if encounter.type not in _IN_SCOPE_TYPES:
            report.add(
                RuleResult(
                    rule_id="ORCH-002",
                    engine="orchestrator",
                    severity=Severity.INFO,
                    message=(
                        f"Encounter type {encounter.type} is out of scope "
                        f"for inpatient validation (expected 3 or 4). Skipping."
                    ),
                    encounter_index=enc_idx,
                )
            )
            continue

        for engine in engines:
            engine.validate(
                encounter=encounter,
                config=claim.contract,
                ref=ref,
                report=report,
                encounter_index=enc_idx,
            )

    return report
