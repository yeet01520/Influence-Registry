#!/usr/bin/env python3
"""
refetch_outside_spending.py
============================

Targeted Schedule E (outside spending) refresh that ONLY re-fetches data
for members in data/members_to_refetch.json. Used after audit_candidate_ids.py
fixes wrong/contaminated candidate IDs in fec.json.

This is much faster than the full refresh-outside-spending workflow because
it only processes ~20-50 affected members instead of all 538. Estimated runtime:
20-30 minutes.

USAGE:
  export FEC_API_KEY="..."
  python3 scripts/refetch_outside_spending.py

INPUTS:
  data/fec.json — must already be patched by audit_candidate_ids.py
  data/members_to_refetch.json — list of member names to re-fetch
  data/outside_spending.json — existing data, will be updated in place

OUTPUTS:
  data/outside_spending.json — updated with fresh data for affected members
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import urllib.request
import urllib.error

# ---------- Tunables ----------
SLEEP_BETWEEN_CALLS = 0.6
SAVE_EVERY          = 10
MAX_RETRIES         = 5
RATE_LIMIT_WAIT     = 30
TOP_N_SPENDERS      = 5

# Sanity check on individual Schedule E records.
# Real-world context:
#   - Largest known single IE in US history: ~$20M (presidential primaries)
#   - Senate race single ad buys: rarely exceed $10M
#   - Luttrell anomaly that prompted this check: $6.3 BILLION (clearly a data error)
# We use a $100M hard cap (5x safety margin over realistic max)
# and log a warning above $25M for human review.
MAX_PLAUSIBLE_SINGLE_EXPENDITURE = 100_000_000  # $100M hard cap
WARN_THRESHOLD                   = 25_000_000   # $25M log threshold

API_BASE  = "https://api.open.fec.gov/v1"
DATA_DIR  = Path(__file__).resolve().parent.parent / "data"
FEC_FILE     = DATA_DIR / "fec.json"
OUT_FILE     = DATA_DIR / "outside_spending.json"
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
            print(f"      HTTP {e.code}: {url[:100]}", flush=True)
            return None
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"      net error attempt {attempt}: {e}", flush=True)
            time.sleep(2 * attempt)
    return None


def fetch_outside_spending_for_candidate(cand_id, api_key):
    """Fetch all Schedule E records for a candidate."""
    by_committee_support = {}
    by_committee_oppose  = {}
    total_support = 0
    total_oppose  = 0
    cycles_seen = set()
    skipped_anomalies = 0
    
    page = 1
    while True:
        params = {
            "candidate_id": cand_id,
            "per_page": 100,
            "page": page,
            "sort": "-expenditure_date",
        }
        data = fec_get("/schedules/schedule_e/", params, api_key)
        if not data or "results" not in data:
            break
        
        results = data["results"]
        if not results:
            break
        
        for r in results:
            amt = r.get("expenditure_amount") or 0
            if amt <= 0:
                continue
            # SANITY CHECK: skip implausibly large single records (FEC data errors)
            if amt > MAX_PLAUSIBLE_SINGLE_EXPENDITURE:
                skipped_anomalies += 1
                cmt = r.get("committee_id", "")
                date = r.get("expenditure_date", "")
                print(f"      ⚠ ANOMALY: skipped ${amt:,} from {cmt} on {date}", flush=True)
                continue
            # WARN on large but plausible records for review
            if amt > WARN_THRESHOLD:
                cmt = r.get("committee_id", "")
                date = r.get("expenditure_date", "")
                print(f"      ℹ large record: ${amt:,} from {cmt} on {date} (kept)", flush=True)
            so = (r.get("support_oppose_indicator") or "").upper()
            cid = r.get("committee_id")
            cname = r.get("committee", {}).get("name") if isinstance(r.get("committee"), dict) else None
            cname = cname or r.get("committee_name") or "Unknown"
            cycle = r.get("election_cycle") or r.get("report_year")
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
        
        pagination = data.get("pagination", {})
        pages = pagination.get("pages", 1)
        if page >= pages:
            break
        page += 1
        time.sleep(SLEEP_BETWEEN_CALLS)
    
    if skipped_anomalies:
        print(f"      ⚠ skipped {skipped_anomalies} implausibly-large records", flush=True)
    
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
    """Aggregate outside spending across all of a member's candidate IDs."""
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
        result = fetch_outside_spending_for_candidate(cid, api_key)
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
    }


def save(data):
    """Atomic write."""
    tmp = OUT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(OUT_FILE)


def main():
    api_key = get_api_key()
    
    if not REFETCH_FILE.exists():
        sys.exit(f"ERROR: {REFETCH_FILE} not found. Run audit_candidate_ids.py first.")
    if not FEC_FILE.exists():
        sys.exit(f"ERROR: {FEC_FILE} not found")
    
    refetch_names = json.loads(REFETCH_FILE.read_text())
    fec_data = json.loads(FEC_FILE.read_text())
    out_data = {}
    if OUT_FILE.exists():
        out_data = json.loads(OUT_FILE.read_text())
    
    print(f"Re-fetching outside spending for {len(refetch_names)} members", flush=True)
    print(f"Existing outside_spending.json has {len(out_data)} entries", flush=True)
    
    fetched = 0
    failed = []
    
    for i, name in enumerate(refetch_names, 1):
        rec = fec_data.get(name)
        if not rec:
            print(f"[{i:4d}/{len(refetch_names)}] {name:<35} (NOT IN fec.json — skip)", flush=True)
            continue
        
        result = fetch_member_outside_spending(name, rec, api_key)
        if result is None:
            print(f"[{i:4d}/{len(refetch_names)}] {name:<35} FAILED", flush=True)
            failed.append(name)
            continue
        
        out_data[name] = result
        fetched += 1
        sup = result["total_supporting"]
        opp = result["total_opposing"]
        print(
            f"[{i:4d}/{len(refetch_names)}] {name:<35} "
            f"support=${sup:>14,}  oppose=${opp:>13,}  "
            f"top={len(result['top_supporters'])}",
            flush=True,
        )
        
        if fetched % SAVE_EVERY == 0:
            save(out_data)
            print(f"      ── Saved progress ──", flush=True)
        
        time.sleep(SLEEP_BETWEEN_CALLS)
    
    save(out_data)
    
    print(f"\n{'='*60}")
    print(f"REFETCH COMPLETE")
    print(f"  Successfully refetched: {fetched}")
    print(f"  Failed: {len(failed)}")
    if failed:
        print(f"  Failed members:")
        for n in failed:
            print(f"    - {n}")


if __name__ == "__main__":
    main()
