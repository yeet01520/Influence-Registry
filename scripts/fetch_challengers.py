#!/usr/bin/env python3
"""
fetch_challengers.py
====================
Builds data/challengers.json from FEC filings, and adds the new names to
data/fec.json so the existing money pipelines pick them up.

WHY BOTH FILES
--------------
A challenger card renders money from fec.json, not from challengers.json. The
seven hand-added challengers work today only because they also happen to sit in
fec.json. Add a name to challengers.json alone and the card comes out empty, so
this writes to both:

  challengers.json  the roster the Challengers tab reads
  fec.json          a stub with name + candidate_id, which refresh_total_raised,
                    fetch_pac_individual_split and fetch_outside_spending then
                    fill in on their next run

THE FILTERING PROBLEM
---------------------
Thousands of people file for federal office each cycle and most raise nothing.
Listing them all would bury the handful who matter. So candidates are kept only
above a receipts threshold, and because the right threshold depends on what the
data looks like this cycle, --dry-run prints the distribution at several cutoffs
BEFORE anything is written. Set MIN_SENATE / MIN_HOUSE from that, then run for
real.

WHAT COUNTS AS A CHALLENGER
---------------------------
FEC's incumbent_challenge field: C = challenger, O = open seat. Incumbents (I)
are excluded, since they are already in the roster. Open-seat candidates are
included because a special election with no incumbent is exactly the case this
was asked for.

HAND-ADDED ENTRIES ARE PRESERVED
--------------------------------
Anything already in challengers.json that this run does not find is kept, not
dropped. Someone may have added a name deliberately before FEC shows money for
them.

USAGE
-----
  export FEC_API_KEY="..."
  python3 scripts/fetch_challengers.py --dry-run     # see the distribution
  python3 scripts/fetch_challengers.py               # write both files
"""

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
import urllib.request
import urllib.error

DATA = Path(__file__).resolve().parent.parent / "data"
CHAL_FILE = DATA / "challengers.json"
FEC_FILE = DATA / "fec.json"

# Cycles to sweep. 2026 is the general; specials that fall in the odd year are
# reported under the even-year cycle by FEC, so one entry covers both.
ELECTION_YEARS = [2026]

# Receipts floors. Tune these from what --dry-run reports rather than guessing:
# a threshold that is right in one cycle is wrong in the next.
MIN_SENATE = 250_000
MIN_HOUSE = 150_000

API_BASE = "https://api.open.fec.gov/v1"
SLEEP = 0.6
MAX_RETRY = 5

DRY = "--dry-run" in sys.argv
API_KEY = os.environ.get("FEC_API_KEY", "").strip()
if not API_KEY:
    sys.exit("ERROR: FEC_API_KEY environment variable not set")

_stats = {"calls": 0}


def fec_get(path, params):
    p = dict(params)
    p["api_key"] = API_KEY
    url = f"{API_BASE}{path}?{urlencode(p, doseq=True)}"
    for attempt in range(1, MAX_RETRY + 1):
        try:
            _stats["calls"] += 1
            req = urllib.request.Request(url, headers={"User-Agent": "InfluenceRegistry/1.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 30 * attempt
                print(f"      rate limited, waiting {wait}s", flush=True)
                time.sleep(wait)
            elif e.code in (400, 404, 422):
                return None
            elif attempt < MAX_RETRY:
                time.sleep(2 ** attempt)
            else:
                return None
        except Exception:
            if attempt < MAX_RETRY:
                time.sleep(2 ** attempt)
            else:
                return None
    return None


def list_candidates(office, year):
    """Every statutory candidate for one office and cycle, challengers and open seats."""
    out, page = [], 1
    while True:
        data = fec_get("/candidates/", {
            "election_year": year, "office": office, "candidate_status": "C",
            "incumbent_challenge": ["C", "O"], "has_raised_funds": "true",
            "per_page": 100, "page": page, "sort": "name",
        })
        rows = (data or {}).get("results") or []
        if not rows:
            break
        out.extend(rows)
        pg = (data or {}).get("pagination") or {}
        if page >= (pg.get("pages") or 1):
            break
        page += 1
        time.sleep(SLEEP)
    return out


def candidate_receipts(cid, year):
    data = fec_get(f"/candidate/{cid}/totals/", {"cycle": year, "per_page": 20})
    rows = (data or {}).get("results") or []
    return max((r.get("receipts") or 0) for r in rows) if rows else 0


def tidy_name(fec_name):
    """FEC stores 'LAST, FIRST MIDDLE'. The site uses 'First Last'."""
    if "," not in fec_name:
        return fec_name.title()
    last, first = fec_name.split(",", 1)
    first = " ".join(w for w in first.split() if len(w) > 1 or w.endswith("."))
    return f"{first.strip().title()} {last.strip().title()}"


def main():
    print(f"Cycles: {ELECTION_YEARS}")
    print(f"Mode: {'DRY RUN (nothing written)' if DRY else 'WRITE'}\n")

    found = []
    for year in ELECTION_YEARS:
        for office, label in (("S", "Senate"), ("H", "House")):
            cands = list_candidates(office, year)
            print(f"  {label} {year}: {len(cands)} candidates who raised funds", flush=True)
            for i, c in enumerate(cands, 1):
                cid = c.get("candidate_id")
                if not cid:
                    continue
                rec = candidate_receipts(cid, year)
                found.append({
                    "cid": cid, "fec_name": c.get("name") or "",
                    "name": tidy_name(c.get("name") or ""),
                    "party": (c.get("party") or "")[:1],
                    "state": c.get("state"), "district": c.get("district"),
                    "chamber": label, "receipts": rec, "year": year,
                    "open_seat": (c.get("incumbent_challenge") == "O"),
                })
                if i % 50 == 0:
                    print(f"      ...{i}/{len(cands)}  [{_stats['calls']} calls]", flush=True)
                time.sleep(SLEEP)

    # ---- distribution, so the threshold is chosen from evidence -------------
    print("\nReceipts distribution:")
    for label, chamber in (("Senate", "Senate"), ("House", "House")):
        vals = sorted((f["receipts"] for f in found if f["chamber"] == chamber), reverse=True)
        if not vals:
            continue
        print(f"  {label}: {len(vals)} candidates")
        for cut in (2_000_000, 1_000_000, 500_000, 250_000, 150_000, 50_000):
            n = sum(1 for v in vals if v >= cut)
            print(f"      >= ${cut:>9,}: {n:>4}")

    keep = [f for f in found
            if f["receipts"] >= (MIN_SENATE if f["chamber"] == "Senate" else MIN_HOUSE)]
    print(f"\nAbove the configured floors "
          f"(Senate ${MIN_SENATE:,} / House ${MIN_HOUSE:,}): {len(keep)}")

    if DRY:
        print("\nTop 20 that would be added:")
        for f in sorted(keep, key=lambda x: -x["receipts"])[:20]:
            seat = f["state"] + (f"-{f['district']}" if f["district"] and f["district"] != "00" else "")
            print(f"   {f['name']:26s} {f['party']:1s} {seat:8s} ${f['receipts']:>12,.0f}"
                  + ("  (open seat)" if f["open_seat"] else ""))
        print("\nDRY RUN: nothing written. Tune MIN_SENATE / MIN_HOUSE, then re-run.")
        return

    # ---- merge into challengers.json ---------------------------------------
    existing = json.loads(CHAL_FILE.read_text()) if CHAL_FILE.exists() else []
    by_name = {c["name"]: c for c in existing}
    next_id = max([c.get("id", 0) for c in existing] or [0]) + 1

    added = 0
    for f in sorted(keep, key=lambda x: -x["receipts"]):
        if f["name"] in by_name:
            continue
        seat = f["state"] + ("-Sen" if f["chamber"] == "Senate"
                             else f"-{f['district']}" if f["district"] else "")
        by_name[f["name"]] = {
            "name": f["name"], "party": f["party"], "state": f["state"],
            "chamber": f["chamber"],
            "role": f"{f['chamber']} Challenger" if not f["open_seat"] else f"{f['chamber']} Candidate",
            "race": f"{seat} {f['year']}",
            "opponent": None,
            "challenger": True,
            "candidate_id": f["cid"],
            "id": next_id,
        }
        next_id += 1
        added += 1

    out = sorted(by_name.values(), key=lambda c: (c.get("chamber") or "", c.get("state") or "", c["name"]))
    CHAL_FILE.write_text(json.dumps(out, indent=1, ensure_ascii=False))

    # ---- stub them into fec.json so the money pipelines fill them in --------
    fec = json.loads(FEC_FILE.read_text())
    stubbed = 0
    for c in out:
        cid = c.get("candidate_id")
        if not cid or c["name"] in fec:
            continue
        fec[c["name"]] = {"candidate_id": cid, "all_candidate_ids": [cid], "committees": []}
        stubbed += 1
    FEC_FILE.write_text(json.dumps(fec, indent=1, ensure_ascii=False))

    print(f"\nWROTE {CHAL_FILE.name}: {len(out)} challengers ({added} new)")
    print(f"WROTE {FEC_FILE.name}: {stubbed} new stub entries")
    print(f"API calls: {_stats['calls']}")
    print("\nNEXT: run Rebuild Committees, then Fix Committee IDs and Refresh Totals, "
          "then Refresh PAC vs Individual Split, then Refresh Outside Spending. "
          "Until those run the new challengers will show no money.")


if __name__ == "__main__":
    main()
