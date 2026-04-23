"""Parse flat CSV/Excel data into validator Claim objects using column mapping.

Supports three mapping features beyond simple column-to-field:

- **Multi-column coalesce** (``columns_by_activity_type``): picks the activity
  code column based on the resolved activity type, with ordered fallback.
- **Long-format diagnoses** (``mode: long_format``): collects one diagnosis per
  row across all rows for a FIN, deduplicates, and infers principal from first
  occurrence when no explicit flag column is provided.
- **Computed fields** (``computed``): derives a value from a formula applied to
  other columns (e.g. ``days_between`` for LOS calculation).
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

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
    parser_behavior = mapping.get("parser_behavior", {})

    claim_key_col = _resolve_column_name(fields["claim.id"], df)
    encounter_key_col = _resolve_encounter_key(group_by, fields, df)

    claims: list[Claim] = []

    for claim_id, claim_rows in df.groupby(claim_key_col, sort=False):
        claim = _build_claim(
            str(claim_id), claim_rows, fields, encounter_key_col, parser_behavior,
        )
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
        if isinstance(spec, dict):
            col = spec.get("column")
        else:
            col = spec
        if col and col in df.columns:
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
    parser_behavior: dict,
) -> Claim:
    """Build a single Claim from its grouped rows.

    Claim-level fields are resolved by scanning all rows in the group and
    taking the first non-null value for each field.  This handles Cerner
    exports where encounter-level metadata (HEALTH_PLAN, INSURANCE_ID, etc.)
    may be populated only on specific charge rows.
    """
    claim_data = {
        "id": claim_id,
        "id_payer": _first_non_null_field(rows, fields.get("claim.id_payer"), str, ""),
        "member_id": _first_non_null_field(rows, fields.get("claim.member_id"), str, ""),
        "payer_id": _first_non_null_field(rows, fields.get("claim.payer_id"), str, ""),
        "provider_id": _first_non_null_field(rows, fields.get("claim.provider_id"), str, ""),
        "emirates_id": _first_non_null_field(rows, fields.get("claim.emirates_id"), str, ""),
        "gross": _first_non_null_decimal(rows, fields.get("claim.gross"), Decimal("0")),
        "patient_share": _first_non_null_decimal(rows, fields.get("claim.patient_share"), Decimal("0")),
        "net": _first_non_null_decimal(rows, fields.get("claim.net"), Decimal("0")),
    }

    # Contract config (use first-non-null across rows)
    contract = _build_contract(rows, fields)
    claim_data["contract"] = contract

    # Encounters
    encounters = _build_encounters(rows, fields, encounter_key_col, parser_behavior)
    claim_data["encounters"] = encounters

    return Claim(**claim_data)


def _build_contract(rows: pd.DataFrame, fields: dict) -> ContractConfig:
    """Build ContractConfig from claim-level fields (first-non-null across rows)."""
    return ContractConfig(
        base_rate_aed=_first_non_null_decimal(rows, fields.get("claim.contract.base_rate_aed"), Decimal("8500")),
        gap_aed=_first_non_null_decimal(rows, fields.get("claim.contract.gap_aed"), Decimal("25000")),
        marginal_pct=_first_non_null_decimal(rows, fields.get("claim.contract.marginal_pct"), Decimal("0.60")),
        product_name=_first_non_null_field(rows, fields.get("claim.contract.product_name"), str, "Basic"),
        lama_mode=_first_non_null_field(rows, fields.get("claim.contract.lama_mode"), str, "advisory"),
    )


def _build_encounters(
    rows: pd.DataFrame,
    fields: dict,
    encounter_key_col: str | None,
    parser_behavior: dict,
) -> list[Encounter]:
    """Group rows into encounters and build Encounter objects."""
    if encounter_key_col and encounter_key_col in rows.columns:
        groups = rows.groupby(encounter_key_col, sort=False)
    else:
        # All rows belong to a single encounter
        groups = [(None, rows)]

    encounters: list[Encounter] = []
    for enc_key, enc_rows in groups:
        enc = _build_single_encounter(enc_rows, fields, str(enc_key or "0"), parser_behavior)
        encounters.append(enc)
    return encounters


def _build_single_encounter(
    rows: pd.DataFrame,
    fields: dict,
    encounter_key: str,
    parser_behavior: dict,
) -> Encounter:
    """Build one Encounter from its rows.

    Encounter-level fields are resolved by scanning all rows in the group and
    taking the first non-null value.  This handles Cerner exports where
    encounter metadata (DRG_CODE, HEALTH_PLAN, etc.) may only be populated
    on specific charge rows (e.g. not on pharmacy or supply lines).
    """
    first_row = rows.iloc[0]

    enc_data: dict[str, Any] = {
        "type": _first_non_null_mapped_int(rows, fields.get("encounter.type"), 3),
        "facility_id": _first_non_null_field(rows, fields.get("encounter.facility_id"), str, ""),
        "patient_id": _first_non_null_field(rows, fields.get("encounter.patient_id"), str, ""),
        "start": _get_datetime(first_row, fields.get("encounter.start")),
        "end": _get_datetime(first_row, fields.get("encounter.end")),
        "start_type": _first_non_null_mapped_int(rows, fields.get("encounter.start_type"), 0),
        "end_type": _first_non_null_mapped_int(rows, fields.get("encounter.end_type"), 0),
        "transfer_source": _first_non_null_field(rows, fields.get("encounter.transfer_source"), str, ""),
        "transfer_destination": _first_non_null_field(rows, fields.get("encounter.transfer_destination"), str, ""),
        "patient_age_years": _first_non_null_optional_int(rows, fields.get("encounter.patient_age_years")),
        "patient_gender": _first_non_null_mapped_str(rows, fields.get("encounter.patient_gender"), ""),
        "patient_date_of_birth": _get_date(first_row, fields.get("encounter.patient_date_of_birth")),
        "regrouped_drg": _first_non_null_optional_str(rows, fields.get("encounter.regrouped_drg")),
    }

    # Actual LOS — may be a direct column or a computed field
    los_spec = fields.get("encounter.actual_los")
    if los_spec and isinstance(los_spec, dict) and "computed" in los_spec:
        enc_data["actual_los"] = _compute_value(first_row, los_spec["computed"])
    else:
        enc_data["actual_los"] = _first_non_null_optional_decimal(rows, los_spec)

    # Reported values (first-non-null across rows)
    enc_data["reported"] = _build_reported(rows, fields)

    # Split payer
    enc_data["split_payer"] = _build_split_payer(first_row, fields)

    # Diagnoses (dispatched by mode — wide or long format)
    enc_data["diagnoses"] = _build_diagnoses(first_row, fields, rows)

    # Activities (one per row in activity granularity)
    activities = _build_activities(rows, fields)

    # Synthesize DRG activity if missing and drg_code is populated
    if parser_behavior.get("auto_synthesize_drg_activity", False):
        has_drg_activity = any(a.type == 9 for a in activities)
        drg_code = enc_data["reported"].drg_code
        if not has_drg_activity and drg_code:
            synthesized = Activity(
                id=f"SYNTH-DRG-{encounter_key}",
                start=enc_data["start"],
                type=9,
                code=drg_code,
                quantity=Decimal("1"),
                net=Decimal("0"),
            )
            activities.append(synthesized)
            logger.info(
                "Synthesized DRG activity for FIN %s from reported.drg_code=%s",
                encounter_key,
                drg_code,
            )

    enc_data["activities"] = activities

    return Encounter(**enc_data)


# ---------------------------------------------------------------------------
# Computed fields
# ---------------------------------------------------------------------------

# Formula registry — add new formulas here as plain functions.
_FORMULA_REGISTRY: dict[str, Any] = {}


def _register_formula(name: str):
    """Decorator to register a computed-field formula."""
    def decorator(fn):
        _FORMULA_REGISTRY[name] = fn
        return fn
    return decorator


@_register_formula("days_between")
def _formula_days_between(
    row: pd.Series,
    *,
    from_column: str,
    to_column: str,
    round_decimals: int = 2,
    **_kwargs: Any,
) -> Decimal | None:
    """Compute fractional days between two datetime columns."""
    from_raw = _clean_cell(row.get(from_column)) if from_column in row.index else None
    to_raw = _clean_cell(row.get(to_column)) if to_column in row.index else None

    if from_raw is None or to_raw is None:
        return None

    dt_from = _parse_flexible_datetime(from_raw)
    dt_to = _parse_flexible_datetime(to_raw)
    if dt_from is None or dt_to is None:
        return None

    delta = dt_to - dt_from
    days = delta.total_seconds() / 86400
    return Decimal(str(round(days, round_decimals)))


def _parse_flexible_datetime(raw: str) -> datetime | None:
    """Try several common datetime formats, return None on failure."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _compute_value(row: pd.Series, computed_spec: dict) -> Decimal | None:
    """Dispatch a computed field to its registered formula."""
    formula_name = computed_spec["formula"]
    fn = _FORMULA_REGISTRY.get(formula_name)
    if fn is None:
        logger.warning("Unknown computed formula: %s", formula_name)
        return None
    params = {k: v for k, v in computed_spec.items() if k != "formula"}
    return fn(row, **params)


# ---------------------------------------------------------------------------
# Multi-column activity code coalesce
# ---------------------------------------------------------------------------


def _resolve_activity_code(
    row: pd.Series,
    code_spec: dict | str | None,
    activity_type: int,
) -> str:
    """Resolve activity code, supporting ``columns_by_activity_type`` coalesce.

    When the spec contains ``columns_by_activity_type``, the parser picks the
    column mapped to the resolved activity type.  If the type is not in the map
    or the mapped column is null, it falls through to ``fallback_columns`` in
    order and takes the first non-null value.
    """
    if code_spec is None:
        return ""

    # Simple column spec (string or dict with "column" key)
    if isinstance(code_spec, str):
        val = _clean_cell(row.get(code_spec)) if code_spec in row.index else None
        return val or ""
    if "column" in code_spec:
        return _get_field_value(row, code_spec, str, "")

    # Multi-column coalesce
    type_map = code_spec.get("columns_by_activity_type", {})
    fallback = code_spec.get("fallback_columns", [])

    # Try the type-specific column first
    mapped_col = type_map.get(activity_type) or type_map.get(str(activity_type))
    if mapped_col and mapped_col in row.index:
        val = _clean_cell(row.get(mapped_col))
        if val:
            return val

    # Fall through to fallback columns
    for col in fallback:
        if col in row.index:
            val = _clean_cell(row.get(col))
            if val:
                return val

    return ""


# ---------------------------------------------------------------------------
# Diagnosis builders
# ---------------------------------------------------------------------------


def _build_diagnoses(
    first_row: pd.Series,
    fields: dict,
    rows: pd.DataFrame,
) -> list[Diagnosis]:
    """Build diagnosis list — dispatches by mode (repeated_columns or long_format)."""
    dx_spec = fields.get("encounter.diagnoses")
    if dx_spec is None:
        return []

    mode = dx_spec.get("mode", "repeated_columns")

    if mode == "long_format":
        return _build_diagnoses_long(rows, dx_spec)
    return _build_diagnoses_wide(first_row, dx_spec)


def _build_diagnoses_long(
    rows: pd.DataFrame,
    dx_spec: dict,
) -> list[Diagnosis]:
    """Build diagnoses from long-format data (one diagnosis per charge row).

    Collects unique (code) values across all rows for the encounter.  When
    ``principal_flag_column`` is null the first unique code encountered is
    treated as the principal diagnosis.  This is a heuristic — callers that
    need deterministic assignment should provide a principal flag column.
    """
    code_col = dx_spec.get("code_column")
    poa_col = dx_spec.get("poa_column")
    principal_flag_col = dx_spec.get("principal_flag_column")

    if not code_col:
        return []

    seen_codes: set[str] = set()
    diagnoses: list[Diagnosis] = []
    principal_assigned = False

    for _, row in rows.iterrows():
        if code_col not in row.index:
            continue
        code = _clean_cell(row.get(code_col))
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)

        poa = ""
        if poa_col and poa_col in row.index:
            poa = _clean_cell(row.get(poa_col)) or ""

        # Determine type
        if principal_flag_col and principal_flag_col in row.index:
            flag = _clean_cell(row.get(principal_flag_col))
            if flag and flag.upper() in ("Y", "1", "TRUE", "YES"):
                dx_type = "Principal"
                principal_assigned = True
            else:
                dx_type = "Secondary"
        elif not principal_assigned:
            dx_type = "Principal"
            principal_assigned = True
        else:
            dx_type = "Secondary"

        diagnoses.append(Diagnosis(type=dx_type, code=code, poa=poa))

    if not principal_flag_col and diagnoses:
        logger.warning(
            "Principal diagnosis inferred from first occurrence; "
            "provide a principal flag column for deterministic assignment."
        )

    return diagnoses


def _build_diagnoses_wide(
    first_row: pd.Series,
    dx_spec: dict,
) -> list[Diagnosis]:
    """Build diagnoses from wide-format repeated columns (original mode)."""
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


# ---------------------------------------------------------------------------
# Reported values, split payer, activities, observations
# ---------------------------------------------------------------------------


def _build_reported(rows: pd.DataFrame, fields: dict) -> ReportedValues:
    """Build ReportedValues using first-non-null across all rows."""
    return ReportedValues(
        drg_code=_first_non_null_optional_str(rows, fields.get("encounter.reported.drg_code")),
        drg_base_payment=_first_non_null_optional_decimal(rows, fields.get("encounter.reported.drg_base_payment")),
        outlier_payment=_first_non_null_optional_decimal(rows, fields.get("encounter.reported.outlier_payment")),
        lama_payment=_first_non_null_optional_decimal(rows, fields.get("encounter.reported.lama_payment")),
        cahms_adjustor=_first_non_null_optional_decimal(rows, fields.get("encounter.reported.cahms_adjustor")),
        total_claim_net=_first_non_null_optional_decimal(rows, fields.get("encounter.reported.total_claim_net")),
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


def _build_activities(rows: pd.DataFrame, fields: dict) -> list[Activity]:
    """Build activity list — one Activity per row."""
    activities: list[Activity] = []
    code_spec = fields.get("activity.code")

    for _, row in rows.iterrows():
        act_id = _get_field_value(row, fields.get("activity.id"), str, "")
        if not act_id:
            continue

        activity_type = _get_mapped_int(row, fields.get("activity.type"), 3)
        code = _resolve_activity_code(row, code_spec, activity_type)
        observations = _build_observations(row, fields)

        activity = Activity(
            id=act_id,
            start=_get_datetime(row, fields.get("activity.start")),
            type=activity_type,
            code=code,
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
            # Pattern-matched (e.g., MODIFIER_1, MODIFIER_2, ...)
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
# First-non-null row scanners
# ---------------------------------------------------------------------------
# These iterate all rows in a group and return the first non-null/non-empty
# value found.  This handles Cerner exports where encounter-level metadata
# (DRG_CODE, HEALTH_PLAN, etc.) may only appear on specific charge rows.


def _first_non_null_field(
    rows: pd.DataFrame,
    spec: dict | str | None,
    cast: type,
    default: Any,
) -> Any:
    """Scan rows for the first non-null value matching a field spec."""
    if spec is None:
        return default
    for _, row in rows.iterrows():
        result = _get_field_value(row, spec, cast, None)
        if result is not None:
            return result
    # All rows null — fall back to spec default or caller default
    if isinstance(spec, dict):
        return spec.get("default", default)
    return default


def _first_non_null_decimal(
    rows: pd.DataFrame,
    spec: dict | str | None,
    default: Decimal,
) -> Decimal:
    if spec is None:
        return default
    for _, row in rows.iterrows():
        result = _get_decimal(row, spec, None)
        if result is not None:
            return result
    if isinstance(spec, dict):
        sd = spec.get("default", default)
        return Decimal(str(sd)) if sd is not None else default
    return default


def _first_non_null_mapped_int(
    rows: pd.DataFrame,
    spec: dict | None,
    default: int,
) -> int:
    if spec is None:
        return default
    for _, row in rows.iterrows():
        result = _get_mapped_int(row, spec, None)
        if result is not None:
            return result
    sd = spec.get("default", default)
    return sd if sd is not None else default


def _first_non_null_mapped_str(
    rows: pd.DataFrame,
    spec: dict | None,
    default: str,
) -> str:
    if spec is None:
        return default
    for _, row in rows.iterrows():
        result = _get_mapped_str(row, spec, None)
        if result is not None:
            return result
    sd = spec.get("default", default)
    return sd if sd is not None else default


def _first_non_null_optional_str(
    rows: pd.DataFrame,
    spec: dict | None,
) -> str | None:
    if spec is None:
        return None
    for _, row in rows.iterrows():
        result = _get_optional_str(row, spec)
        if result is not None:
            return result
    return None


def _first_non_null_optional_int(
    rows: pd.DataFrame,
    spec: dict | None,
) -> int | None:
    if spec is None:
        return None
    for _, row in rows.iterrows():
        result = _get_optional_int(row, spec)
        if result is not None:
            return result
    return None


def _first_non_null_optional_decimal(
    rows: pd.DataFrame,
    spec: dict | None,
) -> Decimal | None:
    if spec is None:
        return None
    for _, row in rows.iterrows():
        result = _get_optional_decimal(row, spec)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Value extraction helpers (single-row)
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

    if isinstance(spec, dict):
        col = spec.get("column")
        spec_default = spec.get("default", default)
    else:
        col = spec
        spec_default = default

    if col is None or col not in row.index:
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

    if not col or col not in row.index:
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

    if not col or col not in row.index:
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

    if isinstance(spec, dict):
        col = spec.get("column")
        spec_default = spec.get("default", default)
    else:
        col = spec
        spec_default = default

    if col is None or col not in row.index:
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
    if not col or col not in row.index:
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
    if not col or col not in row.index:
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
    if not col or col not in row.index:
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

    if not col or col not in row.index:
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
