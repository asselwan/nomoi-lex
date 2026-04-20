"""File upload component for Lex."""

from __future__ import annotations

import streamlit as st

_MAX_SIZE_MB = 50
_WARN_SIZE_MB = 25


def render_upload() -> bytes | None:
    """Render the file uploader and return file content if a file is uploaded.

    Returns None if no file is uploaded yet.
    """
    st.header("Upload Claims File")

    uploaded = st.file_uploader(
        "Upload an Excel (.xlsx) or CSV file containing claims data",
        type=["xlsx", "xls", "csv"],
        accept_multiple_files=False,
    )

    if uploaded is None:
        return None

    size_mb = uploaded.size / (1024 * 1024)

    if size_mb > _MAX_SIZE_MB:
        st.error(f"File exceeds {_MAX_SIZE_MB} MB limit ({size_mb:.1f} MB). Please reduce file size.")
        return None

    if size_mb > _WARN_SIZE_MB:
        st.warning(f"Large file ({size_mb:.1f} MB). Processing may take longer.")

    return uploaded.getvalue()
