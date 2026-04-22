"""Engine 9: Modifiers in IP context (section 5.4).

Only modifiers 25, 24, 50 are relevant for inpatient claims.
Modifier 52 is outpatient E&M, not applicable.

Modifiers are validated as observation fields on relevant CPT codes.
Surgical CPT codes in IP do not generate separate payment (DRG inclusive),
but modifiers are still validated for coding correctness.
"""

from __future__ import annotations

from validator.engines.base import BaseEngine
from validator.models.claim import ContractConfig, Encounter
from validator.models.results import RuleResult, Severity, ValidationReport
from validator.reference.loader import ReferenceData

_CPT_TYPE = 3
_IP_VALID_MODIFIERS = frozenset({"25", "24", "50"})
_OUTPATIENT_ONLY_MODIFIERS = frozenset({"52"})


class ModifiersEngine(BaseEngine):
    @property
    def name(self) -> str:
        return "modifiers"

    def validate(
        self,
        encounter: Encounter,
        config: ContractConfig,
        ref: ReferenceData,
        report: ValidationReport,
        encounter_index: int,
    ) -> None:
        for act_idx, activity in enumerate(encounter.activities):
            if activity.type != _CPT_TYPE:
                continue

            modifier_obs = [
                obs for obs in activity.observations if obs.type == "Modifier"
            ]
            for obs in modifier_obs:
                modifier_value = obs.value.strip()
                if not modifier_value:
                    continue

                if modifier_value in _OUTPATIENT_ONLY_MODIFIERS:
                    report.add(
                        RuleResult(
                            rule_id="MO-001",
                            engine=self.name,
                            severity=Severity.ERROR,
                            message=(
                                f"Modifier {modifier_value} is outpatient E&M only, "
                                f"not applicable for inpatient encounters. "
                                f"Activity: {activity.code}"
                            ),
                            field=(
                                f"encounters[{encounter_index}]"
                                f".activities[{act_idx}].observations"
                            ),
                            encounter_index=encounter_index,
                            activity_index=act_idx,
                        )
                    )
                elif modifier_value not in _IP_VALID_MODIFIERS:
                    report.add(
                        RuleResult(
                            rule_id="MO-002",
                            engine=self.name,
                            severity=Severity.WARNING,
                            message=(
                                f"Modifier {modifier_value} on activity "
                                f"{activity.code} is not in the standard "
                                f"inpatient modifier set (25, 24, 50)"
                            ),
                            field=(
                                f"encounters[{encounter_index}]"
                                f".activities[{act_idx}].observations"
                            ),
                            encounter_index=encounter_index,
                            activity_index=act_idx,
                        )
                    )
