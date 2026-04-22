"""Engine 6: LAMA payment validation (section 4.4.2.2.4).

Always-on rules (both modes):
- SRVC 99-01 required for EncounterEndType 2
- Actual LOS rounded to 2 decimals
- DRG activity Net = 0
- EncounterEndType 3: all activities including DRG are Net = 0
- EndType 2 and 3: no outlier or add-on payments
- Cap check: LAMA payment <= Base Rate * RW

Strict mode (requires alos_trim_points.yaml):
- LAMA Payment = Actual LOS * (DRG RW / DRG ALOS) * Base Rate

Advisory mode (default v1):
- Skips formula validation, emits INFO
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from validator.engines.base import BaseEngine
from validator.models.claim import ContractConfig, Encounter
from validator.models.results import RuleResult, Severity, ValidationReport
from validator.reference.loader import ReferenceData

_SRVC_CODE_TYPE = 8
_DRG_ACTIVITY_TYPE = 9
_LAMA_END_TYPE = 2
_AWOL_END_TYPE = 3


class LAMAEngine(BaseEngine):
    @property
    def name(self) -> str:
        return "lama"

    def validate(
        self,
        encounter: Encounter,
        config: ContractConfig,
        ref: ReferenceData,
        report: ValidationReport,
        encounter_index: int,
    ) -> None:
        is_lama = encounter.end_type == _LAMA_END_TYPE
        is_awol = encounter.end_type == _AWOL_END_TYPE

        if not is_lama and not is_awol:
            self._check_spurious_lama_code(encounter, report, encounter_index)
            return

        if is_lama:
            self._check_lama_code_present(encounter, report, encounter_index)

        self._check_drg_net_zero(encounter, report, encounter_index)

        if is_awol:
            self._check_awol_all_zero(encounter, report, encounter_index)

        self._check_no_outlier_or_addon(encounter, report, encounter_index)

        if is_lama:
            self._check_actual_los_precision(encounter, report, encounter_index)
            self._check_cap(encounter, config, ref, report, encounter_index)

            if config.lama_mode == "strict" and config.alos_file:
                report.add(
                    RuleResult(
                        rule_id="LA-010",
                        engine=self.name,
                        severity=Severity.INFO,
                        message=(
                            "ALOS file configured but strict LAMA formula "
                            "validation not yet implemented"
                        ),
                        encounter_index=encounter_index,
                    )
                )
            else:
                report.add(
                    RuleResult(
                        rule_id="LA-011",
                        engine=self.name,
                        severity=Severity.INFO,
                        message=(
                            "LAMA formula check deferred pending ALOS reference "
                            "table. Cap check and submission shape rules applied."
                        ),
                        encounter_index=encounter_index,
                    )
                )

    def _check_spurious_lama_code(
        self,
        encounter: Encounter,
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        for act_idx, a in enumerate(encounter.activities):
            if a.type == _SRVC_CODE_TYPE and a.code == "99-01":
                report.add(
                    RuleResult(
                        rule_id="LA-001",
                        engine=self.name,
                        severity=Severity.ERROR,
                        message=(
                            "SRVC 99-01 (LAMA) present but EncounterEndType "
                            f"is {encounter.end_type}, not 2 (discharged against advice)"
                        ),
                        field=f"encounters[{enc_idx}].activities[{act_idx}]",
                        encounter_index=enc_idx,
                        activity_index=act_idx,
                    )
                )

    def _check_lama_code_present(
        self,
        encounter: Encounter,
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        has_lama = any(
            a.type == _SRVC_CODE_TYPE and a.code == "99-01"
            for a in encounter.activities
        )
        if not has_lama:
            report.add(
                RuleResult(
                    rule_id="LA-002",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message=(
                        "EncounterEndType is 2 (LAMA) but SRVC 99-01 activity missing"
                    ),
                    field=f"encounters[{enc_idx}].activities",
                    encounter_index=enc_idx,
                )
            )

    def _check_drg_net_zero(
        self,
        encounter: Encounter,
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        for act_idx, a in enumerate(encounter.activities):
            if a.type == _DRG_ACTIVITY_TYPE and a.net != Decimal("0"):
                report.add(
                    RuleResult(
                        rule_id="LA-003",
                        engine=self.name,
                        severity=Severity.ERROR,
                        message=(
                            f"DRG activity must have Net=0 for LAMA/AWOL encounters, "
                            f"got {a.net}"
                        ),
                        field=f"encounters[{enc_idx}].activities[{act_idx}].net",
                        expected="0",
                        actual=str(a.net),
                        encounter_index=enc_idx,
                        activity_index=act_idx,
                    )
                )

    def _check_awol_all_zero(
        self,
        encounter: Encounter,
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        for act_idx, a in enumerate(encounter.activities):
            if a.net != Decimal("0"):
                report.add(
                    RuleResult(
                        rule_id="LA-004",
                        engine=self.name,
                        severity=Severity.ERROR,
                        message=(
                            f"EncounterEndType 3 (absent without leave): all activities "
                            f"must have Net=0. Activity {a.code} has Net={a.net}"
                        ),
                        field=f"encounters[{enc_idx}].activities[{act_idx}].net",
                        expected="0",
                        actual=str(a.net),
                        encounter_index=enc_idx,
                        activity_index=act_idx,
                    )
                )

    def _check_no_outlier_or_addon(
        self,
        encounter: Encounter,
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        blocked_codes = {"99", "98", "99-02", "99-03"}
        for act_idx, a in enumerate(encounter.activities):
            if a.type == _SRVC_CODE_TYPE and a.code in blocked_codes:
                if a.net != Decimal("0"):
                    report.add(
                        RuleResult(
                            rule_id="LA-005",
                            engine=self.name,
                            severity=Severity.ERROR,
                            message=(
                                f"Outlier/add-on (SRVC {a.code}) not applicable for "
                                f"LAMA/AWOL encounters (EndType {encounter.end_type})"
                            ),
                            field=f"encounters[{enc_idx}].activities[{act_idx}].net",
                            encounter_index=enc_idx,
                            activity_index=act_idx,
                        )
                    )

    def _check_actual_los_precision(
        self,
        encounter: Encounter,
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        if encounter.actual_los is None:
            report.add(
                RuleResult(
                    rule_id="LA-006",
                    engine=self.name,
                    severity=Severity.WARNING,
                    message="Actual LOS not provided for LAMA encounter",
                    field=f"encounters[{enc_idx}].actual_los",
                    encounter_index=enc_idx,
                )
            )
            return

        rounded = encounter.actual_los.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if encounter.actual_los != rounded:
            report.add(
                RuleResult(
                    rule_id="LA-007",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message=(
                        f"Actual LOS must be rounded to 2 decimal places. "
                        f"Got {encounter.actual_los}, expected {rounded}"
                    ),
                    field=f"encounters[{enc_idx}].actual_los",
                    expected=str(rounded),
                    actual=str(encounter.actual_los),
                    encounter_index=enc_idx,
                )
            )

    def _check_cap(
        self,
        encounter: Encounter,
        config: ContractConfig,
        ref: ReferenceData,
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        lama_activity = next(
            (
                (i, a)
                for i, a in enumerate(encounter.activities)
                if a.type == _SRVC_CODE_TYPE and a.code == "99-01"
            ),
            None,
        )
        if lama_activity is None:
            return

        act_idx, lama_act = lama_activity
        reported_lama = lama_act.net
        if encounter.reported.lama_payment is not None:
            reported_lama = encounter.reported.lama_payment

        drg_code = encounter.reported.drg_code
        if not drg_code:
            drg_act = next(
                (a for a in encounter.activities if a.type == _DRG_ACTIVITY_TYPE),
                None,
            )
            if drg_act:
                drg_code = drg_act.code

        if not drg_code:
            return

        drg_entry = ref.get_drg(drg_code)
        if drg_entry is None:
            return

        rw = Decimal(str(drg_entry.relative_weight)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        cap = (config.base_rate_aed * rw).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )

        if reported_lama > cap:
            report.add(
                RuleResult(
                    rule_id="LA-008",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message=(
                        f"LAMA payment (AED {reported_lama}) exceeds DRG cap "
                        f"(AED {cap} = Base Rate {config.base_rate_aed} * RW {rw})"
                    ),
                    field=f"encounters[{enc_idx}].activities[{act_idx}].net",
                    expected=f"<= {cap}",
                    actual=str(reported_lama),
                    encounter_index=enc_idx,
                    activity_index=act_idx,
                )
            )
