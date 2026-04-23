"""Drill-down detail panel for a selected claim's validation results."""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from validator.models.results import RuleResult, Severity, ValidationReport

from lex.session.hashing import hash_claim_id

# Severity display order and badge colours (Streamlit markdown)
_SEVERITY_ORDER = [Severity.ERROR, Severity.WARNING, Severity.INFO]
_SEVERITY_COLOURS = {
    Severity.ERROR: "#d32f2f",
    Severity.WARNING: "#ed6c02",
    Severity.INFO: "#0288d1",
}


def render_results_detail(
    claim_id: str,
    report: ValidationReport,
) -> None:
    """Render the full RuleResult list for a selected claim.

    Results are grouped by severity (ERROR first, then WARNING, then INFO).
    """
    st.subheader(f"FIN Detail — {hash_claim_id(claim_id)}")

    if not report.results:
        st.success("No validation findings for this claim.")
        return

    # Group results by severity
    grouped: dict[Severity, list[RuleResult]] = {s: [] for s in _SEVERITY_ORDER}
    for result in report.results:
        grouped[result.severity].append(result)

    for severity in _SEVERITY_ORDER:
        results = grouped[severity]
        if not results:
            continue

        colour = _SEVERITY_COLOURS[severity]
        st.markdown(
            f'<h4 style="color:{colour}">{severity.value} ({len(results)})</h4>',
            unsafe_allow_html=True,
        )

        for r in results:
            _render_single_result(r, colour)


def _render_single_result(r: RuleResult, colour: str) -> None:
    """Render one RuleResult as an expander with details."""
    label = f"{r.rule_id} — {r.message[:80]}"
    with st.expander(label, expanded=(r.severity == Severity.ERROR)):
        cols = st.columns([1, 1, 1])

        with cols[0]:
            st.markdown(f"**Rule ID:** `{r.rule_id}`")
            st.markdown(f"**Engine:** {r.engine}")

        with cols[1]:
            badge = (
                f'<span style="background:{colour};color:#fff;'
                f'padding:2px 8px;border-radius:4px;font-size:0.85em">'
                f"{r.severity.value}</span>"
            )
            st.markdown(f"**Severity:** {badge}", unsafe_allow_html=True)
            if r.field:
                st.markdown(f"**Field:** `{r.field}`")

        with cols[2]:
            impact = _result_impact(r)
            if impact is not None:
                st.markdown(f"**Est. Impact AED:** {impact:,.2f}")

        st.markdown(f"**Message:** {r.message}")

        if r.expected or r.actual:
            st.markdown(f"**Expected:** {r.expected}  |  **Actual:** {r.actual}")


def _result_impact(r: RuleResult) -> float | None:
    """Compute estimated financial impact from expected/actual fields."""
    if not r.expected or not r.actual:
        return None
    try:
        expected = Decimal(r.expected.replace(",", ""))
        actual = Decimal(r.actual.replace(",", ""))
        return float(abs(expected - actual))
    except Exception:
        return None
