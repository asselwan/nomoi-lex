"""Engine 5: CAHMS adjustor validation (section 4.4.2.2.3).

Advisory mode for v1: mental_health_drgs.yaml not yet supplied.
Until it lands, engine emits INFO when SRVC 99-03 is present.

When the file is supplied, switch to strict validation:
- Patient under 18 on a mental health DRG
- Total payment increased by 50%
- Reported via SRVC code 99-03
"""

from __future__ import annotations

from validator.engines.base import BaseEngine
from validator.models.claim import ContractConfig, Encounter
from validator.models.results import RuleResult, Severity, ValidationReport
from validator.reference.loader import ReferenceData

_SRVC_CODE_TYPE = 8
_CAHMS_CODE = "99-03"


class CAHMSEngine(BaseEngine):
    @property
    def name(self) -> str:
        return "cahms"

    def validate(
        self,
        encounter: Encounter,
        config: ContractConfig,
        ref: ReferenceData,
        report: ValidationReport,
        encounter_index: int,
    ) -> None:
        cahms_activities = [
            (i, a)
            for i, a in enumerate(encounter.activities)
            if a.type == _SRVC_CODE_TYPE and a.code == _CAHMS_CODE
        ]

        if not cahms_activities:
            return

        act_idx, cahms_activity = cahms_activities[0]

        if encounter.patient_age_years is not None and encounter.patient_age_years >= 18:
            report.add(
                RuleResult(
                    rule_id="CA-001",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message=(
                        f"CAHMS adjustor (SRVC 99-03) reported but patient age "
                        f"is {encounter.patient_age_years}. "
                        f"CAHMS requires patient under 18 years of age."
                    ),
                    field=f"encounters[{encounter_index}].patient_age_years",
                    expected="< 18",
                    actual=str(encounter.patient_age_years),
                    encounter_index=encounter_index,
                )
            )

        if not config.mental_health_drg_file:
            report.add(
                RuleResult(
                    rule_id="CA-002",
                    engine=self.name,
                    severity=Severity.INFO,
                    message=(
                        "CAHMS adjustor reported. Eligibility validation deferred "
                        "pending MDC 19 DRG list."
                    ),
                    field=f"encounters[{encounter_index}].activities[{act_idx}]",
                    encounter_index=encounter_index,
                    activity_index=act_idx,
                )
            )
        else:
            report.add(
                RuleResult(
                    rule_id="CA-003",
                    engine=self.name,
                    severity=Severity.INFO,
                    message=(
                        "Mental health DRG file configured but strict CAHMS "
                        "eligibility validation not yet implemented"
                    ),
                    encounter_index=encounter_index,
                )
            )
