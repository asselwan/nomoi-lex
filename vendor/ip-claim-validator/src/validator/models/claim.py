"""Claim data models for UAE DoH inpatient pre-submission validation.

The model separates what the provider reported from what the engine
calculates. The whole tool's value is the diff between those two.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class Observation(BaseModel):
    """Observation attached to an activity or encounter."""

    type: str
    code: str
    value: str = ""
    value_type: str = ""


class Activity(BaseModel):
    """Single line item on a claim encounter."""

    id: str
    start: datetime
    type: int = Field(
        description=(
            "3=CPT, 4=HCPCS, 5=Drug, 6=Dental, 8=Service Code, 9=DRG, "
            "10=Scientific Code"
        ),
    )
    code: str
    quantity: Decimal = Decimal("1")
    net: Decimal = Decimal("0")
    clinician: str = ""
    ordering_clinician: str = ""
    prior_authorization_id: str = ""
    observations: list[Observation] = Field(default_factory=list)


class Diagnosis(BaseModel):
    """Diagnosis entry on an encounter."""

    type: Literal["Principal", "Secondary", "Admitting"]
    code: str = Field(description="ICD-10-CM code")
    poa: Literal["Y", "N", "U", "W", "1", ""] = Field(
        default="",
        description="Present on Admission indicator",
    )


class SplitPayerInfo(BaseModel):
    """Information for split DRG payment across multiple payers (section 4.4.2.2.5)."""

    payer_1_days: int = Field(description="X: days covered by Payer 1")
    total_days: int = Field(description="Y: total encounter days")
    payer_1_id: str = ""
    payer_2_id: str = ""
    is_newborn_extension: bool = False


class ReportedValues(BaseModel):
    """Values reported by the provider on the claim. The engine compares
    these against its own calculations to surface discrepancies."""

    drg_code: str | None = None
    drg_base_payment: Decimal | None = None
    outlier_payment: Decimal | None = None
    lama_payment: Decimal | None = None
    cahms_adjustor: Decimal | None = None
    total_claim_net: Decimal | None = None


class Encounter(BaseModel):
    """Single encounter within a claim."""

    facility_id: str = ""
    type: int = Field(
        description=(
            "3=Inpatient Bed no ER, 4=Inpatient Bed with ER"
        ),
    )
    patient_id: str = ""
    start: datetime
    end: datetime | None = None
    start_type: int = 0
    end_type: int = Field(
        default=0,
        description=(
            "Discharge disposition. "
            "2=Discharged against advice (LAMA), "
            "3=Absent without leave"
        ),
    )
    transfer_source: str = ""
    transfer_destination: str = ""
    patient_age_years: int | None = None
    patient_gender: Literal["M", "F", ""] = ""
    patient_date_of_birth: date | None = None
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    activities: list[Activity] = Field(default_factory=list)
    reported: ReportedValues = Field(default_factory=ReportedValues)
    regrouped_drg: str | None = Field(
        default=None,
        description=(
            "Optional DRG code after HAC-driven regrouping. "
            "Supplied by provider or RCM team from their grouper run."
        ),
    )
    split_payer: SplitPayerInfo | None = None
    actual_los: Decimal | None = Field(
        default=None,
        description="Actual length of stay in days, rounded to 2 decimals for LAMA.",
    )


class ContractConfig(BaseModel):
    """Payer contract parameters that drive payment calculations."""

    product_name: str = "Basic"
    base_rate_aed: Decimal = Decimal("8500")
    gap_aed: Decimal = Decimal("25000")
    marginal_pct: Decimal = Decimal("0.60")
    lama_mode: Literal["advisory", "strict"] = "advisory"
    alos_file: str | None = None
    mental_health_drg_file: str | None = None
    pcat_file: str | None = None


class Claim(BaseModel):
    """Top-level claim model for pre-submission validation."""

    id: str
    id_payer: str = ""
    member_id: str = ""
    payer_id: str = ""
    provider_id: str = ""
    emirates_id: str = ""
    gross: Decimal = Decimal("0")
    patient_share: Decimal = Decimal("0")
    net: Decimal = Decimal("0")
    encounters: list[Encounter] = Field(default_factory=list)
    contract: ContractConfig = Field(default_factory=ContractConfig)
