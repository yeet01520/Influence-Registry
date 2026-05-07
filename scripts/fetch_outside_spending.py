#!/usr/bin/env python3
"""
fetch_outside_spending.py

Fetches Schedule E (independent expenditures) data from the FEC API for every
member in data/fec.json, aggregates per-member totals (supporting vs opposing)
and the top spenders, and writes the result to data/outside_spending.json.

Schedule E captures Super PAC and outside-group spending that supports or opposes
a candidate but is NOT coordinated with the candidate's campaign. This is where
the big-money influence often lives (Thiel's $15M to Vance via Protect Ohio Values,
Bloomberg's spending for various Dems, Senate Leadership Fund, etc.) - none of
which appears in the candidate's own committee receipts.

Output schema per member:
{
  "candidate_ids_used": ["S2OH00436", ...],
  "total_supporting": 15234567,
  "total_opposing": 234567,
  "cycles": [2022, 2024],
  "top_supporters": [
    {"committee_id": "C00...", "committee_name": "Protect Ohio Values", "amount": 15000000},
    ...
  ],
  "top_opposers": [
    {"committee_id": "C00...", "committee_name": "Senate Majority PAC", "amount": 234567},
    ...
  ],
  "fetched_at": "2026-05-07T..."
}

Designed to mirror refresh_total_raised.py:
- Same retry/backoff pattern
- Checkpoints every 25 members to a sidecar file
- Resumes if killed mid-run (skips members already in checkpoint)
- Tuned for upgraded FEC API quota (7,200/hour)
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

# ---------- Tunables (mirror refresh_total_raised.py) ----------
SLEEP_BETWEEN_CALLS = 0.6   # seconds; 6,000/hr safely under 7,200 limit
SAVE_EVERY          = 25    # checkpoint every N members
MAX_RETRIES         = 5
RATE_LIMIT_WAIT     = 30    # seconds
TOP_N_SPENDERS      = 5     # how many top supporters/opposers to keep per member

API_BASE   = "https://api.open.fec.gov/v1"
DATA_DIR   = Path(__file__).resolve().parent.parent / "data"
FEC_FILE   = DATA_DIR / "fec.json"
OUT_FILE   = DATA_DIR / "outside_spending.json"
PROG_FILE  = DATA_DIR / "outside_spending.json.progress"


def get_api_key():
    key = os.environ.get("FEC_API_KEY")
    if not key:
        sys.exit("ERROR: FEC_API_KEY environment variable not set")
    return key


def fec_get(path, params, api_key):
    """GET from FEC API with retries. Returns parsed JSON or None on failure."""
    params = {**params, "api_key": api_key}
    url = f"{API_BASE}{path}?{urlencode(params)}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "InfluenceRegistry/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Rate limited; wait longer
                wait = RATE_LIMIT_WAIT * attempt
                print(f"      rate-limited (429), sleep {wait}s...", flush=True)
                time.sleep(wait)
                continue
            if 500 <= e.code < 600:
                print(f"      HTTP {e.code} on attempt {attempt}, retry...", flush=True)
                time.sleep(2 * attempt)
                continue
            # 4xx other than 429: don't retry, candidate may not exist
            print(f"      HTTP {e.code} (not retrying): {url[:100]}", flush=True)
            return None
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"      network error attempt {attempt}: {e}, retry...", flush=True)
            time.sleep(2 * attempt)
        except Exception as e:
            print(f"      unexpected error attempt {attempt}: {e}", flush=True)
            time.sleep(2 * attempt)
    print(f"      FAILED after {MAX_RETRIES} attempts", flush=True)
    return None


def fetch_outside_spending_for_candidate(cand_id, api_key):
    """
    Fetch all Schedule E records for a candidate and aggregate by:
    - support vs oppose totals
    - top spending committees in each direction
    - cycles covered
    
    Uses the schedule_e endpoint with candidate_id_checked filter.
    Paginates through all results.
    """
    by_committee_support = {}  # committee_id -> {name, amount}
    by_committee_oppose  = {}
    total_support = 0
    total_oppose  = 0
    cycles_seen = set()
    
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
        
        # Pagination check
        pagination = data.get("pagination", {})
        pages = pagination.get("pages", 1)
        if page >= pages:
            break
        page += 1
        time.sleep(SLEEP_BETWEEN_CALLS)
    
    # Build top-N lists, sorted by amount desc
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
        # Merge top spenders across cand_ids
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


def load_progress():
    """Load existing outside_spending.json if present (resume across workflow runs)."""
    if OUT_FILE.exists():
        try:
            return json.loads(OUT_FILE.read_text())
        except Exception as e:
            print(f"WARN: could not parse existing {OUT_FILE.name}: {e}", flush=True)
            return {}
    # Fall back to sidecar file (for backwards compat with old runs)
    if PROG_FILE.exists():
        try:
            return json.loads(PROG_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_progress(out_data):
    """Write checkpoint atomically to the actual output file so it gets committed."""
    tmp = OUT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(out_data, indent=2))
    tmp.replace(OUT_FILE)


def main():
    api_key = get_api_key()
    fec_data = json.loads(FEC_FILE.read_text())
    members = list(fec_data.keys())
    print(f"Loaded {len(members)} members from {FEC_FILE.name}", flush=True)
    
    # Resume from progress file if present
    out_data = load_progress()
    done_count = len(out_data)
    if done_count > 0:
        print(f"Resuming: {done_count} members already done", flush=True)
    
    skipped_no_id = 0
    fetched_now = 0
    failed = []
    
    for i, name in enumerate(members, 1):
        if name in out_data:
            continue
        rec = fec_data[name]
        cand_ids = rec.get("all_candidate_ids") or [rec.get("candidate_id")]
        cand_ids = [c for c in cand_ids if c]
        if not cand_ids:
            print(f"[{i:4d}/{len(members)}] {name:<35} (no candidate_id; skip)", flush=True)
            skipped_no_id += 1
            out_data[name] = {
                "candidate_ids_used": [],
                "total_supporting": 0,
                "total_opposing": 0,
                "cycles": [],
                "top_supporters": [],
                "top_opposers": [],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "note": "no candidate_id available",
            }
            continue
        
        result = fetch_member_outside_spending(name, rec, api_key)
        if result is None:
            print(f"[{i:4d}/{len(members)}] {name:<35} FAILED", flush=True)
            failed.append(name)
            continue
        
        out_data[name] = result
        fetched_now += 1
        sup = result["total_supporting"]
        opp = result["total_opposing"]
        n_top = len(result["top_supporters"])
        print(
            f"[{i:4d}/{len(members)}] {name:<35} "
            f"support=${sup:>14,}  oppose=${opp:>10,}  "
            f"top_spenders={n_top}",
            flush=True,
        )
        
        if fetched_now % SAVE_EVERY == 0:
            save_progress(out_data)
            print(f"      ── Saved progress ({len(out_data)} total) ──", flush=True)
        
        time.sleep(SLEEP_BETWEEN_CALLS)
    
    # Final save: write to actual output file (and clean up progress)
    OUT_FILE.write_text(json.dumps(out_data, indent=2))
    if PROG_FILE.exists():
        PROG_FILE.unlink()
    
    print("=" * 70, flush=True)
    print(f"Done. {len(out_data)} members in output file.", flush=True)
    print(f"  newly fetched: {fetched_now}", flush=True)
    print(f"  skipped (no candidate_id): {skipped_no_id}", flush=True)
    print(f"  failed: {len(failed)}", flush=True)
    if failed:
        print("  failed names:", flush=True)
        for n in failed:
            print(f"    - {n}", flush=True)


if __name__ == "__main__":
    main()
