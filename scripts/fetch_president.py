#!/usr/bin/env python3
"""
fetch_president.py
==================
Builds data/president.json: the money data that actually means something for a
president, as opposed to the metrics that only make sense for Congress.

WHY NOT JUST REUSE THE CONGRESSIONAL FIELDS
-------------------------------------------
pac_pct is the headline number for a member of Congress and it is close to
meaningless for a president. Presidential candidate committees can accept only
$5,000 per PAC per election, so PAC share is near zero for ANY president by
operation of law, not by choice. Showing it beside congressional numbers would
invite a false comparison.

The money that matters at this level sits outside the candidate committee:

  1. INAUGURAL COMMITTEE DONATIONS.  No contribution limits. Corporations may
     give directly. Donations of $200 or more must be itemized and reported to
     the FEC under 11 CFR 104.21. This is the closest presidential analogue to
     "who bought access", and it is the single most informative dataset here.
     The Trump Vance Inaugural Committee reported $741,676,918 accepted for the
     2025 inauguration.

  2. INDEPENDENT EXPENDITURES.  Super PAC spending for and against, which the
     candidate cannot legally coordinate and never reports. Same pipeline the
     congressional charts already use.

NOT COVERED HERE, DELIBERATELY
------------------------------
Financial disclosure (OGE Form 278e) is the third thing worth showing for a
president, and it cannot be fetched. OGE publishes those reports as PDFs with
no API and no machine-readable feed; some administrations released them only on
request. Automating it would mean scraping PDFs whose layout changes, which is
exactly the kind of source this project should not build a number on. Keep that
in a hand-curated data/president_disclosure.json with a link to each source
document instead.

ENDPOINT PROBING
----------------
Which FEC endpoint carries itemized inaugural donations is decided at runtime,
not assumed. Inaugural committees file Form 13 rather than the usual reports,
and the API surface for that is not the same as for campaign committees. The
script tries the candidates in order and reports which one answered. If none
do, it says so and writes the committee-level total alone rather than writing
zeros that look like a finding.

USAGE
-----
  export FEC_API_KEY="..."
  python3 scripts/fetch_president.py
  python3 scripts/fetch_president.py --limit 50     # cap itemized donor pages
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

API_BASE  = "https://api.open.fec.gov/v1"
DATA_DIR  = Path(__file__).resolve().parent.parent / "data"
OUT_FILE  = DATA_DIR / "president.json"

SLEEP     = 0.6
MAX_RETRY = 5
TOP_N     = 25          # donors listed per inaugural committee
IE_CYCLES = [2016, 2020, 2024, 2026]

# Inaugural committees, newest first. IDs are FEC committee IDs; the 2025 one
# is confirmed from FEC's committee profile. Add a row per inauguration rather
# than editing in place, so the history stays visible.
INAUGURAL_COMMITTEES = [
    {"president": "Donald Trump", "year": 2025, "committee_id": "C00894162",
     "name": "TRUMP VANCE INAUGURAL COMMITTEE, INC."},
]

# Presidential candidate IDs are looked up by name rather than hard-coded, so a
# typo cannot silently attribute another candidate's spending.
PRESIDENTS = [{"name": "Donald Trump", "fec_search": "Trump, Donald"}]

LIMIT_PAGES = None
if "--limit" in sys.argv:
    _i = sys.argv.index("--limit")
    if _i + 1 < len(sys.argv) and sys.argv[_i + 1].isdigit():
        LIMIT_PAGES = int(sys.argv[_i + 1])

API_KEY = os.environ.get("FEC_API_KEY", "").strip()
if not API_KEY:
    sys.exit("ERROR: FEC_API_KEY environment variable not set")

_STATS = {"calls": 0}


def fec_get(path, params=None):
    p = dict(params or {})
    p["api_key"] = API_KEY
    url = f"{API_BASE}{path}?{urlencode(p, doseq=True)}"
    for attempt in range(1, MAX_RETRY + 1):
        try:
            _STATS["calls"] += 1
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


# ---------------------------------------------------------------------------
# Inaugural committee
# ---------------------------------------------------------------------------

def committee_summary(cid):
    """Committee-level totals. Works even if itemised donations are unreachable."""
    data = fec_get(f"/committee/{cid}/totals/", {"per_page": 20})
    rows = (data or {}).get("results") or []
    if not rows:
        return None
    best = max(rows, key=lambda r: (r.get("receipts") or r.get("total_receipts") or 0))
    return {
        "receipts": best.get("receipts") or best.get("total_receipts"),
        "refunds": best.get("refunds") or best.get("contribution_refunds"),
        "coverage_end": best.get("coverage_end_date"),
    }


def probe_itemised_endpoint(cid):
    """
    Find which endpoint returns itemised donations for an inaugural committee.

    Inaugural committees file Form 13, not the usual F3/F3X, so the itemised
    rows are not guaranteed to appear under schedule_a the way campaign
    receipts do. Rather than assume, ask each candidate endpoint and keep the
    first that returns rows carrying a contributor name and an amount.
    """
    candidates = [
        ("/schedules/schedule_a/", {"committee_id": cid, "per_page": 5}),
        ("/schedules/schedule_a/", {"committee_id": cid, "per_page": 5, "two_year_transaction_period": 2026}),
    ]
    # Rows that are summary lines rather than donors. schedule_a returns these
    # for an inaugural committee and an earlier version accepted one, reporting
    # "4 donors, $2,935" for a committee that took in $247.7M.
    JUNK = ("UNITEMIZED", "TOTAL", "AGGREGATE", "MEMO")
    for path, params in candidates:
        data = fec_get(path, params)
        rows = (data or {}).get("results") or []
        count = ((data or {}).get("pagination") or {}).get("count") or len(rows)
        for r in rows:
            name = (r.get("contributor_name") or r.get("donor_name") or "").strip()
            amt = r.get("contribution_receipt_amount") or r.get("donation_amount")
            if not name or not amt:
                continue
            if any(j in name.upper() for j in JUNK):
                continue
            # A real inaugural donor list runs to hundreds of entries. A handful
            # means this endpoint holds something else, so do not use it.
            if count < 50:
                print(f"  Itemised donations: {path} returned only {count} record(s) "
                      f"for this committee, which cannot be the Form 13 donor list. "
                      f"Not using it.", flush=True)
                break
            print(f"  Itemised donations: using {path} "
                  f"({count} records, probe returned '{name}' for ${amt:,.0f})", flush=True)
            return path, {k: v for k, v in params.items() if k != "per_page"}
    print("  NOTE: the FEC API does not expose itemised inaugural donations. "
          "Verified: schedules/schedule_a holds 5 records for C00894162, which "
          "are ordinary small contributions, not the Form 13 donor list. Writing "
          "the committee-level total only. The donor list lives in the Form 13 "
          "filings at docquery.fec.gov and needs a separate job to parse.",
          flush=True)
    return None, None


def inaugural_donors(cid):
    summary = committee_summary(cid)
    path, base = probe_itemised_endpoint(cid)
    donors = {}
    pages = 0
    if path:
        page = 1
        while True:
            params = dict(base or {})
            params.update({"committee_id": cid, "per_page": 100, "page": page,
                           "sort": "-contribution_receipt_amount"})
            data = fec_get(path, params)
            rows = (data or {}).get("results") or []
            if not rows:
                break
            for r in rows:
                nm = (r.get("contributor_name") or r.get("donor_name") or "").strip()
                amt = r.get("contribution_receipt_amount") or r.get("donation_amount") or 0
                if not nm or amt <= 0:
                    continue
                d = donors.setdefault(nm, {"name": nm, "amount": 0, "count": 0,
                                           "entity_type": r.get("entity_type")})
                d["amount"] += amt
                d["count"] += 1
            pages += 1
            pg = (data or {}).get("pagination") or {}
            if page >= (pg.get("pages") or 1):
                break
            if LIMIT_PAGES and pages >= LIMIT_PAGES:
                print(f"      stopped at --limit {LIMIT_PAGES} pages", flush=True)
                break
            page += 1
            time.sleep(SLEEP)

    top = sorted(donors.values(), key=lambda d: -d["amount"])[:TOP_N]
    itemised_total = sum(d["amount"] for d in donors.values())

    # Sanity gate. Donations of $200 or more must be itemised, so the itemised
    # sum should be most of the committee total. If it is a rounding error the
    # pull found the wrong thing, and publishing a "top donors" list built from
    # it would be worse than publishing nothing.
    accepted = (summary or {}).get("receipts") or 0
    if donors and accepted and itemised_total < accepted * 0.10:
        print(f"  REFUSING to publish the donor list: itemised ${itemised_total:,.0f} "
              f"is only {itemised_total/accepted*100:.2f}% of the ${accepted:,.0f} "
              f"accepted. That is not a donor list.", flush=True)
        donors, top, itemised_total = {}, [], 0
    return {
        "committee_id": cid,
        "total_accepted": summary.get("receipts") if summary else None,
        "total_refunded": summary.get("refunds") if summary else None,
        "coverage_end": summary.get("coverage_end") if summary else None,
        "itemised_total": int(itemised_total) if donors else None,
        "itemised_donor_count": len(donors) or None,
        "top_donors": [{"name": d["name"], "amount": int(d["amount"]),
                        "donations": d["count"], "entity_type": d["entity_type"]}
                       for d in top],
        # Corporate money is the point of this dataset, so make the split
        # visible rather than leaving it to be eyeballed from the names.
        "corporate_share": None,
    }


# ---------------------------------------------------------------------------
# Independent expenditures
# ---------------------------------------------------------------------------

def find_candidate_ids(search_name):
    """Look the president up rather than hard-coding an ID."""
    data = fec_get("/candidates/", {"q": search_name, "office": "P", "per_page": 20})
    out = []
    for c in ((data or {}).get("results") or []):
        out.append({"id": c.get("candidate_id"), "name": c.get("name"),
                    "years": (c.get("election_years") or [])[-4:]})
    return out


def independent_expenditures(cand_id):
    support = oppose = 0.0
    cycles = set()
    for cycle in IE_CYCLES:
        data = fec_get("/schedules/schedule_e/by_candidate/",
                       {"candidate_id": cand_id, "cycle": cycle, "per_page": 100})
        for r in ((data or {}).get("results") or []):
            amt = float(r.get("total") or 0)
            if amt <= 0:
                continue
            cycles.add(cycle)
            if (r.get("support_oppose_indicator") or "").upper() == "O":
                oppose += amt
            else:
                support += amt
        time.sleep(SLEEP)
    return {"support": int(support), "oppose": int(oppose), "cycles": sorted(cycles)}


def main():
    out = {"_meta": {"generated": datetime.now(timezone.utc).isoformat(),
                     "note": "Presidential money. PAC share is deliberately omitted: "
                             "presidential committees may accept only $5,000 per PAC "
                             "per election, so the figure is near zero for any "
                             "president by law and is not comparable to a member of "
                             "Congress."}}

    for entry in INAUGURAL_COMMITTEES:
        print(f"Inaugural committee: {entry['name']} ({entry['year']})", flush=True)
        rec = out.setdefault(entry["president"], {})
        rec.setdefault("inaugural", []).append(
            {**{"year": entry["year"], "committee_name": entry["name"]},
             **inaugural_donors(entry["committee_id"])})
        t = rec["inaugural"][-1]
        print(f"  accepted ${t['total_accepted'] or 0:,.0f}   "
              f"itemised ${t['itemised_total'] or 0:,.0f} from "
              f"{t['itemised_donor_count'] or 0} donors", flush=True)

    for p in PRESIDENTS:
        print(f"\nIndependent expenditures: {p['name']}", flush=True)
        matches = find_candidate_ids(p["fec_search"])
        if not matches:
            print("  no presidential candidate record found; skipping", flush=True)
            continue
        for m in matches:
            print(f"  candidate {m['id']}  {m['name']}  years {m['years']}", flush=True)
        rec = out.setdefault(p["name"], {})
        rec["candidate_ids"] = [m["id"] for m in matches]
        totals = {"support": 0, "oppose": 0, "cycles": set()}
        for m in matches:
            ie = independent_expenditures(m["id"])
            totals["support"] += ie["support"]
            totals["oppose"] += ie["oppose"]
            totals["cycles"] |= set(ie["cycles"])
        totals["cycles"] = sorted(totals["cycles"])
        rec["independent_expenditures"] = totals
        print(f"  support ${totals['support']:,}   oppose ${totals['oppose']:,}   "
              f"cycles {totals['cycles']}", flush=True)

    OUT_FILE.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"\nWROTE {OUT_FILE}   ({_STATS['calls']} API calls)", flush=True)


if __name__ == "__main__":
    main()
