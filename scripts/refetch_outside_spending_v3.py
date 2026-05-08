#!/usr/bin/env python3
"""
refetch_outside_spending_v3.py
================================

CORRECTED outside spending fetcher. Fixes the 24/48-hour notice
double-counting bug in v2.

THE BUG IN v2:
  v2 used /schedules/schedule_e/by_candidate/ which excludes memos but
  still includes 24/48-hour notice filings. When a committee files a
  24-hour report ($1M attack ad on Nov 3), then files a quarterly
  report a few months later that includes the same $1M, by_candidate
  sums BOTH. For members with heavy late-cycle ad activity (Warnock,
  Kelly, Cortez Masto, etc.), this inflates totals by 30-100%.

  Confirmed against FEC.gov: their web display reads "These totals are
  drawn from quarterly, monthly and semi-annual reports. 24- and
  48-Hour Reports of independent expenditures aren't included." The
  underlying URL filter is is_notice=false.

THE v3 FIX:
  Switch from /schedules/schedule_e/by_candidate/ (server-aggregated,
  no is_notice filter) to /schedules/schedule_e/ (raw itemized) with
  is_notice=false explicitly set. Aggregate by committee in our code.
  This matches FEC.gov's published methodology exactly.

USAGE:
  export FEC_API_KEY="..."

  Test mode (3 well-known members, fast, prints results for spot-check):
    python3 scripts/refetch_outside_spending_v3.py --test

  Specific candidate IDs (e.g. spot-check one senator):
    python3 scripts/refetch_outside_spending_v3.py --candidates S0GA00559

  Refetch members listed in data/members_to_refetch.json:
    python3 scripts/refetch_outside_spending_v3.py

  Refetch every member in fec.json (slow, several hours):
    python3 scripts/refetch_outside_spending_v3.py --all

OUTPUTS:
  data/outside_spending.json          (overwritten with v3 data)
  data/outside_spending.json.before_v3 (backup of v2 data)
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import urllib.request
import urllib.error

# Tunables
SLEEP_BETWEEN_CALLS = 0.6
SAVE_EVERY = 25
MAX_RETRIES = 5
RATE_LIMIT_WAIT = 30
TOP_N_SPENDERS = 5
PER_PAGE = 100

API_BASE = "https://api.open.fec.gov/v1"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FEC_FILE = DATA_DIR / "fec.json"
OUT_FILE = DATA_DIR / "outside_spending.json"
BACKUP_FILE = DATA_DIR / "outside_spending.json.before_v3"
REFETCH_FILE = DATA_DIR / "members_to_refetch.json"

# Test fixtures: candidates with publicly-reported career opposition totals,
# used as ground truth in --test mode. Numbers are "ballpark expected"
# from press coverage, not exact targets.
TEST_CANDIDATES = {
    "Raphael Warnock": {
        "candidate_ids": ["S0GA00559"],
        "expected_opposing_range": (110_000_000, 170_000_000),
        "note": "SLF alone $53.7M in 2022. AC, NRA, NRSC add ~$50M. 2020 added another ~$40-60M. Range: $110-170M.",
    },
    "Mark Kelly": {
        "candidate_ids": ["S0AZ00350"],
        "expected_opposing_range": (80_000_000, 130_000_000),
        "note": "DefendArizona $34M, SLF $29M, NRSC $27M in 2022 alone. Plus 2020 special. Range: $80-130M.",
    },
    "Tom Cotton": {
        "candidate_ids": ["S4AR00103"],
        "expected_opposing_range": (0, 5_000_000),
        "note": "2014 + 2020 in deep red AR, no real opposition. Should be near zero opposing.",
    },
}


def get_api_key():
    key = os.environ.get("FEC_API_KEY")
    if not key:
        sys.exit("ERROR: FEC_API_KEY not set")
    return key


def fec_get(path, params, api_key):
    """GET from FEC API with retries on rate limit and 5xx."""
    params = {**params, "api_key": api_key}
    url = f"{API_BASE}{path}?{urlencode(params)}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "InfluenceRegistry/1.0"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = RATE_LIMIT_WAIT * attempt
                print(f"      rate-limited, sleep {wait}s", flush=True)
                time.sleep(wait)
                continue
            if 500 <= e.code < 600:
                time.sleep(2 * attempt)
                continue
            print(f"      HTTP {e.code}: {url[:120]}", flush=True)
            return None
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"      net error attempt {attempt}: {e}", flush=True)
            time.sleep(2 * attempt)
    return None


def fetch_itemized_for_candidate(cand_id, api_key):
    """
    Use FEC's /schedules/schedule_e/ raw itemized endpoint with
    is_notice=false to match FEC.gov's web display methodology.

    Pages through all line items via keyset pagination, aggregates by
    committee in memory.

    Returns same shape as v2's fetch_aggregated_by_candidate output.
    """
    by_committee_support = {}
    by_committee_oppose = {}
    total_support = 0
    total_oppose = 0
    cycles_seen = set()
    pages_fetched = 0

    last_index = None
    last_expenditure_date = None

    while True:
        params = {
            "candidate_id": cand_id,
            "is_notice": "false",
            "data_type": "processed",
            "per_page": PER_PAGE,
            "sort": "expenditure_date",
        }
        # Keyset pagination: if we have last_indexes from previous page, use them
        if last_index is not None:
            params["last_index"] = last_index
        if last_expenditure_date is not None:
            params["last_expenditure_date"] = last_expenditure_date

        data = fec_get("/schedules/schedule_e/", params, api_key)
        if not data or "results" not in data:
            break

        results = data["results"]
        if not results:
            break

        pages_fetched += 1

        for r in results:
            amt = r.get("expenditure_amount") or 0
            if amt <= 0:
                continue

            so = (r.get("support_oppose_indicator") or "").upper()
            cid = r.get("committee_id")
            cname = r.get("committee_name") or "Unknown"
            cycle = r.get("cycle") or 0
            if cycle:
                cycles_seen.add(int(cycle))

            if so == "S":
                total_support += amt
                if cid:
                    if cid not in by_committee_support:
                        by_committee_support[cid] = {"name": cname, "amount": 0}
                    by_committee_support[cid]["amount"] += amt
            elif so == "O":
                total_oppose += amt
                if cid:
                    if cid not in by_committee_oppose:
                        by_committee_oppose[cid] = {"name": cname, "amount": 0}
                    by_committee_oppose[cid]["amount"] += amt

        # Pull next-page cursor
        pagination = data.get("pagination", {})
        last_indexes = pagination.get("last_indexes") or {}
        if not last_indexes:
            break
        new_last_index = last_indexes.get("last_index")
        new_last_date = last_indexes.get("last_expenditure_date")
        if new_last_index == last_index and new_last_date == last_expenditure_date:
            # Cursor didn't advance, stop to avoid infinite loop
            break
        last_index = new_last_index
        last_expenditure_date = new_last_date

        time.sleep(SLEEP_BETWEEN_CALLS)

    top_support = sorted(
        [
            {
                "committee_id": cid,
                "committee_name": v["name"],
                "amount": int(v["amount"]),
            }
            for cid, v in by_committee_support.items()
        ],
        key=lambda x: x["amount"],
        reverse=True,
    )[:TOP_N_SPENDERS]
    top_oppose = sorted(
        [
            {
                "committee_id": cid,
                "committee_name": v["name"],
                "amount": int(v["amount"]),
            }
            for cid, v in by_committee_oppose.items()
        ],
        key=lambda x: x["amount"],
        reverse=True,
    )[:TOP_N_SPENDERS]

    return {
        "total_supporting": int(total_support),
        "total_opposing": int(total_oppose),
        "cycles": sorted(cycles_seen),
        "top_supporters": top_support,
        "top_opposers": top_oppose,
        "_pages_fetched": pages_fetched,
    }


def fetch_member_outside_spending(name, candidate_ids, api_key):
    """Aggregate across all of a member's candidate IDs."""
    cand_ids = [c for c in candidate_ids if c]
    if not cand_ids:
        return None

    total_support = 0
    total_oppose = 0
    all_cycles = set()
    merged_support = {}
    merged_oppose = {}
    total_pages = 0

    for cid in cand_ids:
        result = fetch_itemized_for_candidate(cid, api_key)
        if result is None:
            continue
        total_support += result["total_supporting"]
        total_oppose += result["total_opposing"]
        all_cycles.update(result["cycles"])
        total_pages += result.get("_pages_fetched", 0)
        for entry in result["top_supporters"]:
            cid2 = entry["committee_id"]
            if cid2 not in merged_support:
                merged_support[cid2] = {"name": entry["committee_name"], "amount": 0}
            merged_support[cid2]["amount"] += entry["amount"]
        for entry in result["top_opposers"]:
            cid2 = entry["committee_id"]
            if cid2 not in merged_oppose:
                merged_oppose[cid2] = {"name": entry["committee_name"], "amount": 0}
            merged_oppose[cid2]["amount"] += entry["amount"]
        time.sleep(SLEEP_BETWEEN_CALLS)

    top_support = sorted(
        [
            {"committee_id": cid, "committee_name": v["name"], "amount": v["amount"]}
            for cid, v in merged_support.items()
        ],
        key=lambda x: x["amount"],
        reverse=True,
    )[:TOP_N_SPENDERS]
    top_oppose = sorted(
        [
            {"committee_id": cid, "committee_name": v["name"], "amount": v["amount"]}
            for cid, v in merged_oppose.items()
        ],
        key=lambda x: x["amount"],
        reverse=True,
    )[:TOP_N_SPENDERS]

    return {
        "candidate_ids_used": cand_ids,
        "total_supporting": total_support,
        "total_opposing": total_oppose,
        "cycles": sorted(all_cycles),
        "top_supporters": top_support,
        "top_opposers": top_oppose,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "FEC schedule_e (is_notice=false, processed) aggregated client-side",
        "_pages_fetched": total_pages,
    }


def save(data):
    """Atomic write."""
    tmp = OUT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(OUT_FILE)


def run_test_mode(api_key):
    """
    Spot-check v3 against publicly-reported numbers for 3 well-known
    senators. Prints results without writing to outside_spending.json.
    """
    print("\n" + "=" * 70)
    print("V3 TEST MODE: spot-checking against publicly-reported numbers")
    print("=" * 70)

    # Load v2 data for comparison if available
    v2_data = {}
    if OUT_FILE.exists():
        try:
            v2_data = json.loads(OUT_FILE.read_text())
        except Exception:
            pass

    all_pass = True
    for name, fixture in TEST_CANDIDATES.items():
        print(f"\n--- {name} ---")
        print(f"Note: {fixture['note']}")
        result = fetch_member_outside_spending(
            name, fixture["candidate_ids"], api_key
        )
        if result is None:
            print(f"  FETCH FAILED")
            all_pass = False
            continue

        opp = result["total_opposing"]
        sup = result["total_supporting"]
        lo, hi = fixture["expected_opposing_range"]

        v2_opp = v2_data.get(name, {}).get("total_opposing")
        v2_sup = v2_data.get(name, {}).get("total_supporting")

        print(f"  v3 supporting: ${sup:>14,}")
        print(f"  v3 opposing:   ${opp:>14,}")
        if v2_opp is not None:
            print(f"  v2 supporting: ${v2_sup:>14,}")
            print(f"  v2 opposing:   ${v2_opp:>14,}")
            if opp > 0:
                pct_change = ((opp - v2_opp) / v2_opp) * 100 if v2_opp else 0
                print(f"  delta on opposing: {pct_change:+.1f}% vs v2")

        if lo <= opp <= hi:
            print(f"  PASS: opposing $ in expected range (${lo:,} - ${hi:,})")
        else:
            print(f"  WARN: opposing $ outside expected range (${lo:,} - ${hi:,})")
            all_pass = False

        print(f"  Top opposers (v3):")
        for s in result["top_opposers"][:3]:
            print(f"    ${s['amount']:>13,}  {s['committee_name'][:50]}")
        print(f"  Pages fetched: {result.get('_pages_fetched', '?')}")

    print("\n" + "=" * 70)
    if all_pass:
        print("ALL TESTS PASSED. Safe to run --all for full refetch.")
    else:
        print("SOME TESTS OUT OF EXPECTED RANGE. Review numbers before --all.")
    print("=" * 70)
    return all_pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all",
        action="store_true",
        help="Refetch all members in fec.json (slow, several hours)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run on 3 well-known senators and compare to expected numbers (no writes)",
    )
    parser.add_argument(
        "--candidates",
        type=str,
        default=None,
        help="Comma-separated candidate IDs to test (no writes), e.g. S0GA00559,S0AZ00350",
    )
    args = parser.parse_args()

    api_key = get_api_key()

    if args.test:
        run_test_mode(api_key)
        return

    if args.candidates:
        ids = [c.strip() for c in args.candidates.split(",") if c.strip()]
        print(f"Testing candidate IDs: {ids}")
        for cid in ids:
            print(f"\n--- {cid} ---")
            r = fetch_itemized_for_candidate(cid, api_key)
            if r is None:
                print(f"  FETCH FAILED")
                continue
            print(f"  supporting: ${r['total_supporting']:>14,}")
            print(f"  opposing:   ${r['total_opposing']:>14,}")
            print(f"  cycles:     {r['cycles']}")
            print(f"  top opposers:")
            for s in r["top_opposers"][:5]:
                print(f"    ${s['amount']:>13,}  {s['committee_name'][:50]}")
        return

    if not FEC_FILE.exists():
        sys.exit(f"ERROR: {FEC_FILE} not found")

    fec_data = json.loads(FEC_FILE.read_text())

    if args.all:
        members_to_fetch = sorted(fec_data.keys())
        print(
            f"Refetching ALL {len(members_to_fetch)} members in fec.json (slow, several hours)",
            flush=True,
        )
    elif REFETCH_FILE.exists():
        members_to_fetch = json.loads(REFETCH_FILE.read_text())
        print(
            f"Refetching {len(members_to_fetch)} members from members_to_refetch.json",
            flush=True,
        )
    else:
        sys.exit(
            "ERROR: no --all flag and no members_to_refetch.json. "
            "Try --test first to verify v3 is working."
        )

    out_data = {}
    if OUT_FILE.exists():
        existing = OUT_FILE.read_text()
        BACKUP_FILE.write_text(existing)
        print(
            f"Backed up existing outside_spending.json -> {BACKUP_FILE.name}",
            flush=True,
        )
        out_data = json.loads(existing)

    fetched = 0
    failed = []
    start_time = time.time()

    for i, name in enumerate(members_to_fetch, 1):
        rec = fec_data.get(name)
        if not rec:
            print(
                f"[{i:4d}/{len(members_to_fetch)}] {name:<35} (NOT IN fec.json, skip)",
                flush=True,
            )
            continue

        cand_ids = rec.get("all_candidate_ids") or [rec.get("candidate_id")]
        result = fetch_member_outside_spending(name, cand_ids, api_key)
        if result is None:
            print(
                f"[{i:4d}/{len(members_to_fetch)}] {name:<35} FAILED", flush=True
            )
            failed.append(name)
            continue

        out_data[name] = result
        fetched += 1
        sup = result["total_supporting"]
        opp = result["total_opposing"]
        pages = result.get("_pages_fetched", 0)

        elapsed = time.time() - start_time
        rate = fetched / elapsed if elapsed > 0 else 0
        eta_sec = (len(members_to_fetch) - i) / rate if rate > 0 else 0
        eta_min = eta_sec / 60

        print(
            f"[{i:4d}/{len(members_to_fetch)}] {name:<35} "
            f"S=${sup:>13,}  O=${opp:>13,}  "
            f"pages={pages}  ETA={eta_min:.0f}m",
            flush=True,
        )

        if fetched % SAVE_EVERY == 0:
            save(out_data)
            print(
                f"      Saved progress ({fetched} fetched, {eta_min:.0f}m remaining)",
                flush=True,
            )

        time.sleep(SLEEP_BETWEEN_CALLS)

    save(out_data)

    print(f"\n{'='*70}")
    print(f"V3 REFETCH COMPLETE")
    print(f"  Successfully refetched: {fetched}")
    print(f"  Failed: {len(failed)}")
    print(f"  Total time: {(time.time() - start_time) / 60:.1f} minutes")
    if failed:
        print(f"  Failed members:")
        for n in failed[:20]:
            print(f"    - {n}")


if __name__ == "__main__":
    main()
