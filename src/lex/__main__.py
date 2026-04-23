"""CLI entry point: python -m lex.parser --diagnose <file.csv>"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from lex.parser import load_mapping, parse_file

_TYPE_LABELS = {
    3: "CPT",
    4: "HCPCS",
    5: "Drug",
    6: "Dental",
    8: "Service",
    9: "DRG",
    10: "Scientific",
}


def _diagnose(path: str) -> None:
    mapping = load_mapping()
    claims = parse_file(path, mapping)

    print(f"\n{'=' * 72}")
    print(f"  PARSE DIAGNOSTIC — {path}")
    print(f"  {len(claims)} claim(s) parsed")
    print(f"{'=' * 72}")

    for claim in claims:
        for enc in claim.encounters:
            print(f"\n{'─' * 72}")
            print(f"  FIN:         {claim.id}")
            print(f"  member_id:   {claim.member_id}")
            print(f"  provider_id: {claim.provider_id}")
            print(f"  gross:       {claim.gross}")
            print()
            print(f"  enc.type:     {enc.type} ({_TYPE_LABELS.get(enc.type, '?')})")
            print(f"  enc.start:    {enc.start}")
            print(f"  enc.end:      {enc.end}")
            print(f"  enc.end_type: {enc.end_type}")
            print(f"  enc.los:      {enc.actual_los}")
            print()

            drg = enc.reported.drg_code
            if drg:
                print(f"  reported.drg_code:  {drg}")
            else:
                print(f"  reported.drg_code:  *** NULL — engine will flag SS-02 ***")

            drg_bp = enc.reported.drg_base_payment
            print(f"  reported.drg_base_payment: {drg_bp if drg_bp is not None else '(null)'}")

            # Activity summary
            type_counts: Counter[int] = Counter()
            synth_count = 0
            for act in enc.activities:
                type_counts[act.type] += 1
                if act.id.startswith("SYNTH-DRG-"):
                    synth_count += 1

            print()
            print(f"  Activities ({len(enc.activities)} total):")
            for t in sorted(type_counts):
                label = _TYPE_LABELS.get(t, f"type-{t}")
                count = type_counts[t]
                marker = ""
                if t == 9 and synth_count:
                    marker = f"  (synthesized)"
                print(f"    {label:12s}: {count}{marker}")

            # Diagnoses
            print()
            print(f"  Diagnoses ({len(enc.diagnoses)}):")
            for dx in enc.diagnoses:
                poa_str = f"  POA={dx.poa}" if dx.poa else ""
                print(f"    [{dx.type:9s}] {dx.code}{poa_str}")

    print(f"\n{'=' * 72}\n")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m lex", description="Lex parser CLI")
    parser.add_argument("--diagnose", metavar="FILE", help="Parse a file and print per-FIN diagnostic summary")
    args = parser.parse_args()

    if args.diagnose:
        _diagnose(args.diagnose)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
