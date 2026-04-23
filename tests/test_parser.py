"""Tests for lex.parser — flat CSV → Claim object construction."""

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
    """Minimal valid row with all required columns for one activity."""
    base = {
        "FIN": "CLM-001",
        "Payer Claim Ref": "PAY-001",
        "Member ID": "MEM-100",
        "Payer Code": "INS-01",
        "Facility License": "FAC-555",
        "Emirates ID": "784-1990-1234567-1",
        "Gross Amount": "1000.00",
        "Patient Share": "100.00",
        "Net Amount": "900.00",
        "Encounter Type": "Inpatient",
        "Facility ID": "FAC-555",
        "MRN": "P-001",
        "Admission Date": "15/01/2025 09:00",
        "Discharge Date": "20/01/2025 14:00",
        "Discharge Status": "1",
        "Patient Age": "45",
        "Gender": "Male",
        "DOB": "10/03/1980",
        "DRG Code": "E66A",
        "DRG Base Payment": "8500.00",
        "Principal Dx": "J18.9",
        "Principal Dx POA": "Y",
        "Activity ID": "ACT-001",
        "Service Date": "15/01/2025 10:00",
        "Activity Type": "CPT",
        "Activity Code": "99213",
        "Qty": "1",
        "Activity Net": "500.00",
        "Clinician License": "DOC-123",
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
        assert c.id == "CLM-001"
        assert c.member_id == "MEM-100"
        assert c.emirates_id == "784-1990-1234567-1"
        assert c.gross == Decimal("1000.00")
        assert c.net == Decimal("900.00")

    def test_encounter_fields(self, mapping):
        df = _df_from_rows(_make_row())
        claims = parse_dataframe(df, mapping)
        enc = claims[0].encounters[0]

        assert enc.type == 3
        assert enc.start.day == 15
        assert enc.start.month == 1
        assert enc.end.day == 20
        assert enc.patient_age_years == 45
        assert enc.patient_gender == "M"
        assert enc.patient_date_of_birth is not None
        assert enc.patient_date_of_birth.year == 1980

    def test_activity_fields(self, mapping):
        df = _df_from_rows(_make_row())
        claims = parse_dataframe(df, mapping)
        act = claims[0].encounters[0].activities[0]

        assert act.id == "ACT-001"
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
        assert principal[0].poa == "Y"

    def test_reported_values(self, mapping):
        df = _df_from_rows(_make_row())
        claims = parse_dataframe(df, mapping)
        rep = claims[0].encounters[0].reported

        assert rep.drg_code == "E66A"
        assert rep.drg_base_payment == Decimal("8500.00")


class TestMultiEncounterGrouping:
    def test_two_activities_same_encounter(self, mapping):
        row1 = _make_row(
            **{"Activity ID": "ACT-001", "Activity Code": "99213"}
        )
        row2 = _make_row(
            **{"Activity ID": "ACT-002", "Activity Code": "99214"}
        )
        df = _df_from_rows(row1, row2)
        claims = parse_dataframe(df, mapping)

        assert len(claims) == 1
        assert len(claims[0].encounters[0].activities) == 2

    def test_two_claims(self, mapping):
        row1 = _make_row(**{"FIN": "CLM-001"})
        row2 = _make_row(**{"FIN": "CLM-002"})
        df = _df_from_rows(row1, row2)
        claims = parse_dataframe(df, mapping)

        assert len(claims) == 2
        assert claims[0].id == "CLM-001"
        assert claims[1].id == "CLM-002"


class TestValueMapEnumResolution:
    def test_encounter_type_string_to_int(self, mapping):
        for label, expected in [("Inpatient", 3), ("Inpatient With ER", 4), ("IP-ER", 4)]:
            df = _df_from_rows(_make_row(**{"Encounter Type": label}))
            claims = parse_dataframe(df, mapping)
            assert claims[0].encounters[0].type == expected, f"Failed for {label}"

    def test_encounter_type_numeric_string(self, mapping):
        df = _df_from_rows(_make_row(**{"Encounter Type": "4"}))
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].type == 4

    def test_activity_type_string_to_int(self, mapping):
        for label, expected in [("CPT", 3), ("HCPCS", 4), ("Drug", 5), ("DRG", 9)]:
            df = _df_from_rows(_make_row(**{"Activity Type": label}))
            claims = parse_dataframe(df, mapping)
            assert claims[0].encounters[0].activities[0].type == expected

    def test_gender_map(self, mapping):
        df = _df_from_rows(_make_row(**{"Gender": "Female"}))
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].patient_gender == "F"

    def test_discharge_status_lama(self, mapping):
        df = _df_from_rows(_make_row(**{"Discharge Status": "LAMA"}))
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].end_type == 2


class TestRepeatedDiagnosisColumns:
    def test_secondary_diagnoses(self, mapping):
        row = _make_row(**{
            "Secondary Dx 1": "E11.9",
            "Secondary Dx 1 POA": "N",
            "Secondary Dx 2": "I10",
            "Secondary Dx 2 POA": "Y",
        })
        df = _df_from_rows(row)
        claims = parse_dataframe(df, mapping)
        dxs = claims[0].encounters[0].diagnoses

        secondary = [d for d in dxs if d.type == "Secondary"]
        assert len(secondary) == 2
        assert secondary[0].code == "E11.9"
        assert secondary[0].poa == "N"
        assert secondary[1].code == "I10"

    def test_admitting_diagnosis(self, mapping):
        row = _make_row(**{"Admitting Dx": "R50.9", "Admitting Dx POA": "Y"})
        df = _df_from_rows(row)
        claims = parse_dataframe(df, mapping)
        dxs = claims[0].encounters[0].diagnoses

        admitting = [d for d in dxs if d.type == "Admitting"]
        assert len(admitting) == 1
        assert admitting[0].code == "R50.9"

    def test_no_admitting_when_absent(self, mapping):
        df = _df_from_rows(_make_row())
        claims = parse_dataframe(df, mapping)
        dxs = claims[0].encounters[0].diagnoses

        admitting = [d for d in dxs if d.type == "Admitting"]
        assert len(admitting) == 0


class TestMissingOptionalFieldsUseDefaults:
    def test_missing_contract_fields(self, mapping):
        row = _make_row()
        # Contract columns not present — defaults should apply
        df = _df_from_rows(row)
        claims = parse_dataframe(df, mapping)

        contract = claims[0].contract
        assert contract.base_rate_aed == Decimal("8500")
        assert contract.product_name == "Basic"
        assert contract.lama_mode == "advisory"

    def test_missing_optional_encounter_fields(self, mapping):
        df = _df_from_rows(_make_row())
        claims = parse_dataframe(df, mapping)
        enc = claims[0].encounters[0]

        # actual_los and regrouped_drg have no columns — should be None
        assert enc.actual_los is None
        assert enc.regrouped_drg is None

    def test_missing_split_payer(self, mapping):
        df = _df_from_rows(_make_row())
        claims = parse_dataframe(df, mapping)
        assert claims[0].encounters[0].split_payer is None


class TestObservations:
    def test_modifier_pattern(self, mapping):
        row = _make_row(**{
            "Modifier 1": "26",
            "Modifier 2": "TC",
        })
        df = _df_from_rows(row)
        claims = parse_dataframe(df, mapping)
        obs = claims[0].encounters[0].activities[0].observations

        modifiers = [o for o in obs if o.type == "Modifier"]
        assert len(modifiers) == 2
        assert modifiers[0].code == "26"
        assert modifiers[1].code == "TC"

    def test_tooth_observation(self, mapping):
        row = _make_row(**{"Tooth Number": "14"})
        df = _df_from_rows(row)
        claims = parse_dataframe(df, mapping)
        obs = claims[0].encounters[0].activities[0].observations

        teeth = [o for o in obs if o.type == "Tooth"]
        assert len(teeth) == 1
        assert teeth[0].code == "14"


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
