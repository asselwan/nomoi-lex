"""Thin adapter exposing the validation engine to the Streamlit app."""

from validator.models.claim import Claim
from validator.models.results import ValidationReport
from validator.orchestrator import validate_claim


def run_validation(claim: Claim) -> ValidationReport:
    return validate_claim(claim)
