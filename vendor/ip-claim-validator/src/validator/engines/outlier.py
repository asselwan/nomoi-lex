"""Engine 4: Outlier payment validation (section 4.4.2.2.2).

Non-mental health:
    Outlier = (Cost - (Base Payment + Gap + Add-on HCPCS)) * Marginal

Mental health outlier (HTLOS-based):
    Deferred to advisory mode until mental_health_drgs.yaml is supplied.

Cost basis uses Mandatory Tariff prices plus actual incurred HCPCS and drug cost.
Excluded from cost: claiming errors, duplicates, medically impossible items,
uncovered items.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from validator.engines.base import BaseEngine
from validator.engines.base_payment import calculate_base_payment
from validator.models.claim import ContractConfig, Encounter
from validator.models.results import RuleResult, Severity, ValidationReport
from validator.reference.loader import ReferenceData

_SRVC_CODE_TYPE = 8
_DRG_ACTIVITY_TYPE = 9


class OutlierEngine(BaseEngine):
    @property
    def name(self) -> str:
        return "outlier"

    def validate(
        self,
        encounter: Encounter,
        config: ContractConfig,
        ref: ReferenceData,
        report: ValidationReport,
        encounter_index: int,
    ) -> None:
        outlier_99 = self._find_service_activity(encounter, "99")
        outlier_mh = self._find_service_activity(encounter, "99-02")

        if outlier_mh is not None:
            self._validate_mental_health_outlier(
                encounter, config, report, encounter_index, outlier_mh
            )
            return

        if outlier_99 is None:
            return

        self._validate_non_mh_outlier(
            encounter, config, ref, report, encounter_index, outlier_99
        )

    def _find_service_activity(
        self, encounter: Encounter, code: str
    ) -> tuple[int, object] | None:
        for i, a in enumerate(encounter.activities):
            if a.type == _SRVC_CODE_TYPE and a.code == code:
                return (i, a)
        return None

    def _validate_mental_health_outlier(
        self,
        encounter: Encounter,
        config: ContractConfig,
        report: ValidationReport,
        enc_idx: int,
        outlier_info: tuple[int, object],
    ) -> None:
        act_idx, activity = outlier_info
        if config.mental_health_drg_file:
            report.add(
                RuleResult(
                    rule_id="OL-010",
                    engine=self.name,
                    severity=Severity.INFO,
                    message=(
                        "Mental health DRG file configured but HTLOS outlier "
                        "validation not yet implemented"
                    ),
                    encounter_index=enc_idx,
                )
            )
        else:
            report.add(
                RuleResult(
                    rule_id="OL-011",
                    engine=self.name,
                    severity=Severity.INFO,
                    message=(
                        "Mental health outlier (SRVC 99-02) reported. "
                        "Validation deferred pending mental_health_drgs.yaml "
                        "and ALOS/HTLOS reference data."
                    ),
                    field=f"encounters[{enc_idx}].activities[{act_idx}]",
                    encounter_index=enc_idx,
                    activity_index=act_idx,
                )
            )

    def _validate_non_mh_outlier(
        self,
        encounter: Encounter,
        config: ContractConfig,
        ref: ReferenceData,
        report: ValidationReport,
        enc_idx: int,
        outlier_info: tuple[int, object],
    ) -> None:
        act_idx, outlier_activity = outlier_info

        drg_code = encounter.reported.drg_code
        if not drg_code:
            drg_act = next(
                (a for a in encounter.activities if a.type == _DRG_ACTIVITY_TYPE),
                None,
            )
            if drg_act:
                drg_code = drg_act.code

        if not drg_code:
            report.add(
                RuleResult(
                    rule_id="OL-001",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message="Outlier present but no DRG code found on encounter",
                    encounter_index=enc_idx,
                )
            )
            return

        drg_entry = ref.get_drg(drg_code)
        if drg_entry is None:
            return

        base_payment = calculate_base_payment(
            config.base_rate_aed, drg_entry.relative_weight
        )

        addon_hcpcs = Decimal("0")
        addon_info = self._find_service_activity(encounter, "98")
        if addon_info:
            _, addon_act = addon_info
            addon_hcpcs = addon_act.net

        reported_outlier = outlier_activity.net
        reported_from_model = encounter.reported.outlier_payment
        if reported_from_model is not None:
            reported_outlier = reported_from_model

        total_cost = self._compute_cost_basis(encounter)
        threshold = base_payment + config.gap_aed + addon_hcpcs

        if total_cost <= threshold:
            if reported_outlier > Decimal("0"):
                report.add(
                    RuleResult(
                        rule_id="OL-002",
                        engine=self.name,
                        severity=Severity.ERROR,
                        message=(
                            f"Outlier payment reported (AED {reported_outlier}) but "
                            f"cost (AED {total_cost}) does not exceed threshold "
                            f"(AED {threshold})"
                        ),
                        field=f"encounters[{enc_idx}].activities[{act_idx}].net",
                        expected="0",
                        actual=str(reported_outlier),
                        encounter_index=enc_idx,
                        activity_index=act_idx,
                    )
                )
            return

        calculated_outlier = (
            (total_cost - threshold) * config.marginal_pct
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        if reported_outlier.quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        ) != calculated_outlier:
            report.add(
                RuleResult(
                    rule_id="OL-003",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message=(
                        f"Outlier payment mismatch. "
                        f"Reported: AED {reported_outlier}, "
                        f"Calculated: AED {calculated_outlier}. "
                        f"Formula: (Cost {total_cost} - "
                        f"(Base {base_payment} + Gap {config.gap_aed} + "
                        f"HCPCS {addon_hcpcs})) * Marginal {config.marginal_pct}"
                    ),
                    field=f"encounters[{enc_idx}].activities[{act_idx}].net",
                    expected=str(calculated_outlier),
                    actual=str(reported_outlier),
                    encounter_index=enc_idx,
                    activity_index=act_idx,
                )
            )

    def _compute_cost_basis(self, encounter: Encounter) -> Decimal:
        """Sum the cost basis from ActivityCost observations.

        Per section 4.4.2.2.2: cost uses Mandatory Tariff prices plus
        actual incurred HCPCS and drug cost. We read from ActivityCost
        observations when present, falling back to summing activity nets.
        """
        total = Decimal("0")
        has_cost_obs = False
        for activity in encounter.activities:
            for obs in activity.observations:
                if obs.type == "ActivityCost" and obs.value:
                    total += Decimal(obs.value)
                    has_cost_obs = True

        if has_cost_obs:
            return total

        for activity in encounter.activities:
            if activity.type == _SRVC_CODE_TYPE and activity.code in (
                "99",
                "99-01",
                "99-02",
                "99-03",
            ):
                continue
            for obs in activity.observations:
                if obs.type == "ActivityCost" and obs.value:
                    total += Decimal(obs.value)
                    break
            else:
                total += activity.net

        return total
