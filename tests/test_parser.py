"""Tests for lex.parser — flat CSV → Claim object construction (Cerner schema)."""

from decimal import Decimal

import pandas as pd
import pytest

from lex.parser import (
    ColumnNotFoundError,
    load_mapping,
    parse_dataframe,
)


@pytest.fixture
def mapping():
    return load_mapping()


def _make_row(**overrides) -> dict:
    """Minimal valid row with required Cerner columns for one charge line."""
    base = {
        "FIN": "FIN-001",
        "CLAIM_ID": "",
        "INSURANCE_ID": "INS-100",
        "HEALTH_PLAN": "HP-01",
        "BE": "SSMC",
        "CLAIM_GROSS": "10000.00",
        "PATIENT_SHARE": "500.00",
        "ENCOUNTER_TYPE": "Inpatient",
        "LOCATION": "LOC-01",
        "MRN": "MRN-00000001",
        "FIN_CLASS": "Basic",
        "ADMIT_DATE": "2025-01-15 09:00:00",
        "DISCHARGE_DATE": "2025-01-20 14:00:00",
        "DISCHARGE_DISPOSITION": "Discharged",
        "DRG_CODE": "011011",
        "DRG_BASE_PAYMENT": "8500.00",
        "CHARGE_ITEM_ID": "CHG-001",
        "CHARGE_UPDATE_DT_TM": "2025-01-15 10:00:00",
        "ACTIVITY_TYPE": "CPT",
        "CPT_CODE": "99213",
        "QUANTITY": "1",
        "ACTIVITY_NET_AMT": "500.00",
        "PERFORMING_PHYS_USERNAME": "DOC-123",
        "DIAGNOSIS": "J18.9",
    }
    base.update(overrides)
    return base


def _df_from_rows(*rows) -> pd.DataFrame:
    return pd.DataFrame(rows, dtype=str)


class TestSingleClaimHappyPath:
    def test_parses_single_row_into_one_claim(self, mapping):
        df = _df_from_rows(_make_row())
        claims = parse_dataframe(df, mapping)

        assert len(claims) == 1
        c = claims[0]
        assert c.id == "FIN-001"
        assert c.member_id == "INS-100"
        assert c.emirates_id == ""
        assert c.gross == Decimal("10000.00")
        assert c.net == Decimal("10000.00")  # net maps to CLAIM_GROSS

    def test_encounter_fields(self, mapping):
        df = _df_from_rows(_make_row())
        claims = parse_dataframe(df, mapping)
        enc = claims[0].encounters[0]

        assert enc.type == 3
        assert enc.start.day == 15
        assert enc.start.month == 1
        assert enc.end.day == 20
        assert enc.patient_id == "MRN-00000001"
        # Demographics not in default Cerner export
        assert enc.patient_age_years is None
        assert enc.patient_gender == ""
        assert enc.patient_date_of_birth is None

    def test_activity_fields(self, mapping):
        df = _df_from_rows(_make_row())
        claims = parse_dataframe(df, mapping)
        act = claims[0].encounters[0].activities[0]

        assert act.id == "CHG-001"
        assert act.type == 3  # CPT
        assert act.code == "99213"
        assert act.net == Decimal("500.00")
        assert act.clinician == "DOC-123"

    def test_principal_diagnosis(self, mapping):
        df = _df_from_rows(_make_row())
        claims = parse_dataframe(df, mapping)
        dxs = claims[0].encounters[0].diagnoses

        assert len(dxs) >= 1
        principal = [d for d in dxs if d.type == "Principal"]
        assert len(principal) == 1
        assert principal[0].code == "J18.9"

    def test_reported_values(self, mapping):
        df = _df_from_rows(_make_row())
        claims = parse_dataframe(df, mapping)
        rep = claims[0].encounters[0].reported

        assert rep.drg_code == "011011"
        assert rep.drg_base_payment == Decimal("8500.00")


class TestMultiEncounterGrouping:
    def test_two_activities_same_encounter(self, mapping):
        row1 = _make_row(
            **{"CHARGE_ITEM_ID": "CHG-001", "CPT_CODE": "99213"}
        )
        row2 = _make_row(
            **{"CHARGE_ITEM_ID": "CHG-002", "CPT_CODE": "99214"}
        )
        df = _df_from_rows(row1, row2)
        claims = parse_dataframe(df, mapping)

        assert len(claims) == 1
        assert len(claims[0].encounters[0].activities) == 2

    def test_two_claims(self, mapping):
        row1 = _make_row(**{"FIN": "FIN-001"})
        row2 = _make_row(**{"FIN": "FIN-002"})
        df = _df_from_rows(row1, row2)
        claims = parse_dataframe(df, mapping)

        assert len(claims) == 2
        assert claims[0].id == "FIN-001"
        assert claims[1].id == "FIN-002"


class TestValueMapEnumResolution:
    def test_encounter_type_string_to_int(self, mapping):
        for label, expected in [("Inpatient", 3), ("Inpatient With ER", 4), ("IP-ER", 4)]:
            df = _df_from_rows(_make_row(**{"ENCOUNTER_TYPE": label}))
            claims = parse_dataframe(df, mapping)
            assert claims[0].encounters[0].type == expected, f"Failed for {label}"

    def test_encounter_type_numeric_string(self, mapping):
        df = _df_from_rows(_make_row(**{"ENCOUNTER_TYPE": "4"}))
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].type == 4

    def test_activity_type_string_to_int(self, mapping):
        for label, expected in [("CPT", 3), ("HCPCS", 4), ("Drug", 5), ("DRG", 9)]:
            df = _df_from_rows(_make_row(**{"ACTIVITY_TYPE": label}))
            claims = parse_dataframe(df, mapping)
            assert claims[0].encounters[0].activities[0].type == expected

    def test_gender_map(self, mapping):
        df = _df_from_rows(_make_row(**{"PATIENT_GENDER": "Female"}))
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].patient_gender == "F"

    def test_discharge_disposition_lama(self, mapping):
        df = _df_from_rows(_make_row(**{"DISCHARGE_DISPOSITION": "LAMA"}))
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].end_type == 2


class TestCernerValueMaps:
    def test_emergency_inpatient(self, mapping):
        df = _df_from_rows(_make_row(**{"ENCOUNTER_TYPE": "Emergency Inpatient"}))
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].type == 4

    def test_against_medical_advice(self, mapping):
        df = _df_from_rows(_make_row(**{"DISCHARGE_DISPOSITION": "Against Medical Advice"}))
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].end_type == 2

    def test_pharmacy_activity_type(self, mapping):
        row = _make_row(**{
            "ACTIVITY_TYPE": "Pharmacy",
            "CDMSCHEDPHARM_CODE": "MED-001",
            "CPT_CODE": "",
        })
        df = _df_from_rows(row)
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].activities[0].type == 5


class TestActivityCodeCoalesce:
    def test_matching_type_uses_correct_column(self, mapping):
        """CPT activity type -> CPT_CODE column."""
        row = _make_row(**{
            "ACTIVITY_TYPE": "CPT",
            "CPT_CODE": "99213",
            "HCPCS": "G0105",
        })
        df = _df_from_rows(row)
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].activities[0].code == "99213"

    def test_unmatched_type_falls_through_to_fallback(self, mapping):
        """Type not in columns_by_activity_type -> fallback columns in order."""
        row = _make_row(**{
            "ACTIVITY_TYPE": "10",  # Scientific Code, not in columns_by_activity_type
            "CPT_CODE": "",
            "HCPCS": "",
            "CDMSCHEDPHARM_CODE": "",
            "CDM_CODE": "SC-001",
            "DRG_CODE": "",
        })
        df = _df_from_rows(row)
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].activities[0].code == "SC-001"

    def test_all_null_returns_empty(self, mapping):
        """All code columns empty -> empty string."""
        row = _make_row(**{
            "ACTIVITY_TYPE": "CPT",
            "CPT_CODE": "",
            "DRG_CODE": "",
        })
        df = _df_from_rows(row)
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].activities[0].code == ""


class TestLongFormatDiagnosis:
    def test_collects_unique_codes_across_rows(self, mapping):
        """Multiple rows with different diagnoses -> principal + secondary."""
        row1 = _make_row(**{
            "CHARGE_ITEM_ID": "CHG-001",
            "DIAGNOSIS": "J18.9",
        })
        row2 = _make_row(**{
            "CHARGE_ITEM_ID": "CHG-002",
            "DIAGNOSIS": "E11.9",
            "CPT_CODE": "99214",
        })
        row3 = _make_row(**{
            "CHARGE_ITEM_ID": "CHG-003",
            "DIAGNOSIS": "J18.9",  # duplicate — should be ignored
            "ACTIVITY_TYPE": "Drug",
            "CDMSCHEDPHARM_CODE": "MED-001",
            "CPT_CODE": "",
        })
        df = _df_from_rows(row1, row2, row3)
        claims = parse_dataframe(df, mapping)
        dxs = claims[0].encounters[0].diagnoses

        assert len(dxs) == 2
        assert dxs[0].type == "Principal"
        assert dxs[0].code == "J18.9"
        assert dxs[1].type == "Secondary"
        assert dxs[1].code == "E11.9"

    def test_poa_captured_when_column_present(self, mapping):
        """When DX_POA column is present, POA value flows through."""
        row = _make_row(**{"DIAGNOSIS": "J18.9", "DX_POA": "Y"})
        df = _df_from_rows(row)
        claims = parse_dataframe(df, mapping)
        dxs = claims[0].encounters[0].diagnoses

        assert len(dxs) == 1
        assert dxs[0].poa == "Y"


class TestComputedActualLOS:
    def test_computed_los_from_dates(self, mapping):
        """LOS computed as fractional days between ADMIT_DATE and DISCHARGE_DATE."""
        df = _df_from_rows(_make_row(**{
            "ADMIT_DATE": "2025-01-15 09:00:00",
            "DISCHARGE_DATE": "2025-01-20 14:00:00",
        }))
        claims = parse_dataframe(df, mapping)
        enc = claims[0].encounters[0]

        # 5 days and 5 hours = 5.21 days (rounded to 2 decimals)
        assert enc.actual_los == Decimal("5.21")


class TestMissingOptionalFieldsUseDefaults:
    def test_missing_contract_fields(self, mapping):
        row = _make_row()
        df = _df_from_rows(row)
        claims = parse_dataframe(df, mapping)

        contract = claims[0].contract
        assert contract.base_rate_aed == Decimal("8500")
        assert contract.product_name == "Basic"
        assert contract.lama_mode == "advisory"

    def test_missing_demographics(self, mapping):
        """Patient demographics not in default Cerner export -> None/empty."""
        df = _df_from_rows(_make_row())
        claims = parse_dataframe(df, mapping)
        enc = claims[0].encounters[0]

        assert enc.patient_age_years is None
        assert enc.patient_gender == ""
        assert enc.patient_date_of_birth is None

    def test_missing_split_payer(self, mapping):
        df = _df_from_rows(_make_row())
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].split_payer is None


class TestObservations:
    def test_modifier_pattern(self, mapping):
        row = _make_row(**{
            "MODIFIER_1": "26",
            "MODIFIER_2": "TC",
        })
        df = _df_from_rows(row)
        claims = parse_dataframe(df, mapping)
        obs = claims[0].encounters[0].activities[0].observations

        modifiers = [o for o in obs if o.type == "Modifier"]
        assert len(modifiers) == 2
        assert modifiers[0].code == "26"
        assert modifiers[1].code == "TC"


class TestMissingRequiredColumn:
    def test_missing_fin_raises(self, mapping):
        row = _make_row()
        del row["FIN"]
        df = _df_from_rows(row)

        with pytest.raises(ColumnNotFoundError, match="FIN"):
            parse_dataframe(df, mapping)


class TestMRNFlowThrough:
    def test_mrn_maps_to_encounter_patient_id(self, mapping):
        df = _df_from_rows(_make_row(**{"MRN": "MRN-00000042"}))
        claims = parse_dataframe(df, mapping)
        enc = claims[0].encounters[0]
        assert enc.patient_id == "MRN-00000042"
