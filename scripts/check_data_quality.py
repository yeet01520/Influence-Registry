#!/usr/bin/env python3
"""
check_data_quality.py
======================
Flags potential data-attribution bugs in the FEC payload, particularly
the name-collision pattern that caused Adelita Grijalva to display her
father Raul Grijalva's career data.

USAGE:
    python3 check_data_quality.py [path/to/fec_data.json]

If no path given, reads from index.html applyFECData() embedded payload.

THE BUG IT CATCHES:
fetch_fec_data.py uses token-set overlap matching (line 250 area). For a
member with one common token (surname) AND a state match, it produces
score=3 which passes the >=2 threshold, even though the other tokens
(first name) don't match. This is how Adelita Grijalva's record matched
her father Raul Grijalva's CSV row in OpenSecrets data.

WARNING SIGNS:
1. total_raised = 0 + large sector totals = high suspicion of stale/wrong data
2. cycle = "1990-2024" for a member sworn in post-2024 = wrong cycle attribution
3. sector total >> what's plausible for a member's actual time in office
"""

import json, re, sys
from pathlib import Path

SECTOR_KEYS = ['aipac', 'oil_gas', 'pharma', 'defense', 'finance', 'tech', 'fossil_fuels']


def load_fec_data(source):
    """Load FEC payload from JSON file or extract from index.html."""
    path = Path(source)
    if not path.exists():
        print(f"ERROR: {source} not found", file=sys.stderr)
        sys.exit(1)
    if path.suffix == '.html':
        html = path.read_text()
        m = re.search(r'applyFECData\((\{.*?\})\);', html, re.DOTALL)
        if not m:
            print("ERROR: could not find applyFECData payload in HTML", file=sys.stderr)
            sys.exit(1)
        return json.loads(m.group(1))
    return json.loads(path.read_text())


def load_new_members():
    """Members sworn in 2025+. Maintain this list as new members join."""
    return {
        "Adelita Grijalva",       # AZ-07, sworn in Nov 12 2025
        "Angela Alsobrooks",      # MD Senate, sworn in Jan 2025
        "Sam Liccardo",           # CA-16, sworn in Jan 2025
        "Eugene Vindman",         # VA-07, sworn in Jan 2025
        "Shomari Figures",        # AL-02, sworn in Jan 2025
        "Cleo Fields",            # LA-06 (returning, served 1993-1997)
        "Randy Fine",             # FL-06, sworn in 2025
        "Jimmy Patronis",         # FL-01, sworn in 2025
        "Brian Jack",             # GA-03, sworn in Jan 2025
        "Tom Barrett",            # MI-07, sworn in Jan 2025
        "Kristen McDonald Rivet", # MI-08, sworn in Jan 2025
        # Add as new members are seated
    }


def check_collisions(fec_data, new_members):
    flagged_high = []  # new members with implausible sector totals
    flagged_med = []   # entries with $0 total_raised but sector data
    flagged_low = []   # entries with stale cycle attribution
    
    for name, rec in fec_data.items():
        if not isinstance(rec, dict):
            continue
        sector_sum = sum(rec.get(k, 0) for k in SECTOR_KEYS 
                         if isinstance(rec.get(k), (int, float)))
        total_raised = rec.get('total_raised', 0) or 0
        cycle = rec.get('cycle', '')
        
        # HIGH: new member with significant sector data
        if name in new_members and sector_sum > 100_000:
            flagged_high.append((name, sector_sum, total_raised, cycle))
        
        # MEDIUM: $0 total raised but sector data exists
        elif sector_sum > 50_000 and total_raised == 0:
            flagged_med.append((name, sector_sum, total_raised, cycle))
        
        # LOW: new member with old cycle string
        elif name in new_members and '1990-2024' in str(cycle):
            flagged_low.append((name, sector_sum, total_raised, cycle))
    
    return flagged_high, flagged_med, flagged_low


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else 'index.html'
    fec_data = load_fec_data(source)
    new_members = load_new_members()
    
    print(f"Loaded FEC payload: {len(fec_data)} members")
    print(f"Tracking {len(new_members)} new (post-2024) members\n")
    
    high, med, low = check_collisions(fec_data, new_members)
    
    if high:
        print("🚨 HIGH RISK: New members with significant sector data")
        print("   (Likely name-collision with someone else's career data)")
        print("-" * 70)
        for name, ssum, tr, cycle in sorted(high, key=lambda x: -x[1]):
            print(f"  {name:30s}  sectors=${ssum:>11,}  raised=${tr:>10,}  cycle={cycle}")
        print()
    
    if med:
        print("⚠️  MEDIUM RISK: Sector data present but total_raised=0")
        print("   (May be missing data, may indicate attribution bug)")
        print("-" * 70)
        for name, ssum, tr, cycle in sorted(med, key=lambda x: -x[1]):
            print(f"  {name:30s}  sectors=${ssum:>11,}  cycle={cycle}")
        print()
    
    if low:
        print("ℹ️  LOW RISK: New members tagged with old cycle string")
        print("-" * 70)
        for name, ssum, tr, cycle in low:
            print(f"  {name:30s}  cycle={cycle}  sectors=${ssum:>10,}")
        print()
    
    if not (high or med or low):
        print("✓ No collision suspects found.")
    else:
        total = len(high) + len(med) + len(low)
        print(f"\nSummary: {len(high)} high, {len(med)} medium, {len(low)} low. Review each.")
        sys.exit(1 if high else 0)


if __name__ == '__main__':
    main()
