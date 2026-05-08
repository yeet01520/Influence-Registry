#!/usr/bin/env python3
"""
refetch_outside_spending_v2.py
================================

CORRECTED outside spending fetcher that uses FEC's pre-aggregated 
`/schedules/schedule_e/by_candidate/` endpoint instead of raw 
`/schedules/schedule_e/`.

FIXES THE DOUBLE-COUNTING BUG:
  The raw schedule_e endpoint returns ALL filings including:
    - memo entries (subitemizations of larger filings)
    - 24/48-hour notice filings (re-reported in regular filings)
    - amended filings (both original AND amendment)
  
  Our previous script summed all of these, causing 2-3x inflation.
  Example: Warnock showed $324M support; real number is ~$146M.
  Worker Power PAC for Georgia showed $146M; PAC only raised $1M total.

  The `by_candidate` endpoint pre-aggregates these by FEC, excluding 
  memos and only counting each unique expenditure once.

USAGE:
  export FEC_API_KEY="..."
  python3 scripts/refetch_outside_spending_v2.py [--all]

  --all: refetch every member in fec.json. Default: only members in 
         data/members_to_refetch.json (if present).

OUTPUTS:
  data/outside_spending.json — overwritten with corrected data
  data/outside_spending.json.before_v2 — backup of broken data
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

# ---------- Tunables ----------
SLEEP_BETWEEN_CALLS = 0.6
SAVE_EVERY          = 25
MAX_RETRIES         = 5
RATE_LIMIT_WAIT     = 30
TOP_N_SPENDERS      = 5

# Sanity warning threshold — log records over this for human review
# (No hard cap needed — by_candidate endpoint gives clean aggregates)
WARN_PER_COMMITTEE = 100_000_000  # $100M per committee per candidate is huge

API_BASE  = "https://api.open.fec.gov/v1"
DATA_DIR  = Path(__file__).resolve().parent.parent / "data"
FEC_FILE     = DATA_DIR / "fec.json"
OUT_FILE     = DATA_DIR / "outside_spending.json"
BACKUP_FILE  = DATA_DIR / "outside_spending.json.before_v2"
REFETCH_FILE = DATA_DIR / "members_to_refetch.json"


def get_api_key():
    key = os.environ.get("FEC_API_KEY")
    if not key:
        sys.exit("ERROR: FEC_API_KEY not set")
    return key


def fec_get(path, params, api_key):
    """GET from FEC API with retries."""
    params = {**params, "api_key": api_key}
    url = f"{API_BASE}{path}?{urlencode(params)}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "InfluenceRegistry/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
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


def fetch_aggregated_by_candidate(cand_id, api_key):
    """
    Use FEC's /schedules/schedule_e/by_candidate/ endpoint.
    Returns aggregated totals per (committee, support/oppose) — memos already excluded.
    
    Each result record has:
      - candidate_id
      - committee_id, committee_name
      - support_oppose_indicator: 'S' or 'O'
      - cycle (election cycle)
      - total (aggregated dollar amount)
    """
    by_committee_support = {}
    by_committee_oppose  = {}
    total_support = 0
    total_oppose  = 0
    cycles_seen = set()
    warnings = 0

    page = 1
    while True:
        params = {
            "candidate_id": cand_id,
            "per_page": 100,
            "page": page,
        }
        data = fec_get("/schedules/schedule_e/by_candidate/", params, api_key)
        if not data or "results" not in data:
            break
        
        results = data["results"]
        if not results:
            break
        
        for r in results:
            amt = r.get("total") or 0
            if amt <= 0:
                continue
            
            so = (r.get("support_oppose_indicator") or "").upper()
            cid = r.get("committee_id")
            cname = r.get("committee_name") or "Unknown"
            cycle = r.get("cycle") or 0
            if cycle:
                cycles_seen.add(int(cycle))
            
            # Sanity warning (not a hard cap — these are pre-aggregated)
            if amt > WARN_PER_COMMITTEE:
                warnings += 1
                print(f"      ℹ very large: ${amt:,} from {cname[:40]} cycle {cycle} ({so})", flush=True)
            
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
        
        pagination = data.get("pagination", {})
        pages = pagination.get("pages", 1)
        if page >= pages:
            break
        page += 1
        time.sleep(SLEEP_BETWEEN_CALLS)
    
    top_support = sorted(
        [{"committee_id": cid, "committee_name": v["name"], "amount": int(v["amount"])}
         for cid, v in by_committee_support.items()],
        key=lambda x: x["amount"], reverse=True
    )[:TOP_N_SPENDERS]
    top_oppose = sorted(
        [{"committee_id": cid, "committee_name": v["name"], "amount": int(v["amount"])}
         for cid, v in by_committee_oppose.items()],
        key=lambda x: x["amount"], reverse=True
    )[:TOP_N_SPENDERS]
    
    return {
        "total_supporting": int(total_support),
        "total_opposing": int(total_oppose),
        "cycles": sorted(cycles_seen),
        "top_supporters": top_support,
        "top_opposers": top_oppose,
    }


def fetch_member_outside_spending(name, fec_record, api_key):
    """Aggregate across all of a member's candidate IDs."""
    cand_ids = fec_record.get("all_candidate_ids") or [fec_record.get("candidate_id")]
    cand_ids = [c for c in cand_ids if c]
    if not cand_ids:
        return None
    
    total_support = 0
    total_oppose  = 0
    all_cycles    = set()
    merged_support = {}
    merged_oppose  = {}
    
    for cid in cand_ids:
        result = fetch_aggregated_by_candidate(cid, api_key)
        if result is None:
            continue
        total_support += result["total_supporting"]
        total_oppose  += result["total_opposing"]
        all_cycles.update(result["cycles"])
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
        [{"committee_id": cid, "committee_name": v["name"], "amount": v["amount"]}
         for cid, v in merged_support.items()],
        key=lambda x: x["amount"], reverse=True
    )[:TOP_N_SPENDERS]
    top_oppose = sorted(
        [{"committee_id": cid, "committee_name": v["name"], "amount": v["amount"]}
         for cid, v in merged_oppose.items()],
        key=lambda x: x["amount"], reverse=True
    )[:TOP_N_SPENDERS]
    
    return {
        "candidate_ids_used": cand_ids,
        "total_supporting": total_support,
        "total_opposing": total_oppose,
        "cycles": sorted(all_cycles),
        "top_supporters": top_support,
        "top_opposers": top_oppose,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "FEC schedule_e/by_candidate (memos excluded)",
    }


def save(data):
    """Atomic write."""
    tmp = OUT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(OUT_FILE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", 
                        help="Refetch all members (not just members_to_refetch.json)")
    args = parser.parse_args()
    
    api_key = get_api_key()
    
    if not FEC_FILE.exists():
        sys.exit(f"ERROR: {FEC_FILE} not found")
    
    fec_data = json.loads(FEC_FILE.read_text())
    
    # Determine member list
    if args.all:
        members_to_fetch = sorted(fec_data.keys())
        print(f"Refetching ALL {len(members_to_fetch)} members in fec.json", flush=True)
    elif REFETCH_FILE.exists():
        members_to_fetch = json.loads(REFETCH_FILE.read_text())
        print(f"Refetching {len(members_to_fetch)} members from members_to_refetch.json", flush=True)
    else:
        sys.exit("ERROR: no --all flag and no members_to_refetch.json")
    
    # Backup current outside_spending.json
    out_data = {}
    if OUT_FILE.exists():
        existing = OUT_FILE.read_text()
        BACKUP_FILE.write_text(existing)
        print(f"Backed up existing outside_spending.json -> {BACKUP_FILE.name}", flush=True)
        out_data = json.loads(existing)
    
    fetched = 0
    failed = []
    
    for i, name in enumerate(members_to_fetch, 1):
        rec = fec_data.get(name)
        if not rec:
            print(f"[{i:4d}/{len(members_to_fetch)}] {name:<35} (NOT IN fec.json — skip)", flush=True)
            continue
        
        result = fetch_member_outside_spending(name, rec, api_key)
        if result is None:
            print(f"[{i:4d}/{len(members_to_fetch)}] {name:<35} FAILED", flush=True)
            failed.append(name)
            continue
        
        out_data[name] = result
        fetched += 1
        sup = result["total_supporting"]
        opp = result["total_opposing"]
        print(
            f"[{i:4d}/{len(members_to_fetch)}] {name:<35} "
            f"support=${sup:>14,}  oppose=${opp:>13,}  "
            f"top={len(result['top_supporters'])}",
            flush=True,
        )
        
        if fetched % SAVE_EVERY == 0:
            save(out_data)
            print(f"      ── Saved progress ({fetched} fetched) ──", flush=True)
        
        time.sleep(SLEEP_BETWEEN_CALLS)
    
    save(out_data)
    
    print(f"\n{'='*60}")
    print(f"V2 REFETCH COMPLETE")
    print(f"  Successfully refetched: {fetched}")
    print(f"  Failed: {len(failed)}")
    if failed:
        print(f"  Failed members:")
        for n in failed[:20]:
            print(f"    - {n}")


if __name__ == "__main__":
    main()
