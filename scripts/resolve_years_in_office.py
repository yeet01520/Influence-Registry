#!/usr/bin/env python3
"""
resolve_years_in_office.py
==========================

Replaces placeholder years_in_office values in profiles.json with real
date spans, office-aware when a member has served in multiple chambers.

Format rules:
  - Single chamber, ever: "2019-present" or "2007-2017"
  - Multiple chambers: "Senator 2007-present; Representative 1991-2007"
    (most recent first, semicolon-separated)
  - Continuous service in same chamber: collapsed into one span
  - Gaps > 1 year in same chamber: separate spans within that chamber's group

Source: @unitedstates/congress-legislators (canonical, free).

USAGE:
  python3 scripts/resolve_years_in_office.py [--dry-run]

OUTPUTS:
  data/profiles.json (updated in place)
  data/profiles.json.before_years (backup)
"""

import json
import re
import sys
import urllib.request
from pathlib import Path
from datetime import date

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROFILES_FILE = DATA_DIR / "profiles.json"
BACKUP_FILE = DATA_DIR / "profiles.json.before_years"

CURRENT_URL = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
HISTORICAL_URL = "https://unitedstates.github.io/congress-legislators/legislators-historical.json"

# Map @unitedstates term "type" values to our display labels
CHAMBER_LABELS = {
    "sen": "Senator",
    "rep": "Representative",
}


def fetch_legislators():
    """Pull current + historical, return dict keyed by bioguide_id."""
    legislators = {}
    for url in [CURRENT_URL, HISTORICAL_URL]:
        print(f"Fetching {url}...", flush=True)
        req = urllib.request.Request(
            url, headers={"User-Agent": "InfluenceRegistry/1.0"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for person in data:
            bid = person.get("id", {}).get("bioguide")
            if bid:
                legislators[bid] = person
    print(f"Loaded {len(legislators)} legislator records", flush=True)
    return legislators


def collapse_runs(terms_for_chamber, today):
    """
    Given a list of terms in the same chamber (sorted by start),
    collapse continuous service into (start_year, end_year) tuples.
    Treats 1-year gaps as continuous (Jan 3 transitions, etc).
    Returns list of runs.
    """
    runs = []
    current_start = None
    current_end = None

    for t in terms_for_chamber:
        start = t.get("start", "")
        end = t.get("end", "")
        if not start:
            continue
        start_year = int(start[:4])
        end_year = int(end[:4]) if end else today.year

        if current_start is None:
            current_start = start_year
            current_end = end_year
        elif start_year - current_end <= 1:
            current_end = max(current_end, end_year)
        else:
            runs.append((current_start, current_end))
            current_start = start_year
            current_end = end_year

    if current_start is not None:
        runs.append((current_start, current_end))

    return runs


def format_run(start, end, is_present):
    """Format a single run as '2007-present' or '2007-2017'."""
    if is_present:
        return f"{start}-present"
    return f"{start}-{end}"


def compute_years_in_office(person):
    """
    Compute the years_in_office string for a legislator.
    Returns None if no usable term data.
    """
    terms = person.get("terms", [])
    if not terms:
        return None

    today = date.today()
    today_str = today.isoformat()

    # Group terms by chamber
    by_chamber = {}
    for t in terms:
        chamber = t.get("type")
        if chamber not in CHAMBER_LABELS:
            continue
        by_chamber.setdefault(chamber, []).append(t)

    if not by_chamber:
        return None

    # Sort each chamber's terms by start date
    for chamber in by_chamber:
        by_chamber[chamber].sort(key=lambda t: t.get("start", ""))

    # For each chamber, compute runs and determine if currently serving
    chamber_strs = []  # list of (most_recent_start, label, formatted_runs)
    for chamber, chamber_terms in by_chamber.items():
        runs = collapse_runs(chamber_terms, today)
        if not runs:
            continue

        # Is the most recent term in this chamber ongoing?
        last_term = chamber_terms[-1]
        is_current = last_term.get("end", "") >= today_str

        # Format each run; only the most recent run gets "present" if applicable
        formatted = []
        for i, (s, e) in enumerate(runs):
            is_last = i == len(runs) - 1
            formatted.append(format_run(s, e, is_present=(is_last and is_current)))

        run_str = ", ".join(formatted)
        most_recent_start = runs[-1][0]
        chamber_strs.append((most_recent_start, CHAMBER_LABELS[chamber], run_str))

    if not chamber_strs:
        return None

    # If only one chamber, no label needed
    if len(chamber_strs) == 1:
        return chamber_strs[0][2]

    # Multiple chambers: sort by most_recent_start descending, label each
    chamber_strs.sort(key=lambda x: x[0], reverse=True)
    parts = [f"{label} {runs}" for _, label, runs in chamber_strs]
    return "; ".join(parts)


def is_real_value(value):
    """
    Return True if value already looks like a real years_in_office string
    (so we leave it alone). Real values match these patterns:
      - "2019-present"
      - "2007-2017"
      - "Senator 2021-present; Representative 2007-2017"
      - "2011-2017, 2021-present" (multi-run single chamber)
    Placeholders like "Senator - VA" or "Senator — VA" don't match.
    """
    if not value or not isinstance(value, str):
        return False
    # Strict: at least one YYYY-YYYY or YYYY-present substring
    return bool(re.search(r"\b\d{4}-(\d{4}|present)\b", value))


def main():
    dry_run = "--dry-run" in sys.argv

    if not PROFILES_FILE.exists():
        sys.exit(f"ERROR: {PROFILES_FILE} not found")

    profiles = json.loads(PROFILES_FILE.read_text())
    legislators = fetch_legislators()

    if not dry_run:
        BACKUP_FILE.write_text(json.dumps(profiles, indent=2))
        print(f"Backed up to {BACKUP_FILE.name}", flush=True)

    fixed = 0
    skipped_real = 0
    no_bioguide = []
    no_match = []
    no_terms = []

    for name, profile in sorted(profiles.items()):
        old = profile.get("years_in_office", "")

        # If it already looks valid, skip
        if is_real_value(old):
            skipped_real += 1
            continue

        bid = profile.get("bioguide_id")
        if not bid:
            no_bioguide.append(name)
            continue

        person = legislators.get(bid)
        if not person:
            no_match.append((name, bid))
            continue

        new_value = compute_years_in_office(person)
        if not new_value:
            no_terms.append((name, bid))
            continue

        print(f"  {name}: '{old}' -> '{new_value}'", flush=True)
        if not dry_run:
            profile["years_in_office"] = new_value
        fixed += 1

    if not dry_run:
        tmp = PROFILES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(profiles, indent=2))
        tmp.replace(PROFILES_FILE)

    print(f"\n{'='*60}")
    print(f"{'DRY RUN COMPLETE' if dry_run else 'WRITE COMPLETE'}")
    print(f"  Fixed/would fix:           {fixed}")
    print(f"  Already valid (skipped):   {skipped_real}")
    print(f"  Missing bioguide_id:       {len(no_bioguide)}")
    print(f"  Bioguide not in dataset:   {len(no_match)}")
    print(f"  No usable terms data:      {len(no_terms)}")
    if no_bioguide[:5]:
        print(f"  Sample missing bioguide:   {no_bioguide[:5]}")
    if no_match[:5]:
        print(f"  Sample not in dataset:     {no_match[:5]}")
    if no_terms[:5]:
        print(f"  Sample no terms:           {no_terms[:5]}")


if __name__ == "__main__":
    main()
