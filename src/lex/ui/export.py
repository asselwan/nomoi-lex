"""Annotated CSV export — original rows plus validation summary columns."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from validator.models.claim import Claim
from validator.models.results import Severity, ValidationReport

from lex.session.hashing import hash_claim_id


def build_annotated_csv(
    claims: list[Claim],
    reports: list[ValidationReport],
) -> str:
    """Return CSV text with original claim rows plus appended validation columns.

    Appended columns:
        validation_status  — BLOCKED / REVIEW / READY
        error_count        — count of ERROR-severity results
        warning_count      — count of WARNING-severity results
        info_count         — count of INFO-severity results
        total_estimated_impact_aed — sum of |expected - actual| deltas
        top_issue          — rule_id + message of the highest-severity finding
    """
    rows: list[dict] = []
    for claim, report in zip(claims, reports):
        row = _claim_summary_row(claim, report)
        rows.append(row)

    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def export_csv_filename(original_name: str) -> str:
    """Generate filename: lex_{original_name}_{ISO_timestamp}.csv."""
    stem = Path(original_name).stem if original_name else "export"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"lex_{stem}_{ts}.csv"


def _claim_summary_row(claim: Claim, report: ValidationReport) -> dict:
    """Build one row dict for the annotated CSV."""
    errors = report.error_count
    warnings = report.warning_count
    infos = report.info_count

    if errors > 0:
        status = "BLOCKED"
    elif warnings > 0:
        status = "REVIEW"
    else:
        status = "READY"

    impact = _total_impact(report)
    top = _top_issue(report)

    # Claim identification (hashed for PHI safety)
    drg_code = ""
    enc_type = ""
    if claim.encounters:
        enc = claim.encounters[0]
        enc_type = {3: "Inpatient", 4: "Inpatient With ER"}.get(enc.type, str(enc.type))
        if enc.reported.drg_code:
            drg_code = enc.reported.drg_code

    return {
        "claim_id_hash": hash_claim_id(claim.id),
        "encounter_type": enc_type,
        "drg_code": drg_code,
        "validation_status": status,
        "error_count": errors,
        "warning_count": warnings,
        "info_count": infos,
        "total_estimated_impact_aed": impact,
        "top_issue": top,
    }


def _total_impact(report: ValidationReport) -> float:
    """Sum |expected - actual| deltas across results."""
    total = Decimal("0")
    for r in report.results:
        if r.expected and r.actual:
            try:
                expected = Decimal(r.expected.replace(",", ""))
                actual = Decimal(r.actual.replace(",", ""))
                total += abs(expected - actual)
            except Exception:
                continue
    return float(total)


def _top_issue(report: ValidationReport) -> str:
    """Return rule_id + message of the highest-severity finding, or empty."""
    if not report.results:
        return ""
    # Sort: ERROR > WARNING > INFO
    priority = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    top = min(report.results, key=lambda r: priority.get(r.severity, 99))
    return f"[{top.rule_id}] {top.message}"
