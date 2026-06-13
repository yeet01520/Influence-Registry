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



def fetch_candidate_individual_total(candidate_ids):
    """
    Query /candidates/{cid}/totals/ for each candidate ID and sum
    individual_contributions across all cycles.
    This field covers ALL individual donors (itemized + unitemized)
    and exists on the candidate totals endpoint for H/S/P office types.
    Returns the lifetime individual contribution total.
    """
    total = 0
    for cid in candidate_ids:
        url = f"{BASE}/candidates/{cid}/totals/?" + urllib.parse.urlencode({
            "api_key":  API_KEY,
            "per_page": 40,
        })
        for attempt in range(MAX_RETRIES):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "InfluenceRegistry/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                total += sum(
                    (cycle.get("individual_contributions") or 0)
                    for cycle in (data.get("results") or [])
                )
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = RATE_LIMIT_WAIT * (attempt + 1)
                    print(f"      rate limited (429), waiting {wait}s")
                    time.sleep(wait)
                elif attempt < MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    time.sleep(wait)
                else:
                    break
            except Exception:
                break
        time.sleep(SLEEP_BETWEEN_CALLS)
    return round(total)

def fetch_committee_candidate_ids(committee_id):
    """
    Return the set of candidate IDs officially linked to a committee, via
    /committee/{id}/candidates/. Used to decide which member a committee's
    money really belongs to when the same committee is (wrongly) attached to
    multiple members in fec.json — a symptom of same-surname candidate-ID
    contamination. Returns an empty set on failure (caller treats as 'unknown').
    """
    url = f"{BASE}/committee/{committee_id}/candidates/?" + urllib.parse.urlencode({
        "api_key":  API_KEY,
        "per_page": 100,
    })
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "InfluenceRegistry/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            return {
                c.get("candidate_id")
                for c in (data.get("results") or [])
                if c.get("candidate_id")
            }
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = RATE_LIMIT_WAIT * (attempt + 1)
                print(f"      rate limited (429) on candidates, waiting {wait}s")
                time.sleep(wait)
            elif attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                return set()
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                return set()
    return set()


def build_shared_committee_map(fec):
    """
    Find committees attached to more than one member in fec.json. These are
    contaminated assignments (a committee belongs to exactly one candidate),
    so summing them into every listed member would inflate total_raised. We
    return {committee_id: set(member_names)} for only the shared ones, so the
    main loop can resolve true ownership via the FEC before counting them.
    """
    from collections import defaultdict
    cmte_members = defaultdict(set)
    for nm, ent in fec.items():
        if nm == "_meta" or not isinstance(ent, dict):
            continue
        for c in ent.get("committees") or []:
            cmte_members[c].add(nm)
    return {c: m for c, m in cmte_members.items() if len(m) > 1}


def main():
    if not os.path.exists(FEC_FILE):
        sys.exit(f"ERROR: {FEC_FILE} not found")

    with open(FEC_FILE) as f:
        fec = json.load(f)

    print(f"Loaded {len(fec)} members from {FEC_FILE}")

    # ── Resolve ownership of shared/contaminated committees ────────────────
    # A committee that appears under multiple members really belongs to just
    # one of them. Look up each shared committee's true candidate(s) once, and
    # build a set of (committee_id, member_name) pairs we are ALLOWED to count.
    # For a shared committee, only the member whose candidate_id is officially
    # linked to it gets credited; the others skip it (no double-count).
    shared = build_shared_committee_map(fec)
    if shared:
        print(f"Resolving ownership of {len(shared)} shared committee(s) to avoid double-counting...")
    allowed_pairs = set()   # (committee_id, member_name) we may count
    for cmte, members in shared.items():
        owners = fetch_committee_candidate_ids(cmte)
        time.sleep(SLEEP_BETWEEN_CALLS)
        matched_any = False
        for nm in members:
            cid = (fec.get(nm) or {}).get("candidate_id")
            if cid and cid in owners:
                allowed_pairs.add((cmte, nm))
                matched_any = True
        # If the FEC lookup found no match (or failed), fall back to crediting
        # the single member whose name sorts first, so the money isn't dropped
        # entirely — flagged in the log for manual review.
        if not matched_any:
            fallback = sorted(members)[0]
            allowed_pairs.add((cmte, fallback))
            print(f"  [review] committee {cmte}: no candidate match among "
                  f"{sorted(members)}; credited to {fallback}")

    def may_count(cmte, member):
        """True unless this is a shared committee not owned by this member."""
        if cmte not in shared:
            return True
        return (cmte, member) in allowed_pairs

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
            # Skip shared committees this member doesn't actually own, so a
            # contaminated assignment can't inflate their total.
            if not may_count(cid, name):
                continue
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

        # Also refresh grassroots = all individual contributions
        all_cids = entry.get("all_candidate_ids") or []
        if not all_cids and entry.get("candidate_id"):
            all_cids = [entry["candidate_id"]]
        if all_cids:
            entry["grassroots"] = fetch_candidate_individual_total(all_cids)

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
