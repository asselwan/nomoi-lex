"""Tests for lex.reports.renderer — PDF rendering."""

from __future__ import annotations

import pytest

from validator.models.claim import Claim, Encounter, Activity, ReportedValues
from validator.models.results import RuleResult, Severity, ValidationReport


def _weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
        return True
    except (ImportError, OSError):
        return False


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


@pytest.fixture
def sample_claims():
    return [
        _make_claim("CLM-001", "011011"),
        _make_claim("CLM-002", "041101"),
    ]


@pytest.fixture
def sample_reports():
    return [
        _make_report("CLM-001", errors=2, warnings=1),
        _make_report("CLM-002", warnings=1, infos=2),
    ]


@pytest.fixture
def blocked_report():
    """Single blocked claim for minimal PDF test."""
    return [_make_claim()], [_make_report(errors=1)]


# --- Tests ---


class TestPDFRenderer:
    """Test PDF generation via WeasyPrint."""

    @pytest.mark.skipif(
        not _weasyprint_available(),
        reason="WeasyPrint not installed",
    )
    def test_render_returns_bytes(self, sample_claims, sample_reports):
        from lex.reports.renderer import render_pdf

        result = render_pdf(sample_claims, sample_reports)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.skipif(
        not _weasyprint_available(),
        reason="WeasyPrint not installed",
    )
    def test_pdf_starts_with_magic_bytes(self, sample_claims, sample_reports):
        from lex.reports.renderer import render_pdf

        result = render_pdf(sample_claims, sample_reports)
        assert result[:5] == b"%PDF-"

    @pytest.mark.skipif(
        not _weasyprint_available(),
        reason="WeasyPrint not installed",
    )
    def test_blocked_claim_included(self, blocked_report):
        from lex.reports.renderer import render_pdf

        claims, reports = blocked_report
        result = render_pdf(claims, reports)
        assert result[:5] == b"%PDF-"
        assert len(result) > 500  # Non-trivial content

    @pytest.mark.skipif(
        not _weasyprint_available(),
        reason="WeasyPrint not installed",
    )
    def test_empty_reports_still_renders(self):
        from lex.reports.renderer import render_pdf

        claims = [_make_claim()]
        reports = [_make_report()]  # No findings
        result = render_pdf(claims, reports)
        assert result[:5] == b"%PDF-"


class TestHTMLAssembly:
    """Test the HTML assembly without requiring WeasyPrint."""

    def test_build_html_contains_summary(self, sample_claims, sample_reports):
        from lex.reports.renderer import _build_html, _TEMPLATES_DIR
        from jinja2 import Environment, FileSystemLoader

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=True,
        )
        html = _build_html(env, sample_claims, sample_reports)
        assert "Lex Validation Report" in html
        assert "Errors" in html

    def test_build_html_includes_blocked_claims(self, sample_claims, sample_reports):
        from lex.reports.renderer import _build_html, _TEMPLATES_DIR
        from jinja2 import Environment, FileSystemLoader

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=True,
        )
        html = _build_html(env, sample_claims, sample_reports)
        # CLM-001 has errors → should appear in detail pages
        assert "BLOCKED" in html

    def test_appendix_contains_rule_ids(self, sample_claims, sample_reports):
        from lex.reports.renderer import _build_html, _TEMPLATES_DIR
        from jinja2 import Environment, FileSystemLoader

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=True,
        )
        html = _build_html(env, sample_claims, sample_reports)
        assert "Rule Frequency" in html
        assert "E-000" in html

    def test_no_appendix_when_no_results(self):
        from lex.reports.renderer import _build_html, _TEMPLATES_DIR
        from jinja2 import Environment, FileSystemLoader

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=True,
        )
        claims = [_make_claim()]
        reports = [_make_report()]
        html = _build_html(env, claims, reports)
        assert "Rule Frequency" not in html
