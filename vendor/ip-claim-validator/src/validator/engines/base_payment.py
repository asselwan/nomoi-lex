"""Engine 3: IR-DRG base payment validation (section 4.4.2.2.1).

Base payment = Base Rate * Relative Weight
- Relative weight rounded to 4 decimals
- Payment rounded to whole AED (no decimals)
- Basic Product: Base Rate AED 8,500
- Other products: read from ContractConfig
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from validator.engines.base import BaseEngine
from validator.models.claim import ContractConfig, Encounter
from validator.models.results import RuleResult, Severity, ValidationReport
from validator.reference.loader import ReferenceData

_DRG_ACTIVITY_TYPE = 9


def calculate_base_payment(base_rate: Decimal, relative_weight: float) -> Decimal:
    """Calculate base payment per section 4.4.2.2.1.

    RW rounded to 4 decimals, payment rounded to whole AED.
    """
    rw = Decimal(str(relative_weight)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    raw = base_rate * rw
    return raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


class BasePaymentEngine(BaseEngine):
    @property
    def name(self) -> str:
        return "base_payment"

    def validate(
        self,
        encounter: Encounter,
        config: ContractConfig,
        ref: ReferenceData,
        report: ValidationReport,
        encounter_index: int,
    ) -> None:
        drg_code = encounter.reported.drg_code
        if not drg_code:
            drg_activity = next(
                (a for a in encounter.activities if a.type == _DRG_ACTIVITY_TYPE),
                None,
            )
            if drg_activity:
                drg_code = drg_activity.code
            else:
                return

        drg_entry = ref.get_drg(drg_code)
        if drg_entry is None:
            report.add(
                RuleResult(
                    rule_id="BP-001",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message=f"DRG code {drg_code} not found in reference weights",
                    field=f"encounters[{encounter_index}].reported.drg_code",
                    expected="Valid DRG code from Mandatory Tariff",
                    actual=drg_code,
                    encounter_index=encounter_index,
                )
            )
            return

        calculated = calculate_base_payment(
            config.base_rate_aed, drg_entry.relative_weight
        )

        reported = encounter.reported.drg_base_payment
        if reported is None:
            drg_activity = next(
                (a for a in encounter.activities if a.type == _DRG_ACTIVITY_TYPE),
                None,
            )
            if drg_activity:
                reported = drg_activity.net

        if reported is None:
            report.add(
                RuleResult(
                    rule_id="BP-002",
                    engine=self.name,
                    severity=Severity.WARNING,
                    message=(
                        f"No reported base payment found. "
                        f"Calculated: AED {calculated}"
                    ),
                    field=f"encounters[{encounter_index}].reported.drg_base_payment",
                    expected=str(calculated),
                    encounter_index=encounter_index,
                )
            )
            return

        reported_rounded = reported.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if reported_rounded != calculated:
            report.add(
                RuleResult(
                    rule_id="BP-003",
                    engine=self.name,
                    severity=Severity.ERROR,
                    message=(
                        f"Base payment mismatch. "
                        f"Reported: AED {reported}, "
                        f"Calculated: AED {calculated} "
                        f"(Base Rate {config.base_rate_aed} * "
                        f"RW {drg_entry.relative_weight})"
                    ),
                    field=f"encounters[{encounter_index}].reported.drg_base_payment",
                    expected=str(calculated),
                    actual=str(reported),
                    encounter_index=encounter_index,
                )
            )
