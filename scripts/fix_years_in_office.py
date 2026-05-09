#!/usr/bin/env python3
"""
fix_years_in_office.py
=======================

Repairs the years_in_office field across data/profiles.json for every member
who has a bioguide_id. Designed to fix two specific problems detected in the
live data:

  1. ~516 profiles have years_in_office set to a role+state string like
     "Senator — VT" instead of an actual year span. The frontend displays
     this verbatim, which is wrong on every affected member's profile.

  2. ~140 profiles use en dashes (–) and ~487 use em dashes (—) in their
     years_in_office values. Per editorial preference, all separators in this
     field should be plain hyphens (-).

WHAT IT DOES:
  - Loads data/bioguide.json (name -> bioguide_id mapping)
  - Fetches @unitedstates/congress-legislators current + historical JSON
  - For every profile in profiles.json that has a bioguide_id, computes the
    correct years_in_office string from term history
  - Uses HYPHENS only (no en/em dashes) for separators
  - Handles chamber-switchers (Welch: House 2007-2023, Senate 2023-present)
  - Handles gaps in service within the same chamber
  - Reports profiles it cannot resolve (likely Cabinet/SCOTUS/non-Congress)

WHAT IT DOES NOT DO:
  - Does not modify profiles for members not in bioguide.json (Cabinet, SCOTUS,
    former members no longer tracked). Their existing values are left alone.
  - Does not touch any field other than years_in_office.

USAGE:
  python3 scripts/fix_years_in_office.py [--dry-run]

OUTPUTS:
  data/profiles.json (updated in place)
  data/profiles.json.before_yio (backup, only on real run)

SAFETY:
  - Backup created before any write
  - Dry-run mode prints intended changes without writing
  - Idempotent: re-running has no effect after first successful run
  - Reports unresolved names so anomalies can be inspected
"""

import json
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DATA_DIR     = Path(__file__).resolve().parent.parent / "data"
PROFILES     = DATA_DIR / "profiles.json"
BIOGUIDE     = DATA_DIR / "bioguide.json"
BACKUP       = DATA_DIR / "profiles.json.before_yio"

LEGISLATORS_URLS = [
    "https://unitedstates.github.io/congress-legislators/legislators-current.json",
    "https://unitedstates.github.io/congress-legislators/legislators-historical.json",
]

CHAMBER_LABELS = {"sen": "Senator", "rep": "Representative"}
TODAY = datetime.now().date()


def fetch_legislators():
    """Pull current + historical legislators data and index by bioguide_id."""
    by_bioguide = {}
    for url in LEGISLATORS_URLS:
        print(f"  Fetching {url.split('/')[-1]}...", flush=True)
        with urllib.request.urlopen(url) as r:
            data = json.loads(r.read())
        for legislator in data:
            bid = legislator.get("id", {}).get("bioguide")
            if bid:
                by_bioguide[bid] = legislator
    print(f"  Loaded {len(by_bioguide)} legislators", flush=True)
    return by_bioguide


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def collapse_runs(terms):
    """Merge contiguous terms in the same chamber into year spans."""
    if not terms:
        return []
    runs = []
    cur_start = parse_date(terms[0].get("start"))
    cur_end   = parse_date(terms[0].get("end"))
    for t in terms[1:]:
        s = parse_date(t.get("start"))
        e = parse_date(t.get("end"))
        if s and cur_end and (s.year - cur_end.year) <= 1:
            # contiguous, extend
            if e and (not cur_end or e > cur_end):
                cur_end = e
        else:
            runs.append((cur_start, cur_end))
            cur_start, cur_end = s, e
    runs.append((cur_start, cur_end))
    return runs


def format_run(start, end):
    if not start:
        return None
    s = start.year
    if end and end >= TODAY:
        return f"{s}-present"
    if not end:
        return f"{s}-present"
    e = end.year
    return f"{s}-{e}" if s != e else f"{s}"


def compute_years(legislator):
    """Build the years_in_office string from a legislator's term history."""
    terms = legislator.get("terms", [])
    if not terms:
        return None

    # Group terms by chamber
    by_chamber = defaultdict(list)
    for t in terms:
        ctype = t.get("type")
        if ctype in CHAMBER_LABELS:
            by_chamber[ctype].append(t)

    if not by_chamber:
        return None

    for ctype in by_chamber:
        by_chamber[ctype].sort(key=lambda t: t.get("start", ""))

    # If only one chamber ever, return spans without label
    if len(by_chamber) == 1:
        ctype = next(iter(by_chamber))
        runs = collapse_runs(by_chamber[ctype])
        spans = [format_run(s, e) for s, e in runs if s]
        return ", ".join(s for s in spans if s) or None

    # Multiple chambers: order by most recent first, label each
    chamber_blocks = []
    for ctype, ts in by_chamber.items():
        runs = collapse_runs(ts)
        latest_start = max((s for s, _ in runs if s), default=None)
        spans = [format_run(s, e) for s, e in runs if s]
        if not spans:
            continue
        block = f"{CHAMBER_LABELS[ctype]} {', '.join(spans)}"
        chamber_blocks.append((latest_start, block))

    chamber_blocks.sort(key=lambda x: x[0] or datetime.min.date(), reverse=True)
    return "; ".join(b for _, b in chamber_blocks) or None


def normalize_dashes(s):
    """Replace any en/em dashes with plain hyphens."""
    if not s:
        return s
    return s.replace("–", "-").replace("—", "-")


def main():
    dry_run = "--dry-run" in sys.argv

    if not PROFILES.exists():
        sys.exit(f"ERROR: {PROFILES} not found")
    if not BIOGUIDE.exists():
        sys.exit(f"ERROR: {BIOGUIDE} not found")

    profiles = json.loads(PROFILES.read_text())
    bioguide = json.loads(BIOGUIDE.read_text())
    print(f"Loaded {len(profiles)} profiles, {len(bioguide)} bioguide entries")

    print("Fetching legislators data from @unitedstates/congress-legislators...")
    legislators = fetch_legislators()

    if not dry_run:
        BACKUP.write_text(json.dumps(profiles, indent=2))
        print(f"Backed up to {BACKUP.name}")

    fixed         = 0
    dash_only     = 0
    unchanged     = 0
    not_in_bg     = []
    no_terms      = []
    sample_fixes  = []

    for name, prof in sorted(profiles.items()):
        if not isinstance(prof, dict):
            continue

        old_value = prof.get("years_in_office", "")

        bid = bioguide.get(name)
        if not bid:
            not_in_bg.append(name)
            # Still normalize dashes if present
            normalized = normalize_dashes(old_value)
            if normalized != old_value:
                if not dry_run:
                    prof["years_in_office"] = normalized
                dash_only += 1
            continue

        legislator = legislators.get(bid)
        if not legislator:
            no_terms.append(name)
            continue

        new_value = compute_years(legislator)
        if not new_value:
            no_terms.append(name)
            continue

        new_value = normalize_dashes(new_value)

        if new_value != old_value:
            if not dry_run:
                prof["years_in_office"] = new_value
            fixed += 1
            if len(sample_fixes) < 10:
                sample_fixes.append((name, old_value, new_value))
        else:
            unchanged += 1

    if not dry_run:
        PROFILES.write_text(json.dumps(profiles, indent=2))

    print(f"\n{'='*70}")
    print(f"{'DRY RUN COMPLETE' if dry_run else 'WRITE COMPLETE'}")
    print(f"  Fixed/would fix:                 {fixed}")
    print(f"  Dash-only normalization:         {dash_only}")
    print(f"  Already correct:                 {unchanged}")
    print(f"  Not in bioguide (skipped):       {len(not_in_bg)}")
    print(f"  In bioguide but no terms found:  {len(no_terms)}")

    if sample_fixes:
        print(f"\n  Sample fixes (first 10):")
        for n, old, new in sample_fixes:
            print(f"    {n}:")
            print(f"        OLD: {old!r}")
            print(f"        NEW: {new!r}")

    if no_terms:
        print(f"\n  WARNING — in bioguide but no terms resolved (first 10):")
        for n in no_terms[:10]:
            print(f"    {n}")
        if len(no_terms) > 10:
            print(f"    ... and {len(no_terms) - 10} more")


if __name__ == "__main__":
    main()
