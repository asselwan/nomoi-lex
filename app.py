"""Lex — UAE Claim Presubmission Validation UI.

Single-page Streamlit app: upload → validate → results table → drill-down.
Entry point: `streamlit run app.py`
"""

import io
import streamlit as st

from lex.engine_adapter import run_validation
from lex.parser import ColumnNotFoundError, parse_dataframe, load_mapping, _read_dataframe
from lex.session.state import clear_state, get_state, should_reprocess
from lex.session.env import get_env, is_dev
from lex.ui.upload import render_upload
from lex.ui.results_table import render_results_table
from lex.ui.results_detail import render_results_detail
from lex.ui.export import build_annotated_csv, export_csv_filename
from lex.reports.renderer import render_pdf

st.set_page_config(
    page_title="Lex — Claim Validator",
    page_icon="\u2696",
    layout="wide",
)

st.title("Lex — Presubmission Claim Validation")

if is_dev():
    st.caption(f"Environment: {get_env()}")


def main():
    state = get_state()

    # --- Upload phase ---
    file_content = render_upload()

    if file_content is None:
        if not state.claims:
            st.markdown(
                '<div style="padding:1rem;border-radius:0.5rem;'
                'background-color:#e8f4f2;color:#2a5c56;">'
                "Upload an Excel or CSV file to begin validation.</div>",
                unsafe_allow_html=True,
            )
        else:
            # Re-render results from session state
            _render_results(state)
        return

    # --- Process if new file ---
    if should_reprocess(file_content):
        clear_state()
        state = get_state()
        state.file_hash = _rehash(file_content)

        try:
            _process_file(file_content, state)
        except ColumnNotFoundError as e:
            st.error(str(e))
            return
        except Exception as e:
            st.error(f"Failed to parse file: {e}")
            if is_dev():
                st.exception(e)
            return

    # --- Results phase ---
    _render_results(state)


def _process_file(file_content: bytes, state) -> None:
    """Parse file and run validation on all claims."""
    import pandas as pd
    import tempfile
    import os

    # Determine file type from content (check for xlsx magic bytes)
    is_excel = file_content[:4] == b"PK\x03\x04"

    # Write to temp file for pandas to read
    suffix = ".xlsx" if is_excel else ".csv"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(file_content)
        tmp_path = f.name

    try:
        mapping = load_mapping()
        df = _read_dataframe(tmp_path, sheet_name=0)
        claims = parse_dataframe(df, mapping)
    finally:
        os.unlink(tmp_path)

    state.claims = claims

    # Validate each claim with progress
    reports = []
    n = len(claims)
    progress_text = "Validating claims..."

    if n > 100:
        progress_bar = st.progress(0, text=progress_text)
        for i, claim in enumerate(claims):
            report = run_validation(claim)
            reports.append(report)
            progress_bar.progress((i + 1) / n, text=f"Validated {i + 1}/{n} claims")
        progress_bar.empty()
    else:
        with st.status(progress_text, expanded=False) as status:
            for i, claim in enumerate(claims):
                report = run_validation(claim)
                reports.append(report)
                status.update(label=f"Validated {i + 1}/{n} claims")
            status.update(label=f"Validation complete — {n} claims processed", state="complete")

    state.reports = reports


def _render_results(state) -> None:
    """Render the results table, export buttons, and detail panel."""
    if not state.claims or not state.reports:
        return

    claim_ids = [c.id for c in state.claims]
    render_results_table(claim_ids, state.reports)

    # --- Export buttons ---
    col1, col2, _ = st.columns([1, 1, 4])

    with col1:
        csv_data = build_annotated_csv(state.claims, state.reports)
        original_name = getattr(state, "original_filename", "export")
        st.download_button(
            label="Download CSV",
            data=csv_data,
            file_name=export_csv_filename(original_name),
            mime="text/csv",
        )

    with col2:
        pdf_data = render_pdf(state.claims, state.reports)
        st.download_button(
            label="Download PDF",
            data=pdf_data,
            file_name=export_csv_filename("report").replace(".csv", ".pdf"),
            mime="application/pdf",
        )

    # --- Drill-down detail panel ---
    if state.selected_claim_index is not None:
        idx = state.selected_claim_index
        if 0 <= idx < len(state.claims):
            render_results_detail(claim_ids[idx], state.reports[idx])


def _rehash(content: bytes) -> str:
    from lex.session.hashing import hash_file
    return hash_file(content)


main()
