#!/usr/bin/env python3
"""
refresh_total_raised.py
=======================
Refreshes the `total_raised` field for every member in data/fec.json
using FEC's authoritative /committee/{id}/totals/ endpoint, which
returns the official summary totals filed by each committee.

The existing v7 `total_raised` field is the sum of Schedule A
itemized receipts seen during the original walk, which significantly
undercounts (it's typically $1-2M for senators who actually raised
hundreds of millions). This script replaces those values with the
official cycle-by-cycle totals summed across every authorized committee.

USAGE (in GitHub Actions):
  Reads FEC_API_KEY from environment.
  Reads/writes data/fec.json in the repo root.

USAGE (locally):
  export FEC_API_KEY="your_key_here"
  python3 refresh_total_raised.py
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse
import urllib.error

API_KEY = os.environ.get("FEC_API_KEY")
if not API_KEY:
    sys.exit("ERROR: FEC_API_KEY environment variable not set")

BASE = "https://api.open.fec.gov/v1"

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT   = os.path.dirname(SCRIPT_DIR)
FEC_FILE    = os.path.join(REPO_ROOT, "data", "fec.json")
PROGRESS    = os.path.join(REPO_ROOT, "data", "fec.json.progress")

# Tunables
# FEC API limit (upgraded tier): 7,200 requests/hour = 1 per 0.5 seconds.
# We use 0.6s for a small safety margin.
SLEEP_BETWEEN_CALLS = 0.6    # seconds — 1 call per 0.6s = 6,000/hour, safely under upgraded limit
SAVE_EVERY          = 25     # save progress every N members
MAX_RETRIES         = 5      # retries on transient errors
RATE_LIMIT_WAIT     = 30     # seconds to wait if we still hit a 429 somehow


def fetch_committee_total(committee_id):
    """
    Query /committee/{id}/totals/ and sum receipts across all cycles.
    Returns the lifetime total raised by this committee.
    """
    url = f"{BASE}/committee/{committee_id}/totals/?" + urllib.parse.urlencode({
        "api_key":  API_KEY,
        "per_page": 100,
    })
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "InfluenceRegistry/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            total = sum(
                (cycle.get("receipts") or 0)
                for cycle in (data.get("results") or [])
            )
            return total
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Rate limited. Wait a long time before retrying.
                # FEC's hourly bucket needs to drain.
                wait = RATE_LIMIT_WAIT * (attempt + 1)
                print(f"      rate limited (429), waiting {wait}s")
                time.sleep(wait)
            elif attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"      retry {attempt + 1} after {wait}s (HTTP {e.code})")
                time.sleep(wait)
            else:
                print(f"      FAILED after {MAX_RETRIES} attempts: HTTP {e.code}")
                return None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"      retry {attempt + 1} after {wait}s ({type(e).__name__})")
                time.sleep(wait)
            else:
                print(f"      FAILED after {MAX_RETRIES} attempts: {e}")
                return None
        except Exception as e:
            print(f"      unexpected error: {e}")
            return None
    return None


def main():
    if not os.path.exists(FEC_FILE):
        sys.exit(f"ERROR: {FEC_FILE} not found")

    with open(FEC_FILE) as f:
        fec = json.load(f)

    print(f"Loaded {len(fec)} members from {FEC_FILE}")

    # Resume support: if a previous progress file exists, use it
    completed = set()
    if os.path.exists(PROGRESS):
        with open(PROGRESS) as f:
            try:
                completed = set(json.load(f))
                print(f"Resuming — {len(completed)} members already done\n")
            except Exception:
                completed = set()

    failures = []
    updates  = 0
    total    = len(fec)

    for i, (name, entry) in enumerate(sorted(fec.items())):
        if name in completed:
            continue

        committees = entry.get("committees") or []
        if not committees:
            print(f"[{i + 1:3d}/{total}] {name:<36} skip (no committees)")
            continue

        print(f"[{i + 1:3d}/{total}] {name:<36}", end=" ", flush=True)

        new_total = 0
        any_failed = False
        for cid in committees:
            committee_total = fetch_committee_total(cid)
            if committee_total is None:
                any_failed = True
                continue
            new_total += committee_total
            time.sleep(SLEEP_BETWEEN_CALLS)

        if any_failed and new_total == 0:
            failures.append(name)
            print(f"✗ all committees failed")
            continue

        old_total = entry.get("total_raised", 0)
        entry["total_raised"] = round(new_total)
        updates += 1
        completed.add(name)

        delta_pct = ""
        if old_total > 0:
            delta = (new_total / old_total) - 1
            delta_pct = f" ({delta:+.0%})"
        print(f"${old_total:>12,} -> ${round(new_total):>14,}{delta_pct}")

        # Save progress periodically
        if updates % SAVE_EVERY == 0:
            with open(FEC_FILE, "w") as f:
                json.dump(fec, f, indent=2)
            with open(PROGRESS, "w") as f:
                json.dump(sorted(completed), f)
            print(f"  ──── Saved progress ({updates} updated) ────")

    # Final save
    with open(FEC_FILE, "w") as f:
        json.dump(fec, f, indent=2)

    # Clean up progress file on successful completion
    if os.path.exists(PROGRESS):
        os.remove(PROGRESS)

    print(f"\n{'='*60}")
    print(f"Done. Updated {updates} of {total} members.")
    if failures:
        print(f"\n{len(failures)} members failed and were left unchanged:")
        for f in failures[:20]:
            print(f"  - {f}")
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more")


if __name__ == "__main__":
    main()
