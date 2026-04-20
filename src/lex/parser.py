"""Parse flat CSV/Excel data into validator Claim objects using column mapping."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from validator.models.claim import (
    Activity,
    Claim,
    ContractConfig,
    Diagnosis,
    Encounter,
    Observation,
    ReportedValues,
    SplitPayerInfo,
)

_MAPPING_PATH = Path(__file__).parent / "default_column_mapping.yaml"


def load_mapping(path: Path | None = None) -> dict[str, Any]:
    """Load and return a column mapping configuration."""
    p = path or _MAPPING_PATH
    with open(p) as f:
        return yaml.safe_load(f)


def parse_file(
    file_path: str | Path,
    mapping: dict[str, Any] | None = None,
    sheet_name: str | int = 0,
) -> list[Claim]:
    """Parse an Excel or CSV file into a list of Claim objects.

    Args:
        file_path: Path to the uploaded .xlsx or .csv file.
        mapping: Column mapping dict. If None, loads the default.
        sheet_name: Excel sheet to read (ignored for CSV).

    Returns:
        List of fully-constructed Claim objects ready for validation.
    """
    mapping = mapping or load_mapping()
    df = _read_dataframe(file_path, sheet_name)
    return parse_dataframe(df, mapping)


def parse_dataframe(
    df: pd.DataFrame,
    mapping: dict[str, Any] | None = None,
) -> list[Claim]:
    """Parse a DataFrame into Claim objects using the column mapping."""
    mapping = mapping or load_mapping()
    fields = mapping["fields"]
    group_by = mapping["group_by"]

    claim_key_col = _resolve_column_name(fields["claim.id"], df)
    encounter_key_col = _resolve_encounter_key(group_by, fields, df)

    claims: list[Claim] = []

    for claim_id, claim_rows in df.groupby(claim_key_col, sort=False):
        claim = _build_claim(str(claim_id), claim_rows, fields, encounter_key_col)
        claims.append(claim)

    return claims


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_dataframe(file_path: str | Path, sheet_name: str | int) -> pd.DataFrame:
    p = Path(file_path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(p, sheet_name=sheet_name, dtype=str)
    return pd.read_csv(p, dtype=str)


def _resolve_column_name(field_spec: dict | str, df: pd.DataFrame) -> str:
    """Get the actual column name from a field spec, checking it exists."""
    if isinstance(field_spec, str):
        col = field_spec
    else:
        col = field_spec["column"]
    if col not in df.columns:
        raise ColumnNotFoundError(col, list(df.columns))
    return col


def _resolve_encounter_key(
    group_by: dict, fields: dict, df: pd.DataFrame
) -> str | None:
    """Resolve the encounter grouping column.

    If the encounter key points to a field spec, resolve that column.
    If the named column exists directly in the DataFrame, use it.
    Otherwise, fall back to the claim key (one encounter per claim).
    """
    enc_key = group_by.get("encounter", "encounter_key")

    # Check if it references a mapped field
    enc_field_key = f"encounter.{enc_key}" if not enc_key.startswith("encounter.") else enc_key
    if enc_field_key in fields:
        spec = fields[enc_field_key]
        col = spec["column"] if isinstance(spec, dict) else spec
        if col in df.columns:
            return col

    # Direct column name
    if enc_key in df.columns:
        return enc_key

    # Fall back: use encounter.start as a natural grouping key
    start_spec = fields.get("encounter.start")
    if start_spec:
        col = start_spec["column"] if isinstance(start_spec, dict) else start_spec
        if col in df.columns:
            return col

    return None


def _build_claim(
    claim_id: str,
    rows: pd.DataFrame,
    fields: dict,
    encounter_key_col: str | None,
) -> Claim:
    """Build a single Claim from its grouped rows."""
    first_row = rows.iloc[0]

    # Claim-level fields (taken from first row of the group)
    claim_data = {
        "id": claim_id,
        "id_payer": _get_field_value(first_row, fields.get("claim.id_payer"), str, ""),
        "member_id": _get_field_value(first_row, fields.get("claim.member_id"), str, ""),
        "payer_id": _get_field_value(first_row, fields.get("claim.payer_id"), str, ""),
        "provider_id": _get_field_value(first_row, fields.get("claim.provider_id"), str, ""),
        "emirates_id": _get_field_value(first_row, fields.get("claim.emirates_id"), str, ""),
        "gross": _get_decimal(first_row, fields.get("claim.gross"), Decimal("0")),
        "patient_share": _get_decimal(first_row, fields.get("claim.patient_share"), Decimal("0")),
        "net": _get_decimal(first_row, fields.get("claim.net"), Decimal("0")),
    }

    # Contract config
    contract = _build_contract(first_row, fields)
    claim_data["contract"] = contract

    # Encounters
    encounters = _build_encounters(rows, fields, encounter_key_col)
    claim_data["encounters"] = encounters

    return Claim(**claim_data)


def _build_contract(row: pd.Series, fields: dict) -> ContractConfig:
    """Build ContractConfig from claim-level fields."""
    return ContractConfig(
        base_rate_aed=_get_decimal(row, fields.get("claim.contract.base_rate_aed"), Decimal("8500")),
        gap_aed=_get_decimal(row, fields.get("claim.contract.gap_aed"), Decimal("25000")),
        marginal_pct=_get_decimal(row, fields.get("claim.contract.marginal_pct"), Decimal("0.60")),
        product_name=_get_field_value(row, fields.get("claim.contract.product_name"), str, "Basic"),
        lama_mode=_get_field_value(row, fields.get("claim.contract.lama_mode"), str, "advisory"),
    )


def _build_encounters(
    rows: pd.DataFrame,
    fields: dict,
    encounter_key_col: str | None,
) -> list[Encounter]:
    """Group rows into encounters and build Encounter objects."""
    if encounter_key_col and encounter_key_col in rows.columns:
        groups = rows.groupby(encounter_key_col, sort=False)
    else:
        # All rows belong to a single encounter
        groups = [(None, rows)]

    encounters: list[Encounter] = []
    for _, enc_rows in groups:
        enc = _build_single_encounter(enc_rows, fields)
        encounters.append(enc)
    return encounters


def _build_single_encounter(rows: pd.DataFrame, fields: dict) -> Encounter:
    """Build one Encounter from its rows."""
    first_row = rows.iloc[0]

    enc_data: dict[str, Any] = {
        "type": _get_mapped_int(first_row, fields.get("encounter.type"), 3),
        "facility_id": _get_field_value(first_row, fields.get("encounter.facility_id"), str, ""),
        "patient_id": _get_field_value(first_row, fields.get("encounter.patient_id"), str, ""),
        "start": _get_datetime(first_row, fields.get("encounter.start")),
        "end": _get_datetime(first_row, fields.get("encounter.end")),
        "start_type": _get_mapped_int(first_row, fields.get("encounter.start_type"), 0),
        "end_type": _get_mapped_int(first_row, fields.get("encounter.end_type"), 0),
        "transfer_source": _get_field_value(first_row, fields.get("encounter.transfer_source"), str, ""),
        "transfer_destination": _get_field_value(first_row, fields.get("encounter.transfer_destination"), str, ""),
        "patient_age_years": _get_optional_int(first_row, fields.get("encounter.patient_age_years")),
        "patient_gender": _get_mapped_str(first_row, fields.get("encounter.patient_gender"), ""),
        "patient_date_of_birth": _get_date(first_row, fields.get("encounter.patient_date_of_birth")),
        "actual_los": _get_optional_decimal(first_row, fields.get("encounter.actual_los")),
        "regrouped_drg": _get_optional_str(first_row, fields.get("encounter.regrouped_drg")),
    }

    # Reported values
    enc_data["reported"] = _build_reported(first_row, fields)

    # Split payer
    enc_data["split_payer"] = _build_split_payer(first_row, fields)

    # Diagnoses (from first row — they are encounter-level)
    enc_data["diagnoses"] = _build_diagnoses(first_row, fields, rows)

    # Activities (one per row in activity granularity)
    enc_data["activities"] = _build_activities(rows, fields)

    return Encounter(**enc_data)


def _build_reported(row: pd.Series, fields: dict) -> ReportedValues:
    return ReportedValues(
        drg_code=_get_optional_str(row, fields.get("encounter.reported.drg_code")),
        drg_base_payment=_get_optional_decimal(row, fields.get("encounter.reported.drg_base_payment")),
        outlier_payment=_get_optional_decimal(row, fields.get("encounter.reported.outlier_payment")),
        lama_payment=_get_optional_decimal(row, fields.get("encounter.reported.lama_payment")),
        cahms_adjustor=_get_optional_decimal(row, fields.get("encounter.reported.cahms_adjustor")),
        total_claim_net=_get_optional_decimal(row, fields.get("encounter.reported.total_claim_net")),
    )


def _build_split_payer(row: pd.Series, fields: dict) -> SplitPayerInfo | None:
    days = _get_optional_int(row, fields.get("encounter.split_payer.payer_1_days"))
    total = _get_optional_int(row, fields.get("encounter.split_payer.total_days"))
    if days is None or total is None:
        return None
    return SplitPayerInfo(
        payer_1_days=days,
        total_days=total,
        payer_1_id=_get_field_value(row, fields.get("encounter.split_payer.payer_1_id"), str, ""),
        payer_2_id=_get_field_value(row, fields.get("encounter.split_payer.payer_2_id"), str, ""),
    )


def _build_diagnoses(
    first_row: pd.Series,
    fields: dict,
    rows: pd.DataFrame,
) -> list[Diagnosis]:
    """Build diagnosis list from repeated columns."""
    dx_spec = fields.get("encounter.diagnoses")
    if dx_spec is None:
        return []

    diagnoses: list[Diagnosis] = []

    # Principal diagnosis
    principal_spec = dx_spec.get("principal")
    if principal_spec:
        code = _get_raw_cell(first_row, principal_spec["code"])
        if code:
            poa = _get_raw_cell(first_row, principal_spec.get("poa")) or ""
            diagnoses.append(Diagnosis(type="Principal", code=code, poa=poa))

    # Admitting diagnosis
    admitting_spec = dx_spec.get("admitting")
    if admitting_spec:
        code = _get_raw_cell(first_row, admitting_spec["code"])
        if code:
            poa = _get_raw_cell(first_row, admitting_spec.get("poa")) or ""
            diagnoses.append(Diagnosis(type="Admitting", code=code, poa=poa))

    # Secondary diagnoses (pattern-matched columns)
    secondary_spec = dx_spec.get("secondary")
    if secondary_spec:
        code_pattern = secondary_spec["code_pattern"]
        poa_pattern = secondary_spec.get("poa_pattern")
        max_n = secondary_spec.get("max_n", 20)

        for n in range(1, max_n + 1):
            code_col = code_pattern.replace("{n}", str(n))
            if code_col not in first_row.index:
                break
            code = _clean_cell(first_row.get(code_col))
            if not code:
                continue
            poa = ""
            if poa_pattern:
                poa_col = poa_pattern.replace("{n}", str(n))
                poa = _clean_cell(first_row.get(poa_col)) or ""
            diagnoses.append(Diagnosis(type="Secondary", code=code, poa=poa))

    return diagnoses


def _build_activities(rows: pd.DataFrame, fields: dict) -> list[Activity]:
    """Build activity list — one Activity per row."""
    activities: list[Activity] = []

    for _, row in rows.iterrows():
        act_id = _get_field_value(row, fields.get("activity.id"), str, "")
        if not act_id:
            continue

        observations = _build_observations(row, fields)

        activity = Activity(
            id=act_id,
            start=_get_datetime(row, fields.get("activity.start")),
            type=_get_mapped_int(row, fields.get("activity.type"), 3),
            code=_get_field_value(row, fields.get("activity.code"), str, ""),
            quantity=_get_decimal(row, fields.get("activity.quantity"), Decimal("1")),
            net=_get_decimal(row, fields.get("activity.net"), Decimal("0")),
            clinician=_get_field_value(row, fields.get("activity.clinician"), str, ""),
            ordering_clinician=_get_field_value(row, fields.get("activity.ordering_clinician"), str, ""),
            prior_authorization_id=_get_field_value(row, fields.get("activity.prior_authorization_id"), str, ""),
            observations=observations,
        )
        activities.append(activity)

    return activities


def _build_observations(row: pd.Series, fields: dict) -> list[Observation]:
    """Build observation list from paired/pattern columns."""
    obs_spec = fields.get("activity.observations")
    if obs_spec is None:
        return []

    observations: list[Observation] = []
    entries = obs_spec.get("entries", [])

    for entry in entries:
        obs_type = entry["type"]

        if "code_pattern" in entry:
            # Pattern-matched (e.g., Modifier 1, Modifier 2, ...)
            pattern = entry["code_pattern"]
            max_n = entry.get("max_n", 4)
            for n in range(1, max_n + 1):
                col = pattern.replace("{n}", str(n))
                if col not in row.index:
                    break
                val = _clean_cell(row.get(col))
                if val:
                    observations.append(Observation(type=obs_type, code=val))
        else:
            # Single paired column
            code_col = entry.get("code_column")
            value_col = entry.get("value_column")

            if code_col and code_col in row.index:
                code_val = _clean_cell(row.get(code_col))
                if code_val:
                    obs_value = ""
                    if value_col and value_col in row.index:
                        obs_value = _clean_cell(row.get(value_col)) or ""
                    observations.append(
                        Observation(type=obs_type, code=code_val, value=obs_value)
                    )

    return observations


# ---------------------------------------------------------------------------
# Value extraction helpers
# ---------------------------------------------------------------------------


def _get_raw_cell(row: pd.Series, spec: dict | str | None) -> str | None:
    """Extract raw string value from a cell given a field spec."""
    if spec is None:
        return None
    col = spec["column"] if isinstance(spec, dict) else spec
    if col not in row.index:
        default = spec.get("default") if isinstance(spec, dict) else None
        return default
    return _clean_cell(row.get(col))


def _clean_cell(value: Any) -> str | None:
    """Clean a cell value: strip whitespace, treat NaN/None as None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    return s if s else None


def _get_field_value(
    row: pd.Series,
    spec: dict | str | None,
    cast: type,
    default: Any,
) -> Any:
    """Extract a field value, apply cast, fall back to default."""
    if spec is None:
        return default
    col = spec["column"] if isinstance(spec, dict) else spec
    spec_default = spec.get("default", default) if isinstance(spec, dict) else default

    if col not in row.index:
        return spec_default

    raw = _clean_cell(row.get(col))
    if raw is None:
        return spec_default

    try:
        return cast(raw)
    except (ValueError, TypeError):
        return spec_default


def _get_mapped_int(row: pd.Series, spec: dict | None, default: int) -> int:
    """Extract a value and apply value_map to produce an int."""
    if spec is None:
        return default
    col = spec.get("column", "")
    value_map = spec.get("value_map")
    spec_default = spec.get("default", default)

    if col not in row.index:
        return spec_default if spec_default is not None else default

    raw = _clean_cell(row.get(col))
    if raw is None:
        return spec_default if spec_default is not None else default

    if value_map and raw in value_map:
        return int(value_map[raw])

    # Try direct int conversion
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return spec_default if spec_default is not None else default


def _get_mapped_str(row: pd.Series, spec: dict | None, default: str) -> str:
    """Extract a value and apply value_map to produce a string."""
    if spec is None:
        return default
    col = spec.get("column", "")
    value_map = spec.get("value_map")
    spec_default = spec.get("default", default)

    if col not in row.index:
        return spec_default if spec_default is not None else default

    raw = _clean_cell(row.get(col))
    if raw is None:
        return spec_default if spec_default is not None else default

    if value_map and raw in value_map:
        return str(value_map[raw])

    return raw


def _get_decimal(row: pd.Series, spec: dict | str | None, default: Decimal) -> Decimal:
    """Extract a Decimal value."""
    if spec is None:
        return default
    col = spec["column"] if isinstance(spec, dict) else spec
    spec_default = spec.get("default", default) if isinstance(spec, dict) else default

    if col not in row.index:
        return Decimal(str(spec_default)) if spec_default is not None else default

    raw = _clean_cell(row.get(col))
    if raw is None:
        return Decimal(str(spec_default)) if spec_default is not None else default

    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return default


def _get_optional_decimal(row: pd.Series, spec: dict | None) -> Decimal | None:
    """Extract an optional Decimal — returns None if absent."""
    if spec is None:
        return None
    col = spec.get("column", "")
    if col not in row.index:
        return None
    raw = _clean_cell(row.get(col))
    if raw is None:
        return None
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _get_optional_int(row: pd.Series, spec: dict | None) -> int | None:
    """Extract an optional int — returns None if absent."""
    if spec is None:
        return None
    col = spec.get("column", "")
    if col not in row.index:
        return None
    raw = _clean_cell(row.get(col))
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return None


def _get_optional_str(row: pd.Series, spec: dict | None) -> str | None:
    """Extract an optional string — returns None if absent/empty."""
    if spec is None:
        return None
    col = spec.get("column", "")
    if col not in row.index:
        return None
    return _clean_cell(row.get(col))


def _get_datetime(row: pd.Series, spec: dict | None) -> datetime:
    """Extract a datetime value using the format string in the spec."""
    if spec is None:
        return datetime.min
    col = spec.get("column", "") if isinstance(spec, dict) else spec
    fmt = spec.get("format", "%d/%m/%Y %H:%M") if isinstance(spec, dict) else "%d/%m/%Y %H:%M"

    if col not in row.index:
        return datetime.min

    raw = _clean_cell(row.get(col))
    if raw is None:
        return datetime.min

    try:
        return datetime.strptime(raw, fmt)
    except ValueError:
        # Try ISO format as fallback
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return datetime.min


def _get_date(row: pd.Series, spec: dict | None) -> date | None:
    """Extract an optional date value."""
    if spec is None:
        return None
    col = spec.get("column", "") if isinstance(spec, dict) else spec
    fmt = spec.get("format", "%d/%m/%Y") if isinstance(spec, dict) else "%d/%m/%Y"

    if col not in row.index:
        return None

    raw = _clean_cell(row.get(col))
    if raw is None:
        return None

    try:
        return datetime.strptime(raw, fmt).date()
    except ValueError:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ColumnNotFoundError(Exception):
    """Raised when a required column is missing from the uploaded file."""

    def __init__(self, column: str, available: list[str]):
        self.column = column
        self.available = available
        super().__init__(
            f"Required column '{column}' not found. "
            f"Available columns: {available[:20]}"
        )
