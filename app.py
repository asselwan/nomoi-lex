"""Lex — UAE Claim Pre-Submission Validation UI.

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

st.set_page_config(
    page_title="Lex — Claim Validator",
    page_icon="\u2696",
    layout="wide",
)

st.title("Lex — Pre-Submission Claim Validation")

if is_dev():
    st.caption(f"Environment: {get_env()}")


def main():
    state = get_state()

    # --- Upload phase ---
    file_content = render_upload()

    if file_content is None:
        if not state.claims:
            st.info("Upload an Excel or CSV file to begin validation.")
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
    """Render the results table from session state."""
    if not state.claims or not state.reports:
        return

    claim_ids = [c.id for c in state.claims]
    render_results_table(claim_ids, state.reports)


def _rehash(content: bytes) -> str:
    from lex.session.hashing import hash_file
    return hash_file(content)


main()
