"""Tests for lex.ui.export — annotated CSV export."""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest

from validator.models.claim import Claim, Encounter, Activity, ReportedValues
from validator.models.results import RuleResult, Severity, ValidationReport

from lex.ui.export import build_annotated_csv, export_csv_filename


# --- Fixtures ---


def _make_claim(claim_id: str = "CLM-001", drg: str = "011011") -> Claim:
    return Claim(
        id=claim_id,
        encounters=[
            Encounter(
                type=3,
                start="2025-01-15T09:00:00",
                reported=ReportedValues(drg_code=drg),
                activities=[
                    Activity(id="ACT-1", start="2025-01-15T10:00:00", type=9, code=drg),
                ],
            )
        ],
    )


def _make_report(
    claim_id: str = "CLM-001",
    errors: int = 0,
    warnings: int = 0,
    infos: int = 0,
) -> ValidationReport:
    report = ValidationReport.for_claim(claim_id)
    for i in range(errors):
        report.add(RuleResult(
            rule_id=f"E-{i:03d}",
            engine="TestEngine",
            severity=Severity.ERROR,
            message=f"Error finding {i}",
            expected="1000.00",
            actual="800.00",
        ))
    for i in range(warnings):
        report.add(RuleResult(
            rule_id=f"W-{i:03d}",
            engine="TestEngine",
            severity=Severity.WARNING,
            message=f"Warning finding {i}",
        ))
    for i in range(infos):
        report.add(RuleResult(
            rule_id=f"I-{i:03d}",
            engine="TestEngine",
            severity=Severity.INFO,
            message=f"Info finding {i}",
        ))
    return report


# --- Tests ---


class TestBuildAnnotatedCSV:
    """Test annotated CSV export content."""

    def test_csv_has_validation_columns(self):
        claims = [_make_claim()]
        reports = [_make_report(errors=1, warnings=2)]
        csv_text = build_annotated_csv(claims, reports)

        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)

        assert len(rows) == 1
        row = rows[0]
        assert "validation_status" in row
        assert "error_count" in row
        assert "warning_count" in row
        assert "info_count" in row
        assert "total_estimated_impact_aed" in row
        assert "top_issue" in row

    def test_blocked_status_when_errors(self):
        claims = [_make_claim()]
        reports = [_make_report(errors=2)]
        csv_text = build_annotated_csv(claims, reports)

        reader = csv.DictReader(io.StringIO(csv_text))
        row = next(reader)
        assert row["validation_status"] == "BLOCKED"
        assert row["error_count"] == "2"

    def test_review_status_when_warnings_only(self):
        claims = [_make_claim()]
        reports = [_make_report(warnings=3)]
        csv_text = build_annotated_csv(claims, reports)

        reader = csv.DictReader(io.StringIO(csv_text))
        row = next(reader)
        assert row["validation_status"] == "REVIEW"

    def test_ready_status_when_clean(self):
        claims = [_make_claim()]
        reports = [_make_report()]
        csv_text = build_annotated_csv(claims, reports)

        reader = csv.DictReader(io.StringIO(csv_text))
        row = next(reader)
        assert row["validation_status"] == "READY"

    def test_estimated_impact_calculated(self):
        claims = [_make_claim()]
        reports = [_make_report(errors=1)]  # expected=1000, actual=800 → delta=200
        csv_text = build_annotated_csv(claims, reports)

        reader = csv.DictReader(io.StringIO(csv_text))
        row = next(reader)
        assert float(row["total_estimated_impact_aed"]) == 200.0

    def test_top_issue_contains_rule_id(self):
        claims = [_make_claim()]
        reports = [_make_report(errors=1)]
        csv_text = build_annotated_csv(claims, reports)

        reader = csv.DictReader(io.StringIO(csv_text))
        row = next(reader)
        assert "[E-000]" in row["top_issue"]

    def test_multiple_claims(self):
        claims = [_make_claim("CLM-001"), _make_claim("CLM-002")]
        reports = [_make_report("CLM-001", errors=1), _make_report("CLM-002")]
        csv_text = build_annotated_csv(claims, reports)

        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["validation_status"] == "BLOCKED"
        assert rows[1]["validation_status"] == "READY"

    def test_claim_id_is_hashed(self):
        claims = [_make_claim("CLM-001")]
        reports = [_make_report()]
        csv_text = build_annotated_csv(claims, reports)

        reader = csv.DictReader(io.StringIO(csv_text))
        row = next(reader)
        # Should be a hex hash, not the raw claim ID
        assert row["claim_id_hash"] != "CLM-001"
        assert len(row["claim_id_hash"]) == 12


class TestExportFilename:
    """Test CSV filename pattern."""

    def test_filename_pattern(self):
        name = export_csv_filename("sample_5_claims.csv")
        assert name.startswith("lex_sample_5_claims_")
        assert name.endswith(".csv")

    def test_filename_strips_extension(self):
        name = export_csv_filename("data.xlsx")
        assert "data_" in name
        assert ".xlsx" not in name.replace(".csv", "")

    def test_filename_with_empty_name(self):
        name = export_csv_filename("")
        assert name.startswith("lex_export_")
