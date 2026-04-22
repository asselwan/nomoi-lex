"""Engine 1: Submission shape validation (section 4.4.2.1).

Validates the structural requirements for inpatient IR-DRG claims:
- EncounterType must be 3 or 4
- All activities reported as Fee for Service line items
- Activity.Net = 0 for everything except DRG code, SRVC 99, 98, 99-01, 99-02, 99-03
- Required observations present (ActivityCost, DRG-NotCovered)
"""

from __future__ import annotations

from decimal import Decimal

from validator.engines.base import BaseEngine
from validator.models.claim import Activity, ContractConfig, Encounter
from validator.models.results import RuleResult, Severity, ValidationReport
from validator.reference.loader import ReferenceData

_NONZERO_NET_CODES = frozenset({"98", "99", "99-01", "99-02", "99-03"})
_DRG_ACTIVITY_TYPE = 9


class SubmissionShapeEngine(BaseEngine):
    @property
    def name(self) -> str:
        return "submission_shape"

    def validate(
        self,
        encounter: Encounter,
        config: ContractConfig,
        ref: ReferenceData,
        report: ValidationReport,
        encounter_index: int,
    ) -> None:
        self._check_encounter_type(encounter, report, encounter_index)
        self._check_has_drg_activity(encounter, report, encounter_index)
        self._check_activity_nets(encounter, ref, report, encounter_index)
        self._check_activity_cost_observation(encounter, report, encounter_index)
        self._check_drg_not_covered_observations(encounter, report, encounter_index)

    def _check_encounter_type(
        self,
        encounter: Encounter,
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        if encounter.type not in (3, 4):
            report.add(
                RuleResult(
                    rule_id="SS-001",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message=(
                        f"EncounterType must be 3 or 4 for inpatient claims, "
                        f"got {encounter.type}"
                    ),
                    field=f"encounters[{enc_idx}].type",
                    expected="3 or 4",
                    actual=str(encounter.type),
                    encounter_index=enc_idx,
                )
            )

    def _check_has_drg_activity(
        self,
        encounter: Encounter,
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        has_drg = any(a.type == _DRG_ACTIVITY_TYPE for a in encounter.activities)
        if not has_drg:
            report.add(
                RuleResult(
                    rule_id="SS-002",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message="Inpatient encounter missing IR-DRG activity (type 9)",
                    field=f"encounters[{enc_idx}].activities",
                    encounter_index=enc_idx,
                )
            )

    def _check_activity_nets(
        self,
        encounter: Encounter,
        ref: ReferenceData,
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        for act_idx, activity in enumerate(encounter.activities):
            is_drg = activity.type == _DRG_ACTIVITY_TYPE
            is_allowed_service = (
                activity.type == 8 and activity.code in _NONZERO_NET_CODES
            )
            may_have_nonzero_net = is_drg or is_allowed_service

            if not may_have_nonzero_net and activity.net != Decimal("0"):
                report.add(
                    RuleResult(
                        rule_id="SS-003",
                        engine=self.name,
                        severity=Severity.ERROR,
                        message=(
                            f"Activity.Net must be 0 under DRG inclusive pricing. "
                            f"Activity {activity.code} (type {activity.type}) "
                            f"has Net={activity.net}"
                        ),
                        field=f"encounters[{enc_idx}].activities[{act_idx}].net",
                        expected="0",
                        actual=str(activity.net),
                        encounter_index=enc_idx,
                        activity_index=act_idx,
                    )
                )

    def _check_activity_cost_observation(
        self,
        encounter: Encounter,
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        has_activity_cost = any(
            obs.type == "ActivityCost"
            for act in encounter.activities
            for obs in act.observations
        )
        if not has_activity_cost:
            report.add(
                RuleResult(
                    rule_id="SS-004",
                    engine=self.name,
                    severity=Severity.WARNING,
                    message=(
                        "ActivityCost observation required for inpatient encounters "
                        "per Routine Reporting requirements"
                    ),
                    field=f"encounters[{enc_idx}].activities",
                    encounter_index=enc_idx,
                )
            )

    def _check_drg_not_covered_observations(
        self,
        encounter: Encounter,
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        zero_net_activities = [
            (i, a)
            for i, a in enumerate(encounter.activities)
            if a.net == Decimal("0") and a.type != _DRG_ACTIVITY_TYPE
        ]
        for act_idx, activity in zero_net_activities:
            has_obs = any(
                obs.type == "DRG-NotCovered" for obs in activity.observations
            )
            if not has_obs:
                report.add(
                    RuleResult(
                        rule_id="SS-005",
                        engine=self.name,
                        severity=Severity.WARNING,
                        message=(
                            f"Activity {activity.code} with Net=0 should have "
                            f"DRG-NotCovered observation per Routine Reporting"
                        ),
                        field=(
                            f"encounters[{enc_idx}].activities[{act_idx}].observations"
                        ),
                        encounter_index=enc_idx,
                        activity_index=act_idx,
                    )
                )
