"""Master results table with single-row selection."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from validator.models.results import Severity, ValidationReport

from lex.session.hashing import hash_claim_id
from lex.session.state import get_state


def render_results_table(
    claims_ids: list[str],
    reports: list[ValidationReport],
) -> None:
    """Render the master results table with row selection.

    Columns: Claim ID (hashed), Encounter Type, DRG Code, DRG Description,
    Errors, Warnings, Info, Estimated Impact AED, Status.
    """
    state = get_state()
    rows = _build_table_rows(claims_ids, reports, state.claims)
    df = pd.DataFrame(rows)

    # Sort: BLOCKED > REVIEW > READY, then by estimated impact descending
    status_order = {"BLOCKED": 0, "REVIEW": 1, "READY": 2}
    df["_sort_status"] = df["Status"].map(status_order)
    df = df.sort_values(
        ["_sort_status", "Est. Impact AED"],
        ascending=[True, False],
    ).reset_index(drop=True)
    df = df.drop(columns=["_sort_status"])

    st.header("Validation Results")
    st.caption(f"{len(df)} claims processed")

    selection = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun",
    )

    # Handle row selection
    selected_rows = selection.selection.rows
    if selected_rows:
        idx = selected_rows[0]
        state.selected_claim_index = idx
    else:
        state.selected_claim_index = None


def _build_table_rows(
    claim_ids: list[str],
    reports: list[ValidationReport],
    claims: list,
) -> list[dict]:
    """Build row dicts for the master table."""
    rows = []
    for i, (cid, report) in enumerate(zip(claim_ids, reports)):
        claim = claims[i] if i < len(claims) else None

        # Extract encounter info from first encounter
        enc_type = ""
        drg_code = ""
        drg_desc = ""
        if claim and claim.encounters:
            enc = claim.encounters[0]
            enc_type = _encounter_type_label(enc.type)
            if enc.reported.drg_code:
                drg_code = enc.reported.drg_code
                drg_desc = _truncate(drg_code, 30)  # Placeholder until DRG lookup

        # Severity counts
        errors = report.error_count
        warnings = report.warning_count
        infos = report.info_count

        # Status derivation
        status = _derive_status(errors, warnings)

        # Estimated impact: sum of expected vs actual deltas from results
        impact = _estimate_impact(report)

        rows.append({
            "Claim ID": hash_claim_id(cid),
            "Enc. Type": enc_type,
            "DRG Code": drg_code,
            "DRG Description": drg_desc,
            "Errors": errors,
            "Warnings": warnings,
            "Info": infos,
            "Est. Impact AED": impact,
            "Status": status,
        })

    return rows


def _derive_status(errors: int, warnings: int) -> str:
    """Derive claim status from validation counts."""
    if errors > 0:
        return "BLOCKED"
    if warnings > 0:
        return "REVIEW"
    return "READY"


def _encounter_type_label(enc_type: int) -> str:
    labels = {
        3: "IP (no ER)",
        4: "IP (with ER)",
    }
    return labels.get(enc_type, f"Type {enc_type}")


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


def _estimate_impact(report: ValidationReport) -> float:
    """Estimate financial impact from validation results.

    Uses expected/actual fields on results where available.
    Returns 0.0 if no financial data in results.
    """
    total = Decimal("0")
    for result in report.results:
        if result.expected and result.actual:
            try:
                expected = Decimal(result.expected.replace(",", ""))
                actual = Decimal(result.actual.replace(",", ""))
                total += abs(expected - actual)
            except Exception:
                continue
    return float(total)
