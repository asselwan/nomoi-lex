"""CLI entry point for claim validation.

Usage:
    validate-claim <claim.json>
    validate-claim --stdin

PHI posture: never logs patient identifiers, member IDs, or names.
Claim IDs are SHA-256 hashed (first 12 chars) in output.
"""

from __future__ import annotations

import json
import sys

from validator.models.claim import Claim
from validator.models.results import Severity, ValidationReport
from validator.orchestrator import validate_claim
from validator.reference.loader import get_reference_data


def _format_report(report: ValidationReport) -> str:
    lines: list[str] = []
    lines.append(f"claim  {report.claim_id_hash}")
    lines.append(
        f"  {report.encounter_count} encounter(s)  "
        f"{report.error_count} error  "
        f"{report.warning_count} warning  "
        f"{report.info_count} info"
    )
    lines.append("")

    for r in report.results:
        marker = {
            Severity.ERROR: "ERR",
            Severity.WARNING: "WRN",
            Severity.INFO: "INF",
        }[r.severity]
        enc = f"enc[{r.encounter_index}]" if r.encounter_index is not None else ""
        act = f"act[{r.activity_index}]" if r.activity_index is not None else ""
        loc = " ".join(filter(None, [enc, act]))
        lines.append(f"  [{marker}] {r.rule_id}  {loc}")
        lines.append(f"    {r.message}")
        if r.expected or r.actual:
            lines.append(f"    expected: {r.expected}  actual: {r.actual}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) < 2 and not sys.stdin.isatty():
        data = json.load(sys.stdin)
    elif len(sys.argv) >= 2 and sys.argv[1] == "--stdin":
        data = json.load(sys.stdin)
    elif len(sys.argv) >= 2:
        with open(sys.argv[1]) as f:
            data = json.load(f)
    else:
        print("usage: validate-claim <claim.json>", file=sys.stderr)
        print("       validate-claim --stdin", file=sys.stderr)
        sys.exit(1)

    claim = Claim.model_validate(data)
    ref = get_reference_data()
    report = validate_claim(claim, ref)
    print(_format_report(report))

    if report.error_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
