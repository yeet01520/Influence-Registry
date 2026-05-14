#!/usr/bin/env python3
"""
patch_aipac_contamination.py
=============================
Zeros the AIPAC fields for entries in data/fec.json that match the
surname-fuzzy-fallback bug signature. Use this as an immediate fix while
the patched fetcher re-runs.

The audit script identifies them. This one writes the fix in place.

USAGE
-----
    # Dry run (audit only, no changes):
    python3 patch_aipac_contamination.py --dry-run

    # Apply the patch:
    python3 patch_aipac_contamination.py

Operates on data/fec.json by default; pass a path as the first positional
argument to override.

What it changes
---------------
For each contaminated entry:
  aipac                  → 0
  aipac_pacs             → 0  (already 0; left as-is)
  aipac_ie               → 0  (already 0; left as-is)
  aipac_lobby_donors     → 0
  aipac_sources          → "" (already empty; left as-is)
  special_interest_total → recomputed from the remaining sector fields
  _data_quality          → added: short note explaining the patch

A backup copy of the file is written to data/fec.json.bak before changes.
"""
import json
import sys
import shutil
from pathlib import Path


SECTOR_FIELDS = ["aipac", "fossil_fuels", "pharma", "defense",
                 "finance", "tech", "nra"]


def is_contaminated(entry: dict) -> bool:
    pacs = entry.get("aipac_pacs", 0) or 0
    ie = entry.get("aipac_ie", 0) or 0
    lobby = entry.get("aipac_lobby_donors", 0) or 0
    sources = (entry.get("aipac_sources") or "").strip()
    return pacs == 0 and ie == 0 and lobby > 0 and not sources


def patch(fec_path: Path, dry_run: bool) -> int:
    with fec_path.open() as f:
        data = json.load(f)

    contaminated = [n for n, e in data.items()
                    if isinstance(e, dict) and is_contaminated(e)]

    if not contaminated:
        print("No contaminated entries found. Nothing to do.")
        return 0

    print(f"Found {len(contaminated)} contaminated entries:")
    for name in contaminated:
        e = data[name]
        print(f"  {name:35s}  aipac=${e.get('aipac', 0):,} -> $0")

    if dry_run:
        print("\n(dry run; no changes written)")
        return 0

    # Backup
    backup_path = fec_path.with_suffix(fec_path.suffix + ".bak")
    shutil.copy(fec_path, backup_path)
    print(f"\nBackup written: {backup_path}")

    # Patch each contaminated entry
    for name in contaminated:
        e = data[name]
        e["aipac"] = 0
        e["aipac_lobby_donors"] = 0
        # pacs/ie/sources already empty; leave them
        # Recompute special_interest_total
        sit = sum((e.get(k, 0) or 0) for k in SECTOR_FIELDS)
        e["special_interest_total"] = sit
        e["_data_quality"] = (
            "AIPAC fields zeroed to fix surname-fuzzy-fallback collision. "
            "Original value came from a different person sharing this "
            "member's surname in trackaipac_page.txt. Will be regenerated "
            "by patched fetcher (v7.1+) on next refresh."
        )

    with fec_path.open("w") as f:
        json.dump(data, f, indent=2)

    print(f"Patched: {fec_path} ({len(contaminated)} entries updated)")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if not a.startswith("--")]
    path = Path(args[0] if args else "data/fec.json")
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(path, dry_run))
