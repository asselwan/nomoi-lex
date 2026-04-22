"""PDF report renderer using WeasyPrint and Jinja2 templates."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from validator.models.claim import Claim
from validator.models.results import Severity, ValidationReport

from lex.session.hashing import hash_claim_id

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_SEVERITY_ORDER = [Severity.ERROR, Severity.WARNING, Severity.INFO]


def render_pdf(
    claims: list[Claim],
    reports: list[ValidationReport],
) -> bytes:
    """Render a full PDF report and return raw bytes.

    Structure:
        1. Cover page with run summary
        2. One page per BLOCKED claim with full detail
        3. Aggregate appendix — rule_ids triggered with frequency counts
    """
    from weasyprint import HTML

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )

    html_body = _build_html(env, claims, reports)
    base_tpl = env.get_template("report_base.html")
    full_html = base_tpl.render(
        styles_path=(_TEMPLATES_DIR / "report_styles.css").as_uri(),
        content=html_body,
    )

    pdf_bytes = HTML(
        string=full_html,
        base_url=str(_TEMPLATES_DIR),
    ).write_pdf()

    return pdf_bytes


def _build_html(
    env: Environment,
    claims: list[Claim],
    reports: list[ValidationReport],
) -> str:
    """Assemble the HTML body from template fragments."""
    parts: list[str] = []

    # --- Cover page ---
    summary_tpl = env.get_template("report_summary.html")
    total_errors = sum(r.error_count for r in reports)
    total_warnings = sum(r.warning_count for r in reports)
    total_infos = sum(r.info_count for r in reports)
    total_impact = sum(_report_impact(r) for r in reports)
    blocked_count = sum(1 for r in reports if r.error_count > 0)

    parts.append(summary_tpl.render(
        run_timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        claim_count=len(claims),
        error_count=total_errors,
        warning_count=total_warnings,
        info_count=total_infos,
        blocked_count=blocked_count,
        total_impact=f"{total_impact:,.2f}",
    ))

    # --- Per-BLOCKED-claim detail pages ---
    detail_tpl = env.get_template("report_claim_detail.html")
    for claim, report in zip(claims, reports):
        if report.error_count == 0:
            continue

        grouped = _group_by_severity(report)
        status = "BLOCKED"

        drg_code = ""
        enc_type = ""
        if claim.encounters:
            enc = claim.encounters[0]
            enc_type = {3: "Inpatient", 4: "Inpatient With ER"}.get(enc.type, str(enc.type))
            if enc.reported.drg_code:
                drg_code = enc.reported.drg_code

        parts.append(detail_tpl.render(
            claim_id_hash=hash_claim_id(claim.id),
            status=status,
            status_class="blocked",
            drg_code=drg_code or "N/A",
            encounter_type=enc_type or "N/A",
            grouped_results=grouped,
        ))

    # --- Aggregate appendix ---
    parts.append(_build_appendix(reports))

    return "\n".join(parts)


def _build_appendix(reports: list[ValidationReport]) -> str:
    """Build HTML for the aggregate appendix: rule_id frequency table."""
    counter: Counter[str] = Counter()
    for report in reports:
        for result in report.results:
            counter[result.rule_id] += 1

    if not counter:
        return ""

    rows = ""
    for rule_id, count in counter.most_common():
        rows += f"<tr><td>{rule_id}</td><td>{count}</td></tr>\n"

    return (
        '<div class="appendix">'
        "<h2>Appendix — Rule Frequency</h2>"
        "<table>"
        "<thead><tr><th>Rule ID</th><th>Frequency</th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table></div>"
    )


def _group_by_severity(report: ValidationReport):
    """Group results by severity, return list of (label, results) tuples."""
    grouped = {s: [] for s in _SEVERITY_ORDER}
    for r in report.results:
        grouped[r.severity].append(r)
    return [(s.value, grouped[s]) for s in _SEVERITY_ORDER]


def _report_impact(report: ValidationReport) -> float:
    """Sum |expected - actual| deltas across a report's results."""
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
