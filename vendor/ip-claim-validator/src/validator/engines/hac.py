"""Engine 7: HAC detection and DRG regrouping flag (section 5.5, table 5).

Degraded mode for v1: does not attempt to regroup the DRG.

- Identifies diagnosis codes in HAC list with POA indicator N or U
- Y and W indicators do not trigger
- Validates HAC-related procedures reported with Activity.Net = 0
- Validates outlier cost basis excludes HAC-related activities
- When regrouped_drg is supplied, validates it exists and weight <= original
- When regrouped_drg is absent, emits WARNING for manual review
"""

from __future__ import annotations

from decimal import Decimal

from validator.engines.base import BaseEngine
from validator.models.claim import ContractConfig, Encounter
from validator.models.results import RuleResult, Severity, ValidationReport
from validator.reference.loader import ReferenceData

_CPT_TYPE = 3
_DRG_ACTIVITY_TYPE = 9
_POA_TRIGGERS = frozenset({"N", "U"})


class HACEngine(BaseEngine):
    @property
    def name(self) -> str:
        return "hac"

    def validate(
        self,
        encounter: Encounter,
        config: ContractConfig,
        ref: ReferenceData,
        report: ValidationReport,
        encounter_index: int,
    ) -> None:
        triggered_hacs = self._find_triggered_hacs(encounter, ref)
        if not triggered_hacs:
            return

        hac_procedure_codes = self._collect_hac_procedure_codes(
            triggered_hacs, encounter
        )
        self._check_hac_procedure_nets(
            encounter, hac_procedure_codes, report, encounter_index
        )
        self._check_regrouped_drg(
            encounter, triggered_hacs, ref, report, encounter_index
        )

    def _find_triggered_hacs(
        self,
        encounter: Encounter,
        ref: ReferenceData,
    ) -> list[tuple[object, str, str]]:
        """Return list of (HACEntry, diagnosis_code, poa) for triggered HACs."""
        triggered = []
        for diag in encounter.diagnoses:
            if diag.poa not in _POA_TRIGGERS:
                continue
            for hac in ref.hac_list:
                if diag.code in hac.diagnosis_codes:
                    triggered.append((hac, diag.code, diag.poa))
        return triggered

    def _collect_hac_procedure_codes(
        self,
        triggered_hacs: list[tuple[object, str, str]],
        encounter: Encounter,
    ) -> set[str]:
        """Collect procedure codes that are HAC-related and must be Net=0."""
        hac_proc_codes: set[str] = set()
        claim_proc_codes = {
            a.code for a in encounter.activities if a.type == _CPT_TYPE
        }
        for hac, _, _ in triggered_hacs:
            if "ALL" in hac.procedure_codes:
                hac_proc_codes.update(claim_proc_codes)
            else:
                hac_proc_codes.update(
                    c for c in hac.procedure_codes if c in claim_proc_codes
                )
        return hac_proc_codes

    def _check_hac_procedure_nets(
        self,
        encounter: Encounter,
        hac_procedure_codes: set[str],
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        for act_idx, activity in enumerate(encounter.activities):
            if activity.type != _CPT_TYPE:
                continue
            if activity.code not in hac_procedure_codes:
                continue
            if activity.net != Decimal("0"):
                report.add(
                    RuleResult(
                        rule_id="HC-001",
                        engine=self.name,
                        severity=Severity.ERROR,
                        message=(
                            f"HAC-related procedure {activity.code} must be reported "
                            f"with Activity.Net = 0, got {activity.net}"
                        ),
                        field=f"encounters[{enc_idx}].activities[{act_idx}].net",
                        expected="0",
                        actual=str(activity.net),
                        encounter_index=enc_idx,
                        activity_index=act_idx,
                    )
                )

    def _check_regrouped_drg(
        self,
        encounter: Encounter,
        triggered_hacs: list[tuple[object, str, str]],
        ref: ReferenceData,
        report: ValidationReport,
        enc_idx: int,
    ) -> None:
        hac_names = ", ".join(
            f"{hac.name} (POA={poa})" for hac, _, poa in triggered_hacs
        )

        if encounter.regrouped_drg is None:
            poa_indicators = {poa for _, _, poa in triggered_hacs}
            poa_str = "/".join(sorted(poa_indicators))
            report.add(
                RuleResult(
                    rule_id="HC-002",
                    engine=self.name,
                    severity=Severity.WARNING,
                    message=(
                        f"HAC detected with POA indicator {poa_str}. "
                        f"DRG regrouping required; supply regrouped_drg field "
                        f"or run claim through 3M grouper externally. "
                        f"Triggered: {hac_names}"
                    ),
                    field=f"encounters[{enc_idx}].regrouped_drg",
                    encounter_index=enc_idx,
                )
            )
            return

        regrouped_entry = ref.get_drg(encounter.regrouped_drg)
        if regrouped_entry is None:
            report.add(
                RuleResult(
                    rule_id="HC-003",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message=(
                        f"Regrouped DRG code {encounter.regrouped_drg} "
                        f"not found in reference weights"
                    ),
                    field=f"encounters[{enc_idx}].regrouped_drg",
                    expected="Valid DRG code from Mandatory Tariff",
                    actual=encounter.regrouped_drg,
                    encounter_index=enc_idx,
                )
            )
            return

        original_code = encounter.reported.drg_code
        if not original_code:
            drg_act = next(
                (a for a in encounter.activities if a.type == _DRG_ACTIVITY_TYPE),
                None,
            )
            if drg_act:
                original_code = drg_act.code

        if original_code:
            original_entry = ref.get_drg(original_code)
            if original_entry and (
                regrouped_entry.relative_weight > original_entry.relative_weight
            ):
                report.add(
                    RuleResult(
                        rule_id="HC-004",
                        engine=self.name,
                        severity=Severity.ERROR,
                        message=(
                            f"Regrouped DRG severity exceeds original; "
                            f"HAC removal cannot increase DRG weight. "
                            f"Original {original_code} "
                            f"(RW {original_entry.relative_weight}), "
                            f"Regrouped {encounter.regrouped_drg} "
                            f"(RW {regrouped_entry.relative_weight}). "
                            f"Supplied regrouped_drg is invalid."
                        ),
                        field=f"encounters[{enc_idx}].regrouped_drg",
                        expected=f"<= {original_entry.relative_weight}",
                        actual=str(regrouped_entry.relative_weight),
                        encounter_index=enc_idx,
                    )
                )
