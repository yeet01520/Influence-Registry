#!/usr/bin/env python3
"""
backfill_special_interest_total.py
===================================

One-time patcher: ensures every record in data/fec.json has a
`special_interest_total` field, computed as the sum of the corporate-sector
contribution categories (fossil_fuels + pharma + finance + defense + tech).

WHY THIS EXISTS:
  Some upstream fetches populate `special_interest_total` and others don't.
  When it's missing, the frontend has to fall back to computing it locally
  (which it does), but having a single source of truth in fec.json is
  cleaner and avoids inconsistencies between data and display.

WHAT IT DOES NOT DO:
  - Does not overwrite an existing `special_interest_total` value. If one
    is already there, the script leaves it alone (assumes upstream knows
    something the local computation doesn't, e.g. additional categories).
  - Does not touch profiles.json. profiles.json `corporate_total` is
    static/hand-maintained per-member; the live site will continue to use
    the FEC value via runtime merging (see index.html applyFECData).
  - Does not modify anything other than the `special_interest_total` field.

USAGE:
  python3 scripts/backfill_special_interest_total.py [--dry-run]

OUTPUTS:
  data/fec.json (updated in place)
  data/fec.json.before_sit (backup, only on real run)

SAFETY:
  - Backup is created before any write
  - Dry-run mode prints intended changes without writing
  - Idempotent: running twice has no effect after the first run
"""

import json
import sys
from pathlib import Path

DATA_DIR     = Path(__file__).resolve().parent.parent / "data"
FEC_FILE     = DATA_DIR / "fec.json"
BACKUP_FILE  = DATA_DIR / "fec.json.before_sit"

# Categories that sum into the corporate "special interest total"
# Note: oil_gas is NOT included since it's a duplicate of fossil_fuels in the
# data shape used by frontend. Including both would double-count.
CORPORATE_CATEGORIES = ["fossil_fuels", "pharma", "finance", "defense", "tech"]


def compute_total(record):
    """Sum the corporate-sector amounts in a single FEC record."""
    return sum(int(record.get(cat, 0) or 0) for cat in CORPORATE_CATEGORIES)


def main():
    dry_run = "--dry-run" in sys.argv

    if not FEC_FILE.exists():
        sys.exit(f"ERROR: {FEC_FILE} not found")

    fec = json.loads(FEC_FILE.read_text())
    print(f"Loaded {len(fec)} FEC records from {FEC_FILE.name}", flush=True)

    if not dry_run:
        BACKUP_FILE.write_text(json.dumps(fec, indent=2))
        print(f"Backed up to {BACKUP_FILE.name}", flush=True)

    added       = 0
    already_set = 0
    no_data     = 0
    samples     = []  # for printing a few examples

    for name, record in sorted(fec.items()):
        if not isinstance(record, dict):
            continue

        existing = record.get("special_interest_total")
        if existing is not None and existing != 0:
            already_set += 1
            continue

        new_total = compute_total(record)
        if new_total <= 0:
            no_data += 1
            continue

        if not dry_run:
            record["special_interest_total"] = new_total
        added += 1

        if len(samples) < 10:
            samples.append((name, new_total))

    if not dry_run:
        FEC_FILE.write_text(json.dumps(fec, indent=2))

    print(f"\n{'='*60}")
    print(f"{'DRY RUN COMPLETE' if dry_run else 'WRITE COMPLETE'}")
    print(f"  Added/would add:           {added}")
    print(f"  Already had value:         {already_set}")
    print(f"  No corporate data at all:  {no_data}")
    if samples:
        print(f"\n  Sample additions:")
        for n, t in samples:
            print(f"    {n}: ${t:,}")


if __name__ == "__main__":
    main()
