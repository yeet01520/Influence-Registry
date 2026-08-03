#!/usr/bin/env python3
"""
fetch_outside_spending.py
=========================
The ONE script for outside spending. Replaces both the old
fetch_outside_spending.py and resolve_committee_names.py.

Writes data/outside_spending.json and nothing else. No .before_names,
.before_v2 or .before_v3 side files: those exist because each past fix was a
separate pass that patched the JSON in place and left a backup behind. All of
those passes are folded in here, so one run produces the finished file and git
history is the only backup needed.

OUTPUT, PER MEMBER
------------------
{
  "candidate_ids_used": ["S4OH00192"],
  "total_supporting":  69832691,
  "total_opposing":    85641468,
  "cycles":            [2024, 2026],
  "top_supporters":    [{"committee_id", "committee_name", "amount"}, ...],
  "top_opposers":      [...],
  "fetched_at":        "2026-..."
}

THE THREE BUGS THIS EXISTS NOT TO REPEAT
----------------------------------------
1. CURRENT CYCLE ONLY.
   The schedule_e endpoints default to the current two-year period when given
   no cycle filter. That is how this file came to report $924,425 of outside
   spending on Bernie Moreno while FEC attributes $155.9M to his candidacy,
   and how Thom Tillis fell from $146.9M to $1.4M. Every request below names
   its cycle explicitly.

2. NOTICE DOUBLE-COUNTING.
   Raw Schedule E carries 24- and 48-hour notice filings later superseded by
   the full report, so summing every row counts that money twice. Totals come
   from schedule_e/by_candidate, FEC's own aggregation; the detail endpoint is
   queried with is_notice=false and de-duplicated on sub_id.

3. "Unknown" COMMITTEE NAMES.
   Schedule E rows do not reliably carry committee_name, which is why a
   separate resolve_committee_names.py pass ran afterwards. Names are resolved
   inline from /committee/{id}/ through a run-wide cache, so a committee shared
   by fifty members costs one lookup.

WHY TOTALS AND RANKINGS USE DIFFERENT ENDPOINTS
-----------------------------------------------
by_candidate gives correct totals but no committee breakdown. The detail
endpoint gives the breakdown but is easy to over-count. So totals come from
by_candidate, and detail rows are used ONLY to rank who spent the most. If the
detail pull is incomplete the list is shorter; the totals stay right.

Useful consequence: these totals match ie_support_total / ie_oppose_total in
fec.json by construction, since that field is built from the same endpoint. The
member profile and the At-a-Glance chart therefore cannot disagree, which is
the discrepancy that prompted this rewrite.

RESUME
------
Checkpoints every SAVE_EVERY members and skips members already recorded, so a
killed run picks up where it stopped. For a CLEAN rebuild delete both
data/outside_spending.json.progress and data/outside_spending.json first.
Merging a fresh run into an existing file is what left 339 entries on the
narrow window while 214 kept career-wide data.

USAGE
-----
  export FEC_API_KEY="..."
  python3 scripts/fetch_outside_spending.py
  python3 scripts/fetch_outside_spending.py --limit 10    # smoke test
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
SLEEP_BETWEEN_CALLS = 0.6    # 6,000/hr, safely under the 7,200 upgraded limit
SAVE_EVERY          = 25     # checkpoint every N members
MAX_RETRIES         = 5
RATE_LIMIT_WAIT     = 30     # seconds
TOP_N_SPENDERS      = 5
CYCLES              = [2018, 2020, 2022, 2024, 2026]

API_BASE   = "https://api.open.fec.gov/v1"
DATA_DIR   = Path(__file__).resolve().parent.parent / "data"
FEC_FILE   = DATA_DIR / "fec.json"
OUT_FILE   = DATA_DIR / "outside_spending.json"
PROG_FILE  = DATA_DIR / "outside_spending.json.progress"

LIMIT = None
if "--limit" in sys.argv:
    _i = sys.argv.index("--limit")
    if _i + 1 < len(sys.argv) and sys.argv[_i + 1].isdigit():
        LIMIT = int(sys.argv[_i + 1])

_NAME_CACHE = {}
_STATS = {"calls": 0, "name_lookups": 0, "name_cache_hits": 0}


def get_api_key():
    key = os.environ.get("FEC_API_KEY")
    if not key:
        sys.exit("ERROR: FEC_API_KEY environment variable not set")
    return key


def fec_get(path, params, api_key):
    """GET from the FEC API with retries. Returns parsed JSON or None."""
    params = {**params, "api_key": api_key}
    url = f"{API_BASE}{path}?{urlencode(params)}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _STATS["calls"] += 1
            req = urllib.request.Request(url, headers={"User-Agent": "InfluenceRegistry/1.0"})
            with urllib.request.urlopen(req, timeout=40) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = RATE_LIMIT_WAIT * attempt
                print(f"      rate limited (429), waiting {wait}s", flush=True)
                time.sleep(wait)
            elif e.code in (400, 404, 422):
                return None
            elif attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
            else:
                print(f"      FAILED after {MAX_RETRIES} attempts: HTTP {e.code}", flush=True)
                return None
        except Exception:
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
            else:
                return None
    return None


def committee_name(cid, fallback, api_key):
    """
    Resolve a committee's display name, cached for the whole run.

    Schedule E often omits committee_name, which is why a separate pass used to
    run afterwards and leave a .before_names backup. Doing it here costs one
    lookup per committee for the entire file rather than one per member.
    """
    if fallback and fallback != "Unknown":
        return fallback
    if not cid:
        return "Unknown"
    if cid in _NAME_CACHE:
        _STATS["name_cache_hits"] += 1
        return _NAME_CACHE[cid] or "Unknown"
    _STATS["name_lookups"] += 1
    data = fec_get(f"/committee/{cid}/", {}, api_key)
    res = (data or {}).get("results") or []
    name = (res[0].get("name") or res[0].get("committee_name")) if res else None
    _NAME_CACHE[cid] = name
    time.sleep(SLEEP_BETWEEN_CALLS)
    return name or "Unknown"


def candidate_totals(cand_id, api_key):
    """Authoritative support/oppose totals. Returns (support, oppose, cycles)."""
    support = oppose = 0.0
    cycles_seen = set()
    for cycle in CYCLES:
        data = fec_get("/schedules/schedule_e/by_candidate/",
                       {"candidate_id": cand_id, "cycle": cycle, "per_page": 100}, api_key)
        for r in ((data or {}).get("results") or []):
            amt = float(r.get("total") or 0)
            if amt <= 0:
                continue
            cycles_seen.add(cycle)
            if (r.get("support_oppose_indicator") or "").upper() == "O":
                oppose += amt
            else:
                support += amt
        time.sleep(SLEEP_BETWEEN_CALLS)
    return support, oppose, cycles_seen


def candidate_top_spenders(cand_id, api_key):
    """Rank the committees behind the spending. Detail rows only, never totals."""
    sup, opp = {}, {}
    seen = set()
    for cycle in CYCLES:
        page = 1
        while True:
            data = fec_get("/schedules/schedule_e/", {
                "candidate_id": cand_id, "cycle": cycle, "is_notice": "false",
                "per_page": 100, "page": page, "sort": "-expenditure_date",
            }, api_key)
            results = (data or {}).get("results") or []
            if not results:
                break
            for r in results:
                amt = r.get("expenditure_amount") or 0
                if amt <= 0:
                    continue
                row = r.get("sub_id") or "|".join(str(r.get(k)) for k in
                        ("transaction_id", "committee_id", "expenditure_date", "expenditure_amount"))
                if row in seen:
                    continue
                seen.add(row)
                cid = r.get("committee_id")
                if not cid:
                    continue
                raw = r.get("committee", {}).get("name") if isinstance(r.get("committee"), dict) else None
                raw = raw or r.get("committee_name")
                bucket = opp if (r.get("support_oppose_indicator") or "").upper() == "O" else sup
                if cid not in bucket:
                    bucket[cid] = {"name": raw, "amount": 0}
                bucket[cid]["amount"] += amt
                if raw and raw != "Unknown" and not bucket[cid]["name"]:
                    bucket[cid]["name"] = raw
            pagination = (data or {}).get("pagination") or {}
            if page >= (pagination.get("pages") or 1):
                break
            page += 1
            time.sleep(SLEEP_BETWEEN_CALLS)
        time.sleep(SLEEP_BETWEEN_CALLS)
    return sup, opp


def top_list(bucket, api_key):
    top = sorted(bucket.items(), key=lambda kv: kv[1]["amount"], reverse=True)[:TOP_N_SPENDERS]
    return [{"committee_id": cid,
             "committee_name": committee_name(cid, v["name"], api_key),
             "amount": int(v["amount"])} for cid, v in top]


def fetch_member(rec, api_key):
    cand_ids = rec.get("all_candidate_ids") or [rec.get("candidate_id")]
    cand_ids = [c for c in cand_ids if c]
    if not cand_ids:
        return None
    total_s = total_o = 0.0
    cycles = set()
    sup_all, opp_all = {}, {}
    for cid in cand_ids:
        s, o, cyc = candidate_totals(cid, api_key)
        total_s += s
        total_o += o
        cycles |= cyc
        sup, opp = candidate_top_spenders(cid, api_key)
        for src, dst in ((sup, sup_all), (opp, opp_all)):
            for k, v in src.items():
                if k not in dst:
                    dst[k] = {"name": v["name"], "amount": 0}
                dst[k]["amount"] += v["amount"]
                dst[k]["name"] = dst[k]["name"] or v["name"]
    return {
        "candidate_ids_used": cand_ids,
        "total_supporting": int(round(total_s)),
        "total_opposing": int(round(total_o)),
        "cycles": sorted(cycles),
        "top_supporters": top_list(sup_all, api_key),
        "top_opposers": top_list(opp_all, api_key),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def load_progress():
    if PROG_FILE.exists():
        try:
            return json.loads(PROG_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_progress(out_data):
    tmp = PROG_FILE.with_suffix(".ptmp")
    tmp.write_text(json.dumps(out_data, indent=2))
    tmp.replace(PROG_FILE)
    tmp2 = OUT_FILE.with_suffix(".jtmp")
    tmp2.write_text(json.dumps(out_data, indent=2))
    tmp2.replace(OUT_FILE)


def main():
    api_key = get_api_key()
    fec_data = json.loads(FEC_FILE.read_text())
    members = [k for k in fec_data.keys() if k != "_meta"]
    if LIMIT:
        members = members[:LIMIT]
    print(f"Loaded {len(members)} members from {FEC_FILE.name}", flush=True)
    print(f"Cycles: {CYCLES}", flush=True)

    out_data = load_progress()
    if out_data:
        print(f"Resuming: {len(out_data)} already done. Delete {PROG_FILE.name} "
              f"and {OUT_FILE.name} for a clean rebuild.", flush=True)

    started = time.time()
    fetched = skipped = 0

    for i, name in enumerate(members, 1):
        if name in out_data:
            continue
        result = fetch_member(fec_data[name], api_key)
        if result is None:
            print(f"[{i:4d}/{len(members)}] {name:<34} (no candidate_id; skip)", flush=True)
            skipped += 1
            out_data[name] = {"candidate_ids_used": [], "total_supporting": 0,
                              "total_opposing": 0, "cycles": [], "top_supporters": [],
                              "top_opposers": [], "note": "no candidate_id available",
                              "fetched_at": datetime.now(timezone.utc).isoformat()}
            continue
        out_data[name] = result
        fetched += 1
        print(f"[{i:4d}/{len(members)}] {name:<34} "
              f"support=${result['total_supporting']:>13,}  "
              f"oppose=${result['total_opposing']:>13,}  "
              f"cycles={result['cycles']}", flush=True)
        if fetched % SAVE_EVERY == 0:
            save_progress(out_data)
            print(f"      \u2500\u2500 Saved ({len(out_data)} total, "
                  f"{(time.time()-started)/60:.0f} min, {_STATS['calls']} calls) \u2500\u2500", flush=True)

    OUT_FILE.write_text(json.dumps(out_data, indent=2))
    if PROG_FILE.exists():
        PROG_FILE.unlink()

    unknown = sum(1 for v in out_data.values() if isinstance(v, dict)
                  for lst in (v.get("top_supporters", []), v.get("top_opposers", []))
                  for c in lst if c.get("committee_name") == "Unknown")

    print("=" * 70, flush=True)
    print(f"Done. {len(out_data)} members in {OUT_FILE.name}.", flush=True)
    print(f"  newly fetched: {fetched}   skipped (no candidate_id): {skipped}", flush=True)
    print(f"  API calls: {_STATS['calls']}   committee lookups: {_STATS['name_lookups']} "
          f"(cache hits {_STATS['name_cache_hits']})", flush=True)
    print(f"  committees still named Unknown: {unknown}", flush=True)
    print(f"  elapsed: {(time.time()-started)/60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
