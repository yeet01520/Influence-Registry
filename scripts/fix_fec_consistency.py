#!/usr/bin/env python3
"""
fix_fec_consistency.py — repair internal consistency of data/fec.json

Two invariants the validator enforces that are currently violated in fec.json:

  1. aipac == aipac_pacs + aipac_lobby_donors + aipac_ie
     (the `aipac` field is the total of its three breakdown components)

  2. special_interest_total == aipac_pacs + aipac_lobby_donors + aipac_ie
                              + fossil_fuels + pharma + defense + finance + tech + nra
     (SIT is the sum of all PAC/sector buckets)

This script fixes both, conservatively:

  - If components (pacs + lobby + ie) have data, they're treated as authoritative
    and `aipac` is recomputed to match. This is the most common case (e.g. Jack Reed,
    Lindsey Graham, where `aipac` was stale at $0 but breakdown was populated).

  - If components are all $0 but `aipac` has a value, we attribute the value to
    `aipac_pacs` (PAC money is the most likely source, and AIPAC_DATA — which is
    sourced from TrackAIPAC PAC totals — matches this interpretation). This handles
    cases like Nanette Barragán.

  - special_interest_total is always recomputed from the (now-consistent) parts.

Run from repo root:
    python3 scripts/fix_fec_consistency.py

Outputs:
    data/fec.json (overwritten in place)
    A report of every record changed.
"""

import json
import sys
from pathlib import Path

# Allow override via argv for testing
FEC_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/fec.json")

def g(d, k):
    """Get integer value, treating None/missing as 0."""
    return d.get(k, 0) or 0

def main():
    with open(FEC_PATH) as f:
        fec = json.load(f)

    aipac_fixes = []
    sit_fixes = []
    backfilled_pacs = []

    for name, d in fec.items():
        pacs   = g(d, "aipac_pacs")
        lobby  = g(d, "aipac_lobby_donors")
        ie     = g(d, "aipac_ie")
        stored_aipac = g(d, "aipac")
        components_sum = pacs + lobby + ie

        # Case 1: components all zero but aipac has a value.
        # Attribute the value to aipac_pacs (best-guess; PAC money is the most
        # common AIPAC source and matches the AIPAC_DATA/TrackAIPAC convention).
        if components_sum == 0 and stored_aipac > 0:
            d["aipac_pacs"] = stored_aipac
            pacs = stored_aipac
            components_sum = stored_aipac
            backfilled_pacs.append((name, stored_aipac))

        # Now recompute aipac to match components
        new_aipac = pacs + lobby + ie
        if new_aipac != stored_aipac:
            d["aipac"] = new_aipac
            aipac_fixes.append((name, stored_aipac, new_aipac))

        # Recompute special_interest_total from all additive parts
        new_sit = (pacs + lobby + ie
                   + g(d, "fossil_fuels")
                   + g(d, "pharma")
                   + g(d, "defense")
                   + g(d, "finance")
                   + g(d, "tech")
                   + g(d, "nra"))
        stored_sit = g(d, "special_interest_total")
        if abs(new_sit - stored_sit) > 1:  # ignore sub-dollar rounding
            d["special_interest_total"] = new_sit
            sit_fixes.append((name, stored_sit, new_sit))

    # Write fixed file
    with open(FEC_PATH, "w") as f:
        json.dump(fec, f, indent=2, ensure_ascii=False)

    # Report
    print(f"=== Fixed {FEC_PATH} ===\n")

    if backfilled_pacs:
        print(f"Backfilled aipac_pacs from stored aipac ({len(backfilled_pacs)} records):")
        for name, val in backfilled_pacs:
            print(f"  {name}: aipac_pacs $0 -> ${val:,}")
        print()

    if aipac_fixes:
        print(f"Recomputed aipac to match components ({len(aipac_fixes)} records):")
        for name, old, new in aipac_fixes:
            print(f"  {name}: aipac ${old:,} -> ${new:,}")
        print()

    if sit_fixes:
        print(f"Recomputed special_interest_total ({len(sit_fixes)} records):")
        for name, old, new in sit_fixes:
            print(f"  {name}: SIT ${old:,} -> ${new:,}")
        print()

    if not (aipac_fixes or sit_fixes or backfilled_pacs):
        print("No changes — fec.json was already internally consistent.")

if __name__ == "__main__":
    main()
