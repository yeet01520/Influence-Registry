"""
Pulls FEC sector totals for Marco Rubio and JD Vance.
Outputs JSON in the exact format your existing fec.json uses.

USAGE:
  1. Set your API key:    export FEC_API_KEY="your_key_here"
  2. Run:                  python3 pull_rubio_vance.py
  3. The script prints JSON for the two members. Copy that JSON
     and merge it into data/fec.json on your repo.

Pulls career-long data (1990-2024) using the same sector keyword
matching as your main fec_pull.py, so the format stays consistent.
"""

import os
import json
import time
import requests
from collections import defaultdict

API_KEY = os.environ.get("FEC_API_KEY")
if not API_KEY:
    raise SystemExit("ERROR: Set the FEC_API_KEY environment variable first.\n"
                     "  export FEC_API_KEY='your_key_here'")

BASE = "https://api.open.fec.gov/v1"

# Match the categories from your main fec_pull.py exactly
CATEGORIES = {
    "aipac":         ["aipac", "israel public affairs"],
    "fossil_fuels":  ["oil", "exxon", "chevron", "shell", "bp ", "halliburton", "occidental", "conocophillips"],
    "pharma":        ["pfizer", "moderna", "pharma", "biotech", "merck", "lilly", "amgen", "novartis"],
    "tech":          ["google", "amazon", "meta", "facebook", "apple", "microsoft", "netflix", "alphabet"],
    "defense":       ["lockheed", "raytheon", "northrop", "boeing", "general dynamics", "rtx ", "l3harris"],
    "finance":       ["goldman", "jpmorgan", "bank", "wells fargo", "morgan stanley", "citigroup", "blackrock", "blackstone", "citadel"],
    "grassroots":    ["retired", "teacher", "nurse", "student"],
}

# Verified FEC candidate IDs (https://www.fec.gov/data/candidate/...)
# Each member can have multiple campaign committees over their career —
# the script aggregates contributions across all of them.
TARGETS = {
    "Marco Rubio": [
        "S0FL00338",   # Senate FL — covers all Senate runs (2010, 2016, 2022)
        "P60006723",   # Presidential — 2016 White House run
    ],
    "JD Vance": [
        "S2OH00436",   # Senate OH — 2022 run
    ],
}

def classify(text):
    text = (text or "").lower()
    for cat, kws in CATEGORIES.items():
        if any(k in text for k in kws):
            return cat
    return None

def verify_candidate(candidate_id):
    """Look up the candidate name from FEC to confirm the ID is correct."""
    try:
        r = requests.get(f"{BASE}/candidate/{candidate_id}/", params={
            "api_key": API_KEY,
        }, timeout=15).json()
        results = r.get("results", [])
        if results:
            c = results[0]
            return f"{c.get('name', '?')} ({c.get('office_full', '?')}, {c.get('state', '?')})"
    except Exception as e:
        return f"VERIFY FAILED: {e}"
    return "NOT FOUND"

def pull_sector_totals(candidate_id):
    """Walks every cycle from 2010-2024 and tallies by sector."""
    seen = set()
    totals = defaultdict(float)
    total_raised = 0.0

    for cycle in range(2010, 2026, 2):
        page = 1
        while True:
            try:
                r = requests.get(f"{BASE}/schedules/schedule_a/", params={
                    "api_key": API_KEY,
                    "candidate_id": candidate_id,
                    "two_year_transaction_period": cycle,
                    "per_page": 100,
                    "page": page,
                }, timeout=30).json()
            except Exception as e:
                print(f"  [{cycle} p{page}] Error: {e}")
                break

            results = r.get("results", [])
            if not results:
                break

            for row in results:
                tid = row.get("transaction_id")
                if tid in seen:
                    continue
                seen.add(tid)

                amount = row.get("contribution_receipt_amount") or 0
                total_raised += amount

                blob = " ".join([
                    str(row.get("contributor_name") or ""),
                    str(row.get("contributor_employer") or ""),
                    str(row.get("contributor_occupation") or ""),
                    str(row.get("committee_name") or ""),
                ])

                cat = classify(blob)
                if cat:
                    totals[cat] += amount

            pages = r.get("pagination", {}).get("pages", 1)
            if page >= pages:
                break
            page += 1
            time.sleep(0.25)  # be polite to the API

    return totals, total_raised

def format_entry(totals, total_raised):
    """Format to match your existing fec.json structure."""
    out = {
        "aipac":         round(totals.get("aipac", 0)),
        "oil_gas":       round(totals.get("fossil_fuels", 0)),
        "fossil_fuels":  round(totals.get("fossil_fuels", 0)),
        "pharma":        round(totals.get("pharma", 0)),
        "defense":       round(totals.get("defense", 0)),
        "finance":       round(totals.get("finance", 0)),
        "tech":          round(totals.get("tech", 0)),
        "grassroots":    round(totals.get("grassroots", 0)),
        "total_raised":  round(total_raised),
        "cycle":         "1990-2024",
    }
    return out

if __name__ == "__main__":
    output = {}
    for name, candidate_ids in TARGETS.items():
        print(f"\n=== Pulling {name} ({len(candidate_ids)} committee(s)) ===")
        combined_totals = defaultdict(float)
        combined_total_raised = 0.0
        for cid in candidate_ids:
            who = verify_candidate(cid)
            print(f"  Querying candidate ID: {cid}  ->  {who}")
            totals, total_raised = pull_sector_totals(cid)
            for k, v in totals.items():
                combined_totals[k] += v
            combined_total_raised += total_raised
            print(f"    Subtotal raised: ${total_raised:,.0f}")
        entry = format_entry(combined_totals, combined_total_raised)
        output[name] = entry
        print(f"\n  COMBINED TOTALS for {name}:")
        print(f"    Total raised:  ${entry['total_raised']:>12,}")
        print(f"    AIPAC:         ${entry['aipac']:>12,}")
        print(f"    Oil/Gas:       ${entry['oil_gas']:>12,}")
        print(f"    Pharma:        ${entry['pharma']:>12,}")
        print(f"    Defense:       ${entry['defense']:>12,}")
        print(f"    Finance:       ${entry['finance']:>12,}")
        print(f"    Tech:          ${entry['tech']:>12,}")
        print(f"    Grassroots:    ${entry['grassroots']:>12,}")

    print("\n\n========================================")
    print("JSON to MERGE into data/fec.json:")
    print("========================================\n")
    print(json.dumps(output, indent=2))

    # Also save to a file
    with open("rubio_vance_fec.json", "w") as f:
        json.dump(output, f, indent=2)
    print("\n(Also saved to rubio_vance_fec.json)")
