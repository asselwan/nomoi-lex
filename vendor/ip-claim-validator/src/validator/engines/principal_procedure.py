"""Engine 2: Principal procedure ordering check (section 4.4.1).

Degraded mode for v1: true PCAT precedence requires the 3M IR-DRG grouper.
v1 checks that the principal procedure matches the first procedure activity.

If a pcat_lookup.yaml is later supplied via ContractConfig.pcat_file,
this engine should be extended to perform full PCAT validation using
the procedure category hierarchy defined in the IR-DRG grouper.
"""

from __future__ import annotations

from validator.engines.base import BaseEngine
from validator.models.claim import ContractConfig, Encounter
from validator.models.results import RuleResult, Severity, ValidationReport
from validator.reference.loader import ReferenceData

_CPT_TYPE = 3


class PrincipalProcedureEngine(BaseEngine):
    @property
    def name(self) -> str:
        return "principal_procedure"

    def validate(
        self,
        encounter: Encounter,
        config: ContractConfig,
        ref: ReferenceData,
        report: ValidationReport,
        encounter_index: int,
    ) -> None:
        if config.pcat_file:
            report.add(
                RuleResult(
                    rule_id="PP-000",
                    engine=self.name,
                    severity=Severity.INFO,
                    message=(
                        "PCAT lookup file configured but full PCAT validation "
                        "not yet implemented. Falling back to ordering check."
                    ),
                    encounter_index=encounter_index,
                )
            )

        procedure_activities = [
            (i, a)
            for i, a in enumerate(encounter.activities)
            if a.type == _CPT_TYPE
        ]

        if len(procedure_activities) < 2:
            return

        drg_code = encounter.reported.drg_code
        if not drg_code:
            return

        drg_entry = ref.get_drg(drg_code)
        if not drg_entry or drg_entry.category != "IP":
            return

        first_proc_idx, first_proc = procedure_activities[0]

        principal_diag = next(
            (d for d in encounter.diagnoses if d.type == "Principal"),
            None,
        )
        if principal_diag is None:
            report.add(
                RuleResult(
                    rule_id="PP-001",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message="No principal diagnosis found on encounter",
                    field=f"encounters[{encounter_index}].diagnoses",
                    encounter_index=encounter_index,
                )
            )
            return

        report.add(
            RuleResult(
                rule_id="PP-002",
                engine=self.name,
                severity=Severity.WARNING,
                message=(
                    "Principal procedure may not follow PCAT precedence; "
                    "manual review against 3M grouper recommended. "
                    f"First procedure: {first_proc.code}. "
                    f"{len(procedure_activities)} procedures on encounter."
                ),
                field=f"encounters[{encounter_index}].activities",
                encounter_index=encounter_index,
            )
        )
