"""Engine 8: Split DRG payment for multi-payer encounters (section 4.4.2.2.5).

Inpatient encounter spanning policy expiry:
- Medical case (IM): Payer 1 pays full DRG regardless of LOS
- Surgical case (IP): X/Y formula
  Payer 1 = Total DRG Payment * (X/Y) + ((1 - X/Y) * Total DRG Payment * 30%)
  Payer 2 = Total DRG Payment - Payer 1

Newborn extending beyond mother's coverage:
  Separate billing using mother's insurance details but newborn's member ID.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from validator.engines.base import BaseEngine
from validator.engines.base_payment import calculate_base_payment
from validator.models.claim import ContractConfig, Encounter
from validator.models.results import RuleResult, Severity, ValidationReport
from validator.reference.loader import ReferenceData

_DRG_ACTIVITY_TYPE = 9


class SplitDRGEngine(BaseEngine):
    @property
    def name(self) -> str:
        return "split_drg"

    def validate(
        self,
        encounter: Encounter,
        config: ContractConfig,
        ref: ReferenceData,
        report: ValidationReport,
        encounter_index: int,
    ) -> None:
        if encounter.split_payer is None:
            return

        sp = encounter.split_payer

        if sp.is_newborn_extension:
            report.add(
                RuleResult(
                    rule_id="SD-001",
                    engine=self.name,
                    severity=Severity.INFO,
                    message=(
                        "Newborn coverage extension: bill separately using "
                        "mother's insurance details but newborn's member ID"
                    ),
                    encounter_index=encounter_index,
                )
            )
            return

        if sp.total_days <= 0:
            report.add(
                RuleResult(
                    rule_id="SD-002",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message="Split payer total_days (Y) must be > 0",
                    field=f"encounters[{encounter_index}].split_payer.total_days",
                    encounter_index=encounter_index,
                )
            )
            return

        if sp.payer_1_days < 0 or sp.payer_1_days > sp.total_days:
            report.add(
                RuleResult(
                    rule_id="SD-003",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message=(
                        f"payer_1_days (X={sp.payer_1_days}) must be between "
                        f"0 and total_days (Y={sp.total_days})"
                    ),
                    field=f"encounters[{encounter_index}].split_payer.payer_1_days",
                    encounter_index=encounter_index,
                )
            )
            return

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

        is_surgical = drg_entry.category == "IP"
        is_medical = drg_entry.category == "IM"

        total_drg_payment = calculate_base_payment(
            config.base_rate_aed, drg_entry.relative_weight
        )

        if is_medical:
            report.add(
                RuleResult(
                    rule_id="SD-004",
                    engine=self.name,
                    severity=Severity.INFO,
                    message=(
                        f"Medical DRG ({drg_code}): Payer 1 responsible for "
                        f"full DRG payment (AED {total_drg_payment}) regardless of LOS"
                    ),
                    encounter_index=encounter_index,
                )
            )

        if is_surgical:
            x = Decimal(str(sp.payer_1_days))
            y = Decimal(str(sp.total_days))
            ratio = x / y

            payer_1_amount = (
                total_drg_payment * ratio
                + (Decimal("1") - ratio) * total_drg_payment * Decimal("0.30")
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

            payer_2_amount = total_drg_payment - payer_1_amount

            report.add(
                RuleResult(
                    rule_id="SD-005",
                    engine=self.name,
                    severity=Severity.INFO,
                    message=(
                        f"Surgical DRG ({drg_code}) split: "
                        f"X={sp.payer_1_days}, Y={sp.total_days}. "
                        f"Payer 1: AED {payer_1_amount}, "
                        f"Payer 2: AED {payer_2_amount}. "
                        f"Total: AED {total_drg_payment}"
                    ),
                    encounter_index=encounter_index,
                )
            )
