#!/usr/bin/env python3
"""
refresh_sector_counts.py
========================
Updates the AIPAC entry in data/sector_counts.json from the current state of
data/fec.json, data/senate.json, and data/house.json. Other sectors (oilgas,
pharma, finance, defense, tech, superpac, anycorp) are left untouched since
they're manually curated.

Run after any FEC data refresh to keep the at-a-glance tab numbers in sync
with fec.json. Safe to re-run repeatedly: idempotent.

Methodology: a member counts as "AIPAC-influenced" if their fec.json record
has aipac_pacs + aipac_ie > 0. aipac_lobby_donors is preserved in the data
but does not by itself qualify a member as influenced under the current
TrackAIPAC-consistent definition.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR  = REPO_ROOT / "data"

FEC_PATH    = DATA_DIR / "fec.json"
SENATE_PATH = DATA_DIR / "senate.json"
HOUSE_PATH  = DATA_DIR / "house.json"
SC_PATH     = DATA_DIR / "sector_counts.json"

def load(p):
    if not p.exists():
        sys.exit(f"ERROR: required file not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))

def main():
    fec     = load(FEC_PATH)
    senate  = load(SENATE_PATH)
    house   = load(HOUSE_PATH)
    sc      = load(SC_PATH)

    # AIPAC member counts by party. A member qualifies if pacs + ie > 0
    # in their fec.json entry.
    counts = {"D": 0, "R": 0, "I": 0}
    qualified_names = []
    for m in senate + house:
        rec = fec.get(m["name"])
        if not rec:
            continue
        if rec.get("retired"):
            continue
        pacs = rec.get("aipac_pacs", 0) or 0
        ie   = rec.get("aipac_ie", 0) or 0
        if pacs + ie > 0:
            party = m.get("party")
            if party in counts:
                counts[party] += 1
                qualified_names.append(m["name"])

    # Dollar total: sum of fec.aipac across every record (which under the
    # current methodology equals sum of pacs + ie).
    total_usd = sum((r.get("aipac", 0) or 0)
                    for n, r in fec.items()
                    if isinstance(r, dict))

    # Diff against existing values for the commit message.
    old = sc.get("aipac", {})
    old_D     = old.get("D", 0)
    old_R     = old.get("R", 0)
    old_I     = old.get("I", 0)
    old_total = old.get("total_usd", 0)

    sc["aipac"] = {
        "D":         counts["D"],
        "R":         counts["R"],
        "I":         counts["I"],
        "total_usd": total_usd,
        "note": ("Career AIPAC contributions to current 119th Congress "
                 "members (direct PAC contributions + independent expenditures, "
                 "TrackAIPAC-consistent methodology). Source: TrackAIPAC.com "
                 "+ FEC PAC filings, 2021-2026. Auto-refreshed from data/fec.json "
                 "by scripts/refresh_sector_counts.py."),
    }

    SC_PATH.write_text(json.dumps(sc, indent=2) + "\n", encoding="utf-8")

    # Report.
    print(f"sector_counts.json AIPAC entry refreshed:")
    print(f"  D:         {old_D:>4} -> {counts['D']:>4} ({counts['D']-old_D:+d})")
    print(f"  R:         {old_R:>4} -> {counts['R']:>4} ({counts['R']-old_R:+d})")
    print(f"  I:         {old_I:>4} -> {counts['I']:>4} ({counts['I']-old_I:+d})")
    print(f"  total_usd: ${old_total:>12,} -> ${total_usd:>12,} "
          f"({total_usd-old_total:+,})")
    print(f"  qualified members: {sum(counts.values())}")

if __name__ == "__main__":
    main()
