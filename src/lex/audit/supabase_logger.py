"""Supabase audit logger — logs validation runs and claim outcomes.

Credentials from env vars SUPABASE_URL and SUPABASE_SERVICE_KEY.
Fails gracefully when either is absent: logs a warning to stderr and
skips audit writes so local dev continues to work.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import datetime, timezone

from validator.models.results import ValidationReport

from lex.session.hashing import hash_claim_id

logger = logging.getLogger(__name__)
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(levelname)s [lex.audit] %(message)s"))
logger.addHandler(_handler)
logger.setLevel(logging.WARNING)

# Engine version tag for audit records
ENGINE_VERSION = "0.1.0"


def _get_client():
    """Return a Supabase client or None if credentials are missing."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")

    if not url or not key:
        logger.warning(
            "SUPABASE_URL or SUPABASE_SERVICE_KEY not set — "
            "audit logging disabled."
        )
        return None

    from supabase import create_client

    return create_client(url, key)


def log_validation_run(
    claim_ids: list[str],
    reports: list[ValidationReport],
    file_hash: str,
    user_id: str = "",
) -> str | None:
    """Log a validation run to Supabase. Returns run_id or None on skip.

    Never logs patient names, member IDs, raw diagnosis codes,
    raw activity codes, or any claim content.
    """
    client = _get_client()
    if client is None:
        return None

    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    total_errors = sum(r.error_count for r in reports)
    total_warnings = sum(r.warning_count for r in reports)
    total_infos = sum(r.info_count for r in reports)
    total_impact = _total_impact(reports)

    run_row = {
        "run_id": run_id,
        "run_timestamp": now,
        "user_id": user_id,
        "file_hash": file_hash,
        "claim_count": len(reports),
        "error_count_total": total_errors,
        "warning_count_total": total_warnings,
        "info_count_total": total_infos,
        "estimated_impact_aed_total": total_impact,
        "engine_version": ENGINE_VERSION,
    }

    try:
        client.schema("lex").table("validation_runs").insert(run_row).execute()
    except Exception:
        logger.warning("Failed to write validation_runs row", exc_info=True)
        return None

    # Claim outcomes (no PHI — only hashed IDs and aggregate counts)
    outcome_rows = []
    for cid, report in zip(claim_ids, reports):
        claim_hash = hash_claim_id(cid)

        if report.error_count > 0:
            status = "BLOCKED"
        elif report.warning_count > 0:
            status = "REVIEW"
        else:
            status = "READY"

        rule_ids = list({r.rule_id for r in report.results})

        outcome_rows.append({
            "run_id": run_id,
            "claim_id_hash": claim_hash,
            "drg_code": "",  # Populated below if available
            "encounter_type": "",
            "status": status,
            "rule_ids_triggered": rule_ids,
        })

    if outcome_rows:
        try:
            client.schema("lex").table("claim_outcomes").insert(outcome_rows).execute()
        except Exception:
            logger.warning("Failed to write claim_outcomes rows", exc_info=True)

    return run_id


def _total_impact(reports: list[ValidationReport]) -> float:
    """Sum estimated impact across all reports."""
    from decimal import Decimal

    total = Decimal("0")
    for report in reports:
        for r in report.results:
            if r.expected and r.actual:
                try:
                    expected = Decimal(r.expected.replace(",", ""))
                    actual = Decimal(r.actual.replace(",", ""))
                    total += abs(expected - actual)
                except Exception:
                    continue
    return float(total)
