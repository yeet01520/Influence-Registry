"""
Pulls FEC sector totals for Marco Rubio and JD Vance.

Uses committee_id-based pagination of Schedule A, which is the
reliable way to get full contribution data. Aggregates across
all of each member's authorized campaign committees.

USAGE (in GitHub Actions, via the refresh-fec.yml workflow):
  Just runs. Reads FEC_API_KEY from environment.

USAGE (locally):
  export FEC_API_KEY="your_key_here"
  python3 pull_rubio_vance.py
"""

import os
import json
import time
import requests
from collections import defaultdict

API_KEY = os.environ.get("FEC_API_KEY")
if not API_KEY:
    raise SystemExit("ERROR: Set the FEC_API_KEY environment variable first.")

BASE = "https://api.open.fec.gov/v1"

CATEGORIES = {
    "aipac":         ["aipac", "israel public affairs"],
    "fossil_fuels":  ["oil", "exxon", "chevron", "shell", "halliburton", "occidental", "conocophillips", "petroleum"],
    "pharma":        ["pfizer", "moderna", "pharma", "biotech", "merck", "lilly", "amgen", "novartis", "johnson & johnson"],
    "tech":          ["google", "amazon", "meta", "facebook", "apple", "microsoft", "netflix", "alphabet", "tesla", "uber", "airbnb"],
    "defense":       ["lockheed", "raytheon", "northrop", "boeing", "general dynamics", "rtx", "l3harris"],
    "finance":       ["goldman", "jpmorgan", "wells fargo", "morgan stanley", "citigroup", "blackrock", "blackstone", "citadel", "bank of america"],
    "grassroots":    ["retired", "teacher", "nurse", "student", "homemaker", "self-employed"],
}

# Each member maps to a list of their authorized campaign committee IDs.
# Verified at https://www.fec.gov/data/candidate/{candidate_id}/
TARGETS = {
    "Marco Rubio": [
        "C00458844",   # Marco Rubio for President (2016)
        "C00620518",   # Marco Rubio for Senate (2016, 2022 cycles)
        "C00467795",   # Marco Rubio for US Senate 2010 cycle
    ],
    "JD Vance": [
        "C00783142",   # JD Vance for Senate Inc.
    ],
}

def classify(text):
    text = (text or "").lower()
    for cat, kws in CATEGORIES.items():
        if any(k in text for k in kws):
            return cat
    return None

def get_committee_totals(committee_id):
    """Get the official total raised from FEC's totals endpoint."""
    total = 0.0
    try:
        r = requests.get(f"{BASE}/committee/{committee_id}/totals/", params={
            "api_key": API_KEY,
            "per_page": 100,
        }, timeout=30).json()
        for cycle_summary in r.get("results", []):
            total += cycle_summary.get("receipts", 0) or 0
    except Exception as e:
        print(f"    [totals] Error: {e}")
    return total

def get_committee_name(committee_id):
    """Confirm the committee identity."""
    try:
        r = requests.get(f"{BASE}/committee/{committee_id}/", params={
            "api_key": API_KEY,
        }, timeout=15).json()
        results = r.get("results", [])
        if results:
            c = results[0]
            return c.get("name", "?")
    except Exception:
        pass
    return "?"

def pull_sector_totals_by_committee(committee_id):
    """
    Walk Schedule A receipts for a specific committee and tally by sector.
    Uses committee_id (NOT candidate_id) which is the reliable parameter.
    """
    totals = defaultdict(float)
    seen_ids = set()
    total_pages_seen = 0

    page = 1
    while True:
        try:
            r = requests.get(f"{BASE}/schedules/schedule_a/", params={
                "api_key": API_KEY,
                "committee_id": committee_id,
                "per_page": 100,
                "page": page,
            }, timeout=30).json()
        except Exception as e:
            print(f"    [page {page}] Error: {e}")
            break

        results = r.get("results", [])
        if not results:
            break

        for row in results:
            tid = row.get("transaction_id")
            if tid and tid in seen_ids:
                continue
            if tid:
                seen_ids.add(tid)

            amount = row.get("contribution_receipt_amount") or 0
            blob = " ".join([
                str(row.get("contributor_name") or ""),
                str(row.get("contributor_employer") or ""),
                str(row.get("contributor_occupation") or ""),
                str(row.get("committee_name") or ""),
            ])
            cat = classify(blob)
            if cat:
                totals[cat] += amount

        total_pages_seen += 1
        pagination = r.get("pagination", {})
        pages = pagination.get("pages", 1)
        if page >= pages:
            break

        page += 1
        if total_pages_seen >= 200:  # safety cap
            print(f"    Hit 200-page safety cap")
            break
        time.sleep(0.25)

    return totals, total_pages_seen

def format_entry(combined_totals, total_raised):
    return {
        "aipac":         round(combined_totals.get("aipac", 0)),
        "oil_gas":       round(combined_totals.get("fossil_fuels", 0)),
        "fossil_fuels":  round(combined_totals.get("fossil_fuels", 0)),
        "pharma":        round(combined_totals.get("pharma", 0)),
        "defense":       round(combined_totals.get("defense", 0)),
        "finance":       round(combined_totals.get("finance", 0)),
        "tech":          round(combined_totals.get("tech", 0)),
        "grassroots":    round(combined_totals.get("grassroots", 0)),
        "total_raised":  round(total_raised),
        "cycle":         "1990-2024",
    }

if __name__ == "__main__":
    output = {}
    for name, committee_ids in TARGETS.items():
        print(f"\n{'='*60}")
        print(f"Pulling {name} ({len(committee_ids)} committee(s))")
        print(f"{'='*60}")

        combined_totals = defaultdict(float)
        combined_total_raised = 0.0

        for cid in committee_ids:
            committee_name = get_committee_name(cid)
            print(f"\n  Committee {cid}: {committee_name}")

            # Official total from FEC's summary endpoint
            official_raised = get_committee_totals(cid)
            print(f"    Official total raised: ${official_raised:,.0f}")
            combined_total_raised += official_raised

            # Walk individual contributions for sector classification
            print(f"    Walking Schedule A receipts...")
            sector_totals, pages = pull_sector_totals_by_committee(cid)
            print(f"    Pages processed: {pages}")
            for k, v in sector_totals.items():
                combined_totals[k] += v
                if v > 0:
                    print(f"      {k}: ${v:,.0f}")

        entry = format_entry(combined_totals, combined_total_raised)
        output[name] = entry
        print(f"\n  COMBINED TOTALS for {name}:")
        print(f"    Total raised:  ${entry['total_raised']:>14,}")
        print(f"    AIPAC:         ${entry['aipac']:>14,}")
        print(f"    Oil/Gas:       ${entry['oil_gas']:>14,}")
        print(f"    Pharma:        ${entry['pharma']:>14,}")
        print(f"    Defense:       ${entry['defense']:>14,}")
        print(f"    Finance:       ${entry['finance']:>14,}")
        print(f"    Tech:          ${entry['tech']:>14,}")
        print(f"    Grassroots:    ${entry['grassroots']:>14,}")

    print(f"\n\n{'='*60}")
    print("JSON to MERGE into data/fec.json:")
    print(f"{'='*60}\n")
    print(json.dumps(output, indent=2))

    with open("rubio_vance_fec.json", "w") as f:
        json.dump(output, f, indent=2)
    print("\n(Also saved to rubio_vance_fec.json)")
