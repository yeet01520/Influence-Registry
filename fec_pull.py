import requests
import time
import json
import os
from collections import defaultdict

API_KEY = "y6TD120jyChOZFHcsvbAaczqs2TOHh6sgVq7MF1W"
BASE_URL = "https://api.open.fec.gov/v1/"

OUTPUT_FILE = "fec_special_interest_data.json"
CHECKPOINT_FILE = "checkpoint.json"

CATEGORIES = {
    "AIPAC": ["aipac", "israel public affairs"],
    "Fossil Fuels": ["oil", "exxon", "chevron", "shell", "bp"],
    "Pharma": ["pfizer", "moderna", "pharma", "biotech"],
    "Big Tech": ["google", "amazon", "meta", "facebook", "apple", "microsoft"],
    "Defense": ["lockheed", "raytheon", "northrop"],
    "Finance": ["goldman", "jpmorgan", "bank", "wells fargo"],
    "Grassroots": ["retired", "teacher", "nurse", "student"]
}

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    return {"processed": []}

def save_checkpoint(data):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(data, f)

def load_results():
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r") as f:
            return json.load(f)
    return []

def save_results(results):
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)

def get_candidates():
    candidates = []
    page = 1

    while True:
        res = requests.get(
            f"{BASE_URL}candidates/search/",
            params={
                "api_key": API_KEY,
                "page": page,
                "per_page": 100
            }
        ).json()

        if "results" not in res:
            break

        candidates.extend(res["results"])

        if page >= res["pagination"]["pages"]:
            break

        page += 1
        time.sleep(0.2)

    return candidates

def classify(text):
    text = (text or "").lower()
    for category, keywords in CATEGORIES.items():
        if any(k in text for k in keywords):
            return category
    return None

def get_receipts(candidate_id):
    seen = set()
    totals = defaultdict(float)

    for cycle in range(2000, 2028, 2):
        page = 1

        while True:
            res = requests.get(
                f"{BASE_URL}schedules/schedule_a/",
                params={
                    "api_key": API_KEY,
                    "candidate_id": candidate_id,
                    "two_year_transaction_period": cycle,
                    "per_page": 100,
                    "page": page
                }
            ).json()

            if "results" not in res:
                break

            for r in res["results"]:
                txn_id = r.get("transaction_id")
                if txn_id in seen:
                    continue
                seen.add(txn_id)

                amount = r.get("contribution_receipt_amount", 0)

                text = " ".join([
                    str(r.get("contributor_name", "")),
                    str(r.get("contributor_employer", "")),
                    str(r.get("contributor_occupation", "")),
                    str(r.get("committee_name", ""))
                ])

                category = classify(text)
                if category:
                    totals[category] += amount

            if page >= res["pagination"]["pages"]:
                break

            page += 1
            time.sleep(0.15)

    return totals

def main():
    checkpoint = load_checkpoint()
    processed_ids = set(checkpoint["processed"])
    results = load_results()
    candidates = get_candidates()

    for c in candidates:
        cid = c["candidate_id"]

        if cid in processed_ids:
            continue

        name = c["name"]
        print(f"Processing: {name}")

        try:
            totals = get_receipts(cid)
            total_sum = sum(totals.values())

            results.append({
                "name": name,
                "candidate_id": cid,
                "categories": dict(totals),
                "total_special_interest": total_sum
            })

            processed_ids.add(cid)

            save_results(results)
            save_checkpoint({"processed": list(processed_ids)})

        except Exception as e:
            print(f"Error: {e}")

    print("Finished.")

if __name__ == "__main__":
    main()
