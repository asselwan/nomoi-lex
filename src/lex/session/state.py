"""Streamlit session state management for Lex.

Holds: parsed claims, validation results, column mapping, file hash.
Cleared on new file upload.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import streamlit as st

from validator.models.claim import Claim
from validator.models.results import ValidationReport

from lex.session.hashing import hash_file


@dataclass
class LexSessionState:
    """Typed wrapper around st.session_state keys."""

    file_hash: str = ""
    claims: list[Claim] = field(default_factory=list)
    reports: list[ValidationReport] = field(default_factory=list)
    selected_claim_index: int | None = None


def get_state() -> LexSessionState:
    """Get or initialize the Lex session state."""
    if "lex" not in st.session_state:
        st.session_state["lex"] = LexSessionState()
    return st.session_state["lex"]


def clear_state() -> None:
    """Clear all Lex session state (called on new upload)."""
    st.session_state["lex"] = LexSessionState()


def should_reprocess(file_content: bytes) -> bool:
    """Return True if the uploaded file is different from what's in state."""
    state = get_state()
    new_hash = hash_file(file_content)
    if new_hash == state.file_hash:
        return False
    state.file_hash = new_hash
    return True
