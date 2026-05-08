#!/usr/bin/env python3
"""
structural_id_cleanup.py
=========================

PURELY STRUCTURAL cleanup of fec.json — no API calls.

Drops any candidate_id whose office (first char) or state (chars 2-3) 
doesn't match the member's actual office and state from senate.json/house.json.

Used as a follow-up to audit_candidate_ids.py to catch the ~35 members 
where name-matching failed (Ted Cruz vs CRUZ,RAFAEL EDWARD; Pablo Hernández 
diacritic; etc.) but whose contamination is still structurally identifiable.

Candidate ID format: [office_letter][state_code_2chars][cycle_digit][seq_5chars]
  Example: S2AZ00350 = Senate, AZ, cycle starting 2012, sequence 00350
  Example: H2LA04020 = House, LA, cycle 2012, district 04, sequence 020

USAGE:
  python3 scripts/structural_id_cleanup.py

NO API KEY REQUIRED.

OUTPUTS:
  data/fec.json — patched in place
  data/structural_cleanup_report.md — list of changes
  data/members_to_refetch.json — APPENDS to existing list (members whose primary changed)
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FEC_FILE      = DATA_DIR / "fec.json"
SENATE_FILE   = DATA_DIR / "senate.json"
HOUSE_FILE    = DATA_DIR / "house.json"
REPORT_FILE   = DATA_DIR / "structural_cleanup_report.md"
REFETCH_FILE  = DATA_DIR / "members_to_refetch.json"


def parse_id(cid):
    """Returns (office, state) parsed from a candidate ID, or (None, None) if invalid."""
    if not cid or len(cid) < 4:
        return None, None
    office = cid[0].upper()
    state = cid[2:4].upper()
    if office not in ("H", "S", "P"):
        return None, None
    return office, state


def main():
    if not all(f.exists() for f in [FEC_FILE, SENATE_FILE, HOUSE_FILE]):
        sys.exit("ERROR: required files missing")
    
    fec_data = json.loads(FEC_FILE.read_text())
    senate_data = json.loads(SENATE_FILE.read_text())
    house_data = json.loads(HOUSE_FILE.read_text())
    
    member_info = {}
    for s in senate_data:
        member_info[s["name"]] = ("S", s["state"])
    for h in house_data:
        member_info[h["name"]] = ("H", h["state"])
    
    # Already-existing refetch queue (from audit run)
    existing_refetch = set()
    if REFETCH_FILE.exists():
        existing_refetch = set(json.loads(REFETCH_FILE.read_text()))
    
    primary_changed = []
    contamination_cleaned = []
    no_valid_ids = []  # all IDs were bogus — needs human review
    no_change = []
    
    for name, expected in member_info.items():
        expected_office, expected_state = expected
        rec = fec_data.get(name)
        if not rec:
            continue
        
        all_ids = list(rec.get("all_candidate_ids", []))
        old_primary = rec.get("candidate_id", "")
        
        # Filter to valid IDs (matching office + state)
        valid_ids = []
        dropped_ids = []
        for cid in all_ids:
            office, state = parse_id(cid)
            if office is None:
                dropped_ids.append(cid)  # malformed
                continue
            if office == expected_office and state == expected_state:
                valid_ids.append(cid)
            else:
                dropped_ids.append(cid)
        
        if not valid_ids:
            # All IDs were bogus — leave alone but log
            if dropped_ids:
                no_valid_ids.append({
                    "name": name,
                    "office": expected_office,
                    "state": expected_state,
                    "all_dropped": dropped_ids,
                })
            else:
                no_change.append(name)
            continue
        
        # Pick new primary: keep current if still valid, else use first valid
        new_primary = old_primary if old_primary in valid_ids else valid_ids[0]
        
        # Did anything change?
        if new_primary == old_primary and set(valid_ids) == set(all_ids):
            no_change.append(name)
            continue
        
        # Apply the change
        rec["candidate_id"] = new_primary
        rec["all_candidate_ids"] = valid_ids
        
        if new_primary != old_primary:
            primary_changed.append({
                "name": name,
                "old_primary": old_primary,
                "new_primary": new_primary,
                "dropped": dropped_ids,
            })
        else:
            contamination_cleaned.append({
                "name": name,
                "kept": valid_ids,
                "dropped": dropped_ids,
            })
    
    # Save patched fec.json
    FEC_FILE.write_text(json.dumps(fec_data, indent=2))
    
    # Update refetch queue (anyone whose primary changed should be refetched)
    refetch_set = set(existing_refetch)
    for entry in primary_changed:
        refetch_set.add(entry["name"])
    REFETCH_FILE.write_text(json.dumps(sorted(refetch_set), indent=2))
    
    # Write report
    lines = [
        "# Structural ID Cleanup Report",
        f"_Run: {datetime.now(timezone.utc).isoformat()}_",
        "",
        "## Summary",
        "",
        f"- Members checked: {len(member_info)}",
        f"- Primary changed (needs refetch): {len(primary_changed)}",
        f"- Contamination cleaned (primary unchanged): {len(contamination_cleaned)}",
        f"- All IDs invalid (manual review needed): {len(no_valid_ids)}",
        f"- No change: {len(no_change)}",
        f"- Refetch queue size: {len(refetch_set)}",
        "",
    ]
    
    if primary_changed:
        lines.append("## Primary changed (these need Schedule E refetch)")
        lines.append("")
        for e in primary_changed:
            lines.append(f"- **{e['name']}**: `{e['old_primary']}` → `{e['new_primary']}` (dropped {e['dropped']})")
        lines.append("")
    
    if contamination_cleaned:
        lines.append(f"## Contamination cleaned ({len(contamination_cleaned)} members)")
        lines.append("")
        for e in contamination_cleaned[:30]:
            lines.append(f"- **{e['name']}**: dropped {e['dropped']}")
        if len(contamination_cleaned) > 30:
            lines.append(f"- ...and {len(contamination_cleaned) - 30} more")
        lines.append("")
    
    if no_valid_ids:
        lines.append("## Members with NO valid IDs — manual review needed")
        lines.append("")
        lines.append("These members have no candidate_id matching their state and office.")
        lines.append("Likely causes: incorrectly named in roster, recently appointed without yet running,")
        lines.append("or v7 fetch never found them. fec.json entries left unchanged.")
        lines.append("")
        for e in no_valid_ids:
            lines.append(f"- **{e['name']}** ({e['office']}/{e['state']}): had `{e['all_dropped']}`")
        lines.append("")
    
    REPORT_FILE.write_text("\n".join(lines))
    
    print(f"\n{'='*60}")
    print(f"STRUCTURAL CLEANUP COMPLETE")
    print(f"{'='*60}")
    print(f"  Primary changed: {len(primary_changed)}")
    print(f"  Contamination cleaned: {len(contamination_cleaned)}")
    print(f"  No valid IDs (review needed): {len(no_valid_ids)}")
    print(f"  No change: {len(no_change)}")
    print(f"  → Refetch queue: {len(refetch_set)}")


if __name__ == "__main__":
    main()
