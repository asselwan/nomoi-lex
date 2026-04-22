"""Tests for lex.audit.supabase_logger — graceful degradation path."""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

import pytest

from validator.models.results import RuleResult, Severity, ValidationReport

from lex.audit.supabase_logger import log_validation_run, _get_client


# --- Fixtures ---


def _make_report(
    claim_id: str = "CLM-001",
    errors: int = 0,
    warnings: int = 0,
) -> ValidationReport:
    report = ValidationReport.for_claim(claim_id)
    for i in range(errors):
        report.add(RuleResult(
            rule_id=f"E-{i:03d}",
            engine="TestEngine",
            severity=Severity.ERROR,
            message=f"Error {i}",
        ))
    for i in range(warnings):
        report.add(RuleResult(
            rule_id=f"W-{i:03d}",
            engine="TestEngine",
            severity=Severity.WARNING,
            message=f"Warning {i}",
        ))
    return report


# --- Tests ---


class TestGracefulDegradation:
    """Verify audit logging degrades gracefully when env vars are absent."""

    def test_get_client_returns_none_without_env_vars(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove both vars if present
            os.environ.pop("SUPABASE_URL", None)
            os.environ.pop("SUPABASE_SERVICE_KEY", None)
            client = _get_client()
            assert client is None

    def test_get_client_returns_none_with_empty_url(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "key"}):
            client = _get_client()
            assert client is None

    def test_get_client_returns_none_with_empty_key(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_KEY": ""}):
            client = _get_client()
            assert client is None

    def test_log_validation_run_returns_none_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SUPABASE_URL", None)
            os.environ.pop("SUPABASE_SERVICE_KEY", None)

            result = log_validation_run(
                claim_ids=["CLM-001"],
                reports=[_make_report(errors=1)],
                file_hash="abc123",
            )
            assert result is None

    def test_warning_logged_when_credentials_missing(self, caplog):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SUPABASE_URL", None)
            os.environ.pop("SUPABASE_SERVICE_KEY", None)

            with caplog.at_level(logging.WARNING, logger="lex.audit.supabase_logger"):
                _get_client()

            assert any("audit logging disabled" in r.message for r in caplog.records)

    def test_local_dev_continues_without_supabase(self):
        """End-to-end: calling log_validation_run with no credentials
        must not raise and must return None."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SUPABASE_URL", None)
            os.environ.pop("SUPABASE_SERVICE_KEY", None)

            reports = [
                _make_report("CLM-001", errors=2),
                _make_report("CLM-002", warnings=1),
            ]
            result = log_validation_run(
                claim_ids=["CLM-001", "CLM-002"],
                reports=reports,
                file_hash="deadbeef",
                user_id="test-user",
            )
            assert result is None
